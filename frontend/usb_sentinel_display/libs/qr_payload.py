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

# バージョン8-M（152バイト）に収めるための上限（設計書2.1）。
MAX_PAYLOAD_BYTES = 150
SERIAL_LEN = 32
SHORT_SERIAL_LEN = 16
# 詳細QR（v:3、doc/qr-payload.md第5節）。3枚固定、ヘッダーのdは12桁。
DETAIL_PAGES = 3
DETAIL_DIGEST_LEN = 12
MB = 10**6
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _ascii_serial(serial: str) -> str:
    """表示用にサニタイズしたシリアルをASCII化する（非ASCIIは?）。"""
    return sanitize_text(serial).encode("ascii", "replace").decode("ascii")


def _encode(payload: dict[str, object]) -> str:
    """区切り文字を詰めたASCIIのJSONにする。"""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def build_payload(summary: ResultSummary) -> str:
    """画面用QRペイロード（v:2、設計書2.2）を作る。

    Args:
        summary: 結果の要約。

    Returns:
        150バイト以下のASCII JSON文字列。

    Raises:
        ValueError: sを16文字に切り、stを落としても150バイトを超えるとき。
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
    """バイトをMB（10^6、切り捨て）にする。Noneは透過。"""
    return None if value is None else value // MB


def _round(value: float | None, digits: int) -> float | None:
    """小数をdigits桁に丸める。Noneは透過。"""
    return None if value is None else round(value, digits)


def _db_yyyymmdd(db_date: str | None) -> str | None:
    """ISO 8601の日付（先頭10文字がYYYY-MM-DD）を8桁にする。形式外はNone。"""
    if db_date is None or _DATE_RE.fullmatch(db_date[:10]) is None:
        return None
    return db_date[:10].replace("-", "")


def _db_version_int(text: str | None) -> int | None:
    """ClamAV DBバージョンの文字列を整数にする。整数化できなければNone。"""
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _identity(summary: ResultSummary) -> tuple[str, bool]:
    """QRの`s`に入れる文字列と、それがカーネル名か（`k`を付けるか）を返す。"""
    serial = _ascii_serial(summary.serial)[:SERIAL_LEN]
    return (serial, False) if serial else (summary.kernel_name, True)


def _detail_page1(summary: ResultSummary) -> dict[str, object]:
    """1枚目（同定と判定、doc/qr-payload.md 5.2）の本文。"""
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
    """2枚目（容量とデバイス、doc/qr-payload.md 5.3）の本文。"""
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
    """3枚目（速度とDB、台帳、doc/qr-payload.md 5.4）の本文。"""
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
    """詳細QR（v:3）の1枚分のペイロードを作る（doc/qr-payload.md第5節）。

    Args:
        summary: 結果の要約（detailに材料を持つ）。
        page: 1〜3。

    Returns:
        150バイト以下のASCII JSON文字列。値が無い項目はnullで出し、キーは省かない
        （例外は1枚目の`k`）。

    Raises:
        ValueError: pageが1〜3以外、または（1枚目はsを16文字に切っても）150バイトを超えるとき。
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
    """詳細QRの3枚をまとめて作る。1枚でも超過すればValueError。"""
    return [build_detail_page(summary, page) for page in range(1, DETAIL_PAGES + 1)]
