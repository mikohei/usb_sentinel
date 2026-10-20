from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StepStatus(StrEnum):
    PASS = "pass"  # noqa: S105 -- inspection status, not a credential
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"
    NOT_IMPLEMENTED = "not_implemented"


class InspectionMode(StrEnum):
    """Mode an inspection ran in after the host ceiling (allow_destructive).

    SCAN_ONLY: malware scan and hash inventory only; FULL: destructive capacity and speed steps
    were allowed for this drive (whether they ran is shown by the step statuses).
    """

    SCAN_ONLY = "scan_only"
    FULL = "full"


class Verdict(StrEnum):
    NO_DEFECT = "No Defect"
    MALWARE_DETECTED = "Malware Detected"
    COUNTERFEIT_SUSPECTED = "Counterfeit Suspected"
    SPEC_MISMATCH = "Spec Mismatch"
    DEFECTIVE = "Defective"
    INCONCLUSIVE = "Inconclusive"


@dataclass
class DeviceInfo:
    path: str
    kernel_name: str
    advertised_bytes: int
    vendor: str = ""
    model: str = ""
    serial: str = ""
    removable: bool = False
    bus: str | None = None
    port_path: str | None = None
    # USB link speed (Mbps). None if it cannot be read from sysfs.
    link_speed_mbps: int | None = None


@dataclass
class StepResult:
    name: str
    status: StepStatus
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Step duration (seconds, 2 decimal places). None for results built as SKIPPED.
    elapsed_s: float | None = None


@dataclass
class InspectionResult:
    device: DeviceInfo
    started_at: str
    finished_at: str = ""
    verdict: Verdict = Verdict.INCONCLUSIVE
    # Effective inspection mode (requested mode narrowed by allow_destructive).
    mode: InspectionMode = InspectionMode.SCAN_ONLY
    steps: list[StepResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    output_dir: str = ""
    # Total inspection duration (seconds, 2 decimal places). Set when finished_at is fixed.
    elapsed_s: float | None = None
    # Machine-readable reason codes (e.g. "m:found"). Built independently of reasons.
    reason_codes: list[str] = field(default_factory=list)
    # Overall warnings not tied to a step (e.g. SQLite recording failure).
    warnings: list[str] = field(default_factory=list)
    # Non-destructive comparison of reported vs. nominal capacity. Empty dict if not run.
    advertised_check: dict[str, Any] = field(default_factory=dict)

    def step(self, name: str) -> StepResult | None:
        """Return the first step result whose name matches, or None if there is none."""
        return next((step for step in self.steps if step.name == name), None)
