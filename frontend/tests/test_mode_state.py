"""Tests for mode_state.ModeController transitions (design doc 5.4). Fakes record every call."""

import logging
from pathlib import Path
from typing import Any

import pytest

from usb_sentinel_display.libs import control
from usb_sentinel_display.libs.mode_challenge import Challenge
from usb_sentinel_display.libs.mode_state import (
    CONFIRM_TIMEOUT_S,
    HEARTBEAT_S,
    NOTICE_LONG_S,
    NOTICE_S,
    ModeController,
    ModeState,
    ModeView,
)

CODE = "3142"
T0 = 1_000_000.0


class Clock:
    """Fake `now` that only moves when the test says so."""

    def __init__(self, start: float = T0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Fakes:
    """Injected collaborators. The baseline and strike functions stay real (tmp_path files)."""

    def __init__(self, tmp_path: Path) -> None:
        self.control_dir: Path | None = tmp_path / "control"
        self.control_dir.mkdir(exist_ok=True)
        self.host_dir = tmp_path / "host"
        self.input_dir = tmp_path / "input"
        self.strike_path = tmp_path / "state" / "erase-strikes"
        self.clock = Clock()
        self.inputs: frozenset[str] | None = frozenset({"input0", "input1"})
        self.ceiling: str | None = None
        self.problem: str | None = None
        self.write_fail: str | None = None
        self.reset_fail: str | None = None
        self.mode_file_exists = False
        self.writes: list[tuple[str, int]] = []
        self.resets: list[int] = []
        self.mode_file_present_calls = 0

    def write_mode(self, directory: Path, mode: str, now: float) -> str | None:
        assert directory == self.control_dir
        if self.write_fail is not None:
            return self.write_fail
        self.writes.append((mode, int(now)))
        return None

    def reset_mode(self, directory: Path, now: float) -> str | None:
        assert directory == self.control_dir
        if self.reset_fail is not None:
            return self.reset_fail
        self.resets.append(int(now))
        self.writes.append((control.SCAN_ONLY, int(now)))
        return None

    def control_problem(self, directory: Path | None) -> str | None:
        return control.REASON_DISABLED if directory is None else self.problem

    def read_ceiling(self, directory: Path) -> str | None:
        assert directory == self.host_dir
        return self.ceiling

    def list_inputs(self, directory: Path) -> frozenset[str] | None:
        assert directory == self.input_dir
        return self.inputs

    def mode_file_present(self, directory: Path) -> bool:
        self.mode_file_present_calls += 1
        return self.mode_file_exists

    def make(self, **overrides: object) -> ModeController:
        """Build a controller on these fakes; keyword overrides replace single collaborators."""
        kwargs: dict[str, Any] = {
            "now": self.clock,
            "read_ceiling": self.read_ceiling,
            "write_mode": self.write_mode,
            "reset_mode": self.reset_mode,
            "control_problem": self.control_problem,
            "list_inputs": self.list_inputs,
            "mode_file_present": self.mode_file_present,
            "challenge": Challenge(make_code=lambda: CODE),
        }
        kwargs.update(overrides)
        return ModeController(
            self.control_dir, self.host_dir, self.input_dir, self.strike_path, **kwargs
        )

    def full_writes(self) -> list[tuple[str, int]]:
        return [w for w in self.writes if w[0] == control.FULL]


@pytest.fixture
def fakes(tmp_path: Path) -> Fakes:
    return Fakes(tmp_path)


def started(fakes: Fakes) -> ModeController:
    controller = fakes.make()
    controller.start()
    return controller


def enter(controller: ModeController, digits: str) -> None:
    for digit in digits:
        controller.digit(digit)


def to_full(fakes: Fakes) -> ModeController:
    controller = started(fakes)
    controller.toggle()
    enter(controller, CODE)
    assert controller.state is ModeState.FULL
    return controller


class TestConstants:
    def test_values(self) -> None:
        assert HEARTBEAT_S == 5.0
        assert CONFIRM_TIMEOUT_S == 30.0
        assert NOTICE_S == 5.0
        assert NOTICE_LONG_S == 10.0
        assert HEARTBEAT_S * 3 < control.HOST_MODE_TTL_S
        assert [s.value for s in ModeState] == ["scan_only", "confirming", "full", "stopping"]
        assert tuple(ModeView.__dataclass_fields__) == (
            "state",
            "locked_reason",
            "notice",
            "code",
            "entered",
        )


class TestStart:
    def test_start_resets_real_file(self, fakes: Fakes, tmp_path: Path) -> None:
        """start() writes scan_only over an existing full file (R3), with the real control.py."""
        mode_file = tmp_path / "control" / control.MODE_FILE_NAME
        mode_file.write_bytes(b"full 999\n")
        controller = fakes.make(write_mode=control.write_mode, reset_mode=control.reset_mode)
        controller.start()
        content = mode_file.read_bytes()
        assert content == f"scan_only {int(T0)}\n".encode()
        assert controller.view() == ModeView(ModeState.SCAN_ONLY, None, None, None, 0)

    def test_start_disabled(self, fakes: Fakes) -> None:
        """control_dir None: no write, no notice, locked_disabled; shutdown writes nothing."""
        fakes.control_dir = None
        controller = fakes.make()
        controller.start()
        assert controller.view() == ModeView(
            ModeState.SCAN_ONLY, control.REASON_DISABLED, None, None, 0
        )
        controller.shutdown()
        assert fakes.writes == []
        assert fakes.mode_file_present_calls == 0

    def test_start_no_control(self, fakes: Fakes) -> None:
        """Control dir unusable and no mode file: no write, no reset_failed, lock shown."""
        fakes.problem = control.REASON_NO_CONTROL
        controller = started(fakes)
        assert controller.view() == ModeView(
            ModeState.SCAN_ONLY, control.REASON_NO_CONTROL, None, None, 0
        )
        controller.toggle()
        assert controller.dialog_open is False
        controller.shutdown()
        assert fakes.writes == []

    def test_start_logs_baseline(self, fakes: Fakes, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO)
        started(fakes)
        assert "Input baseline: 2 devices (created)" in caplog.text
        started(fakes)
        assert "Input baseline: 2 devices (reused)" in caplog.text


class TestToggleAndConfirm:
    def test_locked_toggle_notice_no_write(self, fakes: Fakes) -> None:
        fakes.ceiling = "locked_ceiling"
        controller = started(fakes)
        controller.toggle()
        view = controller.view()
        assert view.state is ModeState.SCAN_ONLY
        assert view.notice == "locked_ceiling"
        assert view.locked_reason == "locked_ceiling"
        assert controller.dialog_open is False
        assert fakes.writes == [(control.SCAN_ONLY, int(T0))]
        assert not fakes.strike_path.exists()

    def test_toggle_opens_dialog_without_write(self, fakes: Fakes) -> None:
        controller = started(fakes)
        controller.toggle()
        assert controller.dialog_open is True
        assert controller.view() == ModeView(ModeState.CONFIRMING, None, None, CODE, 0)
        assert fakes.writes == [(control.SCAN_ONLY, int(T0))]

    def test_correct_code_writes_full(self, fakes: Fakes) -> None:
        controller = started(fakes)
        controller.toggle()
        enter(controller, "314")
        assert controller.view().entered == 3
        controller.digit("2")
        assert controller.view() == ModeView(ModeState.FULL, None, None, None, 0)
        assert fakes.writes[-1] == (control.FULL, int(T0))
        assert controller.last_full_stamp == int(T0)
        assert controller.dialog_open is False

    def test_confirm_rereads_ceiling(self, fakes: Fakes) -> None:
        """The ceiling is read again right before writing full."""
        controller = started(fakes)
        controller.toggle()
        enter(controller, "314")
        fakes.ceiling = "locked_ceiling"
        controller.digit("2")
        assert controller.state is ModeState.SCAN_ONLY
        assert controller.view().notice == "locked_ceiling"
        assert fakes.full_writes() == []

    def test_cancel_is_not_wrong(self, fakes: Fakes) -> None:
        controller = started(fakes)
        controller.toggle()
        controller.digit("3")
        controller.cancel()
        assert controller.view() == ModeView(ModeState.SCAN_ONLY, None, None, None, 0)
        assert fakes.full_writes() == []
        controller.toggle()
        assert controller.dialog_open is True  # no cool-down

    def test_toggle_in_dialog_closes_without_write(self, fakes: Fakes) -> None:
        controller = started(fakes)
        controller.toggle()
        controller.toggle()
        assert controller.view() == ModeView(ModeState.SCAN_ONLY, None, None, None, 0)
        assert len(fakes.writes) == 1

    def test_cancel_outside_dialog_is_noop(self, fakes: Fakes) -> None:
        controller = to_full(fakes)
        controller.cancel()
        assert controller.state is ModeState.FULL

    def test_digit_ignored_outside_confirming(self, fakes: Fakes) -> None:
        controller = started(fakes)
        controller.digit("3")
        assert controller.state is ModeState.SCAN_ONLY
        controller = to_full(fakes)
        controller.digit("3")
        assert controller.state is ModeState.FULL

    def test_full_toggle_resets_without_confirm(self, fakes: Fakes) -> None:
        controller = to_full(fakes)
        controller.toggle()
        assert controller.view() == ModeView(ModeState.SCAN_ONLY, None, None, None, 0)
        assert fakes.resets == [int(T0), int(T0)]
        assert controller.dialog_open is False

    def test_confirm_timeout_via_heartbeat(self, fakes: Fakes) -> None:
        controller = started(fakes)
        controller.toggle()
        fakes.clock.advance(CONFIRM_TIMEOUT_S - 0.1)
        controller.heartbeat()
        assert controller.state is ModeState.CONFIRMING
        fakes.clock.advance(0.1)
        controller.heartbeat()
        assert controller.view() == ModeView(ModeState.SCAN_ONLY, None, None, None, 0)
        assert fakes.full_writes() == []

    def test_confirm_timeout_via_digit(self, fakes: Fakes) -> None:
        """A digit after the deadline closes the dialog and is not a wrong answer."""
        controller = started(fakes)
        controller.toggle()
        fakes.clock.advance(CONFIRM_TIMEOUT_S)
        controller.digit("1")
        assert controller.view() == ModeView(ModeState.SCAN_ONLY, None, None, None, 0)
        controller.toggle()
        assert controller.dialog_open is True

    def test_write_failure_on_confirm(self, fakes: Fakes, caplog: pytest.LogCaptureFixture) -> None:
        fakes.write_fail = "OSError: boom"
        controller = started(fakes)
        controller.toggle()
        with caplog.at_level(logging.WARNING):
            enter(controller, CODE)
        assert controller.view().state is ModeState.SCAN_ONLY
        assert controller.view().notice == "write_failed"
        assert "boom" in caplog.text


class TestHeartbeatAndView:
    def test_heartbeat_refreshes_stamp(self, fakes: Fakes) -> None:
        controller = to_full(fakes)
        fakes.clock.advance(HEARTBEAT_S)
        controller.heartbeat()
        assert controller.state is ModeState.FULL
        assert fakes.writes[-1] == (control.FULL, int(T0 + HEARTBEAT_S))
        assert controller.last_full_stamp == int(T0 + HEARTBEAT_S)

    @pytest.mark.parametrize("reason", ["locked_ceiling", "locked_unreadable"])
    def test_heartbeat_ceiling_lost(self, fakes: Fakes, reason: str) -> None:
        controller = to_full(fakes)
        fakes.ceiling = reason
        controller.heartbeat()
        assert controller.view().state is ModeState.SCAN_ONLY
        assert controller.view().notice == "ceiling_lost"
        assert controller.view().locked_reason == reason
        assert len(fakes.resets) == 2

    def test_heartbeat_write_failure_in_full(self, fakes: Fakes) -> None:
        controller = to_full(fakes)
        fakes.write_fail = "OSError: boom"
        controller.heartbeat()
        assert controller.view().state is ModeState.SCAN_ONLY
        assert controller.view().notice == "write_failed"
        assert len(fakes.resets) == 2

    def test_heartbeat_scan_only_unlocks(self, fakes: Fakes) -> None:
        fakes.ceiling = "locked_ceiling"
        controller = started(fakes)
        assert controller.view().locked_reason == "locked_ceiling"
        fakes.ceiling = None
        controller.heartbeat()
        assert controller.view().locked_reason is None

    def test_transient_notice_expires(self, fakes: Fakes) -> None:
        fakes.ceiling = "locked_ceiling"
        controller = started(fakes)
        controller.toggle()
        fakes.clock.advance(NOTICE_S - 0.1)
        assert controller.view().notice == "locked_ceiling"
        fakes.clock.advance(0.1)
        assert controller.view().notice is None

    def test_view_never_shows_code_outside_confirming(self, fakes: Fakes) -> None:
        controller = started(fakes)
        assert controller.view().code is None
        controller.toggle()
        assert controller.view().code == CODE
        controller.cancel()
        assert controller.view().code is None
        controller.toggle()
        enter(controller, CODE)
        assert controller.view().code is None and controller.view().entered == 0

    def test_logs_never_contain_code(self, fakes: Fakes, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        controller = started(fakes)
        controller.toggle()
        enter(controller, "1")
        controller.toggle()
        fakes.clock.advance(61)
        controller.toggle()
        enter(controller, CODE)
        assert controller.state is ModeState.FULL
        assert "Inspection mode set to full" in caplog.text
        assert CODE not in caplog.text

    def test_shutdown_resets_and_never_raises(
        self, fakes: Fakes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        controller = to_full(fakes)
        controller.shutdown()
        assert fakes.resets == [int(T0), int(T0)]
        assert controller.state is ModeState.SCAN_ONLY

        def raising(directory: Path, now: float) -> str | None:
            raise OSError("boom")

        controller = to_full(fakes)
        monkeypatch.setattr(controller, "_reset_mode", raising)
        controller.shutdown()  # must not raise
        assert controller.state is ModeState.SCAN_ONLY
