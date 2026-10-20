"""Pure functions that build one inspections row from the same values as result.json.

Design doc section 5. SQLite is not touched. Every column value is read from InspectionResult
(the source of result.json). New details keys are read with .get() and become None when absent,
such as on ERROR paths.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..models import InspectionResult, StepResult, Verdict

VERDICT_CODES: dict[Verdict, str] = {
    Verdict.NO_DEFECT: "N",
    Verdict.NO_DEFECT_SCAN_ONLY: "n",
    Verdict.MALWARE_DETECTED: "M",
    Verdict.COUNTERFEIT_SUSPECTED: "C",
    Verdict.SPEC_MISMATCH: "S",
    Verdict.DEFECTIVE: "D",
    Verdict.INCONCLUSIVE: "I",
}

STEP_NAMES = ("malware", "hash_inventory", "capacity", "speed", "unmount")


def _details(result: InspectionResult, name: str) -> dict[str, Any]:
    """The step's details. Empty dict if the step does not exist."""
    step = result.step(name)
    return step.details if step else {}


def _nested(details: dict[str, Any], key: str) -> dict[str, Any]:
    """Nested dict in details. Empty dict if absent or not a dict."""
    value = details.get(key)
    return value if isinstance(value, dict) else {}


def _json(values: list[str]) -> str:
    """Encode as a JSON array string (non-ASCII is not escaped)."""
    return json.dumps(values, ensure_ascii=False)


def _status(step: StepResult | None) -> str | None:
    """The step's status string (value of a `step_*` column). None if the step does not exist."""
    return step.status.value if step else None


def _counterfeit_reasons(result: InspectionResult, capacity: dict[str, Any]) -> list[str]:
    """Order-preserving union of the non-destructive "over_advertised" and the step's
    counterfeit_reasons."""
    reasons: list[str] = []
    if result.advertised_check.get("exceeded") is True:
        reasons.append("over_advertised")
    for reason in capacity.get("counterfeit_reasons", []):
        if reason not in reasons:
            reasons.append(reason)
    return reasons


def _started_compact(started_at: str) -> str:
    """Same %Y%m%dT%H%M%SZ format as result_dir_for()."""
    return datetime.fromisoformat(started_at).strftime("%Y%m%dT%H%M%SZ")


def row_from_result(result: InspectionResult, digest: str | None) -> dict[str, Any]:
    """Pure function that builds a dict of all columns from the same values as result.json.

    Args:
        result: Inspection result.
        digest: SHA-256 of result.json (None at start).

    Returns:
        Dict of column name -> value. New details keys are read with .get(); None if absent.
    """
    device = result.device
    malware = _details(result, "malware")
    database = _nested(malware, "database")
    scan_summary = _nested(malware, "scan_summary")
    capacity = _details(result, "capacity")
    speed = _details(result, "speed")
    hash_inventory = _details(result, "hash_inventory")
    db_date = database.get("db_date")
    warnings = [w for step in result.steps for w in step.warnings] + list(result.warnings)
    now = datetime.now(UTC).isoformat()
    return {
        "inspection_id": Path(result.output_dir).name,
        "state": "completed",
        "verdict": result.verdict.value,
        "verdict_code": VERDICT_CODES[result.verdict],
        "mode": result.mode.value,
        "serial": device.serial,
        "kernel_name": device.kernel_name,
        "port_path": device.port_path,
        "vendor": device.vendor,
        "model": device.model,
        "started_at": result.started_at,
        "started_compact": _started_compact(result.started_at),
        "finished_at": result.finished_at or None,
        "elapsed_s": result.elapsed_s,
        "result_digest": digest,
        **{f"step_{name}": _status(result.step(name)) for name in STEP_NAMES},
        "malware_count": len(malware.get("detections", [])),
        "clamav_db_version": database.get("db_version"),
        "clamav_db_date": db_date[:10] if isinstance(db_date, str) else None,
        "clamav_scanned_files": scan_summary.get("scanned_files"),
        "clamav_scan_time_s": scan_summary.get("time_s"),
        "nominal_bytes": result.advertised_check.get("nominal_bytes"),
        "advertised_bytes": device.advertised_bytes,
        "actual_bytes": capacity.get("actual_bytes"),
        "advertised_over": int(result.advertised_check.get("exceeded") is True),
        "capacity_kind": capacity.get("kind"),
        "counterfeit_reasons": _json(_counterfeit_reasons(result, capacity)),
        "lost_ratio": capacity.get("lost_ratio"),
        "probe_verdict_line": capacity.get("probe_verdict_line"),
        "link_speed_mbps": device.link_speed_mbps,
        "device_class": speed.get("device_class"),
        "device_class_basis": speed.get("device_class_basis"),
        "read_mb_s": _nested(speed, "read").get("mb_s"),
        "write_mb_s": _nested(speed, "write").get("mb_s"),
        "threshold_read_mb_s": speed.get("threshold_read_mb_s"),
        "threshold_write_mb_s": speed.get("threshold_write_mb_s"),
        "speed_basis_read": speed.get("basis_read"),
        "speed_basis_write": speed.get("basis_write"),
        "speed_severity": speed.get("severity"),
        "reason_codes": _json(list(result.reason_codes)),
        "reasons": _json(list(result.reasons)),
        "hash_inventory_digest": hash_inventory.get("digest"),
        "hash_inventory_lines": hash_inventory.get("lines"),
        "output_dir": result.output_dir,
        "warnings": _json(warnings),
        "created_at": now,
        "updated_at": now,
    }
