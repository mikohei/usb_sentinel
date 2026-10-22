"""f3 parsers and the rw mount of the capacity step (capacity_fs.py).

The rw mount is the only mount that writes to the sample, so verify via `runner.calls`
that cleanup (delete .h2w, then umount) always happens, even on exception paths.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import F3READ_OK, F3WRITE_OK, make_statvfs
from usb_sentinel.libs import FakeRunner, StepContext
from usb_sentinel.libs.runner import CommandResult
from usb_sentinel.libs.steps.capacity_fs import (
    SPEED_UNIT_TO_MB,
    F3ReadSummary,
    MountError,
    MountpointError,
    NotWritableError,
    UnmountError,
    parse_f3read,
    parse_f3write,
    remove_h2w,
    rw_mounted,
    usage,
)


class TestParseF3Read:
    def test_parses_clean_output(self) -> None:
        summary = parse_f3read(F3READ_OK)
        assert summary == F3ReadSummary(
            ok_sectors=58984375,
            lost_sectors=0,
            corrupted_sectors=0,
            slightly_changed_sectors=0,
            overwritten_sectors=0,
            read_mb_s=62.9,
            validated=tuple(range(1, 30)),
        )
        assert summary.bad_sectors == 0
        assert summary.read_error is False

    def test_parses_data_lost(self) -> None:
        text = (
            "  Data OK: 7.50 GB (14648437 sectors)\n"
            "Data LOST: 22.70 GB (44335938 sectors)\n"
            "\t       Corrupted: 1.00 MB (2048 sectors)\n"
            "\tSlightly changed: 0.00 Byte (3 sectors)\n"
            "\t     Overwritten: 21.70 GB (44333887 sectors)\n"
            "Average reading speed: 60.00 MB/s\n"
        )
        summary = parse_f3read(text)
        assert summary.lost_sectors == 44335938
        assert summary.corrupted_sectors == 2048
        assert summary.slightly_changed_sectors == 3
        assert summary.overwritten_sectors == 44333887
        # Data LOST is already the sum of the three kinds; it is not added to them again.
        assert summary.bad_sectors == 44335938

    def test_missing_speed_line_is_none(self) -> None:
        text = "  Data OK: 1.00 GB (1953125 sectors)\nData LOST: 0.00 Byte (0 sectors)\n"
        summary = parse_f3read(text)
        assert summary.read_mb_s is None
        assert summary.corrupted_sectors == 0

    @pytest.mark.parametrize(
        "text",
        [
            "Data LOST: 0.00 Byte (0 sectors)\n",
            "  Data OK: 1.00 GB (1953125 sectors)\n",
            "f3read: cannot open directory\n",
            "",
        ],
    )
    def test_missing_required_lines_raise(self, text: str) -> None:
        with pytest.raises(ValueError, match="Could not parse f3read output"):
            parse_f3read(text)

    @pytest.mark.parametrize(
        ("unit", "expected"),
        [("KB", 1.5e-3), ("GB", 1500.0), ("TB", 1_500_000.0), ("Byte", 1.5e-6)],
    )
    def test_speed_units_are_converted_to_mb_s(self, unit: str, expected: float) -> None:
        text = (
            "  Data OK: 1.00 GB (1953125 sectors)\n"
            "Data LOST: 0.00 Byte (0 sectors)\n"
            f"Average reading speed: 1.50 {unit}/s\n"
        )
        assert parse_f3read(text).read_mb_s == pytest.approx(expected)


class TestParseF3Write:
    def test_parses_speed_line(self) -> None:
        assert parse_f3write(F3WRITE_OK) == 18.2

    def test_missing_speed_line_is_none(self) -> None:
        assert parse_f3write("Free space: 0.00 Byte\n") is None

    def test_kb_is_converted(self) -> None:
        assert parse_f3write("Average writing speed: 512.00 KB/s\n") == pytest.approx(0.512)

    def test_zero_byte_speed_is_zero_not_none(self) -> None:
        assert parse_f3write("Average writing speed: 0.00 Byte/s\n") == 0.0

    def test_tb_is_converted(self) -> None:
        assert parse_f3write("Average writing speed: 2.00 TB/s\n") == 2_000_000.0

    def test_unit_table_covers_regex_alternatives(self) -> None:
        assert set(SPEED_UNIT_TO_MB) == {"Byte", "KB", "MB", "GB", "TB"}


class TestUsage:
    def test_returns_used_and_free_bytes(self, tmp_path: Path) -> None:
        stat = make_statvfs(f_frsize=4096, f_blocks=1000, f_bfree=400, f_bavail=350)
        used, free = usage(lambda _p: stat, tmp_path)
        assert used == 600 * 4096
        assert free == 350 * 4096

    def test_oserror_is_mountpoint_error(self, tmp_path: Path) -> None:
        def failing(_path: str) -> os.statvfs_result:
            raise OSError(5, "Input/output error")

        with pytest.raises(MountpointError, match="Could not stat the mount point"):
            usage(failing, tmp_path)


class TestRemoveH2w:
    def test_removes_only_numbered_h2w(self, tmp_path: Path) -> None:
        for name in ("1.h2w", "2.h2w", "notes.txt", "x.h2w"):
            (tmp_path / name).write_text("x")
        warnings: list[str] = []
        assert remove_h2w(tmp_path, warnings) == 2
        assert sorted(p.name for p in tmp_path.iterdir()) == ["notes.txt", "x.h2w"]
        assert warnings == []

    def test_unlink_failure_is_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "1.h2w").write_text("x")

        def failing_unlink(self: Path, missing_ok: bool = False) -> None:
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(Path, "unlink", failing_unlink)
        warnings: list[str] = []
        assert remove_h2w(tmp_path, warnings) == 0
        assert warnings == ["Could not delete 1.h2w: Permission denied"]
        # The "previous run" warning is the caller's responsibility, so it is not emitted here.
        assert not any("previous run" in w for w in warnings)


class TestRwMounted:
    def _target(self, ctx: StepContext) -> Path:
        return ctx.config.mount_root / "sda" / "rw" / "0"

    def test_mounts_rw_with_safe_options_and_unmounts(
        self, ctx: StepContext, runner: FakeRunner
    ) -> None:
        runner.register(["mount"])
        runner.register(["umount"])
        commands: list[list[str]] = []
        target = self._target(ctx)

        with rw_mounted(ctx, "/dev/sda1", 0, lambda _p: make_statvfs(), [], commands) as path:
            assert path == target
            assert target.is_dir()

        assert runner.calls == [
            ("mount", "-w", "-o", "noexec,nosuid,nodev", "/dev/sda1", str(target)),
            ("umount", str(target)),
        ]
        assert commands == [list(c) for c in runner.calls]

    def test_mount_failure_does_not_umount(self, ctx: StepContext, runner: FakeRunner) -> None:
        runner.register(["mount"], returncode=32, stderr="cannot mount /dev/sda1 read-only")
        runner.register(["umount"])
        with (
            pytest.raises(MountError, match="read-only"),
            rw_mounted(ctx, "/dev/sda1", 0, lambda _p: make_statvfs(), [], []),
        ):
            pytest.fail("body must not run after mount failure")
        assert not any(c[0] == "umount" for c in runner.calls)

    def test_readonly_flag_raises_and_unmounts(self, ctx: StepContext, runner: FakeRunner) -> None:
        runner.register(["mount"])
        runner.register(["umount"])
        stat = make_statvfs(f_flag=os.ST_RDONLY)
        with (
            pytest.raises(NotWritableError, match="Could not mount read-write"),
            rw_mounted(ctx, "/dev/sda1", 0, lambda _p: stat, [], []),
        ):
            pytest.fail("body must not run when ST_RDONLY is set")
        assert any(c[0] == "umount" for c in runner.calls)

    def test_statvfs_oserror_raises_and_unmounts(
        self, ctx: StepContext, runner: FakeRunner
    ) -> None:
        runner.register(["mount"])
        runner.register(["umount"])

        def failing(_path: str) -> os.statvfs_result:
            raise OSError(5, "Input/output error")

        with (
            pytest.raises(MountpointError, match="Could not stat the mount point"),
            rw_mounted(ctx, "/dev/sda1", 0, failing, [], []),
        ):
            pytest.fail("body must not run when statvfs fails")
        assert any(c[0] == "umount" for c in runner.calls)

    def test_mkdir_failure_does_not_mount(self, ctx: StepContext, runner: FakeRunner) -> None:
        # Make mount_root a regular file so that mkdir fails.
        blocker = ctx.config.mount_root.parent / "blocker"
        blocker.write_text("not a directory")
        ctx.config = replace(ctx.config, mount_root=blocker)
        runner.register(["mount"])
        with (
            pytest.raises(MountpointError, match="^Could not create the mount point"),
            rw_mounted(ctx, "/dev/sda1", 0, lambda _p: make_statvfs(), [], []),
        ):
            pytest.fail("body must not run when mkdir fails")
        assert not any(c[0] == "mount" for c in runner.calls)

    def test_body_exception_still_unmounts(self, ctx: StepContext, runner: FakeRunner) -> None:
        runner.register(["mount"])
        runner.register(["umount"])
        with (
            pytest.raises(RuntimeError, match="boom"),
            rw_mounted(ctx, "/dev/sda1", 0, lambda _p: make_statvfs(), [], []),
        ):
            raise RuntimeError("boom")
        assert runner.calls[-1][0] == "umount"

    def test_body_exception_then_umount_failure_chains_cause(
        self, ctx: StepContext, runner: FakeRunner
    ) -> None:
        runner.register(["mount"])
        runner.register(["umount"], returncode=32, stderr="target is busy")
        with (
            pytest.raises(UnmountError, match="target is busy") as info,
            rw_mounted(ctx, "/dev/sda1", 0, lambda _p: make_statvfs(), [], []),
        ):
            raise RuntimeError("boom")
        assert isinstance(info.value.__cause__, RuntimeError)
        assert str(info.value.__cause__) == "boom"

    def test_umount_failure_after_normal_body_has_no_cause(
        self, ctx: StepContext, runner: FakeRunner
    ) -> None:
        runner.register(["mount"])
        runner.register(["umount"], returncode=32, stderr="target is busy")
        with (
            pytest.raises(UnmountError) as info,
            rw_mounted(ctx, "/dev/sda1", 0, lambda _p: make_statvfs(), [], []),
        ):
            pass
        assert info.value.__cause__ is None

    def test_h2w_removed_before_umount(
        self, ctx: StepContext, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner.register(["mount"])
        runner.register(["umount"])
        target = self._target(ctx)
        seen_at_umount: dict[str, Any] = {}
        original_run = runner.run

        def spying_run(argv: list[str], *, timeout_s: float | None = None) -> CommandResult:
            if argv[0] == "umount":
                seen_at_umount["names"] = sorted(p.name for p in target.iterdir())
            return original_run(argv, timeout_s=timeout_s)

        monkeypatch.setattr(runner, "run", spying_run)
        with rw_mounted(ctx, "/dev/sda1", 0, lambda _p: make_statvfs(), [], []) as path:
            for name in ("1.h2w", "2.h2w", "notes.txt"):
                (path / name).write_text("x")

        assert seen_at_umount["names"] == ["notes.txt"]
