"""f3write+f3read mode of the capacity step (CapacityStep).

Verify the four judgment branches (PASS / short capacity / data corruption / insufficient
verification), summation, and handling of leftover .h2w files. ERROR paths and umount failures
are in test_capacity_errors.py.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

from tests.conftest import (
    F3READ_OK_SECTORS,
    LSBLK_DISK,
    STATVFS_USED_BYTES,
    capacity_step,
    f3read_output,
    make_statvfs,
)
from usb_sentinel.libs import FakeRunner, StepContext, StepStatus
from usb_sentinel.libs.steps.capacity import evaluate

THRESHOLD = 29_760_000_000  # 32GB × 0.93
THRESHOLD_SECTORS = THRESHOLD // 512  # 58_125_000


def _rw_target(ctx: StepContext, index: int = 0) -> Path:
    return ctx.config.mount_root / "sda" / "rw" / str(index)


def _non_lsblk(runner: FakeRunner) -> list[list[str]]:
    return [list(c) for c in runner.calls if c[0] != "lsblk"]


class TestPass:
    def test_pass_records_details(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        result = capacity_step().run(ctx)

        assert result.status is StepStatus.PASS
        assert result.summary.startswith("actual ")
        assert "GB" in result.summary and "%" in result.summary
        d = result.details
        assert d["mode"] == "f3write"
        assert d["advertised_bytes"] == 32_000_000_000
        assert d["threshold_bytes"] == THRESHOLD
        assert d["verified_bytes"] == F3READ_OK_SECTORS * 512
        assert d["used_bytes"] == STATVFS_USED_BYTES
        assert d["actual_bytes"] == d["verified_bytes"] + d["used_bytes"]
        assert d["ratio"] == round(d["actual_bytes"] / 32_000_000_000, 4)
        assert d["verified_ratio"] == round(d["verified_bytes"] / 32_000_000_000, 4)
        assert d["bad_sectors"] == 0
        assert d["defect"] is False
        assert d["probe"] is None
        fs = d["filesystems"][0]
        assert fs["device"] == "/dev/sda1"
        assert fs["mountpoint"] == str(_rw_target(ctx))
        assert fs["used_bytes"] == STATVFS_USED_BYTES
        assert fs["ok_sectors"] == F3READ_OK_SECTORS
        assert fs["write_mb_s"] == 18.2
        assert fs["read_mb_s"] == 62.9
        assert fs["stale_h2w_removed"] == 0
        assert fs["f3write_returncode"] == 0
        assert fs["f3read_returncode"] == 0
        assert "timed_out" not in d
        assert "suppressed_error" not in d

    def test_commands_match_runner_calls_in_order(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        result = capacity_step().run(ctx)

        assert result.details["commands"] == _non_lsblk(f3_runner)
        assert [c[0] for c in result.details["commands"]] == [
            "mount",
            "f3write",
            "f3read",
            "umount",
        ]
        assert result.details["commands"][0] == [
            "mount",
            "-w",
            "-o",
            "noexec,nosuid,nodev",
            "/dev/sda1",
            str(_rw_target(ctx)),
        ]

    def test_exact_threshold_passes(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        # With 0 used and exactly 93% verified, it passes (boundary value).
        f3_runner.register(["f3read"], stdout=f3read_output(THRESHOLD_SECTORS))
        stat = make_statvfs(f_blocks=100, f_bfree=100)

        result = capacity_step(stat).run(ctx)

        assert result.status is StepStatus.PASS
        assert result.details["actual_bytes"] == THRESHOLD

    def test_one_sector_below_threshold_fails(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(["f3read"], stdout=f3read_output(THRESHOLD_SECTORS - 1))
        stat = make_statvfs(f_blocks=100, f_bfree=100)

        result = capacity_step(stat).run(ctx)

        assert result.status is StepStatus.FAIL
        assert result.summary.startswith("Capacity shortfall:")

    def test_multiple_filesystems_are_summed(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        two = json.loads(json.dumps(LSBLK_DISK))
        two["blockdevices"][0]["children"].append(
            {"path": "/dev/sda2", "fstype": "ext4", "type": "part", "mountpoint": None}
        )
        f3_runner.register(["lsblk"], stdout=json.dumps(two))
        f3_runner.register(["f3read"], stdout=f3read_output(30_000_000))

        result = capacity_step().run(ctx)

        fs = result.details["filesystems"]
        assert [e["device"] for e in fs] == ["/dev/sda1", "/dev/sda2"]
        assert [e["mountpoint"] for e in fs] == [str(_rw_target(ctx, 0)), str(_rw_target(ctx, 1))]
        assert result.details["verified_bytes"] == 2 * 30_000_000 * 512
        assert result.details["used_bytes"] == 2 * STATVFS_USED_BYTES
        assert result.status is StepStatus.PASS


class TestFail:
    def test_short_capacity_with_large_lost_ratio_is_counterfeit(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        # Counterfeit: only about 8 GB is readable and the rest is LOST. The loss ratio is 50% or
        # more, so it is suspected counterfeit.
        f3_runner.register(["f3read"], stdout=f3read_output(15_625_000, lost_sectors=44_000_000))

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.FAIL
        assert result.details["defect"] is False
        assert result.details["kind"] == "counterfeit"
        assert result.details["counterfeit"] is True
        assert result.details["counterfeit_reasons"] == ["lost_ratio"]
        assert abs(result.details["lost_ratio"] - 0.738) < 0.001
        assert result.summary.startswith("Suspected counterfeit:")
        assert "lost 73.8%" in result.summary
        assert "threshold 29.76GB" in result.summary

    def test_enough_capacity_with_data_lost_is_defective(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        f3_runner.register(
            ["f3read"],
            stdout=f3read_output(F3READ_OK_SECTORS, lost_sectors=10, corrupted_sectors=2),
        )

        result = capacity_step().run(ctx)

        assert result.status is StepStatus.FAIL
        assert result.details["defect"] is True
        assert result.details["bad_sectors"] == 12
        assert result.summary.startswith("Data corruption: 12 sectors")
        assert "lost 10 / corrupted 2" in result.summary


def _with_nominal(ctx: StepContext, nominal: int) -> StepContext:
    ctx.config = replace(ctx.config, capacity=replace(ctx.config.capacity, nominal_bytes=nominal))
    return ctx


class TestNominal:
    def test_pass_details_have_new_keys_and_unchanged_old_values(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        """On PASS without nominal, existing key values are unchanged and new keys are added."""
        result = capacity_step().run(ctx)

        d = result.details
        assert d["advertised_bytes"] == 32_000_000_000
        assert d["ratio"] == round(d["actual_bytes"] / 32_000_000_000, 4)
        assert d["threshold_bytes"] == THRESHOLD
        assert d["defect"] is False
        assert d["nominal_bytes"] is None
        assert d["base_bytes"] == 32_000_000_000
        assert d["base"] == "advertised"
        assert d["over_ratio"] == 1.07
        assert d["over_threshold_bytes"] is None
        assert d["advertised_ratio"] is None
        assert d["lost_ratio"] == 0.0
        assert d["kind"] == "ok"
        assert d["counterfeit"] is False
        assert d["counterfeit_reasons"] == []
        assert d["probe_verdict_line"] is None

    def test_nominal_becomes_base(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        nominal = 31_000_000_000
        result = capacity_step().run(_with_nominal(ctx, nominal))

        d = result.details
        assert result.status is StepStatus.PASS
        assert d["base"] == "nominal"
        assert d["base_bytes"] == nominal
        assert d["nominal_bytes"] == nominal
        assert d["threshold_bytes"] == math.ceil(nominal * 0.93)
        assert d["over_threshold_bytes"] == 33_170_000_000
        assert d["advertised_ratio"] == round(32_000_000_000 / nominal, 4)
        # ratio is actual / advertised, as before.
        assert d["ratio"] == round(d["actual_bytes"] / 32_000_000_000, 4)
        assert "nominal 31.00GB" in result.summary

    def test_advertised_over_nominal_is_counterfeit(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        # The advertised 32 GB exceeds 107% of the nominal 29 GB (31.03 GB).
        result = capacity_step().run(_with_nominal(ctx, 29_000_000_000))

        assert result.status is StepStatus.FAIL
        assert result.details["kind"] == "counterfeit"
        assert "over_advertised" in result.details["counterfeit_reasons"]
        assert result.summary.startswith(
            "Suspected counterfeit: advertised capacity 32.00GB > limit 31.03GB"
        )

    def test_short_against_nominal(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        # Advertised 32 GB but nominal 64 GB, so an actual 31.2 GB is short.
        result = capacity_step().run(_with_nominal(ctx, 64_000_000_000))

        assert result.status is StepStatus.FAIL
        assert result.details["kind"] == "short"
        assert result.summary.startswith("Capacity shortfall:")
        assert "nominal 64.00GB" in result.summary
        assert result.details["threshold_bytes"] == math.ceil(64_000_000_000 * 0.93)


class TestStaleH2w:
    def test_stale_h2w_removed_and_warned_once(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        target = _rw_target(ctx)
        target.mkdir(parents=True)
        (target / "1.h2w").write_text("x")

        result = capacity_step().run(ctx)

        assert result.details["filesystems"][0]["stale_h2w_removed"] == 1
        assert result.warnings.count("Deleted 1 f3 files left from a previous run") == 1
        assert not (target / "1.h2w").exists()


class TestEvaluate:
    def test_exact_ratio_passes(self) -> None:
        assert evaluate(93, 100) is True

    def test_below_ratio_fails(self) -> None:
        assert evaluate(92, 100) is False

    def test_zero_advertised_fails(self) -> None:
        assert evaluate(100, 0) is False
