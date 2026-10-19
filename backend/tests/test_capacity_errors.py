"""容量ステップ（CapacityStep）のERROR経路とrwマウントの後片付け。

コマンド不在・タイムアウト・権限不足・解析不能と、umount失敗時に一次原因が
`details["suppressed_error"]`に残ることを確認する。
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


class TestErrorPaths:
    def test_nearly_full_drive_is_error_not_pass(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        # 既使用31GB・検証済み1GBでは合計が閾値を超えても根拠がないのでERROR。
        f3_runner.register(["f3read"], stdout=f3read_output(1_953_125))
        stat = make_statvfs(f_frsize=4096, f_blocks=7_812_500, f_bfree=244_141)

        result = capacity_step(stat).run(ctx)

        assert result.status is StepStatus.ERROR
        assert "use_probe" in result.summary
        assert result.summary.startswith("検証済み領域")

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
        assert result.summary.startswith("f3readの出力を解析できない")
        assert "cannot open" in result.summary
        assert result.details["raw_stdout_head"] == "garbage"
        assert f3_runner.calls[-1][0] == "umount"

    def test_f3write_unparseable_is_error(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        # 速度行の数値がfloatに変換できない（"1.2.3"）とき、素のValueErrorではなく
        # f3read側と同じ形のsummaryに変換され、raw_stdout_headも残り、umountも行われる。
        f3_runner.register(
            ["f3write"], stdout="Average writing speed: 1.2.3 MB/s\n", stderr="odd output"
        )

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary.startswith("f3writeの出力を解析できない")
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

        assert result.summary == "権限不足: 書き込みが拒否された"

    def test_f3write_nonzero_with_speed_continues(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(["f3write"], returncode=1, stdout=F3WRITE_OK)

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.PASS
        assert "f3writeが終了コード1を返した" in result.warnings

    def test_f3write_zero_without_speed_continues(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(["f3write"], returncode=0, stdout="Free space: 0.00 Byte\n")

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
        assert result.summary == "権限不足: 書き込み可能でマウントできない"
        assert not any(c[0] == "umount" for c in f3_runner.calls)

    def test_mount_other_failure(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        f3_runner.register(["mount"], returncode=32, stderr="wrong fs type")

        result = capacity_step().run(ctx)

        assert result.summary.startswith("mount失敗:")
        assert "wrong fs type" in result.summary

    def test_st_rdonly_is_permission_error_and_unmounts(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        result = capacity_step(make_statvfs(f_flag=os.ST_RDONLY)).run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary == "権限不足: 書き込み可能でマウントできない"
        assert any(c[0] == "umount" for c in f3_runner.calls)
        assert not any(c[0] == "f3write" for c in f3_runner.calls)

    def test_mkdir_failure(self, ctx: StepContext, f3_runner: FakeRunner, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        ctx.config = replace(ctx.config, mount_root=blocker)

        result = capacity_step().run(ctx)

        assert result.summary.startswith("マウントポイントを作れない")
        assert "権限不足" not in result.summary
        assert not any(c[0] == "mount" for c in f3_runner.calls)

    def test_statvfs_oserror(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        def failing(_path: str) -> os.statvfs_result:
            raise OSError(5, "Input/output error")

        result = CapacityStep(statvfs=failing, monotonic=lambda: 0.0).run(ctx)

        assert result.summary == "マウントポイントを調べられない"
        assert any(c[0] == "umount" for c in f3_runner.calls)

    def test_f3write_timeout_unmounts(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        f3_runner.register(["f3write"], returncode=-1, timed_out=True)

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary == "タイムアウト"
        assert result.details["timed_out"] is True
        assert f3_runner.calls[-1][0] == "umount"

    def test_budget_exhausted_before_f3write(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        clock = iter([0.0, 100_000.0, 100_000.0, 100_000.0])
        step = CapacityStep(statvfs=lambda _p: make_statvfs(), monotonic=lambda: next(clock))

        result = step.run(ctx)

        assert result.summary == "タイムアウト"
        assert not any(c[0] == "f3write" for c in f3_runner.calls)
        assert f3_runner.calls[-1][0] == "umount"

    def test_no_filesystem_is_error_without_mount(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        no_fs = json.loads(json.dumps(LSBLK_DISK))
        no_fs["blockdevices"][0]["children"] = []
        f3_runner.register(["lsblk"], stdout=json.dumps(no_fs))

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert "use_probe" in result.summary
        assert not any(c[0] == "mount" for c in f3_runner.calls)

    def test_lsblk_failure_is_error(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        f3_runner.register(["lsblk"], stdout="not json")

        result = capacity_step().run(ctx)

        assert result.summary.startswith("デバイス情報を取得できない")

    def test_missing_f3write_does_not_mount(self, ctx: StepContext) -> None:
        runner = FakeRunner(missing={"f3write"})
        runner.register(["lsblk"], stdout=json.dumps(LSBLK_DISK))
        ctx.runner = runner

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary == "f3writeが見つからない"
        assert runner.calls == []


class TestUnmountFailure:
    def test_umount_failure_alone_keeps_results(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(["umount"], returncode=32, stderr="target is busy")

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary.startswith("umount失敗:")
        assert "target is busy" in result.summary
        assert result.details["filesystems"][0]["ok_sectors"] == F3READ_OK_SECTORS
        assert "suppressed_error" not in result.details

    def test_st_rdonly_then_umount_failure_keeps_cause(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(["umount"], returncode=32, stderr="target is busy")

        result = capacity_step(make_statvfs(f_flag=os.ST_RDONLY)).run(ctx)

        assert result.summary.startswith("umount失敗")
        assert result.details["suppressed_error"] == "書き込み可能でマウントできない"

    def test_timeout_then_umount_failure_keeps_summary(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(["f3write"], returncode=-1, timed_out=True)
        f3_runner.register(["umount"], returncode=32, stderr="target is busy")

        result = capacity_step().run(ctx)

        assert result.summary.startswith("umount失敗")
        assert result.details["suppressed_error"] == "タイムアウト"
        assert result.details["timed_out"] is True

    def test_parse_error_then_umount_failure_keeps_cause(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(["f3read"], stdout="garbage")
        f3_runner.register(["umount"], returncode=32, stderr="target is busy")

        result = capacity_step().run(ctx)

        assert result.summary.startswith("umount失敗")
        assert result.details["suppressed_error"].startswith("f3readの出力を解析できない")
