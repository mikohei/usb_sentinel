"""f3write mode of the capacity step (format, rw-mount, then f3write -> f3read in batches).

The whole drive is first formatted as exFAT (format_fs.py): an f3probe pre-check or fio from
an earlier run leaves no usable filesystem, and a fresh filesystem lets f3write verify the full
capacity. Mounting and cleanup are handled by capacity_fs.rw_mounted(), the batches by
capacity_batches.py, and the judgment by capacity.CapacityStep.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import StepResult
from . import StepContext
from .capacity_batches import read_batches, write_batches
from .capacity_fs import SECTOR_BYTES, F3ReadSummary, remove_h2w, rw_mounted, usage
from .common import Budget
from .format_fs import FORMAT_COMMANDS, FSTYPE, format_exfat

# Checked before anything touches the drive (the f3probe pre-check included).
F3WRITE_COMMANDS = ("mount", "umount", "f3write", "f3read", *FORMAT_COMMANDS)


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
    """f3write+f3read mode. Format the whole drive, rw-mount it, and verify it.

    The caller has checked F3WRITE_COMMANDS. On failure, result.summary is finalized (or the
    step is stopped as FAIL) and None is returned. Mount and parser exceptions (MountError,
    NotWritableError, UnmountError, ValueError, and so on) propagate to the caller.

    Args:
        ctx: Step context.
        result: Step result to write to.
        budget: Time budget.
        commands: Where run argv values are recorded (appended right before running).
        statvfs: os.statvfs-compatible function used to get usage.

    Returns:
        Totals of the filesystem. None on failure.
    """
    failure = format_exfat(ctx, budget, commands)
    if failure is not None:
        result.summary = failure
        return None
    entry: dict[str, Any] = {"device": ctx.device.path, "fstype": FSTYPE}
    result.details["filesystems"].append(entry)
    with rw_mounted(ctx, ctx.device.path, 0, statvfs, result.warnings, commands) as target:
        entry["mountpoint"] = str(target)
        read_summary = _inspect_filesystem(ctx, result, budget, commands, target, entry, statvfs)
        if read_summary is None:
            return None
    return Totals(
        verified_bytes=read_summary.ok_sectors * SECTOR_BYTES,
        used_bytes=entry["used_bytes"],
        bad_sectors=read_summary.bad_sectors,
    )


def _inspect_filesystem(
    ctx: StepContext,
    result: StepResult,
    budget: Budget,
    commands: list[list[str]],
    target: Path,
    entry: dict[str, Any],
    statvfs: Callable[[str], os.statvfs_result],
) -> F3ReadSummary | None:
    """Run f3write -> f3read in batches on the mounted filesystem.

    On failure, result is finalized and None is returned (so that, even on a return path, it
    can be moved to suppressed_error if umount fails). A ValueError from the parsers is placed
    in rw_mounted()'s pending before run() catches it.
    """
    stale = remove_h2w(target, result.warnings)
    entry["stale_h2w_removed"] = stale
    if stale:
        result.warnings.append(f"Deleted {stale} f3 files left from a previous run")
    entry["used_bytes"], entry["free_bytes"] = usage(statvfs, target)
    last = write_batches(ctx, result, budget, commands, target, entry)
    if last is None:
        return None
    read_summary = read_batches(ctx, result, budget, commands, target, entry, last)
    if read_summary is None:
        return None
    entry.update(
        ok_sectors=read_summary.ok_sectors,
        lost_sectors=read_summary.bad_sectors,
        corrupted_sectors=read_summary.corrupted_sectors,
        slightly_changed_sectors=read_summary.slightly_changed_sectors,
        overwritten_sectors=read_summary.overwritten_sectors,
    )
    return read_summary
