"""f3write mode of the capacity step (rw-mount each filesystem in turn, then f3write -> f3read).

Mounting and cleanup are handled by capacity_fs.rw_mounted(); the judgment is done by
capacity.CapacityStep.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..device import filesystems
from ..models import StepResult
from . import StepContext
from .capacity_fs import (
    SECTOR_BYTES,
    F3ReadSummary,
    parse_f3read,
    parse_f3write,
    remove_h2w,
    rw_mounted,
    usage,
)
from .common import (
    STDERR_HEAD_CHARS,
    TIMEOUT_SUMMARY,
    Budget,
    failure_summary,
    mark_timed_out,
    missing_command,
    not_found_summary,
    query_block_info,
    record_raw_stdout,
    run_within_budget,
)

_F3WRITE_COMMANDS = ("mount", "umount", "f3write", "f3read")


@dataclass
class Totals:
    """Totals across filesystems."""

    verified_bytes: int = 0
    used_bytes: int = 0
    bad_sectors: int = 0


def run_f3write(
    ctx: StepContext,
    result: StepResult,
    budget: Budget,
    commands: list[list[str]],
    statvfs: Callable[[str], os.statvfs_result],
) -> Totals | None:
    """f3write+f3read mode. rw-mount each filesystem in turn and verify it.

    On failure, result.summary is finalized and None is returned. Mount and parser exceptions
    (MountError, NotWritableError, UnmountError, ValueError, and so on) propagate to the caller.

    Args:
        ctx: Step context.
        result: Step result to write to.
        budget: Time budget.
        commands: Where run argv values are recorded (appended right before running).
        statvfs: os.statvfs-compatible function used to get usage.

    Returns:
        Totals across all filesystems. None on failure.
    """
    missing = missing_command(ctx.runner, _F3WRITE_COMMANDS)
    if missing is not None:
        result.summary = not_found_summary(missing)
        return None
    targets = filesystems(query_block_info(ctx.runner, ctx.device.path))
    if not targets:
        result.summary = "No filesystem (consider use_probe = true)"
        return None
    totals = Totals()
    for index, device_path in enumerate(targets):
        entry: dict[str, Any] = {"device": device_path}
        result.details["filesystems"].append(entry)
        with rw_mounted(ctx, device_path, index, statvfs, result.warnings, commands) as target:
            entry["mountpoint"] = str(target)
            read_summary = _inspect_filesystem(
                ctx, result, budget, commands, target, entry, statvfs
            )
            if read_summary is None:
                return None
        totals.verified_bytes += read_summary.ok_sectors * SECTOR_BYTES
        totals.used_bytes += entry["used_bytes"]
        totals.bad_sectors += read_summary.bad_sectors
    return totals


def _inspect_filesystem(
    ctx: StepContext,
    result: StepResult,
    budget: Budget,
    commands: list[list[str]],
    target: Path,
    entry: dict[str, Any],
    statvfs: Callable[[str], os.statvfs_result],
) -> F3ReadSummary | None:
    """Run f3write -> f3read on one mounted filesystem.

    On failure, result.summary is finalized and None is returned (so that, even on a return
    path, it can be moved to suppressed_error if umount fails). A ValueError from
    parse_f3write() or parse_f3read() is placed in rw_mounted()'s pending before run() catches
    it.
    """
    stale = remove_h2w(target, result.warnings)
    entry["stale_h2w_removed"] = stale
    if stale:
        result.warnings.append(f"Deleted {stale} f3 files left from a previous run")
    entry["used_bytes"], entry["free_bytes"] = usage(statvfs, target)

    write = run_within_budget(ctx, budget, ["f3write", str(target)], commands)
    if write is None:
        result.summary = TIMEOUT_SUMMARY
        return None
    entry["f3write_returncode"] = write.returncode
    if write.timed_out:
        mark_timed_out(result)
        return None
    try:
        entry["write_mb_s"] = parse_f3write(write.stdout)
    except ValueError as exc:
        record_raw_stdout(result.details, write)
        raise ValueError(
            f"Could not parse f3write output: {write.stderr[:STDERR_HEAD_CHARS]}"
        ) from exc
    if write.returncode != 0:
        if entry["write_mb_s"] is None:
            result.summary = failure_summary(write, "f3write failed")
            return None
        result.warnings.append(f"f3write returned exit code {write.returncode}")

    read = run_within_budget(ctx, budget, ["f3read", str(target)], commands)
    if read is None:
        result.summary = TIMEOUT_SUMMARY
        return None
    entry["f3read_returncode"] = read.returncode
    if read.timed_out:
        mark_timed_out(result)
        return None
    try:
        read_summary = parse_f3read(read.stdout)
    except ValueError as exc:
        record_raw_stdout(result.details, read)
        raise ValueError(
            f"Could not parse f3read output: {read.stderr[:STDERR_HEAD_CHARS]}"
        ) from exc
    entry.update(
        ok_sectors=read_summary.ok_sectors,
        lost_sectors=read_summary.lost_sectors,
        corrupted_sectors=read_summary.corrupted_sectors,
        slightly_changed_sectors=read_summary.slightly_changed_sectors,
        overwritten_sectors=read_summary.overwritten_sectors,
        read_mb_s=read_summary.read_mb_s,
    )
    return read_summary
