"""GUI tests for the erase-mode confirmation overlay ConfirmView (@pytest.mark.gui)."""

import tkinter as tk

import pytest

from tests.test_gui_smoke import fonts, root
from usb_sentinel_display.libs.ui import confirm as confirm_mod
from usb_sentinel_display.libs.ui import theme
from usb_sentinel_display.libs.ui.confirm import ConfirmView
from usb_sentinel_display.libs.ui.theme import DIALOG_CANCEL, DIALOG_KEY_Y, DIALOG_KEYS, Fonts

__all__ = ["fonts", "root"]

pytestmark = pytest.mark.gui

TITLE_MAX_PX = 340
CANCEL_MAX_PX = 76
LINE_MAX_PX = 440
BODY_BOTTOM_MAX = 116


def make_view(root: tk.Tk, fonts: Fonts) -> tuple[ConfirmView, list[object], tk.Frame]:
    """ConfirmView inside a 480x320 frame; the list records on_digit/on_cancel calls."""
    frame = tk.Frame(root, width=480, height=320)
    frame.pack()
    calls: list[object] = []
    view = ConfirmView(frame, fonts, calls.append, lambda: calls.append("cancel"))
    root.update()
    return view, calls, frame


def item_text(canvas: tk.Canvas, tag: str) -> str:
    (item,) = canvas.find_withtag(tag)
    return str(canvas.itemcget(item, "text"))


class TestWidths:
    @pytest.mark.parametrize("lang", ["en", "ja"])
    def test_texts_fit(self, root: tk.Tk, fonts: Fonts, lang: str) -> None:
        """Title <= 340 px, Cancel <= 76 px, code and hint <= 440 px, body bottom <= 116."""
        view, _calls, _frame = make_view(root, fonts)
        view.show(lang, "3142", 4)
        canvas = view.canvas
        title = item_text(canvas, confirm_mod.TAG_TITLE)
        assert fonts.verdict.measure(title) <= TITLE_MAX_PX, title
        cancel = item_text(canvas, confirm_mod.TAG_CANCEL)
        assert fonts.body.measure(cancel) <= CANCEL_MAX_PX, cancel
        code = item_text(canvas, confirm_mod.TAG_CODE)
        assert code.endswith("3 1 4 2")
        assert fonts.verdict.measure(code) <= LINE_MAX_PX, code
        hint = item_text(canvas, confirm_mod.TAG_HINT)
        assert "4/4" in hint
        assert fonts.body.measure(hint) <= LINE_MAX_PX, hint
        _x0, _y0, _x1, y1 = canvas.bbox(confirm_mod.TAG_BODY)
        assert y1 <= BODY_BOTTOM_MAX, f"{lang} body bottom {y1}"

    def test_title_clears_cancel_button(self, root: tk.Tk, fonts: Fonts) -> None:
        """The title's bbox ends left of the Cancel rectangle in both languages."""
        view, _calls, _frame = make_view(root, fonts)
        for lang in ("en", "ja"):
            view.show(lang, "1111", 0)
            _x0, _y0, x1, _y1 = view.canvas.bbox(confirm_mod.TAG_TITLE)
            assert x1 < DIALOG_CANCEL[0]


class TestShowHide:
    def test_show_hide_visible(self, root: tk.Tk, fonts: Fonts) -> None:
        """Hidden at first; show() places and lifts the canvas; hide() removes it."""
        view, _calls, frame = make_view(root, fonts)
        assert view.visible is False
        assert view.canvas.winfo_manager() == ""
        view.show("en", "3142", 0)
        root.update()
        assert view.visible is True
        assert view.canvas.winfo_manager() == "place"
        assert view.canvas.winfo_x() == 0 and view.canvas.winfo_y() == 0
        assert view.canvas.winfo_width() == 480 and view.canvas.winfo_height() == 320
        assert view.canvas.cget("background") == theme.COLORS["dialog"]
        view.show("ja", "3142", 2)
        assert "（2/4）" in item_text(view.canvas, confirm_mod.TAG_HINT)
        view.hide()
        root.update()
        assert view.visible is False
        assert view.canvas.winfo_manager() == ""
        view.hide()  # idempotent
        assert view.visible is False

    def test_digits_drawn(self, root: tk.Tk, fonts: Fonts) -> None:
        view, _calls, _frame = make_view(root, fonts)
        view.show("en", "3142", 0)
        texts = {
            str(view.canvas.itemcget(i, "text"))
            for i in view.canvas.find_all()
            if view.canvas.type(i) == "text"
        }
        assert {"1", "2", "3", "4", "Cancel", "Turn on erase mode?"} <= texts


class TestTaps:
    def test_digit_buttons_call_on_digit(self, root: tk.Tk, fonts: Fonts) -> None:
        """Tapping the four buttons left to right calls on_digit with 0, 1, 2, 3."""
        view, calls, _frame = make_view(root, fonts)
        view.show("en", "3142", 0)
        root.update()
        y = sum(DIALOG_KEY_Y) // 2
        for x0, x1 in DIALOG_KEYS:
            view.canvas.event_generate("<Button-1>", x=(x0 + x1) // 2, y=y)
        assert calls == [0, 1, 2, 3]

    def test_cancel_and_inert_areas(self, root: tk.Tk, fonts: Fonts) -> None:
        """Cancel calls on_cancel; taps on text, gaps and the background do nothing."""
        view, calls, _frame = make_view(root, fonts)
        view.show("ja", "3142", 1)
        root.update()
        x0, y0, x1, y1 = DIALOG_CANCEL
        view.canvas.event_generate("<Button-1>", x=(x0 + x1) // 2, y=(y0 + y1) // 2)
        assert calls == ["cancel"]
        for x, y in ((240, 22), (240, 60), (240, 140), (240, 172), (120, 250), (236, 250)):
            view.canvas.event_generate("<Button-1>", x=x, y=y)
        view.canvas.event_generate("<Return>")
        assert calls == ["cancel"]
