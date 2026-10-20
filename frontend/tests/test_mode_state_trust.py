"""ModeController x trust.py: BadUSB sequences, strike charge/refund, restarts (design 3.7)."""

import logging
from pathlib import Path

import pytest

from tests.test_mode_state import CODE, Fakes, enter, started, to_full
from usb_sentinel_display.libs import trust
from usb_sentinel_display.libs.mode_state import ModeController, ModeState


@pytest.fixture
def fakes(tmp_path: Path) -> Fakes:
    return Fakes(tmp_path)


def strikes(fakes: Fakes) -> bytes | None:
    return fakes.strike_path.read_bytes() if fakes.strike_path.exists() else None


def add_input(fakes: Fakes, name: str) -> None:
    assert fakes.inputs is not None
    fakes.inputs = fakes.inputs | {name}


class TestBadUsb:
    def test_fixed_sequence_never_writes_full(self, fakes: Fakes) -> None:
        """toggle() then 1 1 1 1 against code 3142: no full, cool-down on, strike file `1\\n`."""
        controller = started(fakes)
        controller.toggle()
        enter(controller, "1111")
        assert controller.view().state is ModeState.SCAN_ONLY
        assert controller.view().notice == "wrong_code"
        assert fakes.full_writes() == []
        assert strikes(fakes) == b"1\n"
        controller.toggle()
        assert controller.dialog_open is False
        assert controller.view().notice == "locked_cooldown"
        assert controller.view().locked_reason == "locked_cooldown"
        fakes.clock.advance(60)
        controller.toggle()
        assert controller.dialog_open is True

    def test_new_input_before_toggle_locks(self, fakes: Fakes) -> None:
        controller = started(fakes)
        add_input(fakes, "input5")
        controller.toggle()
        assert controller.dialog_open is False
        assert controller.view().notice == "locked_new_input"
        assert controller.view().locked_reason == "locked_new_input"
        assert strikes(fakes) is None

    def test_new_input_between_digits_closes_dialog(self, fakes: Fakes) -> None:
        controller = started(fakes)
        controller.toggle()
        controller.digit("3")
        add_input(fakes, "input5")
        enter(controller, "142")
        assert controller.state is ModeState.SCAN_ONLY
        assert controller.view().notice == "locked_new_input"
        assert fakes.full_writes() == []
        assert strikes(fakes) == b"0\n"

    def test_new_input_in_full_resets_at_heartbeat(
        self, fakes: Fakes, caplog: pytest.LogCaptureFixture
    ) -> None:
        controller = to_full(fakes)
        add_input(fakes, "input5")
        with caplog.at_level(logging.INFO):
            controller.heartbeat()
        assert controller.view().state is ModeState.SCAN_ONLY
        assert controller.view().notice == "new_input_reset"
        assert len(fakes.resets) == 2
        assert "New input device detected; erase mode locked" in caplog.text

    def test_baseline_none_locks(self, fakes: Fakes, caplog: pytest.LogCaptureFixture) -> None:
        def no_baseline(directory: Path, current: frozenset[str]) -> frozenset[str] | None:
            return None

        controller = fakes.make(load_baseline=no_baseline)
        with caplog.at_level(logging.WARNING):
            controller.start()
        assert controller.view().locked_reason == "locked_no_input_watch"
        assert "Input baseline unusable; erase mode locked" in caplog.text
        controller.toggle()
        assert controller.dialog_open is False

    def test_inputs_none_at_start_locks(self, fakes: Fakes) -> None:
        fakes.inputs = None
        controller = started(fakes)
        assert controller.view().locked_reason == "locked_no_input_watch"
        assert fakes.control_dir is not None
        assert not (fakes.control_dir / trust.BASELINE_FILE_NAME).exists()

    def test_inputs_none_later_locks(self, fakes: Fakes) -> None:
        controller = started(fakes)
        fakes.inputs = None
        controller.heartbeat()
        assert controller.view().locked_reason == "locked_no_input_watch"
        fakes.inputs = frozenset({"input0", "input1"})
        controller.heartbeat()
        assert controller.view().locked_reason == "locked_no_input_watch"  # for the whole process

    def test_removed_device_does_not_lock(self, fakes: Fakes) -> None:
        controller = started(fakes)
        fakes.inputs = frozenset({"input0"})
        controller.heartbeat()
        assert controller.view().locked_reason is None
        controller.toggle()
        assert controller.dialog_open is True


class TestChargeAndRefund:
    def test_charged_before_dialog(self, fakes: Fakes) -> None:
        controller = started(fakes)
        controller.toggle()
        assert controller.state is ModeState.CONFIRMING
        assert strikes(fakes) == b"1\n"
        assert controller.charged is True and controller.charged_from == 0

    def test_correct_code_refunds(self, fakes: Fakes) -> None:
        to_full(fakes)
        assert strikes(fakes) == b"0\n"

    def test_cancel_refunds(self, fakes: Fakes) -> None:
        controller = started(fakes)
        controller.toggle()
        controller.cancel()
        assert strikes(fakes) == b"0\n"
        assert controller.charged is False

    def test_toggle_in_dialog_refunds(self, fakes: Fakes) -> None:
        controller = started(fakes)
        controller.toggle()
        controller.toggle()
        assert strikes(fakes) == b"0\n"

    def test_timeout_refunds(self, fakes: Fakes) -> None:
        controller = started(fakes)
        controller.toggle()
        fakes.clock.advance(30)
        controller.heartbeat()
        assert strikes(fakes) == b"0\n"

    def test_lock_closing_dialog_refunds(self, fakes: Fakes) -> None:
        controller = started(fakes)
        controller.toggle()
        fakes.ceiling = "locked_ceiling"
        controller.heartbeat()
        assert controller.state is ModeState.SCAN_ONLY
        assert controller.view().notice == "locked_ceiling"
        assert strikes(fakes) == b"0\n"

    def test_wrong_digit_keeps_strike(self, fakes: Fakes, caplog: pytest.LogCaptureFixture) -> None:
        controller = started(fakes)
        controller.toggle()
        with caplog.at_level(logging.INFO):
            controller.digit("4")
        assert strikes(fakes) == b"1\n"
        assert controller.charged is False
        assert "Wrong erase-mode code (strike 1 of 3)" in caplog.text

    def test_failed_charge_locks(self, fakes: Fakes, caplog: pytest.LogCaptureFixture) -> None:
        def failing(path: Path, count: int) -> str | None:
            return "OSError: disk full"

        controller = fakes.make(write_strikes=failing)
        controller.start()
        with caplog.at_level(logging.WARNING):
            controller.toggle()
        assert controller.dialog_open is False
        assert controller.view().notice == "locked_strike_file"
        assert "Could not record erase-mode attempt: OSError: disk full" in caplog.text

    def test_failed_refund_keeps_strike_and_still_writes_full(
        self, fakes: Fakes, caplog: pytest.LogCaptureFixture
    ) -> None:
        calls: list[int] = []

        def flaky(path: Path, count: int) -> str | None:
            calls.append(count)
            if len(calls) == 2:
                return "OSError: refund failed"
            return trust.write_strikes(path, count)

        controller = fakes.make(write_strikes=flaky)
        controller.start()
        controller.toggle()
        with caplog.at_level(logging.WARNING):
            enter(controller, CODE)
        assert controller.state is ModeState.FULL
        assert fakes.full_writes() != []
        assert strikes(fakes) == b"1\n"
        assert "Could not refund erase-mode attempt: OSError: refund failed" in caplog.text

    def test_unreadable_strike_file_locks(self, fakes: Fakes) -> None:
        fakes.strike_path.parent.mkdir(parents=True)
        fakes.strike_path.symlink_to(fakes.strike_path.parent / "elsewhere")
        controller = started(fakes)
        assert controller.view().locked_reason == "locked_strike_file"
        controller.toggle()
        assert controller.dialog_open is False
        assert controller.view().notice == "locked_strike_file"

    def test_third_strike_locks_too_many(self, fakes: Fakes) -> None:
        trust.write_strikes(fakes.strike_path, 2)
        controller = started(fakes)
        assert controller.view().locked_reason is None
        controller.toggle()
        assert strikes(fakes) == b"3\n"
        controller.digit("1")
        assert controller.view().notice == "locked_too_many"
        assert strikes(fakes) == b"3\n"
        assert controller.view().locked_reason == "locked_too_many"
        fakes.clock.advance(100)
        controller.toggle()
        assert controller.dialog_open is False
        assert controller.view().notice == "locked_too_many"

    def test_correct_code_with_two_strikes_works(self, fakes: Fakes) -> None:
        trust.write_strikes(fakes.strike_path, 2)
        to_full(fakes)
        assert strikes(fakes) == b"2\n"

    def test_d1_second_read_none_locks_without_write(self, fakes: Fakes) -> None:
        """read_strikes 0 then None: locked_strike_file, no dialog, write_strikes never called."""
        reads: list[int] = []
        writes: list[int] = []

        def reading(path: Path) -> int | None:
            reads.append(1)
            return 0 if len(reads) == 1 else None

        def writing(path: Path, count: int) -> str | None:
            writes.append(count)
            return None

        controller = fakes.make(read_strikes=reading, write_strikes=writing)
        controller.start()
        controller.toggle()
        assert controller.dialog_open is False
        assert controller.view().notice == "locked_strike_file"
        assert writes == []

    def test_d1_defensive_branch(self, fakes: Fakes, monkeypatch: pytest.MonkeyPatch) -> None:
        """Even if _lock passes with no count read, toggle() does not open or write."""
        writes: list[int] = []

        def writing(path: Path, count: int) -> str | None:
            writes.append(count)
            return None

        controller = fakes.make(write_strikes=writing)
        controller.start()
        monkeypatch.setattr(controller, "_lock", lambda *, attempt: None)
        controller._strikes = None
        controller.toggle()
        assert controller.dialog_open is False
        assert controller.view().notice == "locked_strike_file"
        assert writes == []


class TestRestartSameBoot:
    def test_new_device_seen_by_a_locks_b(self, fakes: Fakes) -> None:
        a = started(fakes)
        add_input(fakes, "input5")
        a.heartbeat()
        assert a.view().locked_reason == "locked_new_input"
        b = started(fakes)
        assert b.view().locked_reason == "locked_new_input"
        b.toggle()
        assert b.dialog_open is False
        assert fakes.full_writes() == []
        assert strikes(fakes) is None

    def test_strikes_carried_over(self, fakes: Fakes) -> None:
        a = started(fakes)
        a.toggle()
        a.digit("1")
        a.shutdown()
        assert strikes(fakes) == b"1\n"
        b = started(fakes)
        assert b.view().locked_reason is None
        b.toggle()
        b.digit("1")
        assert strikes(fakes) == b"2\n"
        fakes.clock.advance(61)
        b.toggle()
        b.digit("1")
        assert b.view().notice == "locked_too_many"
        assert strikes(fakes) == b"3\n"
        c = started(fakes)
        assert c.view().locked_reason == "locked_too_many"

    def test_charge_not_refunded_on_shutdown(self, fakes: Fakes) -> None:
        a = started(fakes)
        a.toggle()
        a.shutdown()
        assert strikes(fakes) == b"1\n"
        b = started(fakes)
        assert b.view().locked_reason is None
        b.toggle()
        assert strikes(fakes) == b"2\n"

    def test_reboot_keeps_strikes_and_admin_reset_unlocks(
        self, fakes: Fakes, tmp_path: Path
    ) -> None:
        trust.write_strikes(fakes.strike_path, 3)
        fresh = tmp_path / "control-after-reboot"
        fresh.mkdir()
        fakes.control_dir = fresh
        add_input(fakes, "input5")  # the new device is in the fresh baseline
        controller = started(fakes)
        assert controller.view().locked_reason == "locked_too_many"
        fakes.strike_path.unlink()
        controller.heartbeat()
        assert controller.view().locked_reason is None
        controller.toggle()
        assert controller.dialog_open is True

    def test_invalid_baseline_locks_b_without_rewrite(self, fakes: Fakes) -> None:
        started(fakes)
        assert fakes.control_dir is not None
        baseline = fakes.control_dir / trust.BASELINE_FILE_NAME
        baseline.write_bytes(b"garbage\n")
        b: ModeController = started(fakes)
        assert b.view().locked_reason == "locked_no_input_watch"
        assert baseline.read_bytes() == b"garbage\n"
