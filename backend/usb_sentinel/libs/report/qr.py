"""Payload v:3 for the file QR code (result-qr.png) (design doc section 6).

All v:1 keys are kept, and material for human judgment is added: capacity, speed, inventory,
reason codes, and so on. `v:2` is used by the regular on-screen QR code (frontend), so the file
QR code uses `3`. New keys in a step's `details` may be missing on ERROR paths or for SKIPPED,
so always read them with `.get()` and never raise `KeyError`. The spec is in
`doc/qr-payload.md`.
"""

import json
from typing import Any

from ..models import InspectionResult, StepResult, StepStatus
from .json_report import digest

# Limit for QR byte mode (version 40, error correction L).
MAX_PAYLOAD_BYTES = 2953
# Constants for the truncation order (design doc section 6.3).
SIGNATURE_LIMIT = 3
REASON_CHARS = 120
SERIAL_CHARS = 128
ELLIPSIS = "…"

_CAPACITY_KEYS = (
    "nominal_bytes",
    "advertised_bytes",
    "actual_bytes",
    "kind",
    "lost_ratio",
    "counterfeit_reasons",
    "probe_verdict_line",
)
_SPEED_KEYS = (
    "threshold_read_mb_s",
    "threshold_write_mb_s",
    "basis_read",
    "basis_write",
    "severity",
)


def _encode(payload: dict[str, Any]) -> str:
    """Encode as compact UTF-8 JSON without separator spaces (non-ASCII is not escaped)."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _size(payload: dict[str, Any]) -> int:
    """Size in bytes as UTF-8."""
    return len(_encode(payload).encode("utf-8"))


def _mb_s(value: object) -> float | None:
    """Extract `mb_s` from the `details.read` / `details.write` dict. None if not a dict."""
    if isinstance(value, dict):
        mb_s = value.get("mb_s")
        return mb_s if isinstance(mb_s, int | float) else None
    return None


def _capacity_payload(step: StepResult | None) -> dict[str, Any] | None:
    """Build the dict only when the capacity step is PASS or FAIL. None otherwise."""
    if step is None or step.status not in (StepStatus.PASS, StepStatus.FAIL):
        return None
    return {key: step.details.get(key) for key in _CAPACITY_KEYS}


def _speed_payload(step: StepResult | None) -> dict[str, Any] | None:
    """Build the dict only when the speed step's `details` has `read`. None otherwise."""
    if step is None or "read" not in step.details:
        return None
    payload: dict[str, Any] = {
        "read_mb_s": _mb_s(step.details.get("read")),
        "write_mb_s": _mb_s(step.details.get("write")),
    }
    payload.update({key: step.details.get(key) for key in _SPEED_KEYS})
    return payload


def _clamav_db_date(database: dict[str, Any]) -> str | None:
    """First 10 characters (YYYY-MM-DD) of `db_date` (ISO 8601). None if absent."""
    db_date = database.get("db_date")
    return db_date[:10] if isinstance(db_date, str) else None


def _clip_reason(reason: str) -> str:
    """Cut a reason to 120 characters, appending "…" when cut."""
    if len(reason) <= REASON_CHARS:
        return reason
    return reason[:REASON_CHARS] + ELLIPSIS


def _shrink(payload: dict[str, Any], serial: str) -> None:
    """Shrink in the order of design doc section 6.3. Recompute after each stage and stop
    once within the limit.

    Raises:
        ValueError: If the limit is still exceeded after applying every stage.
    """

    def over() -> bool:
        """Whether the current payload exceeds the limit."""
        return _size(payload) > MAX_PAYLOAD_BYTES

    if not over():
        return
    payload["truncated"] = True
    # 1. Cut signatures to 3.
    payload["signatures"] = payload["signatures"][:SIGNATURE_LIMIT]
    # 2. Cut each reason to 120 characters.
    if over():
        payload["reasons"] = [_clip_reason(reason) for reason in payload["reasons"]]
    # 3. Remove signatures one at a time, down to 0.
    while over() and payload["signatures"]:
        payload["signatures"].pop()
    # 4. Empty reasons (reason_codes is kept).
    if over():
        payload["reasons"] = []
    # 5. Cut the serial to 128 characters.
    if over():
        payload["serial"] = serial[:SERIAL_CHARS]
    # 6. Drop capacity.probe_verdict_line.
    if over() and isinstance(payload.get("capacity"), dict):
        payload["capacity"]["probe_verdict_line"] = None
    # 7. If still too large, the caller (write_all) ends with exit code 3.
    if over():
        raise ValueError("QR payload too large")


def build_payload(result: InspectionResult) -> str:
    """Build the file QR code payload (v:3).

    Args:
        result: Inspection result. Its `digest()` is taken, so it matches the result.json written
            by `write_all()`.

    Returns:
        JSON string of at most 2953 bytes (UTF-8, no separator spaces).

    Raises:
        ValueError: If the limit is still exceeded after applying the full truncation order.
    """
    malware = result.step("malware")
    details = malware.details if malware else {}
    detections = details.get("detections", [])
    database = details.get("database", {})
    hash_inventory = result.step("hash_inventory")
    hash_details = hash_inventory.details if hash_inventory else {}
    speed = result.step("speed")
    payload: dict[str, Any] = {
        "v": 3,
        "verdict": result.verdict,
        "serial": result.device.serial,
        "kernel_name": result.device.kernel_name,
        "port_path": result.device.port_path,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "elapsed_s": result.elapsed_s,
        "steps": {step.name: step.status for step in result.steps},
        "clamav_db": database.get("db_version"),
        "clamav_db_date": _clamav_db_date(database),
        "digest": digest(result),
        "malware_count": len(detections),
        "signatures": list(dict.fromkeys(detection["signature"] for detection in detections)),
        "advertised_over": result.advertised_check.get("exceeded") is True,
        "capacity": _capacity_payload(result.step("capacity")),
        "link_speed_mbps": result.device.link_speed_mbps,
        "device_class": speed.details.get("device_class") if speed else None,
        "speed": _speed_payload(speed),
        "reason_codes": list(result.reason_codes),
        "reasons": list(result.reasons),
        "hash_inventory_digest": hash_details.get("digest"),
        "hash_inventory_lines": hash_details.get("lines"),
        "truncated": False,
    }
    _shrink(payload, result.device.serial)
    return _encode(payload)
