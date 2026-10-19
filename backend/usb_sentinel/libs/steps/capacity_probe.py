"""f3probe mode of the capacity step (use_probe = true).

f3probe writes directly to the device without a filesystem, so nothing is mounted.
"""

import re
from dataclasses import dataclass

from ..models import StepResult
from . import StepContext
from .common import (
    TIMEOUT_SUMMARY,
    Budget,
    failure_summary,
    mark_timed_out,
    not_found_summary,
    record_raw_stdout,
    run_within_budget,
)

_USABLE = re.compile(r"\*Usable\* size: .*\((\d+) blocks\)")
_ANNOUNCED = re.compile(r"Announced size: .*\((\d+) blocks\)")
_BLOCK_SIZE = re.compile(r"Physical block size: .*\(2\^(\d+) Bytes\)")
_VERDICT = re.compile(r"^(Good news|Bad news):.*$", re.MULTILINE)

# Block size used when there is no Physical block size line.
DEFAULT_BLOCK_SIZE = 512


@dataclass
class ProbeSummary:
    """Summary extracted from f3probe stdout."""

    usable_blocks: int
    announced_blocks: int | None
    block_size: int
    verdict_line: str | None


def parse_f3probe(text: str) -> ProbeSummary:
    """Parse f3probe stdout.

    Raises:
        ValueError: If there is no `*Usable* size` line.
    """
    usable = _USABLE.search(text)
    if usable is None:
        raise ValueError("Could not parse f3probe output")
    announced = _ANNOUNCED.search(text)
    block_size = _BLOCK_SIZE.search(text)
    verdict = _VERDICT.search(text)
    return ProbeSummary(
        usable_blocks=int(usable.group(1)),
        announced_blocks=int(announced.group(1)) if announced else None,
        block_size=2 ** int(block_size.group(1)) if block_size else DEFAULT_BLOCK_SIZE,
        verdict_line=verdict.group(0).strip() if verdict else None,
    )


def run_probe(
    ctx: StepContext, result: StepResult, budget: Budget, commands: list[list[str]]
) -> int | None:
    """Run f3probe and return the actual capacity (bytes).

    On failure, result is left as ERROR and None is returned. CommandNotFoundError propagates
    to the caller.

    Args:
        ctx: Step context.
        result: Step result to write to.
        budget: Time budget.
        commands: Where run argv values are recorded (appended right before running).

    Returns:
        usable_blocks * block_size. None on failure.
    """
    if ctx.runner.which("f3probe") is None:
        result.summary = not_found_summary("f3probe")
        return None
    argv = ["f3probe", "--destructive", "--time-ops", ctx.device.path]
    probe = run_within_budget(ctx, budget, argv, commands)
    if probe is None:
        result.summary = TIMEOUT_SUMMARY
        return None
    if probe.timed_out:
        mark_timed_out(result)
        return None
    try:
        probe_summary = parse_f3probe(probe.stdout)
    except ValueError:
        record_raw_stdout(result.details, probe)
        result.summary = failure_summary(probe, "Could not parse f3probe output")
        return None
    result.details["probe"] = {
        "usable_blocks": probe_summary.usable_blocks,
        "announced_blocks": probe_summary.announced_blocks,
        "block_size": probe_summary.block_size,
        "announced_bytes": (
            probe_summary.announced_blocks * probe_summary.block_size
            if probe_summary.announced_blocks is not None
            else None
        ),
        "verdict_line": probe_summary.verdict_line,
        "returncode": probe.returncode,
    }
    return probe_summary.usable_blocks * probe_summary.block_size
