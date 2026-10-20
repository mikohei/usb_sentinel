"""App-level tests of the inspection-mode toggle (@pytest.mark.gui): keys, dialog, locks.

Lifecycle cases (run(), kiosk close, switch-back, heartbeat) are in test_gui_mode_lifecycle.py.

Helpers come from test_gui_mode (mode_dirs, make_mode_app, CODE, CODE_INDICES). Every App here
uses control/host/input directories under tmp_path and XDG_STATE_HOME=tmp_path/state.
"""

import contextlib
import re
import tkinter as tk
from pathlib import Path

import pytest

from tests.test_gui_mode import (
    CODE,
    CODE_INDICES,
    canvas_texts,
    make_mode_app,
    mode_file,
    scene_snapshot,
    state_home,
    strike_text,
)
from tests.test_gui_smoke import FakeMonitor
from usb_sentinel_display.libs.mode_view import ModeState
from usb_sentinel_display.libs.ui import i18n
from usb_sentinel_display.libs.ui.app import App
from usb_sentinel_display.libs.ui.theme import DIALOG_KEY_Y, DIALOG_KEYS

__all__ = ["destroy", "enter_code_by_keys", "press", "state_home"]

pytestmark = pytest.mark.gui

SCAN_ONLY_RE = re.compile(r"scan_only \d+\n")
FULL_RE = re.compile(r"full \d+\n")


def press(app: App, keysym: str) -> None:
    """Deliver a key event to the root window."""
    app.root.event_generate(f"<{keysym}>")
    app.root.update()


def enter_code_by_keys(app: App) -> None:
    for digit in CODE:
        press(app, f"Key-{digit}")


def tap_digit(app: App, index: int) -> None:
    x0, x1 = DIALOG_KEYS[index]
    app.mode_ui.confirm.canvas.event_generate(
        "<Button-1>", x=(x0 + x1) // 2, y=sum(DIALOG_KEY_Y) // 2
    )
    app.root.update()


def destroy(app: App) -> None:
    with contextlib.suppress(tk.TclError):
        app.root.destroy()


class TestStartupAndKeys:
    def test_startup_resets_full_to_scan_only(self, tmp_path: Path) -> None:
        """Every start writes scan_only, even over a file that said full (R3)."""
        (tmp_path / "control").mkdir()
        (tmp_path / "control" / "mode").write_text("full 1760850000\n", encoding="utf-8")
        app = make_mode_app(tmp_path)
        try:
            content = mode_file(tmp_path)
            assert content is not None and SCAN_ONLY_RE.fullmatch(content)
            view = app.mode_ui.view()
            assert view.state is ModeState.SCAN_ONLY
            assert view.locked_reason is None
            assert app.mode_ui.dialog_open is False
        finally:
            destroy(app)

    def test_m_opens_overlay_without_writing_and_escape_closes(
        self, tmp_path: Path, state_home: Path
    ) -> None:
        """M charges a strike and shows the overlay; Escape closes it and refunds."""
        app = make_mode_app(tmp_path)
        try:
            before = mode_file(tmp_path)
            press(app, "Key-m")
            assert app.mode_ui.dialog_open is True
            assert app.mode_ui.confirm.visible is True
            assert mode_file(tmp_path) == before
            assert strike_text(state_home) == "1\n"
            press(app, "Escape")
            assert app.mode_ui.dialog_open is False
            assert app.mode_ui.confirm.visible is False
            assert mode_file(tmp_path) == before
            assert strike_text(state_home) == "0\n"
            assert app.mode_ui.view().notice is None
        finally:
            destroy(app)

    def test_m_then_code_keys_write_full(self, tmp_path: Path) -> None:
        app = make_mode_app(tmp_path)
        try:
            press(app, "Key-M")
            enter_code_by_keys(app)
            content = mode_file(tmp_path)
            assert content is not None and FULL_RE.fullmatch(content)
            assert app.mode_ui.view().state is ModeState.FULL
            assert app.mode_ui.confirm.visible is False
            texts = canvas_texts(app.left_canvas)
            assert i18n.text("en", "mode.button_full") in texts
        finally:
            destroy(app)

    def test_return_does_nothing(self, tmp_path: Path) -> None:
        app = make_mode_app(tmp_path)
        try:
            press(app, "Key-m")
            press(app, "Return")
            assert app.mode_ui.dialog_open is True
            content = mode_file(tmp_path)
            assert content is not None and SCAN_ONLY_RE.fullmatch(content)
        finally:
            destroy(app)

    def test_wrong_code_locks_with_cooldown(self, tmp_path: Path, state_home: Path) -> None:
        """1 1 1 1 against 3142 writes nothing, keeps the strike, and the next M is refused."""
        app = make_mode_app(tmp_path)
        try:
            press(app, "Key-m")
            for _ in range(4):
                press(app, "Key-1")
            assert app.mode_ui.dialog_open is False
            content = mode_file(tmp_path)
            assert content is not None and SCAN_ONLY_RE.fullmatch(content)
            assert strike_text(state_home) == "1\n"
            assert app.mode_ui.view().notice == "wrong_code"
            press(app, "Key-m")
            assert app.mode_ui.dialog_open is False
            assert app.mode_ui.view().notice == "locked_cooldown"
            assert i18n.text("en", "notice.mode.locked_cooldown") in canvas_texts(app.right_canvas)
        finally:
            destroy(app)

    def test_other_keys_ignored_while_open(self, tmp_path: Path) -> None:
        """1-4 feed the code, not the selection; L, Q, arrows and F5 do nothing."""
        monitor = FakeMonitor(scene_snapshot())
        app = make_mode_app(tmp_path, monitor)
        try:
            app.tick()
            press(app, "Key-m")
            press(app, "Key-3")
            assert app.selected == 0
            assert app.mode_ui.view().entered == 1
            press(app, "Key-l")
            press(app, "Key-q")
            press(app, "Right")
            press(app, "F5")
            assert app.language == "en"
            assert app.qr_mode == 0
            assert app.selected == 0
            assert monitor.reloads == 0
            assert app.mode_ui.dialog_open is True
            press(app, "Key-m")  # M closes the dialog without a strike
            assert app.mode_ui.dialog_open is False
            assert app.mode_ui.view().notice is None
        finally:
            destroy(app)

    def test_digit_buttons_write_full(self, tmp_path: Path) -> None:
        app = make_mode_app(tmp_path)
        try:
            app.left_canvas.event_generate("<Button-1>", x=80, y=304)
            app.root.update()
            assert app.mode_ui.dialog_open is True
            for index in CODE_INDICES:
                tap_digit(app, index)
            content = mode_file(tmp_path)
            assert content is not None and FULL_RE.fullmatch(content)
        finally:
            destroy(app)

    def test_three_sources_of_digit_three(self, tmp_path: Path) -> None:
        """KP_3, key 3 and the third button each enter "3" (the first digit of 3142)."""
        app = make_mode_app(tmp_path)
        try:
            press(app, "Key-m")
            press(app, "Key-3")
            assert app.mode_ui.view().entered == 1
            press(app, "Escape")
            press(app, "Key-m")
            tap_digit(app, 2)
            assert app.mode_ui.view().entered == 1
            press(app, "Escape")
            press(app, "Key-m")
            try:
                press(app, "KP_3")
            except tk.TclError as exc:
                pytest.skip(f"no KP_3 keysym: {exc}")
            assert app.mode_ui.view().entered == 1
        finally:
            destroy(app)


class TestLocks:
    def test_new_input_before_m_locks(self, tmp_path: Path) -> None:
        app = make_mode_app(tmp_path)
        try:
            (tmp_path / "input" / "input9").mkdir()
            press(app, "Key-m")
            assert app.mode_ui.dialog_open is False
            assert app.mode_ui.view().notice == "locked_new_input"
            assert app.mode_ui.view().locked_reason == "locked_new_input"
            assert i18n.text("en", "mode.button_locked") in canvas_texts(app.left_canvas)
        finally:
            destroy(app)

    def test_locked_host_shows_notice(self, tmp_path: Path) -> None:
        app = make_mode_app(tmp_path, enabled=False)
        try:
            assert app.mode_ui.view().locked_reason == "locked_ceiling"
            press(app, "Key-m")
            assert app.mode_ui.dialog_open is False
            assert app.mode_ui.view().notice == "locked_ceiling"
            assert mode_file(tmp_path) is not None
        finally:
            destroy(app)

    def test_second_app_keeps_charged_strike(self, tmp_path: Path, state_home: Path) -> None:
        """Destroying the first App with the dialog open leaves the strike at 1 for the next."""
        first = make_mode_app(tmp_path)
        press(first, "Key-m")
        assert strike_text(state_home) == "1\n"
        destroy(first)
        second = make_mode_app(tmp_path)
        try:
            assert strike_text(state_home) == "1\n"
            assert second.mode_ui.view().locked_reason is None
        finally:
            destroy(second)

    def test_second_app_after_new_input_starts_locked(self, tmp_path: Path) -> None:
        first = make_mode_app(tmp_path)
        (tmp_path / "input" / "input9").mkdir()
        destroy(first)
        second = make_mode_app(tmp_path)
        try:
            assert second.mode_ui.view().locked_reason == "locked_new_input"
            press(second, "Key-m")
            assert second.mode_ui.dialog_open is False
        finally:
            destroy(second)
