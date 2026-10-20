"""ModeController STOPPING window, start_unknown and erase_off_busy (design doc 5.4, pass 3)."""

import logging
from pathlib import Path

import pytest

from tests.test_mode_state import CODE, T0, Fakes, enter, started, to_full
from usb_sentinel_display.libs import control
from usb_sentinel_display.libs.mode_state import NOTICE_LONG_S, ModeState

TTL = control.HOST_MODE_TTL_S


@pytest.fixture
def fakes(tmp_path: Path) -> Fakes:
    return Fakes(tmp_path)


class TestStopping:
    def test_failed_reset_from_full(self, fakes: Fakes, caplog: pytest.LogCaptureFixture) -> None:
        controller = to_full(fakes)
        fakes.reset_fail = "OSError: boom"
        with caplog.at_level(logging.WARNING):
            controller.toggle()
        view = controller.view()
        assert view.state is ModeState.STOPPING
        assert view.notice == "reset_failed"
        assert view.locked_reason is None
        assert controller.stopping_notice == "reset_failed"
        assert "boom" in caplog.text
        fakes.clock.advance(6)
        assert controller.view().notice == "reset_failed"  # persistent, not transient

    def test_next_successful_heartbeat_ends_stopping(self, fakes: Fakes) -> None:
        controller = to_full(fakes)
        fakes.reset_fail = "OSError: boom"
        controller.toggle()
        fakes.reset_fail = None
        fakes.clock.advance(5)
        controller.heartbeat()
        assert controller.view().state is ModeState.SCAN_ONLY
        assert controller.view().notice is None
        assert fakes.resets[-1] == int(T0 + 5)

    def test_persistent_failure_ends_after_ttl_window(self, fakes: Fakes) -> None:
        """STOPPING lasts until last_full_stamp + TTL + 1 and not shorter."""
        controller = to_full(fakes)
        stamp = controller.last_full_stamp
        fakes.reset_fail = "OSError: boom"
        controller.toggle()
        fakes.clock.t = stamp + TTL + 1
        controller.heartbeat()
        assert controller.state is ModeState.STOPPING
        fakes.clock.t = stamp + TTL + 2
        controller.heartbeat()
        assert controller.view().state is ModeState.SCAN_ONLY
        assert controller.view().notice is None
        assert controller.stopping_notice is None

    def test_toggle_in_stopping_retries_without_dialog(self, fakes: Fakes) -> None:
        controller = to_full(fakes)
        fakes.reset_fail = "OSError: boom"
        controller.toggle()
        controller.toggle()
        assert controller.state is ModeState.STOPPING
        assert controller.dialog_open is False
        fakes.reset_fail = None
        controller.toggle()
        assert controller.state is ModeState.SCAN_ONLY
        assert controller.dialog_open is False

    def test_one_warning_per_failure_streak(
        self, fakes: Fakes, caplog: pytest.LogCaptureFixture
    ) -> None:
        controller = to_full(fakes)
        fakes.reset_fail = "OSError: boom"
        with caplog.at_level(logging.WARNING):
            controller.toggle()
            controller.heartbeat()
            controller.heartbeat()
        assert sum("boom" in r.getMessage() for r in caplog.records) == 1
        fakes.reset_fail = None
        controller.heartbeat()
        assert controller.state is ModeState.SCAN_ONLY
        controller.toggle()
        enter(controller, CODE)
        fakes.reset_fail = "OSError: again"
        with caplog.at_level(logging.WARNING):
            controller.toggle()
            controller.heartbeat()
        assert sum("again" in r.getMessage() for r in caplog.records) == 1

    def test_heartbeat_in_full_with_control_lost(self, fakes: Fakes) -> None:
        """Control dir becomes unusable while FULL: STOPPING with reset_failed, nothing written."""
        controller = to_full(fakes)
        writes_before = len(fakes.writes)
        fakes.problem = control.REASON_NO_CONTROL
        controller.heartbeat()
        assert controller.view().state is ModeState.STOPPING
        assert controller.view().notice == "reset_failed"
        assert len(fakes.writes) == writes_before


class TestStartUnknown:
    def test_start_unknown_window(self, fakes: Fakes, caplog: pytest.LogCaptureFixture) -> None:
        fakes.problem = control.REASON_NO_CONTROL
        fakes.mode_file_exists = True
        controller = fakes.make()
        with caplog.at_level(logging.WARNING):
            controller.start()
        view = controller.view()
        assert view.state is ModeState.STOPPING
        assert view.notice == "start_unknown"
        assert fakes.writes == []
        assert fakes.mode_file_present_calls == 1
        assert "Mode control unusable at start" in caplog.text
        controller.toggle()
        assert controller.dialog_open is False
        controller.heartbeat()
        assert fakes.writes == []
        fakes.clock.t = T0 + TTL + 1
        controller.heartbeat()
        assert controller.view().state is ModeState.STOPPING
        assert controller.view().notice == "start_unknown"
        fakes.clock.t = T0 + TTL + 2
        controller.heartbeat()
        assert controller.view().state is ModeState.SCAN_ONLY
        assert controller.view().locked_reason == control.REASON_NO_CONTROL
        assert controller.view().notice is None
        assert fakes.writes == []

    def test_no_mode_file_is_scan_only_at_once(self, fakes: Fakes) -> None:
        fakes.problem = control.REASON_NO_CONTROL
        fakes.mode_file_exists = False
        controller = started(fakes)
        assert controller.view().state is ModeState.SCAN_ONLY
        assert controller.view().locked_reason == control.REASON_NO_CONTROL
        assert controller.view().notice is None

    def test_disabled_does_not_check_mode_file(self, fakes: Fakes) -> None:
        fakes.control_dir = None
        fakes.mode_file_exists = True
        controller = started(fakes)
        assert controller.state is ModeState.SCAN_ONLY
        assert fakes.mode_file_present_calls == 0

    def test_control_ok_does_not_check_mode_file(self, fakes: Fakes) -> None:
        fakes.mode_file_exists = True
        started(fakes)
        assert fakes.mode_file_present_calls == 0

    def test_start_reset_failure(self, fakes: Fakes) -> None:
        fakes.reset_fail = "OSError: boom"
        controller = started(fakes)
        assert controller.view().state is ModeState.STOPPING
        assert controller.view().notice == "reset_failed"
        assert controller.last_full_stamp == int(T0)


class TestEraseOffBusy:
    def test_shown_for_ten_seconds(self, fakes: Fakes) -> None:
        controller = to_full(fakes)
        controller.toggle(drives_in_progress=True)
        assert controller.view().state is ModeState.SCAN_ONLY
        assert controller.view().notice == "erase_off_busy"
        assert fakes.resets[-1] == int(T0)
        fakes.clock.advance(NOTICE_LONG_S - 0.1)
        assert controller.view().notice == "erase_off_busy"
        fakes.clock.advance(0.1)
        assert controller.view().notice is None

    def test_no_notice_without_drives(self, fakes: Fakes) -> None:
        controller = to_full(fakes)
        controller.toggle(drives_in_progress=False)
        assert controller.view().notice is None

    def test_reset_failed_wins(self, fakes: Fakes) -> None:
        controller = to_full(fakes)
        fakes.reset_fail = "OSError: boom"
        controller.toggle(drives_in_progress=True)
        assert controller.view().state is ModeState.STOPPING
        assert controller.view().notice == "reset_failed"

    def test_flag_ignored_in_scan_only(self, fakes: Fakes) -> None:
        controller = started(fakes)
        controller.toggle(drives_in_progress=True)
        assert controller.state is ModeState.CONFIRMING
        assert controller.view().notice is None
