import tkinter as tk
from collections.abc import Callable

from .. import qr_payload, qr_render
from ..models import Slot
from . import theme
from .i18n import text
from .theme import QR_H, QR_W, STRIP_H, Fonts

STRIP_TEXT_X = 4


class QrView:
    """Draw the right 320 px QR canvas (white) and the info strip. A tap toggles normal/detail."""

    def __init__(self, canvas: tk.Canvas, fonts: Fonts, on_toggle: Callable[[], None]) -> None:
        """Take the target Canvas and fonts, and bind a tap to toggle normal/detail.

        Args:
            canvas: The right-side 320 px Canvas.
            fonts: The resolved font set.
            on_toggle: Function called on tap (toggles normal QR/detail QR).
        """
        self.canvas = canvas
        self.fonts = fonts
        # Canvas does not hold a reference to PhotoImage, so keep it here to avoid GC.
        self._image: tk.PhotoImage | None = None
        self._image_key: tuple[str, str, int] | None = None
        canvas.bind("<Button-1>", lambda _e: on_toggle())

    def _photo(self, slot: Slot, qr_mode: int) -> tk.PhotoImage | None:
        """Build a PhotoImage from the slot result and mode. Reuse it for the same result and mode.

        Raises:
            ValueError: When the payload exceeds that page's limit (other pages are unaffected).
        """
        if slot.result is None:
            return None
        key = (slot.result.dir_name, slot.result.digest, qr_mode)
        if self._image is not None and self._image_key == key:
            return self._image
        if qr_mode == 0:
            payload = qr_payload.build_payload(slot.result)
        else:
            payload = qr_payload.build_detail_page(slot.result, qr_mode)
        ppm, _side = qr_render.render(payload)
        self._image = tk.PhotoImage(master=self.canvas, data=ppm)
        self._image_key = key
        return self._image

    def render(self, slot: Slot | None, strip_text: str, qr_mode: int, lang: str) -> None:
        """Draw the QR centered on white and show the text in the info strip.

        Args:
            slot: The selected slot. If None or it has no result, leave it white.
            strip_text: Info strip text.
            qr_mode: 0 for normal QR, 1-3 for detail QR pages.
            lang: 'en' or 'ja' (used for the QR build failure text).
        """
        canvas = self.canvas
        canvas.delete("all")
        canvas.create_rectangle(0, 0, QR_W, QR_H, fill=theme.COLORS["qr_background"], width=0)
        if slot is not None and slot.result is not None:
            try:
                image = self._photo(slot, qr_mode)
            except ValueError:
                self._image = None
                self._image_key = None
                canvas.create_text(
                    QR_W // 2,
                    QR_H // 2,
                    text=text(lang, "qr_failed"),
                    fill=theme.COLORS["qr_module"],
                    font=self.fonts.verdict,
                )
            else:
                canvas.create_image(QR_W // 2, QR_H // 2, image=image)
        else:
            self._image = None
            self._image_key = None
        canvas.create_rectangle(0, QR_H, QR_W, QR_H + STRIP_H, fill=theme.COLORS["strip"], width=0)
        canvas.create_text(
            STRIP_TEXT_X,
            QR_H + STRIP_H // 2,
            text=strip_text,
            anchor="w",
            fill=theme.COLORS["text"],
            font=self.fonts.body,
        )

    @property
    def image(self) -> tk.PhotoImage | None:
        """PhotoImage of the QR currently shown (for tests)."""
        return self._image
