"""容量判定の純関数（f3write・f3probeの両モードと非破壊チェックが共用する）。

外部コマンドもデバイスも触らない。判定の分母はnominal_bytes（商品ページの公称容量）が
設定されていればそれ、無ければlsblkの申告容量（advertised）。
"""

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from ..models import StepStatus

# 消失セクタ比率がこの値以上なら偽装疑い（エイリアスメモリは実容量を超えた分が全て消える）。
LOST_RATIO_COUNTERFEIT = 0.50

_BAD_NEWS = "Bad news:"


def evaluate(actual_bytes: int, advertised_bytes: int, pass_ratio: float = 0.93) -> bool:
    """実容量が分母×pass_ratio以上か。分母が0以下ならFalse。"""
    return advertised_bytes > 0 and actual_bytes >= advertised_bytes * pass_ratio


def _gb(size: int | float) -> str:
    """バイト数を10^9単位のGB表記にする（report/text.pyの公称容量表示と揃える）。"""
    return f"{size / 1e9:.2f}GB"


def over_threshold(nominal: int | None, over_ratio: float) -> int | None:
    """容量超過の境界（バイト）。nominalがNoneまたは0以下ならNone。

    浮動小数のまま掛けると32_000_000_000 × 1.07 = 34240000000.000004となり、ceilで
    1バイトずれる。Fraction(str(over_ratio))は10進表記どおりの有理数になるので、
    境界は「公称×over_ratioをバイトに切り上げた値」に一致する。
    """
    if nominal is None or nominal <= 0:
        return None
    return math.ceil(Fraction(nominal) * Fraction(str(over_ratio)))


def exceeds(value: int, nominal: int | None, over_ratio: float) -> bool:
    """valueがover_threshold()を超えるか。境界がNoneなら常にFalse（整数比較のみ）。"""
    threshold = over_threshold(nominal, over_ratio)
    return threshold is not None and value > threshold


def _over_text(label: str, value: int, nominal: int, over_ratio: float) -> str:
    """容量超過の理由文言（申告容量・実容量で共用）。"""
    threshold = over_threshold(nominal, over_ratio)
    return (
        f"{label}{_gb(value)} > 上限{_gb(threshold or 0)}"
        f"（公称{_gb(nominal)}の{value / nominal * 100:.1f}%）"
    )


def precheck_advertised(advertised: int, nominal: int | None, over_ratio: float) -> dict[str, Any]:
    """申告容量と公称容量の非破壊比較。常に辞書を返す（nominal未設定ならexceeded = False）。"""
    exceeded = exceeds(advertised, nominal, over_ratio)
    summary = ""
    if exceeded and nominal is not None:
        summary = _over_text("申告容量", advertised, nominal, over_ratio)
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
    """容量ステップの判定結果。"""

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
    """偽装疑いの条件を固定順（申告超過→実容量超過→f3probe→消失比率）で評価する。"""
    codes: list[str] = []
    texts: list[str] = []
    if nominal is not None and exceeds(advertised, nominal, over_ratio):
        codes.append("over_advertised")
        texts.append(_over_text("申告容量", advertised, nominal, over_ratio))
    if nominal is not None and exceeds(actual, nominal, over_ratio):
        codes.append("over_actual")
        texts.append(_over_text("実容量", actual, nominal, over_ratio))
    if probe_verdict_line is not None and probe_verdict_line.startswith(_BAD_NEWS):
        codes.append("probe_bad_news")
        texts.append(f"f3probe: {probe_verdict_line}")
    if lost_ratio is not None and lost_ratio >= LOST_RATIO_COUNTERFEIT:
        codes.append("lost_ratio")
        texts.append(
            f"消失{lost_ratio * 100:.1f}%"
            f"（実容量{_gb(actual)} / 公称{_gb(base)}、閾値{_gb(threshold)}）"
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
    """容量の判定。優先順位はcounterfeit>short>defect>unverified>ok。

    Args:
        actual: 実容量（バイト）。
        advertised: lsblkの申告容量（バイト）。
        nominal: 商品ページの公称容量（バイト）。未設定ならNone。
        pass_ratio: 容量不足の下限比率。
        over_ratio: 容量超過の上限比率。
        bad_sectors: f3readの非OKセクタ合計。f3probeモードはNone。
        lost_ratio: 消失セクタ比率。f3probeモードはNone。
        probe_verdict_line: f3probeのGood news／Bad news行。f3writeモードはNone。
        verified_bytes: f3readで検証できたバイト数。f3probeモードはNone。
        threshold: 容量不足の閾値（ceil(base × pass_ratio)）。

    Returns:
        判定結果。defectのsummaryはセクタ合計だけで、内訳は呼び出し側が付ける。
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
            StepStatus.FAIL, "counterfeit", True, tuple(codes), "偽装疑い: " + "、".join(texts)
        )
    ratio = actual / base if base else 0.0
    if not evaluate(actual, base, pass_ratio):
        summary = (
            f"容量不足: 実容量{_gb(actual)} / 公称{_gb(base)}"
            f"（{ratio * 100:.1f}%）、閾値{_gb(threshold)}"
        )
        return CapacityVerdict(StepStatus.FAIL, "short", False, (), summary)
    if bad_sectors is not None and bad_sectors > 0:
        return CapacityVerdict(
            StepStatus.FAIL, "defect", False, (), f"データ破損: {bad_sectors}セクタ"
        )
    if verified_bytes is not None and verified_bytes < threshold:
        used = actual - verified_bytes
        summary = (
            f"検証済み領域{_gb(verified_bytes)}が閾値{_gb(threshold)}に届かない"
            f"（既使用{_gb(used)}、use_probe = trueを検討）"
        )
        return CapacityVerdict(StepStatus.ERROR, "unverified", False, (), summary)
    summary = f"実容量{_gb(actual)} / 公称{_gb(base)}（{ratio * 100:.1f}%）"
    return CapacityVerdict(StepStatus.PASS, "ok", False, (), summary)
