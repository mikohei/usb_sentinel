"""Speed verification step (fio).

Runs a sequential read, then a sequential write, on the unmounted block device. A destructive
test, called only after passing the orchestrator gate. The is_mounted check at the start of
run() is self-defense, not a replacement for the gate.
"""

import time
from collections.abc import Callable
from typing import Any

from ..device import is_mounted
from ..models import StepResult, StepStatus
from ..runner import CommandNotFoundError
from ..speed_floor import Reference, resolve_device_class
from . import StepContext
from .common import (
    PERMISSION_ERRNOS,
    TIMEOUT_SUMMARY,
    Budget,
    DeviceQueryError,
    failure_summary,
    mark_timed_out,
    not_found_summary,
    query_block_info,
    record_raw_stdout,
    run_within_budget,
)
from .speed_fio import FioJob, fio_argv, parse_fio
from .speed_judge import (
    FAIL_PREFIX,
    Mode,
    basis_text,
    class_warnings,
    link_ceiling_warning,
    reference_for,
    severity_for,
)

# FioJob and parse_fio moved to speed_fio.py. The names are kept here for existing importers.
__all__ = ["DEFAULT_MONOTONIC", "FioJob", "SpeedStep", "parse_fio"]

# Module variable that integration tests replace with monkeypatch.
DEFAULT_MONOTONIC: Callable[[], float] = time.monotonic

_LABELS: dict[Mode, str] = {"read": "read", "write": "write"}


class SpeedStep:
    def __init__(self, monotonic: Callable[[], float] | None = None) -> None:
        """Inject dependencies. If None, the module variable at call time is used."""
        self.monotonic = monotonic or DEFAULT_MONOTONIC

    def run(self, ctx: StepContext) -> StepResult:
        """Run speed verification: fio read (--readonly), then write, compared to references.

        The is_mounted check at the start is self-defense: if a mount remains, fio is not
        started and the result is ERROR.

        Args:
            ctx: Step context.

        Returns:
            Step result with PASS, FAIL, SKIPPED (no reference), or ERROR.
        """
        result = StepResult("speed", StepStatus.ERROR)
        speed = ctx.config.speed
        commands: list[list[str]] = []
        try:
            # Self-defense check: do not start fio if the preceding capacity step failed to
            # umount.
            if is_mounted(query_block_info(ctx.runner, ctx.device.path)):
                result.summary = "Still mounted; fio not run"
                return result
            if ctx.runner.which("fio") is None:
                result.summary = not_found_summary("fio")
                return result
            result.details.update(
                block_size=speed.block_size,
                queue_depth=speed.queue_depth,
                duration_s=speed.duration_s,
                pass_ratio=speed.pass_ratio,
                nominal_read_mb_s=speed.nominal_read_mb_s,
                nominal_write_mb_s=speed.nominal_write_mb_s,
                commands=commands,
            )
            link = ctx.device.link_speed_mbps
            device_class, class_basis = resolve_device_class(speed.device_class, link)
            result.details.update(
                device_class=device_class, device_class_basis=class_basis, link_speed_mbps=link
            )
            result.warnings.extend(
                class_warnings(
                    speed.device_class,
                    class_basis,
                    link,
                    has_nominal=speed.nominal_read_mb_s is not None
                    or speed.nominal_write_mb_s is not None,
                )
            )
            budget = Budget(speed.timeout_s, self.monotonic)
            read = self._measure(ctx, result, budget, "read", commands)
            if read is None:
                return result
            write = self._measure(ctx, result, budget, "write", commands)
            if write is None:
                return result
        except CommandNotFoundError as exc:
            result.summary = not_found_summary(str(exc))
            return result
        except DeviceQueryError as exc:
            result.summary = str(exc)
            return result
        return self._judge(result, ctx, device_class, class_basis, read, write)

    def _measure(
        self,
        ctx: StepContext,
        result: StepResult,
        budget: Budget,
        mode: Mode,
        commands: list[list[str]],
    ) -> FioJob | None:
        """Run fio once. On failure, leave result as ERROR and return None."""
        run = run_within_budget(ctx, budget, fio_argv(ctx, mode), commands)
        if run is None:
            result.summary = TIMEOUT_SUMMARY
            return None
        entry: dict[str, Any] = {"returncode": run.returncode}
        result.details[mode] = entry
        if run.timed_out:
            entry["timed_out"] = True
            # The reason code (s:timeout) looks at the top-level timed_out.
            mark_timed_out(result)
            return None
        try:
            job = parse_fio(run.stdout, mode)
        except ValueError:
            record_raw_stdout(entry, run)
            result.summary = failure_summary(run, "Could not parse fio output")
            return None
        entry.update(
            bw_bytes=job.bw_bytes,
            mb_s=round(job.mb_s, 1),
            iops=job.iops,
            io_bytes=job.io_bytes,
            runtime_ms=job.runtime_ms,
            error=job.error,
        )
        if job.error != 0:
            if job.error in PERMISSION_ERRNOS:
                result.summary = f"Permission denied: {_LABELS[mode]} refused (error={job.error})"
            else:
                result.summary = f"fio reported an I/O error (error={job.error})"
            return None
        return job

    def _judge(
        self,
        result: StepResult,
        ctx: StepContext,
        device_class: str | None,
        class_basis: str,
        read: FioJob,
        write: FioJob,
    ) -> StepResult:
        """Compare measurements with references (nominal > class minimum) to decide
        PASS / FAIL / SKIPPED.

        A FAIL gets a severity (severe if measured/reference is under 50%, otherwise minor).
        SKIPPED if there is no reference for either read or write.
        """
        speed = ctx.config.speed
        pass_ratio = speed.pass_ratio
        measured: dict[Mode, float] = {"read": read.mb_s, "write": write.mb_s}
        refs: dict[Mode, Reference] = {
            "read": reference_for("read", speed.nominal_read_mb_s, device_class),
            "write": reference_for("write", speed.nominal_write_mb_s, device_class),
        }
        thresholds: dict[Mode, float | None] = {}
        for mode, ref in refs.items():
            threshold = ref.value * pass_ratio if ref.value is not None else None
            thresholds[mode] = threshold
            result.details[f"basis_{mode}"] = ref.basis
            result.details[f"reference_{mode}_mb_s"] = ref.value
            result.details[f"threshold_{mode}_mb_s"] = threshold
            result.details[f"ratio_{mode}"] = (
                round(measured[mode] / ref.value, 3) if ref.value is not None else None
            )
        result.details["severity"] = None
        ceiling = link_ceiling_warning(refs.values(), ctx.device.link_speed_mbps)
        if ceiling is not None:
            result.warnings.append(ceiling)
        values = f"read {measured['read']:.1f}MB/s, write {measured['write']:.1f}MB/s"
        if thresholds["read"] is None and thresholds["write"] is None:
            result.status = StepStatus.SKIPPED
            result.summary = f"Nominal speed not set ({values})"
            return result
        failing: list[tuple[Mode, float, float]] = [
            (mode, threshold, value)
            for mode, threshold in thresholds.items()
            if threshold is not None
            and (value := refs[mode].value) is not None
            and measured[mode] < threshold
        ]
        if failing:
            shortfalls = [
                f"{_LABELS[mode]} {measured[mode]:.1f}MB/s < threshold {threshold:.1f}MB/s"
                + basis_text(refs[mode], pass_ratio, class_basis)
                for mode, threshold, _value in failing
            ]
            severity = severity_for(measured[mode] / value for mode, _threshold, value in failing)
            result.details["severity"] = severity
            result.status = StepStatus.FAIL
            result.summary = FAIL_PREFIX[severity] + "; ".join(shortfalls)
            return result
        result.status = StepStatus.PASS
        if thresholds["read"] is not None and thresholds["write"] is not None:
            result.summary = (
                f"{values} (threshold {thresholds['read']:.1f} / {thresholds['write']:.1f}MB/s)"
            )
        else:
            parts = [
                f"{_LABELS[mode]} {measured[mode]:.1f}MB/s"
                + (
                    f" (threshold {threshold:.1f}MB/s)"
                    if threshold is not None
                    else " (not judged)"
                )
                for mode, threshold in thresholds.items()
            ]
            result.summary = "; ".join(parts)
        return result
