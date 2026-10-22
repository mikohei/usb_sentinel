"""ERROR paths of the capacity step (CapacityStep) and cleanup of the rw mount.

Covers missing commands, timeouts, insufficient permissions, and unparsable output, and verifies
that the primary cause remains in `details["suppressed_error"]` when umount fails.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from tests.conftest import (
    F3READ_OK,
    F3READ_OK_SECTORS,
    F3WRITE_OK,
    LSBLK_DISK,
    capacity_step,
    f3read_output,
    make_statvfs,
)
from usb_sentinel.libs import FakeRunner, StepContext, StepStatus
from usb_sentinel.libs.steps.capacity import CapacityStep


def _with_timeout(ctx: StepContext, timeout_s: float) -> None:
    ctx.config = replace(ctx.config, capacity=replace(ctx.config.capacity, timeout_s=timeout_s))


class TestErrorPaths:
    def test_nearly_full_drive_is_error_not_pass(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        # With 31 GB already used and 1 GB verified, the total exceeds the threshold but there is
        # no evidence, so ERROR.
        f3_runner.register(["f3read"], stdout=f3read_output(1_953_125))
        stat = make_statvfs(f_frsize=4096, f_blocks=7_812_500, f_bfree=244_141)

        result = capacity_step(stat).run(ctx)

        assert result.status is StepStatus.ERROR
        assert "use_probe" in result.summary
        assert result.summary.startswith("Verified area")

    def test_f3read_nonzero_exit_still_judges(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(["f3read"], returncode=1, stdout=F3READ_OK)

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.PASS
        assert result.details["filesystems"][0]["f3read_returncode"] == 1

    def test_f3read_unparseable_is_error(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        f3_runner.register(["f3read"], returncode=1, stdout="garbage", stderr="cannot open")

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary.startswith("Could not parse f3read output")
        assert "cannot open" in result.summary
        assert result.details["raw_stdout_head"] == "garbage"
        assert f3_runner.calls[-1][0] == "umount"

    def test_f3write_unparseable_is_error(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        # When a speed-line number cannot be converted to float ("1.2.3"), it becomes a summary
        # shaped like the f3read side instead of a bare ValueError, raw_stdout_head is kept,
        # and umount still runs.
        f3_runner.register(
            ["f3write"], stdout="Average writing speed: 1.2.3 MB/s\n", stderr="odd output"
        )

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary.startswith("Could not parse f3write output")
        assert "odd output" in result.summary
        assert result.details["raw_stdout_head"] == "Average writing speed: 1.2.3 MB/s\n"
        assert not any(c[0] == "f3read" for c in f3_runner.calls)
        assert f3_runner.calls[-1][0] == "umount"

    def test_f3write_failure_without_speed_is_error(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(["f3write"], returncode=1, stderr="f3write: No space left")

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert "No space left" in result.summary
        assert not any(c[0] == "f3read" for c in f3_runner.calls)
        assert f3_runner.calls[-1][0] == "umount"

    def test_f3write_permission_denied(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        f3_runner.register(["f3write"], returncode=1, stderr="f3write: Read-only file system")

        result = capacity_step().run(ctx)

        assert result.summary == "Permission denied: write refused"

    def test_f3write_nonzero_with_speed_continues(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(["f3write"], returncode=1, stdout=F3WRITE_OK)

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.PASS
        assert "f3write returned exit code 1" in result.warnings

    def test_f3write_zero_without_speed_continues(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        written = "".join(f"Creating file {n}.h2w ... OK!\n" for n in range(1, 30))
        stdout = written + "Free space: 0.00 Byte\nWriting speed not available\n"
        f3_runner.register(["f3write"], returncode=0, stdout=stdout)

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.PASS
        assert result.details["filesystems"][0]["write_mb_s"] is None
        assert result.warnings == []

    def test_mount_readonly_fallback_is_permission_error(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(
            ["mount"], returncode=32, stderr="mount: cannot mount /dev/sda1 read-only."
        )

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary == "Permission denied: could not mount read-write"
        assert not any(c[0] == "umount" for c in f3_runner.calls)

    def test_mount_other_failure(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        f3_runner.register(["mount"], returncode=32, stderr="wrong fs type")

        result = capacity_step().run(ctx)

        assert result.summary.startswith("mount failed:")
        assert "wrong fs type" in result.summary

    def test_st_rdonly_is_permission_error_and_unmounts(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        result = capacity_step(make_statvfs(f_flag=os.ST_RDONLY)).run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary == "Permission denied: could not mount read-write"
        assert any(c[0] == "umount" for c in f3_runner.calls)
        assert not any(c[0] == "f3write" for c in f3_runner.calls)

    def test_mkdir_failure(self, ctx: StepContext, f3_runner: FakeRunner, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        ctx.config = replace(ctx.config, mount_root=blocker)

        result = capacity_step().run(ctx)

        assert result.summary.startswith("Could not create the mount point")
        assert "Permission denied" not in result.summary
        assert not any(c[0] == "mount" for c in f3_runner.calls)

    def test_statvfs_oserror(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        def failing(_path: str) -> os.statvfs_result:
            raise OSError(5, "Input/output error")

        result = CapacityStep(statvfs=failing, monotonic=lambda: 0.0).run(ctx)

        assert result.summary == "Could not stat the mount point"
        assert any(c[0] == "umount" for c in f3_runner.calls)

    def test_f3write_overall_timeout_unmounts(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        # The overall timeout_s is shorter than the stall cap, so a timeout is ERROR, not slow.
        _with_timeout(ctx, 14400)
        f3_runner.register(["f3write"], returncode=-1, timed_out=True)

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary == "Timed out"
        assert result.details["timed_out"] is True
        assert f3_runner.calls[-1][0] == "umount"

    def test_budget_exhausted_before_f3write(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        _with_timeout(ctx, 14400)
        clock = iter([0.0, 100_000.0, 100_000.0, 100_000.0])
        step = CapacityStep(statvfs=lambda _p: make_statvfs(), monotonic=lambda: next(clock))

        result = step.run(ctx)

        assert result.summary == "Timed out"
        assert f3_runner.calls == []

    def test_drive_without_filesystem_is_formatted(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        # A drive left blank (for example by an earlier inspection) is verified like any other.
        no_fs = json.loads(json.dumps(LSBLK_DISK))
        no_fs["blockdevices"][0]["children"] = []
        f3_runner.register(["lsblk"], stdout=json.dumps(no_fs))

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.PASS
        assert ("mkfs.exfat", "/dev/sda") in f3_runner.calls

    def test_missing_f3write_does_not_mount(self, ctx: StepContext) -> None:
        runner = FakeRunner(missing={"f3write"})
        runner.register(["lsblk"], stdout=json.dumps(LSBLK_DISK))
        ctx.runner = runner

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary == "f3write not found"
        # Not even the f3probe pre-check runs, so the drive is left as it was.
        assert runner.calls == []

    def test_missing_mkfs_does_not_probe(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        f3_runner.missing.add("mkfs.exfat")

        result = capacity_step().run(ctx)

        assert result.summary == "mkfs.exfat not found"
        assert f3_runner.calls == []


class TestUnmountFailure:
    def test_umount_failure_alone_keeps_results(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(["umount"], returncode=32, stderr="target is busy")

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary.startswith("umount failed:")
        assert "target is busy" in result.summary
        assert result.details["filesystems"][0]["ok_sectors"] == F3READ_OK_SECTORS
        assert "suppressed_error" not in result.details

    def test_st_rdonly_then_umount_failure_keeps_cause(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(["umount"], returncode=32, stderr="target is busy")

        result = capacity_step(make_statvfs(f_flag=os.ST_RDONLY)).run(ctx)

        assert result.summary.startswith("umount failed")
        assert result.details["suppressed_error"] == "Could not mount read-write"

    def test_timeout_then_umount_failure_keeps_summary(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        _with_timeout(ctx, 14400)
        f3_runner.register(["f3write"], returncode=-1, timed_out=True)
        f3_runner.register(["umount"], returncode=32, stderr="target is busy")

        result = capacity_step().run(ctx)

        assert result.summary.startswith("umount failed")
        assert result.details["suppressed_error"] == "Timed out"
        assert result.details["timed_out"] is True

    def test_parse_error_then_umount_failure_keeps_cause(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(["f3read"], stdout="garbage")
        f3_runner.register(["umount"], returncode=32, stderr="target is busy")

        result = capacity_step().run(ctx)

        assert result.summary.startswith("umount failed")
        assert result.details["suppressed_error"].startswith("Could not parse f3read output")
