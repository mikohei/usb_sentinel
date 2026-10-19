import tkinter as tk
from collections.abc import Callable
from datetime import UTC, datetime

from ..models import Slot, SlotState
from . import theme
from .i18n import language_button_label, state_label, text, verdict_label
from .theme import (
    ARROW_BTN_W,
    BLINK_BORDER,
    BORDER,
    LANG_BTN_X0,
    LANG_BTN_X1,
    TILE_COUNT,
    TILE_H,
    TILE_W,
    Fonts,
)

# Serial display limit (design doc 1.2).
SERIAL_MAX = 14
SERIAL_HEAD = 13
FALLBACK_HEAD = 9
FALLBACK_HEAD_2ROWS = 5
TWO_ROW_HEAD = 10
TEXT_X = 8


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
    return text if len(text) <= head else text[:head] + "…"


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
    return serial if len(serial) <= SERIAL_MAX else serial[:SERIAL_HEAD] + "…"


def status_text(slot: Slot, now: float, dots: int, lang: str) -> str:
    """State text and time on line 3. While inspecting, elapsed minutes and dots ellipses."""
    state = slot.state
    if state is SlotState.EMPTY:
        return ""
    if state is SlotState.INSPECTING and slot.disk is not None:
        minutes = int(max(0.0, now - slot.disk.attached_at)) // 60
        return text(lang, "inspecting_minutes", minutes=minutes, dots="…" * dots)
    if state in (SlotState.DONE, SlotState.REMOVED) and slot.result is not None:
        key = "done_at" if state is SlotState.DONE else "removed_at"
        return text(lang, key, time=finished_hhmm(slot.result.finished_at, slot.result.mtime))
    word = state_label(lang, state)
    if slot.disk is not None:
        return text(lang, "state_at", word=word, time=local_hhmm(slot.disk.attached_at))
    return word


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
        and slot.result.verdict == "Malware Detected"
    )


class TilesView:
    """Draw the 4 tiles in the left 160 px and the prev/language/next buttons."""

    def __init__(
        self,
        canvas: tk.Canvas,
        fonts: Fonts,
        on_select: Callable[[int], None],
        on_step: Callable[[int], None],
        on_language: Callable[[], None],
        rows: int,
    ) -> None:
        """Take the target and callbacks, and bind tap detection.

        Args:
            canvas: The left-side 160 px Canvas.
            fonts: The resolved font set.
            on_select: Function called when a tile is tapped (slot index).
            on_step: Function called when prev/next is tapped (-1/+1).
            on_language: Function called when the language button is tapped.
            rows: Lines per tile (2 or 3, decided by theme.row_layout()).
        """
        self.canvas = canvas
        self.fonts = fonts
        self.on_select = on_select
        self.on_step = on_step
        self.on_language = on_language
        self.rows = rows
        self._lang = "en"
        self._line_h = (fonts.verdict.metrics("linespace"), fonts.body.metrics("linespace"))
        canvas.bind("<Button-1>", self._on_click)

    def _on_click(self, event: tk.Event) -> None:
        """Map a tap to a tile or button. App decides what prev/next mean (port or page)."""
        index = int(event.y) // TILE_H
        if index < TILE_COUNT:
            self.on_select(index)
        elif int(event.x) < ARROW_BTN_W:
            self.on_step(-1)
        elif int(event.x) < LANG_BTN_X1:
            self.on_language()
        else:
            self.on_step(1)

    def _frame(self, y0: int, outer: str, inner: str | None, background: str) -> None:
        """Paint the 4 px outer frame and, if needed, a 2 px inner frame."""
        canvas = self.canvas
        canvas.create_rectangle(0, y0, TILE_W, y0 + TILE_H, fill=outer, width=0)
        if inner is not None:
            canvas.create_rectangle(
                BORDER, y0 + BORDER, TILE_W - BORDER, y0 + TILE_H - BORDER, fill=inner, width=0
            )
            pad = BORDER + BLINK_BORDER
        else:
            pad = BORDER
        canvas.create_rectangle(
            pad, y0 + pad, TILE_W - pad, y0 + TILE_H - pad, fill=background, width=0
        )

    def _tile(self, slot: Slot, selected: bool, blink_on: bool, now: float, dots: int) -> None:
        """Draw one tile."""
        y0 = slot.index * TILE_H
        background = tile_color(slot)
        blink = theme.COLORS["blink"] if blink_on else background
        if selected:
            inner = blink if is_malware(slot) else None
            self._frame(y0, theme.COLORS["text"], inner, background)
        else:
            outer = blink if is_malware(slot) else background
            self._frame(y0, outer, None, background)
        color = (
            theme.COLORS["empty_text"] if slot.state is SlotState.EMPTY else theme.COLORS["text"]
        )
        verdict_h, body_h = self._line_h
        y = y0 + theme.TILE_PAD
        canvas = self.canvas
        title = title_text(slot, self._lang)
        canvas.create_text(TEXT_X, y, text=title, anchor="nw", fill=color, font=self.fonts.verdict)
        y += verdict_h
        serial = serial_text(slot, self.rows)
        status = status_text(slot, now, dots, self._lang)
        if self.rows == 2:
            line = f"{serial} {status}".strip()
            canvas.create_text(TEXT_X, y, text=line, anchor="nw", fill=color, font=self.fonts.body)
            return
        canvas.create_text(TEXT_X, y, text=serial, anchor="nw", fill=color, font=self.fonts.body)
        y += body_h
        canvas.create_text(TEXT_X, y, text=status, anchor="nw", fill=color, font=self.fonts.body)

    def _buttons(self, lang: str) -> None:
        """Draw the prev/language/next buttons. The language button shows the target in 14 px."""
        canvas = self.canvas
        y0 = TILE_H * TILE_COUNT
        cy = y0 + theme.BTN_H // 2
        canvas.create_rectangle(
            0, y0, TILE_W, y0 + theme.BTN_H, fill=theme.COLORS["button"], width=0
        )
        for x, arrow in ((ARROW_BTN_W // 2, "◀"), ((LANG_BTN_X1 + TILE_W) // 2, "▶")):
            canvas.create_text(x, cy, text=arrow, fill=theme.COLORS["text"], font=self.fonts.arrow)
        canvas.create_text(
            (LANG_BTN_X0 + LANG_BTN_X1) // 2,
            cy,
            text=language_button_label(lang),
            fill=theme.COLORS["text"],
            font=self.fonts.body,
        )

    def render(
        self, slots: list[Slot], selected: int, blink_on: bool, dots: int, now: float, lang: str
    ) -> None:
        """Redraw all 4 tiles and the button row.

        Args:
            slots: Slot list (4 items).
            selected: Index of the selected slot.
            blink_on: Whether the Malware blink frame is bright red.
            dots: Number of ellipses after "inspecting".
            now: Current time (used to compute elapsed minutes).
            lang: 'en' or 'ja'.
        """
        self._lang = lang
        self.canvas.delete("all")
        for slot in slots[:TILE_COUNT]:
            self._tile(slot, slot.index == selected, blink_on, now, dots)
        self._buttons(lang)
