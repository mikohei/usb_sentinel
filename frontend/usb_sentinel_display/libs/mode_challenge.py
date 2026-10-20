"""The on-screen confirmation code for erase mode and its cool-down (design doc 3.6).

A random 4-digit code from the digits 1-4 is shown only on the screen. A device that cannot
see the screen guesses right with probability 1/256 per attempt, and any wrong digit ends the
attempt and starts a 60 s cool-down in this process. The bound against guessing across
restarts is the persistent strike count in libs/trust.py, not this cool-down.

No GUI import, no i18n.
"""

import secrets
from collections.abc import Callable
from enum import StrEnum

CODE_DIGITS = "1234"
CODE_LEN = 4
COOLDOWN_S = 60.0


def random_code() -> str:
    """Return CODE_LEN digits from CODE_DIGITS chosen with secrets.choice."""
    return "".join(secrets.choice(CODE_DIGITS) for _ in range(CODE_LEN))


class Entry(StrEnum):
    """Result of one digit press."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class Challenge:
    """One code at a time, entered digit by digit; a wrong digit fails and starts the cool-down."""

    def __init__(self, *, make_code: Callable[[], str] = random_code) -> None:
        """
        Args:
            make_code: Returns the code for the next attempt (injected by tests and screenshots).
        """
        self._make_code = make_code
        self._code = ""
        self._entered = 0
        self._cooldown_until = float("-inf")

    def begin(self) -> None:
        """Start a new attempt with a fresh code and no digits entered.

        Raises:
            ValueError: make_code returned something other than CODE_LEN digits from CODE_DIGITS.
        """
        code = self._make_code()
        if len(code) != CODE_LEN or any(ch not in CODE_DIGITS for ch in code):
            raise ValueError("make_code must return 4 digits from 1234")
        self._code = code
        self._entered = 0

    def press(self, digit: str, now: float) -> Entry:
        """Enter one digit.

        Args:
            digit: One character "1"-"4"; anything else counts as wrong.
            now: Current time (epoch seconds) used for the cool-down.

        Returns:
            PENDING when the digit is right and more follow, PASSED on the last right digit,
            FAILED otherwise (cool-down starts).
        """
        if self._entered < len(self._code) and digit == self._code[self._entered]:
            self._entered += 1
            return Entry.PASSED if self._entered == CODE_LEN else Entry.PENDING
        self._cooldown_until = now + COOLDOWN_S
        return Entry.FAILED

    def cooling_down(self, now: float) -> bool:
        """Return whether a failed attempt still blocks a new one at time now."""
        return now < self._cooldown_until

    @property
    def code(self) -> str:
        """The current code (empty before the first begin())."""
        return self._code

    @property
    def entered(self) -> int:
        """Digits entered correctly so far (0-4)."""
        return self._entered
