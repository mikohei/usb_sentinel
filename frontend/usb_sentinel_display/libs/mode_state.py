"""State machine of the inspection-mode switch (design doc 5.4).

ModeController owns the SCAN_ONLY / CONFIRMING / FULL / STOPPING states, the input-device watch,
the strike charge and refund, and every write to the control directory. All collaborators are
injectable so the machine is unit-tested with a fake clock and fake files. No GUI import, no i18n:
notices and lock reasons are i18n key suffixes. Log messages never contain the code.
"""

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from . import ceiling, control, sysfs, trust
from .mode_challenge import Challenge, Entry
from .mode_view import (
    CONFIRM_TIMEOUT_S,
    HEARTBEAT_S,
    LOCKED_COOLDOWN,
    LOCKED_NEW_INPUT,
    LOCKED_NO_INPUT_WATCH,
    LOCKED_STRIKE_FILE,
    LOCKED_TOO_MANY,
    NOTICE_CEILING_LOST,
    NOTICE_ERASE_OFF_BUSY,
    NOTICE_LONG_S,
    NOTICE_NEW_INPUT_RESET,
    NOTICE_RESET_FAILED,
    NOTICE_S,
    NOTICE_START_UNKNOWN,
    NOTICE_WRITE_FAILED,
    NOTICE_WRONG_CODE,
    ModeState,
    ModeView,
)

__all__ = [
    "CONFIRM_TIMEOUT_S",
    "HEARTBEAT_S",
    "NOTICE_LONG_S",
    "NOTICE_S",
    "ModeController",
    "ModeState",
    "ModeView",
]

logger = logging.getLogger(__name__)
_CEILING_REASONS = (ceiling.REASON_CEILING_OFF, ceiling.REASON_UNREADABLE)


class ModeController:
    """Switch between scan-only and full inspection; see the transition table in design 5.4."""

    def __init__(
        self,
        control_dir: Path | None,
        host_config_dir: Path,
        sys_class_input: Path,
        strike_path: Path,
        *,
        now: Callable[[], float] = time.time,
        read_ceiling: Callable[[Path], str | None] = ceiling.read_ceiling,
        write_mode: Callable[[Path, str, float], str | None] = control.write_mode,
        reset_mode: Callable[[Path, float], str | None] = control.reset_mode,
        control_problem: Callable[[Path | None], str | None] = control.control_problem,
        mode_file_present: Callable[[Path], bool] = control.mode_file_present,
        list_inputs: Callable[[Path], frozenset[str] | None] = sysfs.list_input_devices,
        load_baseline: Callable[
            [Path, frozenset[str]], frozenset[str] | None
        ] = trust.load_or_create_baseline,
        read_strikes: Callable[[Path], int | None] = trust.read_strikes,
        write_strikes: Callable[[Path, int], str | None] = trust.write_strikes,
        challenge: Challenge | None = None,
    ) -> None:
        self._control_dir = control_dir
        self._host_config_dir = host_config_dir
        self._sys_class_input = sys_class_input
        self._strike_path = strike_path
        self._now = now
        self._read_ceiling = read_ceiling
        self._write_mode = write_mode
        self._reset_mode = reset_mode
        self._control_problem = control_problem
        self._mode_file_present = mode_file_present
        self._list_inputs = list_inputs
        self._load_baseline = load_baseline
        self._read_strikes = read_strikes
        self._write_strikes = write_strikes
        self._challenge = challenge if challenge is not None else Challenge()
        self.state = ModeState.SCAN_ONLY
        self.stopping_notice: str | None = None
        self.last_full_stamp = 0
        self.charged = False
        self.charged_from = 0
        self._strikes: int | None = None
        self._baseline: frozenset[str] | None = None
        self._new_input = False
        self._input_watch_failed = False
        self._locked_reason: str | None = None
        self._notice: str | None = None
        self._notice_until = float("-inf")
        self._confirm_deadline = float("-inf")
        self._failure_warned = False

    # -- helpers -----------------------------------------------------------------------------

    def _control_ok(self) -> bool:
        return self._control_problem(self._control_dir) is None

    def _note_inputs(self, current: frozenset[str] | None) -> None:
        """Record a failed read or a device name outside the baseline (both for the process)."""
        if current is None:
            self._input_watch_failed = True
        elif self._baseline is not None and not self._new_input and current - self._baseline:
            self._new_input = True
            logger.info("New input device detected; erase mode locked")

    def _check_inputs(self) -> None:
        self._note_inputs(self._list_inputs(self._sys_class_input))

    def _lock(self, *, attempt: bool) -> str | None:
        """First lock reason that applies; with attempt the strike and cool-down rules too."""
        problem = self._control_problem(self._control_dir)
        if problem is not None:
            return problem
        if self._new_input:
            return LOCKED_NEW_INPUT
        if self._baseline is None or self._input_watch_failed:
            return LOCKED_NO_INPUT_WATCH
        if attempt:
            self._strikes = self._read_strikes(self._strike_path)
            if self._strikes is None:
                return LOCKED_STRIKE_FILE
            if self._strikes >= trust.MAX_STRIKES:
                return LOCKED_TOO_MANY
            if self._challenge.cooling_down(self._now()):
                return LOCKED_COOLDOWN
        return self._read_ceiling(self._host_config_dir)

    def _refund(self) -> None:
        if self.charged:
            reason = self._write_strikes(self._strike_path, self.charged_from)
            if reason is not None:
                logger.warning("Could not refund erase-mode attempt: %s", reason)
        self.charged = False

    def _set_notice(self, key: str, seconds: float = NOTICE_S) -> None:
        self._notice = key
        self._notice_until = self._now() + seconds

    def _to_scan_only(self) -> None:
        self.state = ModeState.SCAN_ONLY
        self._locked_reason = self._lock(attempt=True)

    def _warn_once(self, message: str, reason: str) -> None:
        """One WARNING per failure streak; cleared by _clear_failure()."""
        if not self._failure_warned:
            logger.warning("%s: %s", message, reason)
            self._failure_warned = True

    def _clear_failure(self) -> None:
        self._failure_warned = False

    def _reset(self, now: float) -> bool:
        """Put the host back to scan_only. False leaves STOPPING (or SCAN_ONLY once stale)."""
        if self._control_ok() and self._control_dir is not None:
            reason = self._reset_mode(self._control_dir, now)
            if reason is None:
                self.stopping_notice = None
                self._clear_failure()
                self._to_scan_only()
                return True
            self._warn_once("Could not reset inspection mode", reason)
        if now <= self.last_full_stamp + control.HOST_MODE_TTL_S + 1:
            self.state = ModeState.STOPPING
            if self.stopping_notice != NOTICE_START_UNKNOWN:
                self.stopping_notice = NOTICE_RESET_FAILED
        else:
            self.stopping_notice = None
            self._to_scan_only()
        return False

    def _close_dialog(self, notice: str | None) -> None:
        """Leave CONFIRMING without a wrong answer: refund the strike and show the notice."""
        self._refund()
        if notice is not None:
            self._set_notice(notice)
        self._to_scan_only()

    def _write_full(self, now: float) -> bool:
        if self._control_dir is None:
            return False
        reason = self._write_mode(self._control_dir, control.FULL, now)
        if reason is not None:
            self._warn_once("Could not write inspection mode full", reason)
            return False
        self.last_full_stamp = int(now)
        self._clear_failure()
        return True

    # -- public API --------------------------------------------------------------------------

    def start(self) -> None:
        """Take the input baseline and reset the host to scan_only; never restores full (R3)."""
        now = self._now()
        current = self._list_inputs(self._sys_class_input)
        existed = False
        if self._control_ok() and self._control_dir is not None:
            if current is not None:
                existed = os.path.lexists(self._control_dir / trust.BASELINE_FILE_NAME)
                self._baseline = self._load_baseline(self._control_dir, current)
            if self._baseline is None:
                logger.warning("Input baseline unusable; erase mode locked")
            else:
                origin = "reused" if existed else "created"
                logger.info("Input baseline: %d devices (%s)", len(self._baseline), origin)
        self._note_inputs(current)
        if self._control_dir is None:
            self._to_scan_only()
        elif self._control_ok():
            self.last_full_stamp = int(now)
            self._reset(now)
        elif self._mode_file_present(self._control_dir):
            self.last_full_stamp = int(now)
            self.state = ModeState.STOPPING
            self.stopping_notice = NOTICE_START_UNKNOWN
            logger.warning(
                "Mode control unusable at start; a mode file exists; "
                "showing erase as possibly on for %d s",
                control.HOST_MODE_TTL_S + 1,
            )
        else:
            self._to_scan_only()

    def toggle(self, *, drives_in_progress: bool = False) -> None:
        """Mode button or M key. drives_in_progress is only used when leaving FULL."""
        now = self._now()
        if self.state is ModeState.CONFIRMING:
            self._close_dialog(None)
        elif self.state is ModeState.FULL:
            logger.info("Inspection mode set to scan_only")
            if self._reset(now) and drives_in_progress:
                self._set_notice(NOTICE_ERASE_OFF_BUSY, NOTICE_LONG_S)
        elif self.state is ModeState.STOPPING:
            self._reset(now)
        else:
            self._check_inputs()
            self._locked_reason = self._lock(attempt=True)
            if self._locked_reason is not None:
                self._set_notice(self._locked_reason)
                return
            if self._strikes is None:
                self._set_notice(LOCKED_STRIKE_FILE)
                return
            self.charged_from = self._strikes
            reason = self._write_strikes(self._strike_path, self.charged_from + 1)
            if reason is not None:
                logger.warning("Could not record erase-mode attempt: %s", reason)
                self._set_notice(LOCKED_STRIKE_FILE)
                return
            self.charged = True
            self._challenge.begin()
            self._confirm_deadline = now + CONFIRM_TIMEOUT_S
            self.state = ModeState.CONFIRMING

    def digit(self, value: str) -> None:
        """One code digit ("1"-"4"; anything else is wrong). Ignored outside the dialog."""
        if self.state is not ModeState.CONFIRMING:
            return
        now = self._now()
        self._check_inputs()
        if now >= self._confirm_deadline:
            self._close_dialog(None)
            return
        reason = self._lock(attempt=False)
        if reason is not None:
            self._close_dialog(reason)
            return
        result = self._challenge.press(value, now)
        if result is Entry.PENDING:
            return
        if result is Entry.FAILED:
            self.charged = False  # the strike stays
            strikes = self.charged_from + 1
            logger.info("Wrong erase-mode code (strike %d of %d)", strikes, trust.MAX_STRIKES)
            self._set_notice(LOCKED_TOO_MANY if strikes >= trust.MAX_STRIKES else NOTICE_WRONG_CODE)
            self._to_scan_only()
            return
        self._refund()
        reason = self._lock(attempt=False)
        if reason is not None:
            self._set_notice(reason)
            self._to_scan_only()
        elif self._write_full(now):
            self.state = ModeState.FULL
            logger.info("Inspection mode set to full")
        else:
            self._set_notice(NOTICE_WRITE_FAILED)
            self._to_scan_only()

    def cancel(self) -> None:
        """Cancel button or Escape: close the dialog without a wrong answer."""
        if self.state is ModeState.CONFIRMING:
            self._close_dialog(None)

    def heartbeat(self) -> None:
        """Every HEARTBEAT_S: refresh the full stamp, re-check locks, time out the dialog."""
        now = self._now()
        if self.state is ModeState.FULL:
            self._check_inputs()
            reason = self._lock(attempt=False)
            if reason is not None:
                self._reset(now)
                if reason == LOCKED_NEW_INPUT:
                    reason = NOTICE_NEW_INPUT_RESET
                elif reason in _CEILING_REASONS:
                    reason = NOTICE_CEILING_LOST
                self._set_notice(reason)
            elif not self._write_full(now):
                self._reset(now)
                self._set_notice(NOTICE_WRITE_FAILED)
        elif self.state is ModeState.CONFIRMING:
            self._check_inputs()
            if now >= self._confirm_deadline:
                self._close_dialog(None)
            else:
                reason = self._lock(attempt=False)
                if reason is not None:
                    self._close_dialog(reason)
        elif self.state is ModeState.STOPPING:
            self._reset(now)
        else:
            self._check_inputs()
            self._locked_reason = self._lock(attempt=True)

    def shutdown(self) -> None:
        """Reset the host to scan_only; a charged strike is kept; never raises."""
        try:
            if self._control_ok() and self._control_dir is not None:
                reason = self._reset_mode(self._control_dir, self._now())
                if reason is not None:
                    logger.warning("Could not reset inspection mode at shutdown: %s", reason)
        except Exception:
            logger.exception("Unexpected error while resetting the inspection mode")
        self.state = ModeState.SCAN_ONLY

    def view(self) -> ModeView:
        """Snapshot for the GUI; the code is exposed only while CONFIRMING."""
        confirming = self.state is ModeState.CONFIRMING
        if self.state is ModeState.STOPPING:
            notice = self.stopping_notice
        else:
            notice = self._notice if self._now() < self._notice_until else None
        return ModeView(
            state=self.state,
            locked_reason=self._locked_reason if self.state is ModeState.SCAN_ONLY else None,
            notice=notice,
            code=self._challenge.code if confirming else None,
            entered=self._challenge.entered if confirming else 0,
        )

    @property
    def dialog_open(self) -> bool:
        return self.state is ModeState.CONFIRMING
