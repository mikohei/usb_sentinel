"""Types and constants shared by mode_state.py and the GUI layer (design doc 5.4).

Kept apart from the controller so mode_state.py stays near 300 lines. No GUI import, no i18n: the
notice and lock-reason strings are i18n key suffixes (`notice.mode.<key>` in ui/i18n.py).
"""

from dataclasses import dataclass
from enum import StrEnum

HEARTBEAT_S = 5.0  # keep HEARTBEAT_S * 3 < control.HOST_MODE_TTL_S
CONFIRM_TIMEOUT_S = 30.0
NOTICE_S = 5.0
NOTICE_LONG_S = 10.0  # erase_off_busy: the operator must act on it

# Lock reasons produced by the controller itself (the others come from control.py / ceiling.py).
LOCKED_NEW_INPUT = "locked_new_input"
LOCKED_NO_INPUT_WATCH = "locked_no_input_watch"
LOCKED_STRIKE_FILE = "locked_strike_file"
LOCKED_TOO_MANY = "locked_too_many"
LOCKED_COOLDOWN = "locked_cooldown"
# Notices that are not lock reasons.
NOTICE_WRONG_CODE = "wrong_code"
NOTICE_WRITE_FAILED = "write_failed"
NOTICE_RESET_FAILED = "reset_failed"
NOTICE_START_UNKNOWN = "start_unknown"
NOTICE_ERASE_OFF_BUSY = "erase_off_busy"
NOTICE_CEILING_LOST = "ceiling_lost"
NOTICE_NEW_INPUT_RESET = "new_input_reset"


class ModeState(StrEnum):
    """State of the display's inspection-mode switch."""

    SCAN_ONLY = "scan_only"
    CONFIRMING = "confirming"
    FULL = "full"
    STOPPING = "stopping"  # reset failed; a fresh full file may still be honoured by the host


@dataclass(frozen=True)
class ModeView:
    """What the GUI draws: state, lock, strip notice and the dialog code."""

    state: ModeState
    locked_reason: str | None  # None means the button is enabled
    notice: str | None  # i18n key suffix for the strip, or None
    code: str | None  # shown in the dialog while CONFIRMING
    entered: int  # digits entered so far (0-4)
