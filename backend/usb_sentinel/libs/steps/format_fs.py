"""Format the whole drive as exFAT (destructive; full mode only).

Used before f3write (f3probe and fio from an earlier run leave no usable filesystem) and once
more at the end of a full inspection, so the drive can be used and inspected again. Called only
after the orchestrator gate `permitted` (scan and hashing passed, full mode, unmounted).

The filesystem goes on the whole disk without a partition table: the container has device
nodes only for the paths that existed at insertion, so a newly created partition could not be
opened.
"""

import math
import time
from typing import Any

from ..device import is_mounted
from . import StepContext
from .common import (
    STDERR_HEAD_CHARS,
    TIMEOUT_SUMMARY,
    Budget,
    DeviceQueryError,
    query_block_info,
    run_within_budget,
)

FSTYPE = "exfat"
FORMAT_COMMANDS = ("wipefs", "mkfs.exfat")
# Upper limit per command (seconds). A quick format writes only metadata, tens of MB even for
# an 8 TB drive.
FORMAT_TIMEOUT_S = 1800.0


def format_argvs(device_path: str) -> list[list[str]]:
    """argv list: erase every old signature (partition tables included), then make exFAT."""
    return [
        ["wipefs", "--all", "--force", device_path],
        ["mkfs.exfat", device_path],
    ]


def format_exfat(ctx: StepContext, budget: Budget, commands: list[list[str]]) -> str | None:
    """Make one exFAT filesystem on the whole device (`ctx.device.path`).

    Args:
        ctx: Step context.
        budget: Time budget of the caller. Each command is also capped at FORMAT_TIMEOUT_S.
        commands: Where run argv values are recorded (appended right before running).

    Returns:
        None on success. Otherwise the failure summary ("Format failed: ...", or "Timed out"
        when the caller's budget is used up).
    """
    for argv in format_argvs(ctx.device.path):
        run = run_within_budget(ctx, budget, argv, commands, cap_s=FORMAT_TIMEOUT_S)
        if run is None:
            return TIMEOUT_SUMMARY
        if run.timed_out:
            return f"Format failed: {argv[0]} timed out"
        if not run.ok:
            output = (run.stderr or run.stdout or f"exit code {run.returncode}").strip()
            return f"Format failed: {argv[0]}: {output[:STDERR_HEAD_CHARS]}"
    return None


def reformat(ctx: StepContext) -> dict[str, Any]:
    """Format the whole drive as exFAT after the destructive steps ran.

    Called only when `permitted` was true, so never for a drive with malware or an unfinished
    scan. Skipped while anything on the drive is still mounted (a failed umount).

    Returns:
        The `reformat` record of result.json (status "done" / "failed" / "skipped").
    """
    commands: list[list[str]] = []
    record: dict[str, Any] = {"fstype": FSTYPE, "commands": commands}
    try:
        still_mounted = is_mounted(query_block_info(ctx.runner, ctx.device.path))
    except DeviceQueryError as exc:
        return {**record, "status": "skipped", "summary": str(exc)}
    if still_mounted:
        return {**record, "status": "skipped", "summary": "Still mounted"}
    failure = format_exfat(ctx, Budget(math.inf, time.monotonic), commands)
    if failure is not None:
        return {**record, "status": "failed", "summary": failure}
    return {**record, "status": "done", "summary": f"Formatted as {FSTYPE}"}
