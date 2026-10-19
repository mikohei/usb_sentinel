"""Parts shared by the capacity and speed steps.

Time budget, permission checks, lsblk queries, command execution, and failure wording.
summary wording appears in result.json, so changing the strings of constants and functions here
affects external compatibility.
"""

from collections.abc import Callable, Iterable
from typing import Any

from ..device import block_info
from ..models import StepResult
from ..runner import CommandResult, CommandRunner
from . import StepContext

# errno values in fio jobs[0].error treated as permission errors (EPERM, EACCES, EROFS).
PERMISSION_ERRNOS: frozenset[int] = frozenset({1, 13, 30})

_PERMISSION_PHRASES = ("Permission denied", "Read-only file system", "Operation not permitted")

# summary when an external command times out or the time budget is used up.
TIMEOUT_SUMMARY = "タイムアウト"
# summary when a write is denied for lack of permission or write protection.
WRITE_DENIED_SUMMARY = "権限不足: 書き込みが拒否された"
# Number of leading stderr characters included in summary.
STDERR_HEAD_CHARS = 200
# Number of leading stdout characters kept in details["raw_stdout_head"].
RAW_STDOUT_HEAD_CHARS = 500


class Budget:
    """Time budget for a whole step. Remaining seconds are passed to each external command as
    timeout_s."""

    def __init__(self, timeout_s: float, monotonic: Callable[[], float]) -> None:
        """Record the start time.

        Args:
            timeout_s: Seconds allowed for the whole step.
            monotonic: Function returning monotonic time (replaced in tests).
        """
        self.timeout_s = timeout_s
        self.monotonic = monotonic
        self.started = monotonic()

    def remaining(self) -> float:
        """Return the remaining seconds. Stays at 0.0 once exceeded."""
        return max(0.0, self.timeout_s - (self.monotonic() - self.started))


def bytes_to_mb_s(bytes_per_s: float) -> float:
    """Convert bytes/s to MB/s (10^6 bytes/s)."""
    return bytes_per_s / 1e6


def is_permission_error(text: str) -> bool:
    """Whether stdout/stderr wording indicates lack of permission or write protection.

    Case-insensitive "read-only" is included to also catch "cannot mount /dev/sdX read-only",
    which util-linux mount -w prints for a write-protected device.
    """
    return any(phrase in text for phrase in _PERMISSION_PHRASES) or "read-only" in text.lower()


class DeviceQueryError(RuntimeError):
    """Getting device information with lsblk failed (environment problem)."""


def query_block_info(runner: CommandRunner, path: str) -> dict[str, Any]:
    """Call device.block_info() and wrap failures in DeviceQueryError.

    Args:
        runner: Command runner.
        path: Path of the target device.

    Returns:
        Dict of the first block device from lsblk.

    Raises:
        DeviceQueryError: On lsblk failure, invalid JSON, or a missing blockdevices key.
    """
    try:
        return block_info(runner, path)
    except (RuntimeError, ValueError, KeyError) as exc:
        raise DeviceQueryError(f"デバイス情報を取得できない: {exc}") from exc


def not_found_summary(command: str) -> str:
    """The "<command> not found" summary (in Japanese).

    Used both when which() finds nothing and for CommandNotFoundError.

    Args:
        command: Command name (str(exc) for CommandNotFoundError).

    Returns:
        Wording for summary.
    """
    return f"{command}が見つからない"


def missing_command(runner: CommandRunner, commands: Iterable[str]) -> str | None:
    """Return the first command name that which() cannot find. None if all exist.

    Args:
        runner: Command runner.
        commands: Command names to check (checked in order; stops at the first missing one).

    Returns:
        The missing command name. None if all exist.
    """
    for command in commands:
        if runner.which(command) is None:
            return command
    return None


def run_within_budget(
    ctx: StepContext, budget: Budget, argv: list[str], commands: list[list[str]]
) -> CommandResult | None:
    """If budget remains, record argv in commands and run it. Otherwise return None unrun.

    budget.remaining() is called twice: once to check the budget and once to set timeout_s.
    Tests with a fixed clock depend on this call count, so do not change it.

    Args:
        ctx: Step context (its runner is used).
        budget: Time budget.
        argv: argv to run (fixed list).
        commands: Where run argv values are recorded (appended right before running).

    Returns:
        Result of the command. None if it was not run because the budget ran out.
    """
    if budget.remaining() <= 0:
        return None
    commands.append(argv)
    return ctx.runner.run(argv, timeout_s=budget.remaining())


def mark_timed_out(result: StepResult) -> None:
    """Set details["timed_out"] = True and the timeout summary (TIMEOUT_SUMMARY).

    Args:
        result: Step result to write to.
    """
    result.details["timed_out"] = True
    result.summary = TIMEOUT_SUMMARY


def failure_summary(run: CommandResult, what: str) -> str:
    """summary for a failed command.

    WRITE_DENIED_SUMMARY for permission-error wording; otherwise the head of stderr is
    appended.

    Args:
        run: Result of the failed command.
        what: Failure heading (e.g. the Japanese for "f3write failed" or "cannot parse fio
            output").

    Returns:
        Wording for summary.
    """
    if is_permission_error(run.stdout + run.stderr):
        return WRITE_DENIED_SUMMARY
    return f"{what}: {run.stderr[:STDERR_HEAD_CHARS]}"


def record_raw_stdout(target: dict[str, Any], run: CommandResult) -> None:
    """Keep the head of unparsable stdout in target["raw_stdout_head"].

    Args:
        target: Where to write (result.details or a per-read/write entry).
        run: Result of the command whose output could not be parsed.
    """
    target["raw_stdout_head"] = run.stdout[:RAW_STDOUT_HEAD_CHARS]
