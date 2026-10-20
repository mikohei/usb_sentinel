"""App lifecycle tests of the inspection-mode toggle (@pytest.mark.gui).

run() resetting the mode, the kiosk close request, switching back with drives in progress and
the heartbeat timer. Helpers come from test_gui_mode and test_gui_mode_app.
"""

from pathlib import Path

import pytest

from tests.test_gui_mode import canvas_texts, make_mode_app, mode_file, scene_snapshot, state_home
from tests.test_gui_mode_app import FULL_RE, SCAN_ONLY_RE, destroy, enter_code_by_keys, press
from tests.test_gui_smoke import FakeMonitor, scene_a_slots, summary
from usb_sentinel_display.libs.mode_view import ModeState
from usb_sentinel_display.libs.models import Slot, SlotState
from usb_sentinel_display.libs.monitor import Snapshot
from usb_sentinel_display.libs.ui import i18n

__all__ = ["state_home"]

pytestmark = pytest.mark.gui


def done_only_snapshot() -> Snapshot:
    """Slots with only DONE and EMPTY states (no drive in progress)."""
    slots = scene_a_slots()
    slots[2] = Slot(2, "P3", SlotState.EMPTY, None, None, None, False)
    slots[3] = Slot(3, "P4", SlotState.DONE, None, summary("Defective"), None, False)
    return Snapshot(slots, 0, None, None, 4)


class TestLifecycle:
    def test_run_ending_writes_scan_only(self, tmp_path: Path) -> None:
        """run() returns when the root is destroyed and resets the mode in its finally."""
        app = make_mode_app(tmp_path)
        try:
            press(app, "Key-m")
            enter_code_by_keys(app)
            content = mode_file(tmp_path)
            assert content is not None and FULL_RE.fullmatch(content)
            app.root.after(50, app.root.destroy)
            app.run()
            content = mode_file(tmp_path)
            assert content is not None and SCAN_ONLY_RE.fullmatch(content)
        finally:
            destroy(app)

    def test_kiosk_close_request_is_ignored(self, tmp_path: Path) -> None:
        """In kiosk mode WM_DELETE_WINDOW is a no-op; without kiosk it stays tkinter's destroy."""
        app = make_mode_app(tmp_path, kiosk=True)
        try:
            command = str(app.root.tk.call("wm", "protocol", app.root, "WM_DELETE_WINDOW"))
            assert command and not command.endswith("destroy")
            app.root.tk.call(command)
            app.root.update()
            assert app.root.winfo_exists() == 1
        finally:
            destroy(app)
        plain = make_mode_app(tmp_path)
        try:
            # tkinter's default handler for a plain window is root.destroy.
            command = str(plain.root.tk.call("wm", "protocol", plain.root, "WM_DELETE_WINDOW"))
            assert command.endswith("destroy")
        finally:
            destroy(plain)

    @pytest.mark.parametrize("busy", [True, False])
    def test_switch_back_with_drive_in_progress(self, tmp_path: Path, busy: bool) -> None:
        """Leaving erase mode with an INSPECTING slot shows erase_off_busy; DONE/EMPTY do not."""
        monitor = FakeMonitor(scene_snapshot() if busy else done_only_snapshot())
        app = make_mode_app(tmp_path, monitor)
        try:
            app.tick()
            press(app, "Key-m")
            enter_code_by_keys(app)
            assert app.mode_ui.view().state is ModeState.FULL
            press(app, "Key-m")
            content = mode_file(tmp_path)
            assert content is not None and SCAN_ONLY_RE.fullmatch(content)
            notice = app.mode_ui.view().notice
            texts = canvas_texts(app.right_canvas)
            busy_text = i18n.text("en", "notice.mode.erase_off_busy")
            if busy:
                assert notice == "erase_off_busy"
                assert busy_text in texts
            else:
                assert notice is None
                assert busy_text not in texts
        finally:
            destroy(app)

    def test_heartbeat_keeps_full_fresh(self, tmp_path: Path) -> None:
        """A heartbeat in FULL rewrites the stamp; the timer survives a controller error."""
        app = make_mode_app(tmp_path)
        try:
            press(app, "Key-m")
            enter_code_by_keys(app)
            (tmp_path / "control" / "mode").write_text("full 1\n", encoding="utf-8")
            app.mode_ui._heartbeat()
            content = mode_file(tmp_path)
            assert content is not None and FULL_RE.fullmatch(content) and content != "full 1\n"
            assert app.mode_ui._job is not None
        finally:
            destroy(app)

    def test_heartbeat_error_is_logged_and_rescheduled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        app = make_mode_app(tmp_path)
        try:

            def boom() -> None:
                raise RuntimeError("heartbeat failed")

            monkeypatch.setattr(app.mode_ui.controller, "heartbeat", boom)
            app.mode_ui._heartbeat()
            assert "heartbeat failed" in caplog.text
            assert app.mode_ui._job is not None
        finally:
            destroy(app)


class TestKioskModeKey:
    def test_m_key_toggles_mode_in_kiosk(self, tmp_path: Path) -> None:
        """--kiosk でも M でダイアログが開き、コード入力で消去モードになり、もう一度 M で戻る。"""
        app = make_mode_app(tmp_path, kiosk=True)
        try:
            press(app, "Key-m")
            assert app.mode_ui.dialog_open is True
            enter_code_by_keys(app)
            content = mode_file(tmp_path)
            assert content is not None and FULL_RE.fullmatch(content)
            assert app.mode_ui.view().state is ModeState.FULL
            press(app, "Key-M")
            content = mode_file(tmp_path)
            assert content is not None and SCAN_ONLY_RE.fullmatch(content)
            assert app.mode_ui.view().state is ModeState.SCAN_ONLY
        finally:
            destroy(app)
