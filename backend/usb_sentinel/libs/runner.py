"""Running external commands.

All subprocess calls are centralized here. argv is always a fixed list; commands are never
built by string concatenation. Tests replace this with FakeRunner.
"""

import shutil
import subprocess
from dataclasses import dataclass, replace
from typing import Protocol


class CommandNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        """True if the exit code is 0 and it did not time out."""
        return self.returncode == 0 and not self.timed_out

    def check(self) -> "CommandResult":
        """Raise RuntimeError (stderr, else argv) if not ok. Return self if ok."""
        if not self.ok:
            raise RuntimeError(self.stderr or f"command failed: {self.argv}")
        return self


class CommandRunner(Protocol):
    def which(self, command: str) -> str | None:
        """Resolved path of the command. None if not found."""
        ...

    def run(self, argv: list[str], *, timeout_s: float | None = None) -> CommandResult:
        """Run argv and return the result. Exceeding timeout_s yields timed_out=True."""
        ...


class SubprocessRunner:
    def which(self, command: str) -> str | None:
        """Search PATH with shutil.which()."""
        return shutil.which(command)

    def run(self, argv: list[str], *, timeout_s: float | None = None) -> CommandResult:
        """Run argv with subprocess.run(). stdout/stderr are text; invalid bytes are replaced.

        Args:
            argv: argv to run (fixed list).
            timeout_s: Seconds. When exceeded, returns a result with exit code -1 and
                timed_out=True.

        Returns:
            Result of the command.

        Raises:
            CommandNotFoundError: If argv[0] cannot be executed.
        """
        try:
            process = subprocess.run(
                argv, capture_output=True, text=True, errors="replace", timeout=timeout_s
            )
        except FileNotFoundError as exc:
            raise CommandNotFoundError(argv[0]) from exc
        except subprocess.TimeoutExpired:
            return CommandResult(tuple(argv), -1, stderr="timeout", timed_out=True)
        return CommandResult(tuple(argv), process.returncode, process.stdout, process.stderr)


class FakeRunner:
    def __init__(self, missing: set[str] | None = None) -> None:
        """Fake runner that returns registered results.

        Args:
            missing: Command names for which which() returns None and run() raises
                CommandNotFoundError.
        """
        self.missing = set(missing or ())
        self.calls: list[tuple[str, ...]] = []
        self.registered: dict[tuple[str, ...], CommandResult] = {}

    def which(self, command: str) -> str | None:
        """Treat any command not in missing as existing at `/usr/bin/<command>`."""
        return None if command in self.missing else f"/usr/bin/{command}"

    def register(
        self,
        argv: list[str],
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        timed_out: bool = False,
    ) -> None:
        """Register a result returned on an argv prefix match. Re-registering argv overwrites.

        Args:
            argv: Prefix to match (e.g. `["clamscan", "--version"]`).
            returncode: Exit code to return.
            stdout: Standard output to return.
            stderr: Standard error to return.
            timed_out: Whether to return it as a timed-out result.
        """
        self.registered[tuple(argv)] = CommandResult(
            tuple(argv), returncode, stdout, stderr, timed_out
        )

    def run(self, argv: list[str], *, timeout_s: float | None = None) -> CommandResult:
        """Record the call in calls and return the registered result with the longest prefix.

        Args:
            argv: argv to run.
            timeout_s: Accepted but unused (an argument of the CommandRunner protocol).

        Returns:
            Registered result (with argv replaced by the actual call).

        Raises:
            CommandNotFoundError: If argv[0] is in missing.
            AssertionError: If no registration matches (missing test setup).
        """
        args = tuple(argv)
        self.calls.append(args)
        if argv[0] in self.missing:
            raise CommandNotFoundError(argv[0])
        matches = [key for key in self.registered if args[: len(key)] == key]
        if not matches:
            raise AssertionError(f"unregistered: {argv}")
        return replace(self.registered[max(matches, key=len)], argv=args)
