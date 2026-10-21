from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

# result.json verdict of a ClamAV detection (backend Verdict.MALWARE_DETECTED).
MALWARE_VERDICT = "Malware Detected"


class SlotState(StrEnum):
    """Display state of a slot (design doc 3.4)."""

    EMPTY = "empty"
    WAITING = "waiting"
    INSPECTING = "inspecting"
    DONE = "done"
    NO_RESULT = "no_result"
    BROKEN = "broken"
    REMOVED = "removed"


@dataclass(frozen=True)
class AttachedDisk:
    """A connected USB disk read from sysfs."""

    kernel_name: str
    port_path: str
    serial: str
    attached_at: float


@dataclass(frozen=True)
class ResultDetail:
    """Inputs for the detail QR (v:3). Values missing from result.json are None.

    Capacities stay in bytes; MB conversion, rounding, and coding are done by qr_payload.
    `capacity_kind` None means "the capacity step is not PASS/FAIL and has no kind",
    which shows as `?` in the QR. `advertised_over` is None if `nominal_bytes` is unset.
    """

    elapsed_s: float | None = None
    clamav_db_version: str | None = None
    clamav_db_date: str | None = None  # kept as an ISO 8601 string
    nominal_bytes: int | None = None
    advertised_bytes: int | None = None
    actual_bytes: int | None = None
    advertised_over: bool | None = None
    capacity_kind: str | None = None
    lost_ratio: float | None = None
    counterfeit_reasons: tuple[str, ...] | None = None
    link_speed_mbps: int | None = None
    device_class: str | None = None
    read_mb_s: float | None = None
    write_mb_s: float | None = None
    threshold_read_mb_s: float | None = None
    threshold_write_mb_s: float | None = None
    speed_basis_read: str | None = None
    speed_basis_write: str | None = None
    speed_severity: str | None = None
    hash_digest: str | None = None
    hash_lines: int | None = None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResultSummary:
    """Only the fields of result.json needed for display."""

    dir_name: str
    path: Path
    mtime: float
    verdict: str
    serial: str
    vendor: str
    model: str
    kernel_name: str
    port_path: str | None
    started_at: str
    finished_at: str
    steps: tuple[tuple[str, str], ...]
    malware_count: int
    digest: str
    detail: ResultDetail = field(default_factory=ResultDetail)
    # Inspection mode recorded by the backend ("scan_only" or "full"); None for older results.
    mode: str | None = None


@dataclass(frozen=True)
class ResultRecord:
    """Read result for one result directory."""

    name: str
    mtime: float
    serial_part: str
    summary: ResultSummary | None
    broken: bool
    # Whether the record comes from an SQLite `state = 'started'` row (started, no final result).
    started: bool = False
    # Port path and kernel name of a started row, used for binding (when there is no result.json).
    port_path: str | None = None
    kernel_name: str | None = None


@dataclass(frozen=True)
class Slot:
    """A slot that corresponds to one tile on screen."""

    index: int
    label: str
    state: SlotState
    disk: AttachedDisk | None
    result: ResultSummary | None
    unit_state: str | None
    fallback: bool
    # Pre-removal kernel name shown by the fallback slot while REMOVED.
    retained_kernel: str | None = None


# Mapping from backend Verdict values (strings in result.json) to 1-char QR payload codes.
VERDICT_CODES: dict[str, str] = {
    "No Defect": "N",
    "No Defect (Scan Only)": "n",
    "Malware Detected": "M",
    "Counterfeit Suspected": "C",
    "Spec Mismatch": "S",
    "Defective": "D",
    "Inconclusive": "I",
}

# Short codes for step names. Names not in the table use their first 3 chars.
STEP_CODES: dict[str, str] = {
    "malware": "m",
    "hash_inventory": "h",
    "capacity": "c",
    "speed": "s",
    "unmount": "u",
}

# Mapping from backend StepStatus values to 1-char codes. Unknown values are "?".
STATUS_CODES: dict[str, str] = {
    "pass": "P",
    "fail": "F",
    "error": "E",
    "skipped": "S",
    "not_implemented": "N",
}

# Mapping from capacity kind (capacity.details.kind) to detail QR `ck` (doc/qr-payload.md 6.5).
# "?" when there is no kind (the step is not PASS/FAIL).
CAPACITY_KIND_CODES: dict[str, str] = {
    "ok": "-",
    "short": "S",
    "defect": "D",
    "counterfeit": "C",
    "unverified": "U",
}

# Mapping from device class (speed.details.device_class) to detail QR `dc` (doc/qr-payload.md 6.4).
DEVICE_CLASS_CODES: dict[str, str] = {
    "hdd_5400": "h5",
    "hdd_7200": "h7",
    "sata_ssd": "ss",
    "nvme_ssd": "ns",
    "sd_c10": "s1",
    "sd_u3": "s3",
    "usb2_flash": "u2",
    "usb3_flash": "u3",
}

# Mapping from speed verdict basis (basis_read) to detail QR `sb` (doc/qr-payload.md 6.7).
BASIS_CODES: dict[str, str] = {
    "nominal": "n",
    "class": "c",
    "none": "-",
}

# Mapping from speed severity to detail QR `sv`. "-" unless FAIL.
SEVERITY_CODES: dict[str, str] = {
    "severe": "s",
    "minor": "m",
}
