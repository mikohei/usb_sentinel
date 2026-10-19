"""Error paths of the speed step (permissions, I/O errors, parse failures, timeouts, missing fio,
leftover mounts).

Helpers (fio_runner, _step, _with_speed, _fio_calls) are borrowed from test_speed.py.
"""

from __future__ import annotations

import json

import pytest

from tests import test_speed
from tests.conftest import fio_json
from tests.test_speed import (
    LSBLK_UNMOUNTED,
    READ_ARGV,
    WRITE_ARGV,
    _fio_calls,
    _step,
    _with_speed,
)
from usb_sentinel.libs import FakeRunner, InspectionResult, StepContext, StepStatus
from usb_sentinel.libs.orchestrator import reason_codes_for
from usb_sentinel.libs.steps.speed import SpeedStep

fio_runner = test_speed.fio_runner


class TestErrorPaths:
    @pytest.mark.parametrize("errno", [13, 30, 1])
    def test_write_permission_errno(
        self, ctx: StepContext, fio_runner: FakeRunner, errno: int
    ) -> None:
        fio_runner.register(WRITE_ARGV, stdout=fio_json("write", error=errno))

        result = _step().run(_with_speed(ctx, nominal_read_mb_s=90.0))

        assert result.status is StepStatus.ERROR
        assert result.summary == f"権限不足: 書き込みが拒否された（error={errno}）"
        assert result.details["read"]["mb_s"] == 85.3
        assert result.details["write"]["error"] == errno

    def test_read_permission_errno_names_read(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        # When the read fio returns EACCES, the message says the read was denied and the write
        # does not run.
        fio_runner.register(READ_ARGV, stdout=fio_json("read", error=13))

        result = _step().run(_with_speed(ctx, nominal_read_mb_s=90.0))

        assert result.status is StepStatus.ERROR
        assert result.summary == "権限不足: 読み取りが拒否された（error=13）"
        assert result.details["read"]["error"] == 13
        assert len(_fio_calls(fio_runner)) == 1

    def test_write_io_error(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        fio_runner.register(WRITE_ARGV, stdout=fio_json("write", error=5))

        result = _step().run(ctx)

        assert result.summary == "fioがI/Oエラーを報告（error=5）"

    def test_unparseable_with_permission_text(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        fio_runner.register(
            WRITE_ARGV, returncode=1, stdout="", stderr="fio: open /dev/sda: Permission denied"
        )

        result = _step().run(ctx)

        assert result.summary == "権限不足: 書き込みが拒否された"

    def test_unparseable_other(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        fio_runner.register(WRITE_ARGV, returncode=1, stdout="garbage", stderr="boom")

        result = _step().run(ctx)

        assert result.summary == "fioの出力を解析できない: boom"
        assert result.details["write"]["raw_stdout_head"] == "garbage"

    def test_nonzero_exit_with_valid_json_is_accepted(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        fio_runner.register(READ_ARGV, returncode=1, stdout=fio_json("read"))

        result = _step().run(ctx)

        assert result.status is StepStatus.SKIPPED
        assert result.details["read"]["returncode"] == 1

    def test_read_timeout_stops_before_write(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        fio_runner.register(READ_ARGV, returncode=-1, timed_out=True)

        result = _step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary == "タイムアウト"
        assert result.details["read"]["timed_out"] is True
        assert len(_fio_calls(fio_runner)) == 1

    def test_timeout_sets_top_level_flag_and_reason_code(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        """A fio timeout also sets the top-level timed_out, and the reason code becomes
        s:timeout."""
        fio_runner.register(READ_ARGV, returncode=-1, timed_out=True)

        result = _step().run(ctx)
        inspection = InspectionResult(ctx.device, "2025-01-01T00:00:00+00:00", steps=[result])

        assert result.status is StepStatus.ERROR
        assert result.details["timed_out"] is True
        assert reason_codes_for(inspection) == ["s:timeout"]

    def test_budget_exhausted_after_read(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        # Budget creation -> before read -> read timeout_s -> before write (budget exhausted).
        clock = iter([0.0, 0.0, 0.0, 10_000.0, 10_000.0])

        result = SpeedStep(monotonic=lambda: next(clock)).run(ctx)

        assert result.summary == "タイムアウト"
        assert len(_fio_calls(fio_runner)) == 1
        assert result.details["read"]["mb_s"] == 85.3

    def test_missing_fio(self, ctx: StepContext) -> None:
        runner = FakeRunner(missing={"fio"})
        runner.register(["lsblk"], stdout=json.dumps(LSBLK_UNMOUNTED))
        ctx.runner = runner

        result = _step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary == "fioが見つからない"
        assert _fio_calls(runner) == []

    def test_mounted_device_blocks_fio(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        mounted = json.loads(json.dumps(LSBLK_UNMOUNTED))
        mounted["blockdevices"][0]["children"][0]["mountpoint"] = "/mnt/sda1"
        fio_runner.register(["lsblk"], stdout=json.dumps(mounted))

        result = _step().run(ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary == "マウントが残っているためfioを実行しない"
        assert _fio_calls(fio_runner) == []

    def test_lsblk_without_blockdevices(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        fio_runner.register(["lsblk"], stdout=json.dumps({"devices": []}))

        result = _step().run(ctx)

        assert result.summary.startswith("デバイス情報を取得できない")
        assert _fio_calls(fio_runner) == []
