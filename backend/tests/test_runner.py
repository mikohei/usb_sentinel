"""Behavior of `FakeRunner`. Ensures the test infrastructure itself works correctly."""

from __future__ import annotations

import pytest

from usb_sentinel.libs import CommandNotFoundError, FakeRunner


def test_longer_prefix_wins() -> None:
    """A more specific (longer) registered prefix takes priority."""
    fake = FakeRunner()
    fake.register(["clamscan"], returncode=1, stdout="scan output")
    fake.register(["clamscan", "--version"], stdout="ClamAV 1.0.3/27200/Mon Feb 12 09:23:12 2024")

    version = fake.run(["clamscan", "--version"])
    assert version.stdout.startswith("ClamAV")
    assert version.returncode == 0

    scan = fake.run(["clamscan", "--recursive", "/mnt/sda1"])
    assert scan.stdout == "scan output"
    assert scan.returncode == 1


def test_later_registration_overrides_same_prefix() -> None:
    """Re-registering the same prefix lets the later one win, so fixture defaults can be
    overridden."""
    fake = FakeRunner()
    fake.register(["lsblk"], stdout="first")
    fake.register(["lsblk"], stdout="second")

    assert fake.run(["lsblk", "/dev/sda"]).stdout == "second"


def test_returns_actual_argv() -> None:
    """The argv of the returned CommandResult is the one actually called."""
    fake = FakeRunner()
    fake.register(["lsblk"])

    result = fake.run(["lsblk", "--json", "/dev/sda"])
    assert result.argv == ("lsblk", "--json", "/dev/sda")


def test_records_calls_in_order() -> None:
    """Call order is recorded. Used to verify the orchestrator."""
    fake = FakeRunner()
    fake.register(["mount"])
    fake.register(["umount"])

    fake.run(["mount", "-o", "ro", "/dev/sda1", "/mnt"])
    fake.run(["umount", "/mnt"])

    assert [call[0] for call in fake.calls] == ["mount", "umount"]


def test_missing_command_raises() -> None:
    """For commands in `missing`, which() returns None and running them raises."""
    fake = FakeRunner(missing={"f3probe"})
    fake.register(["f3probe"])

    assert fake.which("f3probe") is None
    assert fake.which("clamscan") == "/usr/bin/clamscan"
    with pytest.raises(CommandNotFoundError):
        fake.run(["f3probe", "/dev/sda"])


def test_unregistered_command_raises() -> None:
    """Unregistered commands do not silently succeed; they fail the test."""
    fake = FakeRunner()
    with pytest.raises(AssertionError, match="unregistered"):
        fake.run(["fio", "--version"])


def test_check_raises_on_failure() -> None:
    """`check()` raises on abnormal exit."""
    fake = FakeRunner()
    fake.register(["lsblk"], returncode=32, stderr="no such device")

    result = fake.run(["lsblk", "/dev/sdz"])
    assert not result.ok
    with pytest.raises(RuntimeError, match="no such device"):
        result.check()


def test_timed_out_is_not_ok() -> None:
    """A timeout is treated as failure regardless of returncode."""
    fake = FakeRunner()
    fake.register(["clamscan"], returncode=0, timed_out=True)

    assert not fake.run(["clamscan", "/mnt"]).ok
