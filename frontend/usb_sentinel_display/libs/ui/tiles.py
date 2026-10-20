import tkinter as tk
from collections.abc import Callable

from ..geometry import IDENTITY, Geometry
from ..mode_view import ModeView
from ..models import Slot, SlotState
from ..result_file import drive_tag
from . import theme
from .i18n import language_button_label, text
from .theme import (
    ARROW_BTN_W,
    BLINK_BORDER,
    BORDER,
    LANG_BTN_X0,
    LANG_BTN_X1,
    MODE_ROW_H,
    MODE_ROW_Y0,
    NAV_ROW_H,
    NAV_ROW_Y0,
    TAG_RIGHT_X,
    TILE_COUNT,
    TILE_H,
    TILE_W,
    Fonts,
)
from .tiles_text import (  # noqa: F401 -- re-exported for callers of the old module path
    FALLBACK_HEAD,
    FALLBACK_HEAD_2ROWS,
    SERIAL_HEAD,
    SERIAL_MAX,
    TWO_ROW_HEAD,
    finished_hhmm,
    fit_text,
    hit_target,
    is_malware,
    local_hhmm,
    mode_button,
    serial_text,
    status_text,
    tag_key,
    tile_color,
    title_text,
    truncate,
)

TEXT_X = 8
# Gap between the shortened serial and the drive tag on line 2.
TAG_GAP = 6


class TilesView:
    """Draw the 4 tiles in the left 160 px, the prev/language/next row and the mode button."""

    def __init__(
        self,
        canvas: tk.Canvas,
        fonts: Fonts,
        on_select: Callable[[int], None],
        on_step: Callable[[int], None],
        on_language: Callable[[], None],
        on_mode: Callable[[], None],
        rows: int,
        *,
        geometry: Geometry = IDENTITY,
    ) -> None:
        """Take the target and callbacks, and bind tap detection.

        Args:
            canvas: The left-side 160 px Canvas.
            fonts: The resolved font set.
            on_select: Function called when a tile is tapped (slot index).
            on_step: Function called when prev/next is tapped (-1/+1).
            on_language: Function called when the language button is tapped.
            on_mode: Function called when the mode button is tapped.
            rows: Lines per tile (2 or 3, decided by theme.row_layout()).
            geometry: Design-unit to device-pixel conversion (the Canvas is drawn in design
                units and scaled once per redraw; taps are converted back).
        """
        self.canvas = canvas
        self.fonts = fonts
        self.geometry = geometry
        self.on_select = on_select
        self.on_step = on_step
        self.on_language = on_language
        self.on_mode = on_mode
        self.rows = rows
        self._lang = "en"
        # Line heights in design units: the fonts are device-sized, the drawing is not yet.
        self._line_h = (
            fonts.verdict.metrics("linespace") / geometry.scale,
            fonts.body.metrics("linespace") / geometry.scale,
        )
        canvas.bind("<Button-1>", self._on_click)

    def _on_click(self, event: tk.Event) -> None:
        """Map a tap to a tile or button. App decides what prev/next mean (port or page)."""
        geometry = self.geometry
        target, index = hit_target(int(geometry.design(event.x)), int(geometry.design(event.y)))
        if target == "tile":
            self.on_select(index)
        elif target == "prev":
            self.on_step(-1)
        elif target == "language":
            self.on_language()
        elif target == "next":
            self.on_step(1)
        else:
            self.on_mode()

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

    def _tag(self, slot: Slot) -> str | None:
        """Drive tag text for line 2 (3-line layout, DONE/REMOVED with a known mode), else None."""
        if self.rows != 3 or slot.result is None:
            return None
        if slot.state not in (SlotState.DONE, SlotState.REMOVED):
            return None
        key = tag_key(drive_tag(slot.result))
        return text(self._lang, key) if key is not None else None

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
        # A long label (e.g. 異常なし（検査のみ）) switches to the compact font instead of clipping.
        title_room = self.geometry.px(TILE_W - TEXT_X * 2)
        title_font = self.fonts.verdict
        if title_font.measure(title) > title_room:
            title_font = self.fonts.verdict_compact
        canvas.create_text(TEXT_X, y, text=title, anchor="nw", fill=color, font=title_font)
        y += verdict_h
        serial = serial_text(slot, self.rows)
        status = status_text(slot, now, dots, self._lang)
        if self.rows == 2:
            line = f"{serial} {status}".strip()
            canvas.create_text(TEXT_X, y, text=line, anchor="nw", fill=color, font=self.fonts.body)
            return
        tag = self._tag(slot)
        if tag is not None:
            measure = self.fonts.body.measure
            # Both sides in device pixels: the font measures at device size.
            room = self.geometry.px(TILE_W - TEXT_X * 2 - TAG_GAP) - measure(tag)
            serial = fit_text(serial, room, measure)
            canvas.create_text(
                TAG_RIGHT_X, y, text=tag, anchor="ne", fill=color, font=self.fonts.body
            )
        canvas.create_text(TEXT_X, y, text=serial, anchor="nw", fill=color, font=self.fonts.body)
        y += body_h
        canvas.create_text(TEXT_X, y, text=status, anchor="nw", fill=color, font=self.fonts.body)

    def _buttons(self, lang: str, mode: ModeView) -> None:
        """Draw the prev/language/next row, the separator and the mode button (design 3.5)."""
        canvas = self.canvas
        cy = NAV_ROW_Y0 + NAV_ROW_H // 2
        canvas.create_rectangle(
            0, NAV_ROW_Y0, TILE_W, NAV_ROW_Y0 + NAV_ROW_H, fill=theme.COLORS["button"], width=0
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
        key, background, foreground = mode_button(mode)
        canvas.create_rectangle(
            0,
            MODE_ROW_Y0,
            TILE_W,
            MODE_ROW_Y0 + MODE_ROW_H,
            fill=theme.COLORS[background],
            width=0,
        )
        canvas.create_line(
            0,
            MODE_ROW_Y0,
            TILE_W,
            MODE_ROW_Y0,
            fill=theme.COLORS["separator"],
            width=self.geometry.px(1),
        )
        canvas.create_text(
            TILE_W // 2,
            MODE_ROW_Y0 + MODE_ROW_H // 2,
            text=text(lang, key),
            fill=theme.COLORS[foreground],
            font=self.fonts.body,
            tags="mode_label",
        )

    def render(
        self,
        slots: list[Slot],
        selected: int,
        blink_on: bool,
        dots: int,
        now: float,
        lang: str,
        mode: ModeView,
    ) -> None:
        """Redraw all 4 tiles, the navigation row and the mode button.

        Args:
            slots: Slot list (4 items).
            selected: Index of the selected slot.
            blink_on: Whether the Malware blink frame is bright red.
            dots: Number of ellipses after "inspecting".
            now: Current time (used to compute elapsed minutes).
            lang: 'en' or 'ja'.
            mode: Current inspection-mode view (button text and colour).
        """
        self._lang = lang
        self.canvas.delete("all")
        for slot in slots[:TILE_COUNT]:
            self._tile(slot, slot.index == selected, blink_on, now, dots)
        self._buttons(lang, mode)
        geometry = self.geometry
        if not geometry.is_identity:
            self.canvas.scale("all", 0, 0, geometry.scale, geometry.scale)
