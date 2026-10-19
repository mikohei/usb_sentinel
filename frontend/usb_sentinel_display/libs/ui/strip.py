"""Pure functions that build the info strip (320x24) text. Does not load the GUI library."""

from ..models import Slot, SlotState
from ..monitor import Snapshot
from ..qr_payload import DETAIL_DIGEST_LEN, DETAIL_PAGES
from ..units import unit_name
from .i18n import state_label, text
from .theme import SCREEN_H, SCREEN_W

STRIP_SERIAL_HEAD = 8
STRIP_DIGEST_HEAD = 16


def resolution_warning(width: int, height: int) -> tuple[int, int] | None:
    """Measured (width, height) if not 480x320, else None (design doc 1.1). Caller writes text."""
    if (width, height) == (SCREEN_W, SCREEN_H):
        return None
    return (width, height)


def _slot_label(slot: Slot, lang: str) -> str:
    """Port name, or "P?" if it cannot be mapped."""
    return text(lang, "fallback_label") if slot.fallback else slot.label


def normal_strip_text(slot: Slot, lang: str) -> str:
    """Text during normal QR. With a result: serial prefix and first 16 digest chars."""
    label = _slot_label(slot, lang)
    result = slot.result
    if result is None:
        return f"{label} {state_label(lang, slot.state)}"
    serial = result.serial[:STRIP_SERIAL_HEAD]
    if len(result.serial) > STRIP_SERIAL_HEAD:
        serial += "…"
    return f"{label} {serial} d:{result.digest[:STRIP_DIGEST_HEAD]}"


def detail_strip_text(slot: Slot, lang: str, page: int) -> str:
    """Text during detail QR: mode and page, the arrow keys' role, and first 12 digest chars."""
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
    """Pick the info strip text by priority.

    Priority: resolution warning > internal error > notice > "N others" + journalctl hint > normal.

    Args:
        snapshot: Contents of the latest tick.
        selected: Index of the selected slot.
        unit_template: Unit name template used in the journalctl hint.
        resolution: Measured size if not 480x320. None means no warning.
        lang: 'en' or 'ja'.
        qr_mode: 0 for normal QR, 1-3 for detail QR pages.
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
