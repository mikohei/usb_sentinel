"""速度判定の材料を組む純関数群（参照値の決定・重大度・警告文）。

fioは呼ばない。speed.pyのrun()からだけ使う。業界最低値の表と種別の決定は
speed_floor.pyが持ち、ここはそれを読み・書きの参照値と文言に落とす。
"""

from collections.abc import Iterable
from typing import Literal

from ..speed_floor import DEVICE_FLOORS, USB2_HIGH_SPEED_MBPS, Reference, effective_mb_s

Mode = Literal["read", "write"]

# 実測 / 基準値 がこの値未満のFAILはsevere、それ以外のFAILはminor。
SEVERE_RATIO = 0.50
# 種別の基準値がリンク速度の実効上限のこの割合以上なら「頭打ちの可能性」を警告する。
LINK_CEILING_RATIO = 0.9

FAIL_PREFIX: dict[str, str] = {
    "severe": "速度不足（著しく下回る）: ",
    "minor": "速度不足（わずかに下回る）: ",
}


def reference_for(mode: Mode, nominal: float | None, device_class: str | None) -> Reference:
    """公称値 > 種別の最低値 > なし、の優先順位で基準値を決める。

    Args:
        mode: "read" または "write"。
        nominal: `nominal_*_mb_s`の設定値。未設定ならNone。
        device_class: resolve_device_class()が決めた種別キー。不明ならNone。

    Returns:
        基準値と根拠。両方無ければvalue None・basis "none"。
    """
    if nominal is not None:
        return Reference(nominal, "nominal", device_class)
    if device_class is not None:
        floor = DEVICE_FLOORS[device_class]
        value = floor.read_mb_s if mode == "read" else floor.write_mb_s
        return Reference(value, "class", device_class)
    return Reference(None, "none", device_class)


def class_warnings(
    configured: str | None,
    class_basis: str,
    link_speed_mbps: int | None,
    *,
    has_nominal: bool,
) -> list[str]:
    """種別決定に関する警告（リンク速度からの推定、USB1.1リンク）を返す。

    Args:
        configured: `[speed] device_class`の設定値。
        class_basis: resolve_device_class()が返した根拠。
        link_speed_mbps: USBリンク速度（Mbps）。不明ならNone。
        has_nominal: `nominal_read_mb_s`・`nominal_write_mb_s`のどちらかが設定済みならTrue。
            設定済みなら公称値で判定できるので「`nominal_*`を設定」の警告は出さない。

    Returns:
        警告文のリスト。該当が無ければ空。
    """
    warnings: list[str] = []
    if class_basis == "link_speed":
        warnings.append(
            "種別未設定のためUSBフラッシュ既定値で判定"
            "（HDD・SSD・SDは`[speed] device_class`を設定）"
        )
    if (
        configured is None
        and not has_nominal
        and link_speed_mbps is not None
        and 0 < link_speed_mbps < USB2_HIGH_SPEED_MBPS
    ):
        warnings.append(f"USB1.1リンク（{link_speed_mbps}Mbps）は判定基準なし。`nominal_*`を設定")
    return warnings


def link_ceiling_warning(
    references: Iterable[Reference], link_speed_mbps: int | None
) -> str | None:
    """種別の基準値がリンク速度の実効上限に迫るときの警告を1件だけ返す。

    公称値（basis "nominal"）は利用者が与えた値なので比べない。

    Args:
        references: 読み・書きの基準値。
        link_speed_mbps: USBリンク速度（Mbps）。Noneなら判定しない。

    Returns:
        警告文。該当が無ければNone。
    """
    if link_speed_mbps is None:
        return None
    effective = effective_mb_s(link_speed_mbps)
    for ref in references:
        if (
            ref.basis == "class"
            and ref.value is not None
            and ref.value >= effective * LINK_CEILING_RATIO
        ):
            return (
                f"基準値{ref.value:.1f}MB/sはリンク速度{link_speed_mbps}Mbpsの実効上限"
                f"（約{effective:.1f}MB/s）を超える。ホストの上限で頭打ちの可能性。"
                "`nominal_*`の設定を推奨"
            )
    return None


def basis_text(ref: Reference, pass_ratio: float, class_basis: str) -> str:
    """FAILのsummaryに付ける根拠の括弧書きを組む。

    公称「（公称90.0×0.80）」、種別設定「（USB3.0フラッシュ既定50.0×0.80）」、
    種別推定「（USB3.0フラッシュ既定50.0×0.80・種別推定）」。

    Args:
        ref: 比較した基準値。valueがNoneなら空文字。
        pass_ratio: 合格比率。
        class_basis: 種別の根拠。"link_speed"なら「・種別推定」を付ける。

    Returns:
        括弧書きの文字列。
    """
    if ref.value is None:
        return ""
    if ref.basis == "nominal" or ref.device_class is None:
        return f"（公称{ref.value:.1f}×{pass_ratio:.2f}）"
    label = DEVICE_FLOORS[ref.device_class].label
    suffix = "・種別推定" if class_basis == "link_speed" else ""
    return f"（{label}既定{ref.value:.1f}×{pass_ratio:.2f}{suffix}）"


def severity_for(ratios: Iterable[float]) -> str:
    """閾値を下回った項目の比率から全体の重大度を決める。一方がsevereなら全体もsevere。

    Args:
        ratios: 実測 / 基準値（下回った項目だけ）。

    Returns:
        "severe" または "minor"。
    """
    return "severe" if any(ratio < SEVERE_RATIO for ratio in ratios) else "minor"
