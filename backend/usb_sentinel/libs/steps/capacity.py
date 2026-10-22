"""Capacity verification step (f3write+f3read, or f3probe).

A destructive test, called only after passing the orchestrator gate. All external commands go
through ctx.runner.run(), with argv as a fixed list. f3write mode lives in capacity_write.py
(batches in capacity_batches.py) and f3probe mode in capacity_probe.py. In f3write mode,
f3probe runs first (probe_first) so that counterfeit capacity ends the step in minutes.
"""

import math
import os
import time
from collections.abc import Callable

from ..models import StepResult, StepStatus
from ..runner import CommandNotFoundError
from . import StepContext
from .capacity_fs import MountError, MountpointError, NotWritableError, UnmountError
from .capacity_judge import CapacityVerdict, evaluate, judge, over_threshold
from .capacity_probe import run_probe
from .capacity_write import F3WRITE_COMMANDS, Totals, run_f3write
from .common import (
    STDERR_HEAD_CHARS,
    Budget,
    DeviceQueryError,
    is_permission_error,
    missing_command,
    not_found_summary,
)

# evaluate() moved to capacity_judge.py. The name is kept here for existing importers.
__all__ = ["DEFAULT_MONOTONIC", "DEFAULT_STATVFS", "CapacityStep", "evaluate"]

# Module variables that integration tests replace with monkeypatch.
DEFAULT_STATVFS: Callable[[str], os.statvfs_result] = os.statvfs
DEFAULT_MONOTONIC: Callable[[], float] = time.monotonic

# f3read sector counts (keys of filesystems[].{key}_sectors). lost is the non-OK total and the
# other three are its breakdown.
_BAD_SECTOR_KEYS = ("lost", "corrupted", "slightly_changed", "overwritten")
# summary when an rw mount fails for lack of permission or write protection.
_MOUNT_NOT_WRITABLE_SUMMARY = "Permission denied: could not mount read-write"


class CapacityStep:
    def __init__(
        self,
        statvfs: Callable[[str], os.statvfs_result] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        """Inject dependencies. If None, the module variables at call time are used."""
        self.statvfs = statvfs or DEFAULT_STATVFS
        self.monotonic = monotonic or DEFAULT_MONOTONIC

    def run(self, ctx: StepContext) -> StepResult:
        """Run capacity verification: f3probe if use_probe, otherwise f3write+f3read.

        Exceptions (missing command, lsblk failure, mount failure, parser failure, umount
        failure) are converted to an ERROR summary here and never propagate.

        Args:
            ctx: Step context.

        Returns:
            Step result with PASS, FAIL, or ERROR.
        """
        result = StepResult("capacity", StepStatus.ERROR)
        config = ctx.config.capacity
        advertised = ctx.device.advertised_bytes
        base_bytes = config.nominal_bytes or advertised
        commands: list[list[str]] = []
        result.details.update(
            mode="f3probe" if config.use_probe else "f3write",
            advertised_bytes=advertised,
            nominal_bytes=config.nominal_bytes,
            base_bytes=base_bytes,
            base="nominal" if config.nominal_bytes else "advertised",
            pass_ratio=config.pass_ratio,
            over_ratio=config.over_ratio,
            threshold_bytes=math.ceil(base_bytes * config.pass_ratio),
            over_threshold_bytes=over_threshold(config.nominal_bytes, config.over_ratio),
            commands=commands,
            filesystems=[],
            probe=None,
        )
        result.details["probe_first"] = config.probe_first and not config.use_probe
        # timeout_s = 0 means no overall limit; every command then gets its own cap.
        budget = Budget(config.timeout_s or math.inf, self.monotonic)
        try:
            if config.use_probe:
                actual = run_probe(ctx, result, budget, commands)
                if actual is None:
                    return result
                return self._judge_probe(result, actual)
            missing = missing_command(ctx.runner, F3WRITE_COMMANDS)
            if missing is not None:
                result.summary = not_found_summary(missing)
                return result
            if config.probe_first and self._precheck(ctx, result, budget, commands):
                return result
            totals = run_f3write(ctx, result, budget, commands, self.statvfs)
            if totals is None:
                return result
            return self._judge_f3write(result, totals)
        except CommandNotFoundError as exc:
            result.summary = not_found_summary(str(exc))
        except DeviceQueryError as exc:
            result.summary = str(exc)
        except MountpointError as exc:
            result.summary = str(exc)
        except MountError as exc:
            if is_permission_error(str(exc)):
                result.summary = _MOUNT_NOT_WRITABLE_SUMMARY
            else:
                result.summary = f"mount failed: {str(exc)[:STDERR_HEAD_CHARS]}"
        except NotWritableError:
            result.summary = _MOUNT_NOT_WRITABLE_SUMMARY
        except ValueError as exc:
            # Already-converted messages from parse_f3write() / parse_f3read()
            # (run_probe() finalizes its own summary).
            result.summary = str(exc)
        except UnmountError as exc:
            cause = exc.__cause__
            suppressed = str(cause) if cause is not None else result.summary
            if suppressed:
                result.details["suppressed_error"] = suppressed
            result.status = StepStatus.ERROR
            result.summary = f"umount failed: {str(exc)[:STDERR_HEAD_CHARS]}"
        return result

    def _precheck(
        self, ctx: StepContext, result: StepResult, budget: Budget, commands: list[list[str]]
    ) -> bool:
        """f3probe before f3write. True if f3probe found counterfeit capacity (result is final).

        A pre-check that does not finish only adds a warning; f3write then decides.
        """
        try:
            actual = run_probe(ctx, result, budget, commands)
        except CommandNotFoundError as exc:
            result.summary, actual = not_found_summary(str(exc)), None
        if actual is None:
            result.warnings.append(f"f3probe pre-check did not finish: {result.summary}")
            result.summary = ""
            result.details.pop("timed_out", None)
            return False
        verdict_line = result.details["probe"]["verdict_line"] or ""
        if not verdict_line.startswith("Bad news:"):
            return False
        self._judge_probe(result, actual)
        return True

    def _judge_f3write(self, result: StepResult, totals: Totals) -> StepResult:
        """Judgment for f3write mode (done after umount). The core is capacity_judge.judge()."""
        entries = result.details["filesystems"]
        bad = {key: sum(entry[f"{key}_sectors"] for entry in entries) for key in _BAD_SECTOR_KEYS}
        # lost is f3read's Data LOST, already the total of the other three kinds.
        sectors = sum(entry["ok_sectors"] for entry in entries) + bad["lost"]
        advertised: int = result.details["advertised_bytes"]
        verdict = self._judge(
            result,
            actual=totals.used_bytes + totals.verified_bytes,
            bad_sectors=totals.bad_sectors,
            lost_ratio=bad["lost"] / sectors if sectors else None,
            probe_verdict_line=None,
            verified_bytes=totals.verified_bytes,
        )
        result.details.update(
            used_bytes=totals.used_bytes,
            verified_ratio=round(totals.verified_bytes / advertised, 4) if advertised else 0.0,
        )
        if verdict.kind == "defect":
            result.summary += (
                f" (corrupted {bad['corrupted']} / slightly changed {bad['slightly_changed']}"
                f" / overwritten {bad['overwritten']})"
            )
        return result

    def _judge_probe(self, result: StepResult, actual: int) -> StepResult:
        """Judgment for f3probe mode. There is no bad_sectors data, so defect is always False."""
        self._judge(
            result,
            actual=actual,
            bad_sectors=None,
            lost_ratio=None,
            probe_verdict_line=result.details["probe"]["verdict_line"],
            verified_bytes=None,
        )
        result.details.update(used_bytes=None, verified_ratio=None)
        return result

    def _judge(
        self,
        result: StepResult,
        *,
        actual: int,
        bad_sectors: int | None,
        lost_ratio: float | None,
        probe_verdict_line: str | None,
        verified_bytes: int | None,
    ) -> CapacityVerdict:
        """Call judge() and write status, summary, and details common to both modes to result."""
        details = result.details
        advertised: int = details["advertised_bytes"]
        nominal: int | None = details["nominal_bytes"]
        verdict = judge(
            actual=actual,
            advertised=advertised,
            nominal=nominal,
            pass_ratio=details["pass_ratio"],
            over_ratio=details["over_ratio"],
            bad_sectors=bad_sectors,
            lost_ratio=lost_ratio,
            probe_verdict_line=probe_verdict_line,
            verified_bytes=verified_bytes,
            threshold=details["threshold_bytes"],
        )
        details.update(
            actual_bytes=actual,
            verified_bytes=verified_bytes,
            ratio=round(actual / advertised, 4) if advertised else 0.0,
            bad_sectors=bad_sectors,
            defect=verdict.kind == "defect",
            kind=verdict.kind,
            counterfeit=verdict.counterfeit,
            counterfeit_reasons=list(verdict.counterfeit_reasons),
            lost_ratio=round(lost_ratio, 4) if lost_ratio is not None else None,
            advertised_ratio=round(advertised / nominal, 4) if nominal else None,
            probe_verdict_line=probe_verdict_line,
        )
        result.status = verdict.status
        result.summary = verdict.summary
        return verdict
