"""Speed step (SpeedStep) and the fio output parser.

Writing destroys the device, so verify that the read (with --readonly) runs first and that fio
is not started if a mount remains.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tests.conftest import fio_json
from usb_sentinel.libs import FakeRunner, StepContext, StepStatus
from usb_sentinel.libs.config import SpeedConfig
from usb_sentinel.libs.steps.speed import FioJob, SpeedStep, parse_fio

LSBLK_UNMOUNTED = {
    "blockdevices": [
        {
            "name": "sda",
            "path": "/dev/sda",
            "type": "disk",
            "mountpoint": None,
            "children": [{"path": "/dev/sda1", "fstype": "vfat", "mountpoint": None}],
        }
    ]
}

READ_ARGV = ["fio", "--name=seq_read"]
WRITE_ARGV = ["fio", "--name=seq_write"]


class TestParseFio:
    def test_bw_bytes(self) -> None:
        job = parse_fio(fio_json("read", bw_bytes=85_300_000), "read")
        assert job == FioJob(
            error=0, bw_bytes=85_300_000, iops=81.3, io_bytes=2_559_000_000, runtime_ms=30001
        )
        assert job.mb_s == 85.3

    def test_bw_only_is_kib(self) -> None:
        job = parse_fio(fio_json("write", bw_bytes=None, bw=1000), "write")
        assert job.bw_bytes == 1_024_000

    def test_leading_warning_lines_are_skipped(self) -> None:
        text = fio_json(
            "read", prefix="fio: this platform does not support process shared mutexes\n"
        )
        assert parse_fio(text, "read").bw_bytes == 85_300_000

    def test_error_is_reported(self) -> None:
        assert parse_fio(fio_json("write", error=13), "write").error == 13

    def test_missing_error_key_is_zero(self) -> None:
        assert parse_fio(fio_json("read", error=None), "read").error == 0

    @pytest.mark.parametrize(
        "text",
        [
            "not json",
            "",
            json.dumps({"jobs": []}),
            json.dumps({"jobs": [{"write": {"bw_bytes": 1}}]}),
            json.dumps({"jobs": [{"read": {}}]}),
            json.dumps({"jobs": "oops"}),
        ],
    )
    def test_invalid_output_raises(self, text: str) -> None:
        with pytest.raises(ValueError, match="fioの出力を解析できない"):
            parse_fio(text, "read")


@pytest.fixture
def fio_runner(runner: FakeRunner) -> FakeRunner:
    """Register an unmounted lsblk and fio with 85.3 MB/s read and 20.1 MB/s write."""
    runner.register(["lsblk"], stdout=json.dumps(LSBLK_UNMOUNTED))
    runner.register(READ_ARGV, stdout=fio_json("read", bw_bytes=85_300_000))
    runner.register(WRITE_ARGV, stdout=fio_json("write", bw_bytes=20_100_000))
    return runner


def _with_speed(ctx: StepContext, **kwargs: float | str | None) -> StepContext:
    ctx.config = replace(ctx.config, speed=SpeedConfig(**kwargs))  # ty: ignore[invalid-argument-type]  kwargs are only the test's nominal speeds and device kind
    return ctx


def _with_link(ctx: StepContext, link_speed_mbps: int | None) -> StepContext:
    ctx.device = replace(ctx.device, link_speed_mbps=link_speed_mbps)
    return ctx


def _step() -> SpeedStep:
    return SpeedStep(monotonic=lambda: 0.0)


def _fio_calls(runner: FakeRunner) -> list[list[str]]:
    return [list(c) for c in runner.calls if c[0] == "fio"]


class TestJudgement:
    def test_both_nominal_pass(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        result = _step().run(_with_speed(ctx, nominal_read_mb_s=90.0, nominal_write_mb_s=20.0))

        assert result.status is StepStatus.PASS
        assert result.summary == "読み取り85.3MB/s、書き込み20.1MB/s（閾値72.0 / 16.0MB/s）"
        assert result.details["threshold_read_mb_s"] == 72.0
        assert result.details["threshold_write_mb_s"] == 16.0
        assert result.details["read"]["mb_s"] == 85.3
        assert result.details["write"]["mb_s"] == 20.1
        assert result.details["read"]["error"] == 0

    def test_read_below_threshold_fails(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        fio_runner.register(READ_ARGV, stdout=fio_json("read", bw_bytes=40_100_000))

        result = _step().run(_with_speed(ctx, nominal_read_mb_s=90.0, nominal_write_mb_s=20.0))

        assert result.status is StepStatus.FAIL
        assert (
            result.summary
            == "速度不足（著しく下回る）: 読み取り40.1MB/s < 閾値72.0MB/s（公称90.0×0.80）"
        )
        assert result.details["severity"] == "severe"
        assert "defect" not in result.details

    def test_exact_threshold_passes(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        # 80.0 MB/s = exactly nominal 100.0 x 0.80 (boundary value).
        fio_runner.register(READ_ARGV, stdout=fio_json("read", bw_bytes=80_000_000))

        result = _step().run(_with_speed(ctx, nominal_read_mb_s=100.0))

        assert result.status is StepStatus.PASS

    def test_read_only_nominal_pass(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        result = _step().run(_with_speed(ctx, nominal_read_mb_s=90.0))

        assert result.status is StepStatus.PASS
        assert result.summary == "読み取り85.3MB/s（閾値72.0MB/s）、書き込み20.1MB/s（判定対象外）"
        assert result.details["threshold_write_mb_s"] is None

    def test_read_only_nominal_fail(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        result = _step().run(_with_speed(ctx, nominal_read_mb_s=120.0))

        assert result.status is StepStatus.FAIL
        assert result.summary.startswith(
            "速度不足（わずかに下回る）: 読み取り85.3MB/s < 閾値96.0MB/s"
        )
        assert result.details["severity"] == "minor"

    def test_write_only_nominal_pass(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        result = _step().run(_with_speed(ctx, nominal_write_mb_s=20.0))

        assert result.status is StepStatus.PASS
        assert result.summary == "読み取り85.3MB/s（判定対象外）、書き込み20.1MB/s（閾値16.0MB/s）"
        assert result.details["threshold_read_mb_s"] is None

    def test_no_nominal_is_skipped(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        result = _step().run(ctx)

        assert result.status is StepStatus.SKIPPED
        assert result.summary == "公称速度が未設定（読み取り85.3MB/s、書き込み20.1MB/s）"
        assert result.details["read"]["mb_s"] == 85.3
        assert result.details["threshold_read_mb_s"] is None


class TestDetails:
    def test_new_keys_and_link_speed_copy(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        result = _step().run(
            _with_link(_with_speed(ctx, nominal_read_mb_s=90.0, nominal_write_mb_s=20.0), 5000)
        )

        d = result.details
        for key in (
            "device_class",
            "device_class_basis",
            "link_speed_mbps",
            "basis_read",
            "basis_write",
            "reference_read_mb_s",
            "reference_write_mb_s",
            "ratio_read",
            "ratio_write",
            "severity",
        ):
            assert key in d, key
        assert d["link_speed_mbps"] == 5000
        assert d["ratio_read"] == 0.948
        assert d["ratio_write"] == 1.005
        # Existing keys keep both their meaning and their values.
        assert d["nominal_read_mb_s"] == 90.0
        assert d["threshold_write_mb_s"] == 16.0

    def test_missing_reference_yields_none(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        result = _step().run(_with_speed(ctx, nominal_read_mb_s=90.0))

        d = result.details
        assert d["basis_write"] == "none"
        assert d["reference_write_mb_s"] is None
        assert d["ratio_write"] is None
        assert d["threshold_write_mb_s"] is None
        assert d["link_speed_mbps"] is None


class TestArgv:
    def test_read_runs_first_with_readonly(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        result = _step().run(ctx)

        calls = _fio_calls(fio_runner)
        assert len(calls) == 2
        read, write = calls
        assert read[1] == "--name=seq_read"
        assert "--rw=read" in read and "--readonly" in read
        assert write[1] == "--name=seq_write"
        assert "--rw=write" in write and "--readonly" not in write
        for argv in calls:
            for option in (
                "--filename=/dev/sda",
                "--direct=1",
                "--ioengine=libaio",
                "--bs=1M",
                "--iodepth=8",
                "--runtime=30",
                "--time_based",
                "--output-format=json",
            ):
                assert option in argv
        assert result.details["commands"] == calls

    def test_config_values_fill_argv(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        ctx.config = replace(
            ctx.config, speed=SpeedConfig(block_size="4k", queue_depth=32, duration_s=5)
        )

        _step().run(ctx)

        read = _fio_calls(fio_runner)[0]
        assert "--bs=4k" in read and "--iodepth=32" in read and "--runtime=5" in read
