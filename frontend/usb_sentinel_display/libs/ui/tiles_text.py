"""Pure helpers behind the tile Canvas: display strings, tap mapping and the mode button look.

No tkinter here; tiles.py re-exports every name so older imports keep working.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from ..mode_view import ModeState, ModeView
from ..models import MALWARE_VERDICT, Slot, SlotState
from ..result_file import TAG_ERASED, TAG_SCAN
from . import theme
from .i18n import state_label, text, verdict_label
from .theme import ARROW_BTN_W, LANG_BTN_X1, MODE_ROW_Y0, NAV_ROW_Y0, TILE_H

# Serial display limit (design doc 1.2).
SERIAL_MAX = 14
SERIAL_HEAD = 13
FALLBACK_HEAD = 9
FALLBACK_HEAD_2ROWS = 5
TWO_ROW_HEAD = 10
ELLIPSIS = "…"

# Tag key -> i18n key (design doc inspection-mode 3.9, D4).
_TAG_KEYS: dict[str, str] = {TAG_SCAN: "mode.tag_scan", TAG_ERASED: "mode.tag_erased"}


def local_hhmm(ts: float) -> str:
    """Turn epoch seconds into local-time HH:MM."""
    return datetime.fromtimestamp(ts, tz=UTC).astimezone().strftime("%H:%M")


def finished_hhmm(finished_at: str, fallback_ts: float) -> str:
    """Turn finished_at (ISO 8601) into HH:MM. Use fallback_ts if unparsable."""
    try:
        parsed = datetime.fromisoformat(finished_at)
    except ValueError:
        return local_hhmm(fallback_ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone().strftime("%H:%M")


def truncate(text: str, head: int) -> str:
    """Beyond head chars, return head chars plus an ellipsis."""
    return text if len(text) <= head else text[:head] + ELLIPSIS


def fit_text(text: str, max_px: int, measure: Callable[[str], int]) -> str:
    """Shorten text from the end, adding an ellipsis, until it measures at most max_px.

    Args:
        text: Text to fit.
        max_px: Available width in pixels.
        measure: Font measuring function (tkfont.Font.measure).

    Returns:
        The text unchanged if it fits, else the longest `head…` that fits, else "" when not even
        the ellipsis fits.
    """
    if measure(text) <= max_px:
        return text
    for end in range(len(text) - 1, 0, -1):
        candidate = text[:end] + ELLIPSIS
        if measure(candidate) <= max_px:
            return candidate
    return ELLIPSIS if measure(ELLIPSIS) <= max_px else ""


def serial_text(slot: Slot, rows: int) -> str:
    """Serial shown on line 2. With fallback, add the kernel name (design doc 1.2)."""
    serial = ""
    if slot.result is not None:
        serial = slot.result.serial
    elif slot.disk is not None:
        serial = slot.disk.serial
    kernel_name = slot.disk.kernel_name if slot.disk is not None else slot.retained_kernel
    if slot.fallback and kernel_name is not None:
        head = FALLBACK_HEAD_2ROWS if rows == 2 else FALLBACK_HEAD
        return f"{truncate(serial, head)} {kernel_name}"
    if rows == 2:
        return truncate(serial, TWO_ROW_HEAD)
    return serial if len(serial) <= SERIAL_MAX else serial[:SERIAL_HEAD] + ELLIPSIS


def status_text(slot: Slot, now: float, dots: int, lang: str) -> str:
    """State text and time on line 3. While inspecting, elapsed minutes and dots ellipses."""
    state = slot.state
    if state is SlotState.EMPTY:
        return ""
    if state is SlotState.INSPECTING and slot.disk is not None:
        minutes = int(max(0.0, now - slot.disk.attached_at)) // 60
        return text(lang, "inspecting_minutes", minutes=minutes, dots=ELLIPSIS * dots)
    if state in (SlotState.DONE, SlotState.REMOVED) and slot.result is not None:
        key = "done_at" if state is SlotState.DONE else "removed_at"
        return text(lang, key, time=finished_hhmm(slot.result.finished_at, slot.result.mtime))
    word = state_label(lang, state)
    if slot.disk is not None:
        return text(lang, "state_at", word=word, time=local_hhmm(slot.disk.attached_at))
    return word


def files_text(slot: Slot, lang: str) -> tuple[str, str] | None:
    """File count for the right end of line 3: a finished result whose hash inventory has rows.

    A drive straight from the factory should be empty, so files on it are worth a look even
    when the verdict is clean.

    Returns:
        (full, short) texts, the short one for when the full one does not fit. None for an
        empty or missing inventory.
    """
    if slot.state not in (SlotState.DONE, SlotState.REMOVED) or slot.result is None:
        return None
    lines = slot.result.detail.hash_lines
    if lines is None or lines <= 0:
        return None
    return text(lang, "tile.files", count=lines), text(lang, "tile.files_short", count=lines)


def title_text(slot: Slot, lang: str) -> str:
    """Port name and verdict on line 1 (state text when there is no verdict)."""
    label = text(lang, "fallback_label") if slot.fallback else slot.label
    if slot.state in (SlotState.DONE, SlotState.REMOVED) and slot.result is not None:
        return f"{label} {verdict_label(lang, slot.result.verdict)}"
    return f"{label} {state_label(lang, slot.state)}"


def tile_color(slot: Slot) -> str:
    """Tile background color. Done uses the verdict color; removed a darkened verdict color."""
    if slot.state in (SlotState.DONE, SlotState.REMOVED) and slot.result is not None:
        color = theme.verdict_color(slot.result.verdict)
        return theme.darken(color) if slot.state is SlotState.REMOVED else color
    return theme.STATE_COLORS.get(slot.state, theme.COLORS["no_result"])


def is_malware(slot: Slot) -> bool:
    """Whether it should blink (a connected Malware Detected)."""
    return (
        slot.state is SlotState.DONE
        and slot.result is not None
        and slot.result.verdict == MALWARE_VERDICT
    )


def hit_target(x: int, y: int) -> tuple[str, int]:
    """Map a tap on the left Canvas to ("tile", i), ("prev"|"language"|"next"|"mode", 0).

    y < 256 is a tile (y // 64); 256 <= y < 288 is the navigation row split by x; y >= 288 is
    the mode button at every x.
    """
    if y < NAV_ROW_Y0:
        return ("tile", max(y, 0) // TILE_H)
    if y < MODE_ROW_Y0:
        if x < ARROW_BTN_W:
            return ("prev", 0)
        if x < LANG_BTN_X1:
            return ("language", 0)
        return ("next", 0)
    return ("mode", 0)


def tag_key(tag: str | None) -> str | None:
    """i18n key for a result_file.drive_tag() value ("scan" / "erased"); None otherwise."""
    return _TAG_KEYS.get(tag) if tag is not None else None


def mode_button(view: ModeView) -> tuple[str, str, str]:
    """(i18n key, background colour key, text colour key) of the mode button (design 3.5).

    SCAN_ONLY and CONFIRMING share the enabled look; FULL and STOPPING use the erase colour;
    a locked SCAN_ONLY is greyed out.
    """
    if view.state is ModeState.FULL:
        return ("mode.button_full", "erase_on", "text")
    if view.state is ModeState.STOPPING:
        return ("mode.button_stopping", "erase_on", "text")
    if view.state is ModeState.SCAN_ONLY and view.locked_reason is not None:
        return ("mode.button_locked", "button", "empty_text")
    return ("mode.button_scan_only", "button", "text")
