from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class SlotState(StrEnum):
    """スロットの表示状態（設計書3.4）。"""

    EMPTY = "empty"
    WAITING = "waiting"
    INSPECTING = "inspecting"
    DONE = "done"
    NO_RESULT = "no_result"
    BROKEN = "broken"
    REMOVED = "removed"


@dataclass(frozen=True)
class AttachedDisk:
    """sysfsから読んだ接続中のUSBディスク。"""

    kernel_name: str
    port_path: str
    serial: str
    attached_at: float


@dataclass(frozen=True)
class ResultDetail:
    """詳細QR（v:3）の材料。result.jsonに無い値はNone。

    容量はバイト単位のまま持ち、MB換算や丸め、コード化はQR生成側（qr_payload）で行う。
    `capacity_kind`がNoneのときは「容量ステップがPASS／FAIL以外でkindが無い」を意味し、
    QRでは`?`になる。`advertised_over`は`nominal_bytes`が未設定ならNone。
    """

    elapsed_s: float | None = None
    clamav_db_version: str | None = None
    clamav_db_date: str | None = None  # ISO 8601の文字列のまま
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
    """result.jsonから表示に必要な項目だけを取り出したもの。"""

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


@dataclass(frozen=True)
class ResultRecord:
    """結果ディレクトリ1件分の読み取り結果。"""

    name: str
    mtime: float
    serial_part: str
    summary: ResultSummary | None
    broken: bool
    # SQLiteの`state = 'started'`行（開始したが完了結果が無い）に由来するレコードか。
    started: bool = False
    # started行が持つ結び付け用のポートパスとカーネル名（result.jsonが無いときに使う）。
    port_path: str | None = None
    kernel_name: str | None = None


@dataclass(frozen=True)
class Slot:
    """画面のタイル1枚に対応するスロット。"""

    index: int
    label: str
    state: SlotState
    disk: AttachedDisk | None
    result: ResultSummary | None
    unit_state: str | None
    fallback: bool
    # 抜去（REMOVED）中のフォールバックスロットが表示に使う、抜去前のカーネル名。
    retained_kernel: str | None = None


# backendのVerdictの値（result.jsonの文字列）とQRペイロードの1文字コードの対応。
VERDICT_CODES: dict[str, str] = {
    "No Defect": "N",
    "Malware Detected": "M",
    "Counterfeit Suspected": "C",
    "Spec Mismatch": "S",
    "Defective": "D",
    "Inconclusive": "I",
}

# ステップ名の短縮コード。表に無い名前は先頭3文字を使う。
STEP_CODES: dict[str, str] = {
    "malware": "m",
    "hash_inventory": "h",
    "capacity": "c",
    "speed": "s",
    "unmount": "u",
}

# backendのStepStatusの値と1文字コードの対応。未知の値は"?"。
STATUS_CODES: dict[str, str] = {
    "pass": "P",
    "fail": "F",
    "error": "E",
    "skipped": "S",
    "not_implemented": "N",
}

# 容量の区別（capacity.details.kind）と詳細QRの`ck`の対応（doc/qr-payload.md 6.5）。
# kindが無い（ステップがPASS／FAIL以外）ときは"?"。
CAPACITY_KIND_CODES: dict[str, str] = {
    "ok": "-",
    "short": "S",
    "defect": "D",
    "counterfeit": "C",
    "unverified": "U",
}

# デバイス種別（speed.details.device_class）と詳細QRの`dc`の対応（doc/qr-payload.md 6.4）。
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

# 速度判定の根拠（basis_read）と詳細QRの`sb`の対応（doc/qr-payload.md 6.7）。
BASIS_CODES: dict[str, str] = {
    "nominal": "n",
    "class": "c",
    "none": "-",
}

# 速度判定の重大度（severity）と詳細QRの`sv`の対応。FAILでなければ"-"。
SEVERITY_CODES: dict[str, str] = {
    "severe": "s",
    "minor": "m",
}
