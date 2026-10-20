"""Parse result directory names, convert times, and summarize result.json (ResultSummary).

ResultStore in results.py scans the file system and matches it against the SQLite index.
Functions here open no files (except file_digest); they are pure functions of their inputs.
"""

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

from .models import ResultSummary
from .result_detail import parse_detail
from .sysfs import sanitize_text

# Result directory name: <timestamp>-<serial>[-<6 digits>].
DIR_NAME_RE = re.compile(r"^(\d{8}T\d{6}Z)-(.+?)(?:-(\d{6}))?$")
RESULT_FILE = "result.json"
# Values of result.json `mode` (backend InspectionMode). Anything else is treated as unknown.
MODES: frozenset[str] = frozenset({"scan_only", "full"})
# Steps that write to the drive, and the statuses meaning they did not run.
_DESTRUCTIVE_STEPS = frozenset({"capacity", "speed"})
_NOT_RUN = frozenset({"skipped", "not_implemented"})
# drive_tag() values; the UI maps them to mode.tag_<value>.
TAG_ERASED = "erased"
TAG_SCAN = "scan"


def parse_dir_name(name: str) -> tuple[str, str] | None:
    """Split a result directory name into timestamp and serial parts. None if malformed."""
    match = DIR_NAME_RE.match(name)
    if match is None:
        return None
    return str(match.group(1)), str(match.group(2))


def serial_for_dir(serial: str, kernel: str) -> str:
    """Convert a serial for directory names by the same rule as backend result_dir_for()."""
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", serial)[:128]
    return safe or f"{kernel}-unknown-serial"


def compact_utc(started_at: object, fallback: str) -> str:
    """Turn an ISO 8601 time into YYYYmmddTHHMMSSZ. On failure, the first 16 chars of fallback."""
    try:
        parsed = datetime.fromisoformat(started_at)  # ty: ignore[invalid-argument-type] -- wrong types are caught as TypeError
    except (ValueError, TypeError):
        return fallback[:16]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def iso_utc(ts: float) -> str:
    """Turn epoch seconds into ISO 8601 like backend started_at (UTC, with microseconds)."""
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="microseconds")


def started_at_lower_bound(ts: float) -> str:
    """Lower bound passed to the index's started_at string comparison: floored to seconds.

    backend isoformat() omits ".000000" when microseconds are 0, so a bound with microseconds
    drops same-second rows because '+' < '.'. Flooring to seconds keeps all same-second rows.
    """
    return datetime.fromtimestamp(int(ts // 1), UTC).isoformat(timespec="seconds")


def epoch_from_iso(text: str) -> float | None:
    """Turn an ISO 8601 time into epoch seconds. No tz means UTC. None if unparsable."""
    try:
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def file_digest(path: Path) -> str:
    """SHA-256 (lowercase hex) of the file bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _malware_count(steps: list[object]) -> int:
    """Number of detections in the malware step, or 0."""
    for step in steps:
        if isinstance(step, dict) and step.get("name") == "malware":
            details = step.get("details")
            detections = details.get("detections") if isinstance(details, dict) else None
            return len(detections) if isinstance(detections, list) else 0
    return 0


def parse_summary(
    data: object, *, dir_name: str, path: Path, mtime: float, digest: str
) -> ResultSummary | None:
    """Build a display summary from result.json contents. None if a required key is missing."""
    if not isinstance(data, dict):
        return None
    device = data.get("device")
    steps = data.get("steps")
    verdict = data.get("verdict")
    started_at = data.get("started_at")
    if (
        not isinstance(device, dict)
        or not isinstance(steps, list)
        or not isinstance(verdict, str)
        or not isinstance(started_at, str)
    ):
        return None
    kernel_name = device.get("kernel_name")
    serial = device.get("serial")
    if not isinstance(kernel_name, str) or not isinstance(serial, str):
        return None
    port_path = device.get("port_path")
    finished_at = data.get("finished_at")
    mode = data.get("mode")
    return ResultSummary(
        dir_name=dir_name,
        path=path,
        mtime=mtime,
        verdict=verdict,
        serial=sanitize_text(serial),
        vendor=sanitize_text(str(device.get("vendor", ""))),
        model=sanitize_text(str(device.get("model", ""))),
        kernel_name=kernel_name,
        port_path=port_path if isinstance(port_path, str) else None,
        started_at=started_at,
        finished_at=finished_at if isinstance(finished_at, str) else "",
        steps=tuple(
            (str(step["name"]), str(step.get("status", "")))
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("name"), str)
        ),
        malware_count=_malware_count(steps),
        digest=digest,
        detail=parse_detail(data),
        mode=mode if isinstance(mode, str) and mode in MODES else None,
    )


def drive_tag(summary: ResultSummary) -> str | None:
    """What the inspection did to the drive (design doc 3.9): "erased", "scan" or None.

    The step statuses are checked before the mode, so the tag stays right even if a destructive
    step ran in scan-only mode. An `error` counts as erased: f3/fio may have written part of
    the drive before failing.
    """
    for name, status in summary.steps:
        if name in _DESTRUCTIVE_STEPS and status not in _NOT_RUN:
            return TAG_ERASED
    return TAG_SCAN if summary.mode is not None else None
