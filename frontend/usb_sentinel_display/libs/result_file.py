"""結果ディレクトリ名の解析、時刻の変換、result.jsonの要約（ResultSummary）。

ファイルシステムの走査とSQLite索引の突き合わせはresults.pyのResultStoreが行う。
ここに置く関数はファイルを開かず（file_digestを除く）、受け取った値だけで動く純関数である。
"""

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

from .models import ResultSummary
from .result_detail import parse_detail
from .sysfs import sanitize_text

# 結果ディレクトリ名: <timestamp>-<serial>[-<6桁>]。
DIR_NAME_RE = re.compile(r"^(\d{8}T\d{6}Z)-(.+?)(?:-(\d{6}))?$")
RESULT_FILE = "result.json"


def parse_dir_name(name: str) -> tuple[str, str] | None:
    """結果ディレクトリ名をタイムスタンプとシリアル部分に分ける。形式外ならNone。"""
    match = DIR_NAME_RE.match(name)
    if match is None:
        return None
    return str(match.group(1)), str(match.group(2))


def serial_for_dir(serial: str, kernel: str) -> str:
    """backendのresult_dir_for()と同じ規則でシリアルをディレクトリ名用に変換する。"""
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", serial)[:128]
    return safe or f"{kernel}-unknown-serial"


def compact_utc(started_at: object, fallback: str) -> str:
    """ISO 8601の時刻をYYYYmmddTHHMMSSZに直す。失敗したらfallbackの先頭16文字。"""
    try:
        parsed = datetime.fromisoformat(started_at)  # ty: ignore[invalid-argument-type] -- 型違いはTypeErrorで捕まえる
    except (ValueError, TypeError):
        return fallback[:16]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def iso_utc(ts: float) -> str:
    """エポック秒をbackendのstarted_atと同じISO 8601（UTC、マイクロ秒付き）にする。"""
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="microseconds")


def started_at_lower_bound(ts: float) -> str:
    """索引のstarted_atに文字列比較で渡す下限。秒に切り下げ、マイクロ秒を付けない。

    backendのisoformat()はマイクロ秒0のとき".000000"を省くので、マイクロ秒付きの下限と
    同じ秒の行が'+' < '.'の比較で落ちる。秒で切り下げれば同じ秒の行はすべて候補に入る。
    """
    return datetime.fromtimestamp(int(ts // 1), UTC).isoformat(timespec="seconds")


def epoch_from_iso(text: str) -> float | None:
    """ISO 8601の時刻をエポック秒にする。tzが無ければUTCとみなす。解釈できなければNone。"""
    try:
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def file_digest(path: Path) -> str:
    """ファイルのバイト列のSHA-256（小文字hex）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _malware_count(steps: list[object]) -> int:
    """malwareステップのdetections要素数。無ければ0。"""
    for step in steps:
        if isinstance(step, dict) and step.get("name") == "malware":
            details = step.get("details")
            detections = details.get("detections") if isinstance(details, dict) else None
            return len(detections) if isinstance(detections, list) else 0
    return 0


def parse_summary(
    data: object, *, dir_name: str, path: Path, mtime: float, digest: str
) -> ResultSummary | None:
    """result.jsonの内容から表示用の要約を作る。必須キーが欠けていればNone。"""
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
    )
