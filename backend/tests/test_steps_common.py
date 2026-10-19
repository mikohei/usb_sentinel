"""Shared parts of the capacity and speed steps (Budget, permission check, lsblk query)."""

from __future__ import annotations

import json

import pytest

from usb_sentinel.libs import FakeRunner
from usb_sentinel.libs.steps.common import (
    PERMISSION_ERRNOS,
    Budget,
    DeviceQueryError,
    bytes_to_mb_s,
    is_permission_error,
    query_block_info,
)


class FakeClock:
    """Injectable monotonic clock. Advance it with advance()."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestBudget:
    def test_remaining_decreases_with_time(self) -> None:
        clock = FakeClock()
        budget = Budget(30.0, clock)
        assert budget.remaining() == 30.0
        clock.advance(10)
        assert budget.remaining() == 20.0

    def test_remaining_clamps_to_zero(self) -> None:
        # After overrun it does not go negative; it stays at 0.0.
        clock = FakeClock()
        budget = Budget(30.0, clock)
        clock.advance(31)
        assert budget.remaining() == 0.0
        clock.advance(100)
        assert budget.remaining() == 0.0


def test_bytes_to_mb_s_uses_decimal_megabytes() -> None:
    assert bytes_to_mb_s(85_300_000) == 85.3


@pytest.mark.parametrize(
    "text",
    [
        "mount: /run/x: Permission denied.",
        "f3write: Read-only file system",
        "fio: Operation not permitted",
        "mount: /run/x: cannot mount /dev/sdb1 read-only.",
        "mount: WARNING: device is READ-ONLY",
    ],
)
def test_permission_phrases_are_detected(text: str) -> None:
    assert is_permission_error(text) is True


def test_unrelated_text_is_not_permission_error() -> None:
    assert is_permission_error("mount: wrong fs type, bad option") is False
    assert is_permission_error("") is False


def test_permission_errnos() -> None:
    assert frozenset({1, 13, 30}) == PERMISSION_ERRNOS


class TestQueryBlockInfo:
    def test_returns_first_block_device(self) -> None:
        runner = FakeRunner()
        info = {"name": "sda", "path": "/dev/sda", "children": []}
        runner.register(["lsblk"], stdout=json.dumps({"blockdevices": [info]}))
        assert query_block_info(runner, "/dev/sda") == info

    def test_nonzero_exit_is_device_query_error(self) -> None:
        runner = FakeRunner()
        runner.register(["lsblk"], returncode=32, stderr="not a block device")
        with pytest.raises(DeviceQueryError, match="^Could not get device information: "):
            query_block_info(runner, "/dev/sda")

    def test_invalid_json_is_device_query_error(self) -> None:
        runner = FakeRunner()
        runner.register(["lsblk"], stdout="not json")
        with pytest.raises(DeviceQueryError, match="^Could not get device information: "):
            query_block_info(runner, "/dev/sda")

    def test_missing_blockdevices_key_is_device_query_error(self) -> None:
        runner = FakeRunner()
        runner.register(["lsblk"], stdout=json.dumps({"devices": []}))
        with pytest.raises(DeviceQueryError, match="^Could not get device information: "):
            query_block_info(runner, "/dev/sda")

    def test_empty_blockdevices_is_device_query_error(self) -> None:
        # UnsafeDeviceError derives from RuntimeError, so it is wrapped the same way.
        runner = FakeRunner()
        runner.register(["lsblk"], stdout=json.dumps({"blockdevices": []}))
        with pytest.raises(DeviceQueryError, match="^Could not get device information: "):
            query_block_info(runner, "/dev/sda")
