"""Batched f3write / f3read for the capacity step (f3write mode).

The filesystem is written and read back `batch_files` 1 GiB files at a time, so that a clear
quality failure ends the step with that failure instead of running for days on a large drive:

- write: a "Write failure" (I/O error) is defect. The write speed below the speed step's
  threshold for `slow_batches` batches in a row is slow. A batch that cannot keep `stall_mb_s`
  is slow (stalled).
- read: a read I/O error or a missing file is defect at once. Non-OK sectors end the read-back
  while files remain (defect, or counterfeit when at least half of what was read is lost); in
  the last batch they go to the normal judgment, which then has complete data.

Early ends set the step to FAIL through `stop()`. Problems of the environment (unparsable
output, permission, the overall `timeout_s`) stay ERROR as before.
"""

from pathlib import Path
from typing import Any

from ..models import StepResult, StepStatus
from ..runner import CommandResult
from ..speed_floor import resolve_device_class
from . import StepContext
from .capacity_fs import SECTOR_BYTES, F3ReadSummary, parse_f3read, parse_f3write_batch
from .capacity_judge import LOST_RATIO_COUNTERFEIT
from .common import (
    STDERR_HEAD_CHARS,
    TIMEOUT_SUMMARY,
    Budget,
    failure_summary,
    mark_timed_out,
    record_raw_stdout,
    run_within_budget,
)
from .speed_judge import reference_for

# f3 writes 1 GiB per .h2w file.
FILE_BYTES = 1 << 30
SECTORS_PER_FILE = FILE_BYTES // SECTOR_BYTES
_IO_ERROR = "Input/output error"


def stop(
    result: StepResult,
    kind: str,
    summary: str,
    *,
    reasons: tuple[str, ...] = (),
    bad_sectors: int | None = None,
    lost_ratio: float | None = None,
) -> None:
    """End the step early with a quality verdict (FAIL).

    Args:
        result: Step result to write to.
        kind: "defect", "slow", or "counterfeit".
        summary: Summary that states the failure found.
        reasons: counterfeit_reasons (for kind "counterfeit").
        bad_sectors: Non-OK sectors found so far. None if not known.
        lost_ratio: Ratio of non-OK sectors among those read. None if not known.
    """
    result.status = StepStatus.FAIL
    result.summary = summary
    result.details.update(
        kind=kind,
        defect=kind == "defect",
        counterfeit=kind == "counterfeit",
        counterfeit_reasons=list(reasons),
        bad_sectors=bad_sectors,
        lost_ratio=round(lost_ratio, 4) if lost_ratio is not None else None,
        actual_bytes=None,
        verified_bytes=None,
        stopped_early=True,
    )


def write_threshold_mb_s(ctx: StepContext) -> float | None:
    """Write speed threshold of the speed step (reference x pass_ratio). None without one."""
    speed = ctx.config.speed
    device_class, _basis = resolve_device_class(speed.device_class, ctx.device.link_speed_mbps)
    reference = reference_for("write", speed.nominal_write_mb_s, device_class)
    return reference.value * speed.pass_ratio if reference.value is not None else None


def batch_cap_s(ctx: StepContext, files: int) -> float:
    """Seconds a batch of `files` files may take at the stall rate (stall_mb_s)."""
    return files * FILE_BYTES / (ctx.config.capacity.stall_mb_s * 1e6)


def _run_batch(
    ctx: StepContext, budget: Budget, argv: list[str], commands: list[list[str]], files: int
) -> tuple[CommandResult | None, bool]:
    """Run one batch. The second value is True when the stall cap, not the overall budget,
    sets the timeout (so a timeout means the drive stalled)."""
    cap = batch_cap_s(ctx, files)
    stall_limited = cap < budget.remaining()
    return run_within_budget(ctx, budget, argv, commands, cap_s=cap), stall_limited


def _stalled(ctx: StepContext, action: str, files: str, count: int) -> str:
    """Summary of a stalled batch."""
    cap = batch_cap_s(ctx, count)
    stall = ctx.config.capacity.stall_mb_s
    return f"{action} stalled: {files} not finished within {cap:.0f}s (below {stall:g}MB/s)"


def _mean_mb_s(batches: list[tuple[int, float]]) -> float | None:
    """Average speed over batches of (files, MB/s), weighted by time (harmonic mean)."""
    seconds = sum(files / mb_s for files, mb_s in batches if mb_s > 0)
    files = sum(files for files, mb_s in batches if mb_s > 0)
    return round(files / seconds, 2) if seconds else None


def write_batches(
    ctx: StepContext,
    result: StepResult,
    budget: Budget,
    commands: list[list[str]],
    target: Path,
    entry: dict[str, Any],
) -> int | None:
    """Fill the mounted filesystem with f3write, one batch at a time.

    Returns:
        The highest file number written. None when the step has ended (an ERROR summary is
        set, or the step was stopped as FAIL).
    """
    config = ctx.config.capacity
    threshold = write_threshold_mb_s(ctx)
    speeds: list[tuple[int, float]] = []
    slow: list[tuple[int, float]] = []
    entry.update(write_threshold_mb_s=threshold, write_batches_mb_s=[])
    last = 0
    start = 1
    while True:
        end = start + config.batch_files - 1
        files = f"files {start}-{end}"
        argv = ["f3write", f"--start-at={start}", f"--end-at={end}", str(target)]
        write, stall_limited = _run_batch(ctx, budget, argv, commands, config.batch_files)
        if write is None:
            result.summary = TIMEOUT_SUMMARY
            return None
        entry["f3write_returncode"] = write.returncode
        if write.timed_out:
            if stall_limited:
                stop(result, "slow", _stalled(ctx, "Write", files, config.batch_files))
            else:
                mark_timed_out(result)
            return None
        try:
            batch = parse_f3write_batch(write.stdout)
        except ValueError as exc:
            record_raw_stdout(result.details, write)
            raise ValueError(
                f"Could not parse f3write output: {write.stderr[:STDERR_HEAD_CHARS]}"
            ) from exc
        if batch.failures or (write.returncode != 0 and _IO_ERROR in write.stderr):
            reason = batch.failures[0] if batch.failures else _IO_ERROR
            stop(result, "defect", f"Write error in {files}: {reason}")
            return None
        # Exit 1 with "No space!" only means the previous batch filled the filesystem.
        if write.returncode != 0 and not (batch.disk_full and not batch.written):
            if batch.write_mb_s is None:
                result.summary = failure_summary(write, "f3write failed")
                return None
            warning = f"f3write returned exit code {write.returncode}"
            if warning not in result.warnings:
                result.warnings.append(warning)
        last = max(batch.written, default=last)
        if batch.write_mb_s is not None and batch.written:
            speeds.append((len(batch.written), batch.write_mb_s))
            entry["write_batches_mb_s"].append(round(batch.write_mb_s, 1))
            if threshold is not None and batch.write_mb_s < threshold:
                slow.append((start, batch.write_mb_s))
            else:
                slow.clear()
            if len(slow) >= config.slow_batches:
                measured = " / ".join(f"{mb_s:.1f}" for _start, mb_s in slow)
                stop(
                    result,
                    "slow",
                    f"Write speed shortfall: {len(slow)} batches in a row below threshold"
                    f" {threshold:.1f}MB/s (files {slow[0][0]}-{end}: {measured}MB/s)",
                )
                return None
        if batch.disk_full or batch.attempted < config.batch_files:
            break
        start = end + 1
    entry.update(write_mb_s=_mean_mb_s(speeds), files_written=last)
    return last


def _add(total: F3ReadSummary, batch: F3ReadSummary) -> F3ReadSummary:
    """Sum two read summaries (lost_sectors normalized to the non-OK total)."""
    return F3ReadSummary(
        ok_sectors=total.ok_sectors + batch.ok_sectors,
        lost_sectors=total.bad_sectors + batch.bad_sectors,
        corrupted_sectors=total.corrupted_sectors + batch.corrupted_sectors,
        slightly_changed_sectors=total.slightly_changed_sectors + batch.slightly_changed_sectors,
        overwritten_sectors=total.overwritten_sectors + batch.overwritten_sectors,
        read_error=total.read_error or batch.read_error,
    )


def _stop_read(
    result: StepResult, total: F3ReadSummary, missing: int, files: str, remaining: bool
) -> None:
    """Stop the read-back with defect, or counterfeit when at least half is lost."""
    lost = total.bad_sectors + missing * SECTORS_PER_FILE
    read = total.ok_sectors + lost
    ratio = lost / read if read else None
    parts: list[str] = []
    if lost:
        parts.append(
            f"{lost} sectors lost (corrupted {total.corrupted_sectors}"
            f" / slightly changed {total.slightly_changed_sectors}"
            f" / overwritten {total.overwritten_sectors})"
        )
    if missing:
        parts.append(f"{missing} {'file' if missing == 1 else 'files'} missing")
    if total.read_error:
        parts.append("read I/O error")
    found = "; ".join(parts)
    tail = "; remaining files not read" if remaining else ""
    if ratio is not None and ratio >= LOST_RATIO_COUNTERFEIT:
        summary = (
            f"Suspected counterfeit: lost {ratio * 100:.1f}% in read-back up to {files}"
            f" ({found}){tail}"
        )
        stop(
            result,
            "counterfeit",
            summary,
            reasons=("lost_ratio",),
            bad_sectors=lost,
            lost_ratio=ratio,
        )
        return
    summary = f"Data corruption in read-back of {files}: {found}{tail}"
    stop(result, "defect", summary, bad_sectors=lost, lost_ratio=ratio)


def read_batches(
    ctx: StepContext,
    result: StepResult,
    budget: Budget,
    commands: list[list[str]],
    target: Path,
    entry: dict[str, Any],
    last: int,
) -> F3ReadSummary | None:
    """Read back files 1..last with f3read, one batch at a time.

    Returns:
        Totals over all files. None when the step has ended (ERROR summary or FAIL).
    """
    batch_files = ctx.config.capacity.batch_files
    total = F3ReadSummary(ok_sectors=0, lost_sectors=0)
    speeds: list[tuple[int, float]] = []
    start = 1
    while start <= last:
        end = min(start + batch_files - 1, last)
        count = end - start + 1
        files = f"files {start}-{end}"
        argv = ["f3read", f"--start-at={start}", f"--end-at={end}", str(target)]
        read, stall_limited = _run_batch(ctx, budget, argv, commands, count)
        if read is None:
            result.summary = TIMEOUT_SUMMARY
            return None
        entry["f3read_returncode"] = read.returncode
        if read.timed_out:
            if stall_limited:
                stop(result, "slow", _stalled(ctx, "Read", files, count))
            else:
                mark_timed_out(result)
            return None
        try:
            batch = parse_f3read(read.stdout)
        except ValueError as exc:
            record_raw_stdout(result.details, read)
            raise ValueError(
                f"Could not parse f3read output: {read.stderr[:STDERR_HEAD_CHARS]}"
            ) from exc
        total = _add(total, batch)
        if batch.read_mb_s is not None:
            speeds.append((count, batch.read_mb_s))
        missing = len(set(range(start, end + 1)) - set(batch.validated))
        if batch.read_error or missing or (batch.bad_sectors and end < last):
            _stop_read(result, total, missing, files, remaining=end < last)
            return None
        start = end + 1
    entry["read_mb_s"] = _mean_mb_s(speeds)
    return total
