"""Pure functions for capacity judgment (shared by f3write mode, f3probe mode, and the
non-destructive check).

Touches neither external commands nor the device. The denominator is nominal_bytes (nominal
capacity from the product page) if set, otherwise the capacity reported by lsblk (advertised).
"""

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from ..models import StepStatus

# A lost-sector ratio at or above this suggests counterfeit (aliased memory loses everything
# beyond the real capacity).
LOST_RATIO_COUNTERFEIT = 0.50

_BAD_NEWS = "Bad news:"


def evaluate(actual_bytes: int, advertised_bytes: int, pass_ratio: float = 0.93) -> bool:
    """Whether the actual capacity is >= denominator x pass_ratio. False if the denominator <= 0."""
    return advertised_bytes > 0 and actual_bytes >= advertised_bytes * pass_ratio


def _gb(size: int | float) -> str:
    """Format bytes as GB in units of 10^9 (matching the advertised capacity in report/text.py)."""
    return f"{size / 1e9:.2f}GB"


def over_threshold(nominal: int | None, over_ratio: float) -> int | None:
    """Boundary (bytes) for excess capacity. None if nominal is None or <= 0.

    Multiplying as floats gives 32_000_000_000 x 1.07 = 34240000000.000004, and ceil is off by
    one byte. Fraction(str(over_ratio)) is the exact rational of the decimal notation, so the
    boundary equals "nominal x over_ratio rounded up to a whole byte".
    """
    if nominal is None or nominal <= 0:
        return None
    return math.ceil(Fraction(nominal) * Fraction(str(over_ratio)))


def exceeds(value: int, nominal: int | None, over_ratio: float) -> bool:
    """Whether value exceeds over_threshold(). Always False if the boundary is None (integer
    comparison only)."""
    threshold = over_threshold(nominal, over_ratio)
    return threshold is not None and value > threshold


def _over_text(label: str, value: int, nominal: int, over_ratio: float) -> str:
    """Reason wording for excess capacity (shared by reported and actual capacity)."""
    threshold = over_threshold(nominal, over_ratio)
    return (
        f"{label} {_gb(value)} > limit {_gb(threshold or 0)}"
        f" ({value / nominal * 100:.1f}% of nominal {_gb(nominal)})"
    )


def precheck_advertised(advertised: int, nominal: int | None, over_ratio: float) -> dict[str, Any]:
    """Non-destructive comparison of reported vs. nominal capacity. Always returns a dict
    (exceeded = False if nominal is unset)."""
    exceeded = exceeds(advertised, nominal, over_ratio)
    summary = ""
    if exceeded and nominal is not None:
        summary = _over_text("advertised capacity", advertised, nominal, over_ratio)
    return {
        "nominal_bytes": nominal,
        "advertised_bytes": advertised,
        "over_ratio": over_ratio,
        "over_threshold_bytes": over_threshold(nominal, over_ratio),
        "advertised_ratio": round(advertised / nominal, 4) if nominal else None,
        "exceeded": exceeded,
        "summary": summary,
    }


@dataclass(frozen=True)
class CapacityVerdict:
    """Judgment result of the capacity step."""

    status: StepStatus
    # "ok" | "short" | "defect" | "counterfeit" | "unverified"
    kind: str
    counterfeit: bool
    # "over_advertised" | "over_actual" | "probe_bad_news" | "lost_ratio"
    counterfeit_reasons: tuple[str, ...]
    summary: str


def _counterfeit_reasons(
    *,
    actual: int,
    advertised: int,
    nominal: int | None,
    base: int,
    over_ratio: float,
    lost_ratio: float | None,
    probe_verdict_line: str | None,
    threshold: int,
) -> tuple[list[str], list[str]]:
    """Evaluate counterfeit conditions in a fixed order (reported excess -> actual excess ->
    f3probe -> lost ratio)."""
    codes: list[str] = []
    texts: list[str] = []
    if nominal is not None and exceeds(advertised, nominal, over_ratio):
        codes.append("over_advertised")
        texts.append(_over_text("advertised capacity", advertised, nominal, over_ratio))
    if nominal is not None and exceeds(actual, nominal, over_ratio):
        codes.append("over_actual")
        texts.append(_over_text("actual capacity", actual, nominal, over_ratio))
    if probe_verdict_line is not None and probe_verdict_line.startswith(_BAD_NEWS):
        codes.append("probe_bad_news")
        texts.append(f"f3probe: {probe_verdict_line}")
    if lost_ratio is not None and lost_ratio >= LOST_RATIO_COUNTERFEIT:
        codes.append("lost_ratio")
        texts.append(
            f"lost {lost_ratio * 100:.1f}%"
            f" (actual {_gb(actual)} / nominal {_gb(base)}, threshold {_gb(threshold)})"
        )
    return codes, texts


def judge(
    *,
    actual: int,
    advertised: int,
    nominal: int | None,
    pass_ratio: float,
    over_ratio: float,
    bad_sectors: int | None,
    lost_ratio: float | None,
    probe_verdict_line: str | None,
    verified_bytes: int | None,
    threshold: int,
) -> CapacityVerdict:
    """Judge capacity. Priority: counterfeit > short > defect > unverified > ok.

    Args:
        actual: Actual capacity (bytes).
        advertised: Capacity reported by lsblk (bytes).
        nominal: Nominal capacity from the product page (bytes). None if unset.
        pass_ratio: Lower ratio for insufficient capacity.
        over_ratio: Upper ratio for excess capacity.
        bad_sectors: Total f3read non-OK sectors. None in f3probe mode.
        lost_ratio: Lost-sector ratio. None in f3probe mode.
        probe_verdict_line: f3probe Good news / Bad news line. None in f3write mode.
        verified_bytes: Bytes verified by f3read. None in f3probe mode.
        threshold: Threshold for insufficient capacity (ceil(base x pass_ratio)).

    Returns:
        Judgment result. The defect summary has only the sector total; the caller adds the
        breakdown.
    """
    base = nominal or advertised
    codes, texts = _counterfeit_reasons(
        actual=actual,
        advertised=advertised,
        nominal=nominal,
        base=base,
        over_ratio=over_ratio,
        lost_ratio=lost_ratio,
        probe_verdict_line=probe_verdict_line,
        threshold=threshold,
    )
    if codes:
        return CapacityVerdict(
            StepStatus.FAIL,
            "counterfeit",
            True,
            tuple(codes),
            "Suspected counterfeit: " + "; ".join(texts),
        )
    ratio = actual / base if base else 0.0
    if not evaluate(actual, base, pass_ratio):
        summary = (
            f"Capacity shortfall: actual {_gb(actual)} / nominal {_gb(base)}"
            f" ({ratio * 100:.1f}%), threshold {_gb(threshold)}"
        )
        return CapacityVerdict(StepStatus.FAIL, "short", False, (), summary)
    if bad_sectors is not None and bad_sectors > 0:
        return CapacityVerdict(
            StepStatus.FAIL, "defect", False, (), f"Data corruption: {bad_sectors} sectors"
        )
    if verified_bytes is not None and verified_bytes < threshold:
        used = actual - verified_bytes
        summary = (
            f"Verified area {_gb(verified_bytes)} is below threshold {_gb(threshold)}"
            f" (already used {_gb(used)}; consider use_probe = true)"
        )
        return CapacityVerdict(StepStatus.ERROR, "unverified", False, (), summary)
    summary = f"actual {_gb(actual)} / nominal {_gb(base)} ({ratio * 100:.1f}%)"
    return CapacityVerdict(StepStatus.PASS, "ok", False, (), summary)
