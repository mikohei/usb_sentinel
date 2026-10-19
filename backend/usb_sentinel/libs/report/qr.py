"""ファイル用QR（result-qr.png）のペイロードv:3（設計書第6節）。

v:1のキーは全て残し、容量・速度・台帳・理由コードなど人が判断する材料を足す。
`v:2`は画面用の通常QR（frontend）が使っているため、ファイル用は`3`にする。
ステップの`details`の新キーはERROR経路やSKIPPEDでは無いことがあるので、必ず`.get()`で読み
`KeyError`を出さない。仕様は`doc/qr-payload.md`に記す。
"""

import json
from typing import Any

from ..models import InspectionResult, StepResult, StepStatus
from .json_report import digest

# QRバイトモード（バージョン40、誤り訂正L）の上限。
MAX_PAYLOAD_BYTES = 2953
# 省略順序（設計書第6.3節）の定数。
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
    """区切り文字を詰めたUTF-8のJSONにする（日本語はエスケープしない）。"""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _size(payload: dict[str, Any]) -> int:
    """UTF-8でのバイト数。"""
    return len(_encode(payload).encode("utf-8"))


def _mb_s(value: object) -> float | None:
    """`details.read`／`details.write`の辞書から`mb_s`を取り出す。辞書でなければNone。"""
    if isinstance(value, dict):
        mb_s = value.get("mb_s")
        return mb_s if isinstance(mb_s, int | float) else None
    return None


def _capacity_payload(step: StepResult | None) -> dict[str, Any] | None:
    """容量ステップがPASSまたはFAILのときだけ辞書を組む。それ以外はNone。"""
    if step is None or step.status not in (StepStatus.PASS, StepStatus.FAIL):
        return None
    return {key: step.details.get(key) for key in _CAPACITY_KEYS}


def _speed_payload(step: StepResult | None) -> dict[str, Any] | None:
    """速度ステップの`details`に`read`があるときだけ辞書を組む。それ以外はNone。"""
    if step is None or "read" not in step.details:
        return None
    payload: dict[str, Any] = {
        "read_mb_s": _mb_s(step.details.get("read")),
        "write_mb_s": _mb_s(step.details.get("write")),
    }
    payload.update({key: step.details.get(key) for key in _SPEED_KEYS})
    return payload


def _clamav_db_date(database: dict[str, Any]) -> str | None:
    """`db_date`（ISO 8601）の先頭10文字（YYYY-MM-DD）。無ければNone。"""
    db_date = database.get("db_date")
    return db_date[:10] if isinstance(db_date, str) else None


def _clip_reason(reason: str) -> str:
    """理由文を120文字に切り、切ったら末尾に「…」を付ける。"""
    if len(reason) <= REASON_CHARS:
        return reason
    return reason[:REASON_CHARS] + ELLIPSIS


def _shrink(payload: dict[str, Any], serial: str) -> None:
    """設計書第6.3節の順で縮める。各段階の後に再計算し、上限以下になれば止める。

    Raises:
        ValueError: 全段階を適用しても上限を超えるとき。
    """

    def over() -> bool:
        """現在のpayloadが上限を超えているか。"""
        return _size(payload) > MAX_PAYLOAD_BYTES

    if not over():
        return
    payload["truncated"] = True
    # 1. シグネチャを3件に切る。
    payload["signatures"] = payload["signatures"][:SIGNATURE_LIMIT]
    # 2. 理由の各要素を120文字に切る。
    if over():
        payload["reasons"] = [_clip_reason(reason) for reason in payload["reasons"]]
    # 3. シグネチャを1件ずつ0件まで減らす。
    while over() and payload["signatures"]:
        payload["signatures"].pop()
    # 4. 理由を空にする（reason_codesは残す）。
    if over():
        payload["reasons"] = []
    # 5. シリアルを128文字に切る。
    if over():
        payload["serial"] = serial[:SERIAL_CHARS]
    # 6. capacity.probe_verdict_lineを落とす。
    if over() and isinstance(payload.get("capacity"), dict):
        payload["capacity"]["probe_verdict_line"] = None
    # 7. それでも超えれば呼び出し元（write_all）で終了コード3になる。
    if over():
        raise ValueError("QR payload too large")


def build_payload(result: InspectionResult) -> str:
    """ファイル用QRのペイロード（v:3）を作る。

    Args:
        result: 検査結果。`digest()`を取るので`write_all()`が書くresult.jsonと対応する。

    Returns:
        2953バイト以下のJSON文字列（UTF-8、区切り文字なし）。

    Raises:
        ValueError: 省略順序を全て適用しても上限を超えるとき。
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
