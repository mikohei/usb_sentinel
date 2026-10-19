"""情報ストリップ（320x24）の文言を組む純関数。GUIライブラリを読まない。"""

from ..models import Slot, SlotState
from ..monitor import Snapshot
from ..qr_payload import DETAIL_DIGEST_LEN, DETAIL_PAGES
from ..units import unit_name
from .i18n import state_label, text
from .theme import SCREEN_H, SCREEN_W

STRIP_SERIAL_HEAD = 8
STRIP_DIGEST_HEAD = 16


def resolution_warning(width: int, height: int) -> tuple[int, int] | None:
    """480x320でなければ実測の(幅, 高さ)、そうならNone（設計書1.1）。文章化は呼び出し側。"""
    if (width, height) == (SCREEN_W, SCREEN_H):
        return None
    return (width, height)


def _slot_label(slot: Slot, lang: str) -> str:
    """ポート名。対応付け不能なら「P?」。"""
    return text(lang, "fallback_label") if slot.fallback else slot.label


def normal_strip_text(slot: Slot, lang: str) -> str:
    """通常QR表示中の文言。結果があればシリアル先頭とダイジェスト先頭16桁。"""
    label = _slot_label(slot, lang)
    result = slot.result
    if result is None:
        return f"{label} {state_label(lang, slot.state)}"
    serial = result.serial[:STRIP_SERIAL_HEAD]
    if len(result.serial) > STRIP_SERIAL_HEAD:
        serial += "…"
    return f"{label} {serial} d:{result.digest[:STRIP_DIGEST_HEAD]}"


def detail_strip_text(slot: Slot, lang: str, page: int) -> str:
    """詳細QR表示中の文言。モードとページ、◀▶の役割、ダイジェスト先頭12桁。"""
    label = _slot_label(slot, lang)
    mode = text(lang, "qr_mode_detail", page=page, total=DETAIL_PAGES)
    hint = text(lang, "qr_detail_hint")
    if slot.result is None:
        return f"{label} {mode} {hint}"
    return f"{label} {mode} {hint} d:{slot.result.digest[:DETAIL_DIGEST_LEN]}"


def strip_text(
    snapshot: Snapshot,
    selected: int,
    unit_template: str,
    resolution: tuple[int, int] | None,
    lang: str,
    qr_mode: int,
) -> str:
    """情報ストリップの文言を優先順で選ぶ。

    優先: 解像度警告 > 内部エラー > notice > 「他N台」とjournalctlの案内 > 通常表示。

    Args:
        snapshot: 直近のtickの内容。
        selected: 選択中のスロット番号。
        unit_template: journalctlの案内に使うユニット名の雛形。
        resolution: 480x320でない実測値。Noneなら警告なし。
        lang: 'en'または'ja'。
        qr_mode: 0が通常QR、1〜3が詳細QRのページ。
    """
    if resolution is not None:
        width, height = resolution
        return text(lang, "resolution", width=width, height=height)
    if snapshot.error is not None:
        if snapshot.error_repeated:
            return text(lang, "internal_error_repeated")
        return text(lang, "internal_error", name=snapshot.error)
    if snapshot.notice is not None:
        return text(lang, "notice." + snapshot.notice)
    slot = snapshot.slots[selected] if selected < len(snapshot.slots) else None
    parts: list[str] = []
    if snapshot.overflow > 0:
        parts.append(text(lang, "overflow", count=snapshot.overflow))
    if slot is not None and slot.state is SlotState.NO_RESULT and slot.disk is not None:
        unit = unit_name(unit_template, slot.disk.kernel_name)
        parts.append(text(lang, "check_journal", unit=unit))
    if parts:
        return " ".join(parts)
    if slot is None:
        return ""
    if qr_mode == 0:
        return normal_strip_text(slot, lang)
    return detail_strip_text(slot, lang, qr_mode)
