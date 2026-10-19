from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StepStatus(StrEnum):
    PASS = "pass"  # noqa: S105 -- inspection status, not a credential
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"
    NOT_IMPLEMENTED = "not_implemented"


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
    # USBリンク速度（Mbps）。sysfsから読めなければNone。
    link_speed_mbps: int | None = None


@dataclass
class StepResult:
    name: str
    status: StepStatus
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # ステップの所要時間（秒、小数2桁）。SKIPPEDとして組み立てたものはNone。
    elapsed_s: float | None = None


@dataclass
class InspectionResult:
    device: DeviceInfo
    started_at: str
    finished_at: str = ""
    verdict: Verdict = Verdict.INCONCLUSIVE
    steps: list[StepResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    output_dir: str = ""
    # 検査全体の所要時間（秒、小数2桁）。finished_at確定時に入る。
    elapsed_s: float | None = None
    # 機械可読の理由コード（例: "m:found"）。reasonsとは独立に組む。
    reason_codes: list[str] = field(default_factory=list)
    # ステップに属さない全体の警告（SQLite記録失敗など）。
    warnings: list[str] = field(default_factory=list)
    # 申告容量と公称容量の非破壊比較。未実施なら空の辞書。
    advertised_check: dict[str, Any] = field(default_factory=dict)

    def step(self, name: str) -> StepResult | None:
        """名前が一致する最初のステップ結果。無ければNone。"""
        return next((step for step in self.steps if step.name == name), None)
