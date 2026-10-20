"""Tests for mode_challenge.py: the on-screen 4-digit code and the cool-down (design doc 3.6)."""

import pytest

from usb_sentinel_display.libs.mode_challenge import (
    CODE_DIGITS,
    CODE_LEN,
    COOLDOWN_S,
    Challenge,
    Entry,
    random_code,
)

CODE = "3142"


def fixed_code() -> str:
    return CODE


def challenge() -> Challenge:
    c = Challenge(make_code=fixed_code)
    c.begin()
    return c


class TestConstants:
    def test_values(self) -> None:
        assert CODE_DIGITS == "1234"
        assert CODE_LEN == 4
        assert COOLDOWN_S == 60.0


class TestRandomCode:
    def test_four_digits_from_1234_in_every_position(self) -> None:
        """1000 samples: always 4 chars from 1234, and every digit appears in every position."""
        seen: list[set[str]] = [set() for _ in range(CODE_LEN)]
        for _ in range(1000):
            code = random_code()
            assert len(code) == CODE_LEN
            assert set(code) <= set(CODE_DIGITS)
            for index, digit in enumerate(code):
                seen[index].add(digit)
        assert all(position == set(CODE_DIGITS) for position in seen)

    def test_default_make_code_is_random_code(self) -> None:
        c = Challenge()
        c.begin()
        assert len(c.code) == CODE_LEN and set(c.code) <= set(CODE_DIGITS)


class TestPress:
    def test_correct_sequence_passes_on_fourth(self) -> None:
        c = challenge()
        assert c.code == CODE
        assert c.entered == 0
        assert c.press("3", 0.0) is Entry.PENDING
        assert c.entered == 1
        assert c.press("1", 0.0) is Entry.PENDING
        assert c.press("4", 0.0) is Entry.PENDING
        assert c.entered == 3
        assert c.press("2", 0.0) is Entry.PASSED
        assert c.entered == 4
        assert c.cooling_down(0.0) is False

    def test_wrong_first_digit_fails(self) -> None:
        c = challenge()
        assert c.press("1", 10.0) is Entry.FAILED
        assert c.cooling_down(10.0) is True

    def test_wrong_fourth_digit_fails(self) -> None:
        c = challenge()
        for digit in "314":
            assert c.press(digit, 0.0) is Entry.PENDING
        assert c.press("3", 0.0) is Entry.FAILED

    @pytest.mark.parametrize("value", ["0", "5", "", "33", "a", "\n"])
    def test_values_outside_digits_fail(self, value: str) -> None:
        c = challenge()
        assert c.press(value, 0.0) is Entry.FAILED
        assert c.cooling_down(0.0) is True

    def test_cooldown_boundary(self) -> None:
        """cooling_down is true at +59.9 s and false at +60 s."""
        c = challenge()
        c.press("1", 100.0)
        assert c.cooling_down(100.0) is True
        assert c.cooling_down(159.9) is True
        assert c.cooling_down(160.0) is False

    def test_no_cooldown_before_any_failure(self) -> None:
        c = challenge()
        assert c.cooling_down(0.0) is False
        assert c.cooling_down(1e12) is False

    def test_begin_resets_entered_and_makes_new_code(self) -> None:
        codes = iter(["3142", "1234"])
        c = Challenge(make_code=lambda: next(codes))
        c.begin()
        c.press("3", 0.0)
        c.press("1", 0.0)
        assert c.entered == 2
        c.begin()
        assert c.entered == 0
        assert c.code == "1234"
        assert c.press("1", 0.0) is Entry.PENDING

    def test_begin_keeps_cooldown(self) -> None:
        """A new attempt does not clear the cool-down of the previous failure."""
        c = challenge()
        c.press("1", 100.0)
        c.begin()
        assert c.cooling_down(150.0) is True

    @pytest.mark.parametrize("bad", ["314", "31425", "3140", "abcd", ""])
    def test_invalid_make_code_raises(self, bad: str) -> None:
        c = Challenge(make_code=lambda: bad)
        with pytest.raises(ValueError, match="code"):
            c.begin()

    def test_press_before_begin_fails(self) -> None:
        """Without begin() there is no code, so any digit is wrong."""
        c = Challenge(make_code=fixed_code)
        assert c.press("3", 0.0) is Entry.FAILED
