import subprocess
from dataclasses import dataclass
from typing import Protocol

from .sysfs import KERNEL_NAME_RE

# ActiveState values treated as "inspecting".
ACTIVE_STATES = frozenset({"active", "activating"})
# Fixed arguments for systemctl show. Unit names follow them.
SHOW_ARGV = ("systemctl", "show", "-p", "Id", "-p", "ActiveState")


@dataclass(frozen=True)
class CommandOutput:
    """Result of running an external command."""

    returncode: int
    stdout: str
    timed_out: bool


class Runner(Protocol):
    """Protocol for swapping out external command execution."""

    def run(self, argv: list[str], *, timeout_s: float) -> CommandOutput:
        """Run argv and return the result. Never raises; failures go in the return value."""
        ...


class SubprocessRunner:
    """Run commands for real with subprocess."""

    def run(self, argv: list[str], *, timeout_s: float) -> CommandOutput:
        """Run argv. A missing command returns 127; a timeout returns timed_out=True.

        Args:
            argv: argv to run (a fixed list; never build commands by joining strings).
            timeout_s: Timeout in seconds.

        Returns:
            Exit code and stdout. Missing is `CommandOutput(127, "", False)`; a timeout is
            `CommandOutput(-1, "", True)`。
        """
        try:
            process = subprocess.run(
                argv, capture_output=True, text=True, errors="replace", timeout=timeout_s
            )
        except FileNotFoundError:
            return CommandOutput(127, "", False)
        except subprocess.TimeoutExpired:
            return CommandOutput(-1, "", True)
        return CommandOutput(process.returncode, process.stdout, False)


def unit_name(template: str, kernel: str) -> str:
    """Build a unit name by filling the kernel name into unit_template.

    Raises:
        ValueError: When the kernel name is not of the form sd[a-z]{1,3}.
    """
    if not KERNEL_NAME_RE.fullmatch(kernel):
        raise ValueError(f"invalid kernel name: {kernel!r}")
    # str.format interprets attribute access and other placeholders, so use plain replacement.
    return template.replace("{kernel}", kernel)


def parse_show_output(text: str) -> dict[str, str]:
    """Convert systemctl show -p Id -p ActiveState output into unit name -> ActiveState."""
    states: dict[str, str] = {}
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                fields[key] = value
        if "Id" in fields and "ActiveState" in fields:
            states[fields["Id"]] = fields["ActiveState"]
    return states


def query_unit_states(
    runner: Runner, units: list[str], *, timeout_s: float = 2.0
) -> dict[str, str]:
    """Query the ActiveState of several units with one systemctl show.

    Args:
        runner: Command runner.
        units: List of unit names. If empty, return an empty dict without calling.
        timeout_s: Timeout in seconds.

    Returns:
        Unit name -> ActiveState. An empty dict on failure (non-zero, timeout, missing).
    """
    if not units:
        return {}
    output = runner.run([*SHOW_ARGV, *units], timeout_s=timeout_s)
    if output.timed_out or output.returncode != 0:
        return {}
    return parse_show_output(output.stdout)
