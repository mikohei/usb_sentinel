import json
import re

from .models import (
    BASIS_CODES,
    CAPACITY_KIND_CODES,
    DEVICE_CLASS_CODES,
    SEVERITY_CODES,
    STATUS_CODES,
    STEP_CODES,
    VERDICT_CODES,
    ResultSummary,
)
from .result_file import compact_utc
from .sysfs import sanitize_text

# Limits to fit QR version 8-M (152 bytes) (design doc 2.1).
MAX_PAYLOAD_BYTES = 150
SERIAL_LEN = 32
SHORT_SERIAL_LEN = 16
# Detail QR (v:3, doc/qr-payload.md section 5). Always 3 pages; header d is 12 chars.
DETAIL_PAGES = 3
DETAIL_DIGEST_LEN = 12
MB = 10**6
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _ascii_serial(serial: str) -> str:
    """Make the display-sanitized serial ASCII (non-ASCII becomes ?)."""
    return sanitize_text(serial).encode("ascii", "replace").decode("ascii")


def _encode(payload: dict[str, object]) -> str:
    """Serialize to ASCII JSON with compact separators."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def build_payload(summary: ResultSummary) -> str:
    """Build the on-screen QR payload (v:2, design doc 2.2).

    Args:
        summary: Result summary.

    Returns:
        ASCII JSON string of 150 bytes or less.

    Raises:
        ValueError: When it exceeds 150 bytes even after cutting s to 16 chars and dropping st.
    """
    serial = _ascii_serial(summary.serial)[:SERIAL_LEN]
    payload: dict[str, object] = {"v": 2, "r": VERDICT_CODES.get(summary.verdict, "?")}
    if serial:
        payload["s"] = serial
    else:
        payload["s"] = summary.kernel_name
        payload["k"] = 1
    payload["t"] = compact_utc(summary.started_at, summary.dir_name)
    payload["d"] = summary.digest[:16]
    payload["st"] = ",".join(
        f"{STEP_CODES.get(name, name[:3])}:{STATUS_CODES.get(status, '?')}"
        for name, status in summary.steps
    )
    payload["n"] = summary.malware_count
    text = _encode(payload)
    if len(text) > MAX_PAYLOAD_BYTES and serial:
        payload["s"] = serial[:SHORT_SERIAL_LEN]
        text = _encode(payload)
    if len(text) > MAX_PAYLOAD_BYTES:
        del payload["st"]
        text = _encode(payload)
    if len(text) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"QR payload too large: {len(text)} bytes")
    return text


def _mb(value: int | None) -> int | None:
    """Convert bytes to MB (10^6, truncated). None passes through."""
    return None if value is None else value // MB


def _round(value: float | None, digits: int) -> float | None:
    """Round a float to digits places. None passes through."""
    return None if value is None else round(value, digits)


def _db_yyyymmdd(db_date: str | None) -> str | None:
    """Turn an ISO 8601 date (first 10 chars YYYY-MM-DD) into 8 digits. None if malformed."""
    if db_date is None or _DATE_RE.fullmatch(db_date[:10]) is None:
        return None
    return db_date[:10].replace("-", "")


def _db_version_int(text: str | None) -> int | None:
    """Turn a ClamAV DB version string into an int. None if it cannot be converted."""
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _identity(summary: ResultSummary) -> tuple[str, bool]:
    """Return the string for the QR `s` and whether it is a kernel name (whether to add `k`)."""
    serial = _ascii_serial(summary.serial)[:SERIAL_LEN]
    return (serial, False) if serial else (summary.kernel_name, True)


def _detail_page1(summary: ResultSummary) -> dict[str, object]:
    """Body of page 1 (identity and verdict, doc/qr-payload.md 5.2)."""
    serial, is_kernel = _identity(summary)
    body: dict[str, object] = {"t": compact_utc(summary.started_at, summary.dir_name), "s": serial}
    if is_kernel:
        body["k"] = 1
    detail = summary.detail
    body["r"] = VERDICT_CODES.get(summary.verdict, "?")
    body["e"] = None if detail.elapsed_s is None else round(detail.elapsed_s)
    body["m"] = summary.malware_count
    body["dv"] = _db_version_int(detail.clamav_db_version)
    return body


def _detail_page2(summary: ResultSummary) -> dict[str, object]:
    """Body of page 2 (capacity and device, doc/qr-payload.md 5.3)."""
    detail = summary.detail
    kind = detail.capacity_kind
    over = detail.advertised_over
    return {
        "cn": _mb(detail.nominal_bytes),
        "ca": _mb(detail.advertised_bytes),
        "cx": _mb(detail.actual_bytes),
        "ck": "?" if kind is None else CAPACITY_KIND_CODES.get(kind, "?"),
        "co": None if detail.nominal_bytes is None or over is None else int(over),
        "cl": _round(detail.lost_ratio, 4),
        "ls": detail.link_speed_mbps,
        "dc": None if detail.device_class is None else DEVICE_CLASS_CODES.get(detail.device_class),
        "hn": detail.hash_lines,
    }


def _detail_page3(summary: ResultSummary) -> dict[str, object]:
    """Body of page 3 (speed, DB, and hash inventory, doc/qr-payload.md 5.4)."""
    detail = summary.detail
    return {
        "sr": _round(detail.read_mb_s, 1),
        "sw": _round(detail.write_mb_s, 1),
        "tr": _round(detail.threshold_read_mb_s, 1),
        "tw": _round(detail.threshold_write_mb_s, 1),
        "sb": BASIS_CODES.get(detail.speed_basis_read or "", "-"),
        "sv": SEVERITY_CODES.get(detail.speed_severity or "", "-"),
        "db": _db_yyyymmdd(detail.clamav_db_date),
        "hd": None if detail.hash_digest is None else detail.hash_digest[:16],
    }


def build_detail_page(summary: ResultSummary, page: int) -> str:
    """Build the payload for one detail QR (v:3) page (doc/qr-payload.md section 5).

    Args:
        summary: Result summary (its detail holds the inputs).
        page: 1〜3。

    Returns:
        ASCII JSON string of 150 bytes or less. Missing values are written as null and keys are
        never omitted (except `k` on page 1).

    Raises:
        ValueError: If page is not 1-3, or it exceeds 150 bytes (page 1: even after cutting s).
    """
    if page not in range(1, DETAIL_PAGES + 1):
        raise ValueError(f"detail page out of range: {page}")
    payload: dict[str, object] = {
        "v": 3,
        "p": f"{page}/{DETAIL_PAGES}",
        "d": summary.digest[:DETAIL_DIGEST_LEN],
    }
    if page == 1:
        payload.update(_detail_page1(summary))
    elif page == 2:
        payload.update(_detail_page2(summary))
    else:
        payload.update(_detail_page3(summary))
    text = _encode(payload)
    if len(text) > MAX_PAYLOAD_BYTES and page == 1 and "k" not in payload:
        payload["s"] = str(payload["s"])[:SHORT_SERIAL_LEN]
        text = _encode(payload)
    if len(text) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"QR detail payload too large: page {page}, {len(text)} bytes")
    return text


def build_detail_pages(summary: ResultSummary) -> list[str]:
    """Build all 3 detail QR pages. ValueError if any page exceeds the limit."""
    return [build_detail_page(summary, page) for page in range(1, DETAIL_PAGES + 1)]
