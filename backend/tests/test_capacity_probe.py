"""容量ステップのf3probeモード（use_probe = true）。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from usb_sentinel.libs import FakeRunner, StepContext, StepStatus
from usb_sentinel.libs.config import CapacityConfig
from usb_sentinel.libs.steps.capacity import CapacityStep
from usb_sentinel.libs.steps.capacity_probe import ProbeSummary, parse_f3probe

F3PROBE_GENUINE = (
    "F3 probe 8.0\n"
    "Copyright (C) 2010 Digirati Internet LTDA.\n"
    "\n"
    "WARNING: Probing normally takes from a few seconds to 15 minutes.\n"
    "Probing /dev/sda: done\n"
    "\n"
    "Good news: The device `/dev/sda' is the real thing\n"
    "\n"
    "Device geometry:\n"
    "\t         *Usable* size: 29.80 GB (62500000 blocks)\n"
    "\t        Announced size: 29.80 GB (62500000 blocks)\n"
    "\t                Module: 32.00 GB (2^35 Bytes)\n"
    "\tApproximate cache size: 0.00 Byte (0 blocks), need-reset=no\n"
    "\t   Physical block size: 512.00 Byte (2^9 Bytes)\n"
    "\n"
    "Probe time: 1'10\"\n"
)

F3PROBE_FAKE = (
    "Bad news: The device `/dev/sda' is a counterfeit of type limbo\n"
    "\n"
    "Device geometry:\n"
    "\t         *Usable* size: 7.50 GB (15625000 blocks)\n"
    "\t        Announced size: 29.80 GB (62500000 blocks)\n"
    "\t   Physical block size: 512.00 Byte (2^9 Bytes)\n"
)


class TestParseF3Probe:
    def test_genuine(self) -> None:
        assert parse_f3probe(F3PROBE_GENUINE) == ProbeSummary(
            usable_blocks=62500000,
            announced_blocks=62500000,
            block_size=512,
            verdict_line="Good news: The device `/dev/sda' is the real thing",
        )

    def test_counterfeit(self) -> None:
        summary = parse_f3probe(F3PROBE_FAKE)
        assert summary.usable_blocks == 15625000
        assert summary.announced_blocks == 62500000
        assert summary.verdict_line is not None
        assert summary.verdict_line.startswith("Bad news:")

    def test_missing_block_size_defaults_to_512(self) -> None:
        text = "*Usable* size: 1.00 GB (1953125 blocks)\n"
        summary = parse_f3probe(text)
        assert summary.block_size == 512
        assert summary.announced_blocks is None
        assert summary.verdict_line is None

    def test_4k_block_size(self) -> None:
        text = "*Usable* size: 1.00 GB (244140 blocks)\nPhysical block size: 4.00 KB (2^12 Bytes)\n"
        assert parse_f3probe(text).block_size == 4096

    def test_missing_usable_line_raises(self) -> None:
        with pytest.raises(ValueError, match="f3probeの出力を解析できない"):
            parse_f3probe("Announced size: 29.80 GB (62500000 blocks)\n")


@pytest.fixture
def probe_ctx(ctx: StepContext) -> StepContext:
    ctx.config = replace(ctx.config, capacity=CapacityConfig(use_probe=True))
    return ctx


def _step() -> CapacityStep:
    return CapacityStep(monotonic=lambda: 0.0)


class TestProbeMode:
    def test_genuine_passes(self, probe_ctx: StepContext, runner: FakeRunner) -> None:
        runner.register(["f3probe"], stdout=F3PROBE_GENUINE)

        result = _step().run(probe_ctx)

        assert result.status is StepStatus.PASS
        d = result.details
        assert d["mode"] == "f3probe"
        assert d["filesystems"] == []
        assert d["verified_bytes"] is None
        assert d["used_bytes"] is None
        assert d["verified_ratio"] is None
        assert d["bad_sectors"] is None
        assert d["defect"] is False
        assert d["actual_bytes"] == 62500000 * 512
        assert d["probe"] == {
            "usable_blocks": 62500000,
            "announced_blocks": 62500000,
            "block_size": 512,
            "announced_bytes": 62500000 * 512,
            "verdict_line": "Good news: The device `/dev/sda' is the real thing",
            "returncode": 0,
        }
        assert d["commands"] == [["f3probe", "--destructive", "--time-ops", "/dev/sda"]]
        assert runner.calls == [("f3probe", "--destructive", "--time-ops", "/dev/sda")]

    def test_bad_news_is_counterfeit(self, probe_ctx: StepContext, runner: FakeRunner) -> None:
        # 終了コードが非0でも解析結果だけで判定する。Bad news行は偽装疑い。
        runner.register(["f3probe"], returncode=1, stdout=F3PROBE_FAKE)

        result = _step().run(probe_ctx)

        assert result.status is StepStatus.FAIL
        assert result.details["defect"] is False
        assert result.details["kind"] == "counterfeit"
        assert result.details["counterfeit"] is True
        assert result.details["counterfeit_reasons"] == ["probe_bad_news"]
        assert result.summary.startswith("偽装疑い:")
        assert result.details["probe"]["returncode"] == 1

    def test_probe_verdict_line_copied_to_top_level(
        self, probe_ctx: StepContext, runner: FakeRunner
    ) -> None:
        """QR・SQLiteがdetails.probeを掘らずに読めるようトップレベルにも写す。"""
        runner.register(["f3probe"], stdout=F3PROBE_GENUINE)

        result = _step().run(probe_ctx)

        d = result.details
        assert d["probe_verdict_line"] == d["probe"]["verdict_line"]
        assert d["probe_verdict_line"].startswith("Good news:")
        assert d["lost_ratio"] is None
        assert d["kind"] == "ok"
        assert d["counterfeit"] is False
        assert d["counterfeit_reasons"] == []
        assert d["base"] == "advertised"
        assert d["nominal_bytes"] is None

    def test_permission_denied(self, probe_ctx: StepContext, runner: FakeRunner) -> None:
        runner.register(["f3probe"], returncode=1, stderr="f3probe: Permission denied")

        result = _step().run(probe_ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary == "権限不足: 書き込みが拒否された"

    def test_unparseable(self, probe_ctx: StepContext, runner: FakeRunner) -> None:
        runner.register(["f3probe"], returncode=1, stdout="garbage", stderr="boom")

        result = _step().run(probe_ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary == "f3probeの出力を解析できない: boom"
        assert result.details["raw_stdout_head"] == "garbage"

    def test_timeout(self, probe_ctx: StepContext, runner: FakeRunner) -> None:
        runner.register(["f3probe"], returncode=-1, timed_out=True)

        result = _step().run(probe_ctx)

        assert result.summary == "タイムアウト"
        assert result.details["timed_out"] is True

    def test_budget_exhausted_does_not_run(
        self, probe_ctx: StepContext, runner: FakeRunner
    ) -> None:
        clock = iter([0.0, 100_000.0])
        runner.register(["f3probe"], stdout=F3PROBE_GENUINE)

        result = CapacityStep(monotonic=lambda: next(clock)).run(probe_ctx)

        assert result.summary == "タイムアウト"
        assert runner.calls == []

    def test_missing_f3probe(self, probe_ctx: StepContext) -> None:
        probe_ctx.runner = FakeRunner(missing={"f3probe"})

        result = _step().run(probe_ctx)

        assert result.status is StepStatus.ERROR
        assert result.summary == "f3probeが見つからない"
