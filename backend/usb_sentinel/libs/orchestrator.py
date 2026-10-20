import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .device import block_info, ensure_safe, is_mounted, probe
from .hashing import inventory
from .models import DeviceInfo, InspectionMode, InspectionResult, StepResult, StepStatus, Verdict
from .mounter import mounted
from .runner import CommandRunner
from .steps import StepContext
from .steps.capacity import CapacityStep
from .steps.capacity_judge import precheck_advertised
from .steps.malware import MalwareScanStep
from .steps.speed import SpeedStep
from .store import InspectionStore, NullStore


def result_dir_for(root: Path, started: datetime, device: DeviceInfo) -> Path:
    """Decide the result directory `<root>/<YYYYmmddTHHMMSSZ>-<serial>`.

    In the serial, characters other than alphanumerics and `_.-` are replaced with `_`, and the
    result is cut to 128 characters. If empty, `<kernel_name>-unknown-serial` is used. Same rule
    as the frontend's `result_file.serial_for_dir()`.

    Args:
        root: Results root (config.results_dir).
        started: Inspection start time (UTC).
        device: Device information.

    Returns:
        Path of the result directory (not created).
    """
    serial = device.serial or f"{device.kernel_name}-unknown-serial"
    serial = re.sub(r"[^a-zA-Z0-9_.-]", "_", serial)[:128]
    return root / f"{started.strftime('%Y%m%dT%H%M%SZ')}-{serial}"


# Skip reasons of the capacity/speed gate in inspect() that mean "scan-only by design".
_SCAN_ONLY_DESTRUCTIVE_OFF = "allow_destructive = false"
_SCAN_ONLY_MODE = "Inspection mode is scan_only"
_SCAN_ONLY_SKIP_REASONS = (_SCAN_ONLY_DESTRUCTIVE_OFF, _SCAN_ONLY_MODE)
SCAN_ONLY_REASON = "Scan only: capacity and speed not tested"


def _is_scan_only_pass(result: InspectionResult) -> bool:
    """検査のみモードで、容量と速度以外がすべて PASS かを返す。

    Args:
        result: 判定対象の検査結果。

    Returns:
        PASS 以外のステップが、検査のみの理由で SKIPPED になった capacity と speed だけなら True。
    """
    if result.mode is not InspectionMode.SCAN_ONLY:
        return False
    malware = result.step("malware")
    if malware is None or malware.status is not StepStatus.PASS:
        return False
    return all(
        step.status is StepStatus.PASS
        or (
            step.status is StepStatus.SKIPPED
            and step.name in ("capacity", "speed")
            and step.summary in _SCAN_ONLY_SKIP_REASONS
        )
        for step in result.steps
    )


def classify(result: InspectionResult) -> tuple[Verdict, list[str]]:
    """Decide the verdict. Priority: Malware -> Counterfeit Suspected -> Defective ->
    Spec Mismatch -> No Defect -> No Defect (Scan Only) -> Inconclusive.

    No Defect (Scan Only) is returned only when the effective mode is scan-only, malware and
    every other step passed, and capacity/speed were skipped solely by the scan-only gate."""
    malware = result.step("malware")
    if malware and malware.status == StepStatus.FAIL:
        return Verdict.MALWARE_DETECTED, [
            f"Malware detections: {len(malware.details.get('detections', []))}"
        ]
    failures = [step for step in result.steps if step.status == StepStatus.FAIL]
    reasons = [f"{step.name}: {step.summary}" for step in failures]
    # An excess found by the non-destructive check (reported capacity comparison before
    # mounting) also counts as suspected counterfeit.
    over_advertised = result.advertised_check.get("exceeded") is True
    if over_advertised:
        reasons.insert(0, result.advertised_check["summary"])
    if over_advertised or any(step.details.get("counterfeit") is True for step in failures):
        return Verdict.COUNTERFEIT_SUSPECTED, reasons
    if any(step.details.get("defect") is True for step in failures):
        # Capacity matches the report but data gets corrupted (f3read reports non-OK sectors).
        return Verdict.DEFECTIVE, reasons
    if failures:
        return Verdict.SPEC_MISMATCH, reasons
    if result.steps and all(step.status == StepStatus.PASS for step in result.steps):
        return Verdict.NO_DEFECT, ["All steps passed"]
    if _is_scan_only_pass(result):
        return Verdict.NO_DEFECT_SCAN_ONLY, [SCAN_ONLY_REASON]
    return Verdict.INCONCLUSIVE, [
        f"{step.name}: {step.summary}" for step in result.steps if step.status != StepStatus.PASS
    ]


# Step abbreviations for reason_codes (same as st in the on-screen QR code).
_STEP_ABBR = {"malware": "m", "hash_inventory": "h", "capacity": "c", "speed": "s", "unmount": "u"}
# Capacity step counterfeit_reasons -> codes.
_COUNTERFEIT_CODES = {
    "over_advertised": "c:over",
    "over_actual": "c:over",
    "probe_bad_news": "c:probe",
    "lost_ratio": "c:lost",
}


def _fail_codes(step: StepResult) -> list[str]:
    """Reason codes for a FAIL step."""
    if step.name == "malware":
        return ["m:found"]
    if step.name == "capacity":
        codes = [
            _COUNTERFEIT_CODES[reason] for reason in step.details.get("counterfeit_reasons", [])
        ]
        codes += {"short": ["c:short"], "defect": ["c:defect"]}.get(step.details.get("kind"), [])
        return codes
    if step.name == "speed":
        # severity is set by the severity judgment in speed.py. No code if absent.
        severity = step.details.get("severity")
        return [f"s:{severity}"] if severity in ("severe", "minor") else []
    return []


def reason_codes_for(result: InspectionResult) -> list[str]:
    """Build machine-readable reason codes from each step's status and details.

    Independent of classify(). The format is "<step abbreviation>:<code>". c:over is added only
    once even if both the non-destructive check and the step trigger it. Codes follow the order
    of result.steps.
    """
    codes: list[str] = []
    if result.advertised_check.get("exceeded") is True:
        codes.append("c:over")
    for step in result.steps:
        if step.status == StepStatus.PASS:
            continue
        if step.status == StepStatus.FAIL:
            candidates = _fail_codes(step)
        else:
            abbr = _STEP_ABBR.get(step.name, step.name[:1])
            timed_out = step.details.get("timed_out") is True
            candidates = [f"{abbr}:timeout" if timed_out else f"{abbr}:{step.status.value}"]
        for code in candidates:
            if code == "c:over" and code in codes:
                continue
            codes.append(code)
    return codes


def scan_executed(malware: StepResult) -> bool:
    """Whether clamscan ran a scan (detections is set only after a run)."""
    return "detections" in malware.details


def _timed(run: Callable[[StepContext], StepResult], ctx: StepContext) -> StepResult:
    """Run the step and return it with its duration (seconds, 2 decimals) in elapsed_s."""
    started = time.monotonic()
    result = run(ctx)
    result.elapsed_s = round(time.monotonic() - started, 2)
    return result


def inspect(
    runner: CommandRunner,
    config: Config,
    path: str,
    store: InspectionStore | None = None,
    mode: InspectionMode = InspectionMode.SCAN_ONLY,
) -> InspectionResult:
    """Drive the whole inspection sequence.

    probe -> safety guard -> ro mount -> malware -> inventory -> destructive tests -> verdict.

    When malware is detected, destructive tests (f3, fio) are not run. The condition `permitted`
    for running destructive tests (all steps PASS, allow_destructive, inspection mode full, and
    unmounted) is a safety gate and must not be relaxed. The hash inventory is built only after
    clamscan has run a scan.

    Args:
        runner: Command runner.
        config: Configuration. `allow_destructive` is the ceiling: when false, the effective
            mode is scan_only whatever `mode` says.
        path: Path of the target device.
        store: Destination for the start record. None disables recording (NullStore).
        mode: Requested inspection mode (from `--mode`). Default scan_only.

    Returns:
        Inspection result with verdict, reason codes, and duration filled in.

    Raises:
        UnsafeDeviceError: For a device that fails the safety guard (no mount, no scan).
    """
    if store is None:
        store = NullStore()
    device = probe(runner, path)
    ensure_safe(runner, device)
    started = datetime.now(UTC)
    output = result_dir_for(config.results_dir, started, device)
    # Repeated inspections must never overwrite previous evidence.
    if output.exists():
        output = output.with_name(f"{output.name}-{started.strftime('%f')}")
    output.mkdir(parents=True)
    result = InspectionResult(device, started.isoformat(), output_dir=str(output))
    # The host ceiling narrows the requested mode; the start record already sees the result.
    result.mode = mode if config.allow_destructive else InspectionMode.SCAN_ONLY
    # Non-destructive check of the reported capacity. Uses only the lsblk values already read
    # by probe() and does not touch the device. Not added to result.steps (so it does not
    # affect permitted or the number of digits of st in the on-screen QR code).
    result.advertised_check = precheck_advertised(
        device.advertised_bytes, config.capacity.nominal_bytes, config.capacity.over_ratio
    )
    # Start record. The inspection continues on failure (store never raises; it returns a
    # reason string).
    failure = store.record_started(result)
    if failure:
        result.warnings.append(f"SQLite record failed (start): {failure}")
    ctx = StepContext(runner, config, device, output)
    try:
        with mounted(ctx):
            malware = _timed(MalwareScanStep().run, ctx)
            result.steps.append(malware)
            # Build the inventory only after clamscan has opened the files (SOP: do not open
            # files before the scan). After a scan, build it regardless of the result (PASS,
            # FAIL, ERROR, or timeout).
            if not config.hash_inventory:
                result.steps.append(
                    StepResult(
                        "hash_inventory", StepStatus.SKIPPED, "Disabled (hash_inventory = false)"
                    )
                )
            elif not scan_executed(malware):
                result.steps.append(
                    StepResult(
                        "hash_inventory", StepStatus.SKIPPED, "Not created because no scan ran"
                    )
                )
            else:
                result.steps.append(_timed(inventory, ctx))
    except RuntimeError as exc:
        if not result.steps:
            result.steps.append(StepResult("malware", StepStatus.ERROR, f"Mount failed: {exc}"))
        else:
            result.steps.append(StepResult("unmount", StepStatus.ERROR, str(exc)))
        if result.step("hash_inventory") is None:
            result.steps.append(StepResult("hash_inventory", StepStatus.SKIPPED, "Mount failed"))
    permitted = all(
        step.status == StepStatus.PASS
        or (step.name == "hash_inventory" and not config.hash_inventory)
        for step in result.steps
    )
    reason = "Scan and hashing not completed"
    if not config.allow_destructive:
        permitted, reason = False, _SCAN_ONLY_DESTRUCTIVE_OFF
    elif result.mode is not InspectionMode.FULL:
        permitted, reason = False, _SCAN_ONLY_MODE
    elif is_mounted(block_info(runner, path)):
        permitted, reason = False, "Still mounted"
    for name, step in (("capacity", CapacityStep()), ("speed", SpeedStep())):
        result.steps.append(
            _timed(step.run, ctx) if permitted else StepResult(name, StepStatus.SKIPPED, reason)
        )
    result.verdict, result.reasons = classify(result)
    result.reason_codes = reason_codes_for(result)
    now = datetime.now(UTC)
    result.finished_at = now.isoformat()
    result.elapsed_s = round((now - started).total_seconds(), 2)
    return result
