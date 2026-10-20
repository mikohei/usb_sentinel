"""Confirmation overlay for erase mode: a random 4-digit code entered with keys or buttons.

Design doc (inspection-mode) 3.6. The Canvas covers the whole 480x320 frame, so taps cannot
reach the tiles or the QR canvas while it is open. There is no Erase button and Enter does
nothing; the fourth correct digit confirms.
"""

import tkinter as tk
from collections.abc import Callable

from . import theme
from .i18n import text
from .theme import DIALOG_CANCEL, DIALOG_KEY_Y, DIALOG_KEYS, SCREEN_H, SCREEN_W, Fonts

TITLE_POS = (220, 22)
BODY_POS = (240, 48)
BODY_WIDTH = 440
CODE_POS = (240, 140)
HINT_POS = (240, 172)
# Canvas tags, used by tests and the screenshot script to find the items.
TAG_TITLE = "title"
TAG_BODY = "body"
TAG_CODE = "code"
TAG_HINT = "hint"
TAG_CANCEL = "cancel_label"


def confirm_hit(x: int, y: int) -> tuple[str, int] | None:
    """Map a tap on the dialog to ("digit", 0-3), ("cancel", 0) or None (text, gaps)."""
    cx0, cy0, cx1, cy1 = DIALOG_CANCEL
    if cx0 <= x < cx1 and cy0 <= y < cy1:
        return ("cancel", 0)
    ky0, ky1 = DIALOG_KEY_Y
    if ky0 <= y < ky1:
        for index, (x0, x1) in enumerate(DIALOG_KEYS):
            if x0 <= x < x1:
                return ("digit", index)
    return None


class ConfirmView:
    """The code dialog as a Canvas child of the App frame, hidden with place_forget()."""

    def __init__(
        self,
        frame: tk.Frame,
        fonts: Fonts,
        on_digit: Callable[[int], None],
        on_cancel: Callable[[], None],
    ) -> None:
        """Create the hidden Canvas and bind taps.

        Args:
            frame: The 480x320 App frame.
            fonts: The resolved font set.
            on_digit: Called with the 0-based button index (left to right).
            on_cancel: Called when the Cancel button is tapped.
        """
        self.canvas = tk.Canvas(
            frame,
            width=SCREEN_W,
            height=SCREEN_H,
            highlightthickness=0,
            bd=0,
            background=theme.COLORS["dialog"],
        )
        self.fonts = fonts
        self.on_digit = on_digit
        self.on_cancel = on_cancel
        self._visible = False
        self.canvas.bind("<Button-1>", self._on_click)

    def _on_click(self, event: tk.Event) -> None:
        """Dispatch a tap; anything outside the five buttons is ignored."""
        hit = confirm_hit(int(event.x), int(event.y))
        if hit is None:
            return
        kind, index = hit
        if kind == "digit":
            self.on_digit(index)
        else:
            self.on_cancel()

    @property
    def visible(self) -> bool:
        """Whether the dialog currently covers the screen."""
        return self._visible

    def show(self, lang: str, code: str, entered: int) -> None:
        """Draw the dialog for the code and progress, and raise it above both canvases.

        Args:
            lang: 'en' or 'ja'.
            code: The 4-digit code to display.
            entered: Digits entered so far (0-4), shown in the hint.
        """
        canvas = self.canvas
        fonts = self.fonts
        white = theme.COLORS["text"]
        canvas.delete("all")
        canvas.create_rectangle(0, 0, SCREEN_W, SCREEN_H, fill=theme.COLORS["dialog"], width=0)
        cx0, cy0, cx1, cy1 = DIALOG_CANCEL
        canvas.create_rectangle(cx0, cy0, cx1, cy1, fill=theme.COLORS["dialog_key"], width=0)
        canvas.create_text(
            (cx0 + cx1) // 2,
            (cy0 + cy1) // 2,
            text=text(lang, "mode.confirm_cancel"),
            fill=white,
            font=fonts.body,
            tags=TAG_CANCEL,
        )
        canvas.create_text(
            *TITLE_POS,
            text=text(lang, "mode.confirm_title"),
            fill=white,
            font=fonts.verdict,
            tags=TAG_TITLE,
        )
        canvas.create_text(
            *BODY_POS,
            text=text(lang, "mode.confirm_body"),
            anchor="n",
            width=BODY_WIDTH,
            justify="center",
            fill=white,
            font=fonts.body,
            tags=TAG_BODY,
        )
        canvas.create_text(
            *CODE_POS,
            text=text(lang, "mode.confirm_code", code=" ".join(code)),
            fill=white,
            font=fonts.verdict,
            tags=TAG_CODE,
        )
        canvas.create_text(
            *HINT_POS,
            text=text(lang, "mode.confirm_hint", n=entered),
            fill=white,
            font=fonts.body,
            tags=TAG_HINT,
        )
        ky0, ky1 = DIALOG_KEY_Y
        for index, (x0, x1) in enumerate(DIALOG_KEYS):
            canvas.create_rectangle(x0, ky0, x1, ky1, fill=theme.COLORS["dialog_key"], width=0)
            canvas.create_text(
                (x0 + x1) // 2,
                (ky0 + ky1) // 2,
                text=str(index + 1),
                fill=white,
                font=fonts.verdict,
            )
        if not self._visible:
            canvas.place(x=0, y=0)
            self._visible = True
        # Canvas.lift() is the item-level tag_raise; raise the widget itself above both canvases.
        tk.Misc.tkraise(canvas)

    def hide(self) -> None:
        """Remove the dialog from the screen (no-op when hidden)."""
        if self._visible:
            self.canvas.place_forget()
            self._visible = False
