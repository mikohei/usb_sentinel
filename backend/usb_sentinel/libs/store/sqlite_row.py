"""result.jsonと同じ値からinspectionsの1行を組む純関数（設計書第5節）。SQLiteには触らない。

列の値はすべてInspectionResult（result.jsonの元）から読む。detailsの新キーは.get()で読み、
ERROR経路などで無ければNoneにする。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..models import InspectionResult, StepResult, Verdict

VERDICT_CODES: dict[Verdict, str] = {
    Verdict.NO_DEFECT: "N",
    Verdict.MALWARE_DETECTED: "M",
    Verdict.COUNTERFEIT_SUSPECTED: "C",
    Verdict.SPEC_MISMATCH: "S",
    Verdict.DEFECTIVE: "D",
    Verdict.INCONCLUSIVE: "I",
}

STEP_NAMES = ("malware", "hash_inventory", "capacity", "speed", "unmount")


def _details(result: InspectionResult, name: str) -> dict[str, Any]:
    """ステップのdetails。ステップが無ければ空の辞書。"""
    step = result.step(name)
    return step.details if step else {}


def _nested(details: dict[str, Any], key: str) -> dict[str, Any]:
    """detailsの入れ子辞書。無いか辞書でなければ空の辞書。"""
    value = details.get(key)
    return value if isinstance(value, dict) else {}


def _json(values: list[str]) -> str:
    """JSON配列の文字列にする（日本語はエスケープしない）。"""
    return json.dumps(values, ensure_ascii=False)


def _status(step: StepResult | None) -> str | None:
    """ステップのstatus文字列（`step_*`列の値）。ステップが無ければNone。"""
    return step.status.value if step else None


def _counterfeit_reasons(result: InspectionResult, capacity: dict[str, Any]) -> list[str]:
    """非破壊チェックの"over_advertised"とステップのcounterfeit_reasonsの順序保持和集合。"""
    reasons: list[str] = []
    if result.advertised_check.get("exceeded") is True:
        reasons.append("over_advertised")
    for reason in capacity.get("counterfeit_reasons", []):
        if reason not in reasons:
            reasons.append(reason)
    return reasons


def _started_compact(started_at: str) -> str:
    """result_dir_for()と同じ%Y%m%dT%H%M%SZ形式。"""
    return datetime.fromisoformat(started_at).strftime("%Y%m%dT%H%M%SZ")


def row_from_result(result: InspectionResult, digest: str | None) -> dict[str, Any]:
    """result.jsonと同じ値から全列の辞書を組む純関数。

    Args:
        result: 検査結果。
        digest: result.jsonのSHA-256（開始時はNone）。

    Returns:
        列名→値の辞書。detailsの新キーは.get()で読み、無ければNone。
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
