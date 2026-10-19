"""Pure functions for capacity judgment (steps/capacity_judge.py).

Verify the capacity-overrun boundary (Fraction keeps it exact to the byte), the
non-destructive check dict, and judge() priority (counterfeit > short > defect > unverified >
ok).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from usb_sentinel.libs import StepStatus
from usb_sentinel.libs.steps.capacity_judge import (
    LOST_RATIO_COUNTERFEIT,
    CapacityVerdict,
    evaluate,
    exceeds,
    judge,
    over_threshold,
    precheck_advertised,
)

GB32 = 32_000_000_000
THRESHOLD32 = 29_760_000_000  # 32GB × 0.93


class TestOverThreshold:
    @pytest.mark.parametrize(
        ("nominal", "expected"),
        [
            (GB32, 34_240_000_000),
            (64_000_000_000, 68_480_000_000),
            (128_000_000_000, 136_960_000_000),
            (1_000_000_000_000, 1_070_000_000_000),
        ],
    )
    def test_exact_without_float_drift(self, nominal: int, expected: int) -> None:
        # Multiplying as a float gives ...000.000004 and ceil adds 1. With Fraction it matches.
        assert over_threshold(nominal, 1.07) == expected
        # The float version gets +1 (if this premise breaks, Fraction becomes unnecessary).
        assert math.ceil(nominal * 1.07) == expected + 1

    def test_none_and_zero_give_none(self) -> None:
        assert over_threshold(None, 1.07) is None
        assert over_threshold(0, 1.07) is None
        assert over_threshold(-1, 1.07) is None


class TestExceeds:
    def test_boundary_is_not_exceeded(self) -> None:
        assert exceeds(34_240_000_000, GB32, 1.07) is False

    def test_one_byte_over_is_exceeded(self) -> None:
        assert exceeds(34_240_000_001, GB32, 1.07) is True

    def test_none_or_zero_nominal_never_exceeds(self) -> None:
        assert exceeds(10**15, None, 1.07) is False
        assert exceeds(10**15, 0, 1.07) is False


class TestPrecheckAdvertised:
    def test_unset_nominal(self) -> None:
        check = precheck_advertised(GB32, None, 1.07)

        assert check["exceeded"] is False
        assert check["advertised_ratio"] is None
        assert check["over_threshold_bytes"] is None
        assert check["summary"] == ""
        assert check["nominal_bytes"] is None
        assert check["advertised_bytes"] == GB32
        assert check["over_ratio"] == 1.07

    def test_exceeded(self) -> None:
        check = precheck_advertised(GB32, 29_000_000_000, 1.07)

        assert check["exceeded"] is True
        assert check["over_threshold_bytes"] == 31_030_000_000
        assert check["advertised_ratio"] == round(GB32 / 29_000_000_000, 4)
        assert len(str(check["advertised_ratio"]).split(".")[1]) <= 4
        for word in ("advertised capacity", "limit", "nominal"):
            assert word in check["summary"]

    def test_within_band(self) -> None:
        check = precheck_advertised(GB32, GB32, 1.07)

        assert check["exceeded"] is False
        assert check["advertised_ratio"] == 1.0
        assert check["summary"] == ""


def _judge(**overrides: int | float | str | None) -> CapacityVerdict:
    """Default to a PASS case (32 GB, 1 GB used, about 30.2 GB verified) and override some
    values."""
    params: dict[str, Any] = {
        "actual": 31_200_000_000,
        "advertised": GB32,
        "nominal": None,
        "pass_ratio": 0.93,
        "over_ratio": 1.07,
        "bad_sectors": 0,
        "lost_ratio": 0.0,
        "probe_verdict_line": None,
        "verified_bytes": 30_200_000_000,
        "threshold": THRESHOLD32,
    }
    params.update(overrides)
    return judge(**params)


class TestJudgeWithoutNominal:
    """Without nominal, the judgment matches the current four branches (short / defect /
    unverified / ok)."""

    def test_ok(self) -> None:
        verdict = _judge()
        assert verdict.status is StepStatus.PASS
        assert verdict.kind == "ok"
        assert verdict.counterfeit is False
        assert verdict.counterfeit_reasons == ()
        assert verdict.summary.startswith("actual 31.20GB / nominal 32.00GB")

    def test_short(self) -> None:
        verdict = _judge(actual=THRESHOLD32 - 1, verified_bytes=THRESHOLD32 - 1)
        assert verdict.status is StepStatus.FAIL
        assert verdict.kind == "short"
        assert verdict.summary.startswith("Capacity shortfall:")
        assert "threshold 29.76GB" in verdict.summary

    def test_defect(self) -> None:
        verdict = _judge(bad_sectors=12)
        assert verdict.status is StepStatus.FAIL
        assert verdict.kind == "defect"
        assert verdict.summary == "Data corruption: 12 sectors"

    def test_unverified(self) -> None:
        verdict = _judge(verified_bytes=1_000_000_000)
        assert verdict.status is StepStatus.ERROR
        assert verdict.kind == "unverified"
        assert "Verified area 1.00GB" in verdict.summary
        assert "already used 30.20GB" in verdict.summary

    def test_probe_mode_has_no_unverified(self) -> None:
        # f3probe mode is called with verified_bytes=None and bad_sectors=None.
        verdict = _judge(verified_bytes=None, bad_sectors=None, lost_ratio=None)
        assert verdict.kind == "ok"


class TestJudgeCounterfeit:
    def test_lost_ratio_just_below_threshold_is_not_counterfeit(self) -> None:
        verdict = _judge(lost_ratio=0.4999, bad_sectors=1)
        assert verdict.counterfeit is False
        assert verdict.kind == "defect"

    def test_lost_ratio_at_threshold_is_counterfeit(self) -> None:
        assert LOST_RATIO_COUNTERFEIT == 0.50
        verdict = _judge(lost_ratio=0.5000, bad_sectors=1)
        assert verdict.status is StepStatus.FAIL
        assert verdict.kind == "counterfeit"
        assert verdict.counterfeit is True
        assert verdict.counterfeit_reasons == ("lost_ratio",)
        assert verdict.summary.startswith("Suspected counterfeit: lost 50.0%")
        assert "threshold 29.76GB" in verdict.summary

    def test_bad_news_is_counterfeit(self) -> None:
        line = "Bad news: The device `/dev/sda' is a counterfeit of type limbo"
        verdict = _judge(probe_verdict_line=line, verified_bytes=None, bad_sectors=None)
        assert verdict.counterfeit_reasons == ("probe_bad_news",)
        assert verdict.summary == f"Suspected counterfeit: f3probe: {line}"

    def test_good_news_is_not_counterfeit(self) -> None:
        verdict = _judge(
            probe_verdict_line="Good news: The device `/dev/sda' is the real thing",
            verified_bytes=None,
            bad_sectors=None,
        )
        assert verdict.counterfeit is False
        assert verdict.kind == "ok"

    def test_over_advertised_only(self) -> None:
        verdict = _judge(nominal=29_000_000_000, actual=29_500_000_000, threshold=26_970_000_000)
        assert verdict.counterfeit_reasons == ("over_advertised",)
        assert verdict.summary.startswith(
            "Suspected counterfeit: advertised capacity 32.00GB > limit 31.03GB ("
        )

    def test_over_actual_only(self) -> None:
        verdict = _judge(
            nominal=29_000_000_000,
            advertised=29_000_000_000,
            actual=31_500_000_000,
            threshold=26_970_000_000,
        )
        assert verdict.counterfeit_reasons == ("over_actual",)
        assert verdict.summary.startswith(
            "Suspected counterfeit: actual capacity 31.50GB > limit 31.03GB ("
        )

    def test_all_reasons_in_fixed_order_joined(self) -> None:
        line = "Bad news: counterfeit"
        verdict = _judge(
            nominal=29_000_000_000,
            actual=31_500_000_000,
            threshold=26_970_000_000,
            probe_verdict_line=line,
            lost_ratio=0.75,
            bad_sectors=5,
        )
        assert verdict.counterfeit_reasons == (
            "over_advertised",
            "over_actual",
            "probe_bad_news",
            "lost_ratio",
        )
        assert verdict.summary.count("; ") >= 3
        assert verdict.summary.startswith("Suspected counterfeit: advertised capacity")
        assert f"; f3probe: {line}; lost 75.0%" in verdict.summary


class TestJudgePriority:
    def test_counterfeit_beats_short_and_defect(self) -> None:
        verdict = _judge(
            actual=9_000_000_000, verified_bytes=8_000_000_000, lost_ratio=0.7, bad_sectors=100
        )
        assert verdict.kind == "counterfeit"

    def test_short_beats_defect(self) -> None:
        verdict = _judge(actual=THRESHOLD32 - 1, verified_bytes=THRESHOLD32 - 1, bad_sectors=100)
        assert verdict.kind == "short"

    def test_defect_beats_unverified(self) -> None:
        verdict = _judge(bad_sectors=1, verified_bytes=1_000_000_000)
        assert verdict.kind == "defect"

    def test_nominal_changes_base_of_short(self) -> None:
        # Even with 32 GB advertised, a 40 GB nominal makes an actual 31.2 GB short (nominal is
        # the denominator).
        verdict = _judge(nominal=40_000_000_000, threshold=37_200_000_000)
        assert verdict.kind == "short"
        assert "nominal 40.00GB" in verdict.summary

    def test_kinds_are_five_values(self) -> None:
        kinds = {
            _judge().kind,
            _judge(actual=1, verified_bytes=1).kind,
            _judge(bad_sectors=1).kind,
            _judge(verified_bytes=1).kind,
            _judge(lost_ratio=0.9).kind,
        }
        assert kinds == {"ok", "short", "defect", "unverified", "counterfeit"}


class TestEvaluateReexport:
    def test_evaluate_lives_here(self) -> None:
        assert evaluate(93, 100) is True
        assert evaluate(92, 100) is False
