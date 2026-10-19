"""結果ディレクトリの走査とSQLite索引の突き合わせ（ResultStore）。

ディレクトリ名の解析・時刻の変換・result.jsonの要約（parse_summary）はresult_file.pyにある。
"""

import hashlib
import json
import os
from pathlib import Path

from .models import ResultRecord, ResultSummary
from .result_file import (
    DIR_NAME_RE,
    RESULT_FILE,
    compact_utc,
    epoch_from_iso,
    file_digest,
    iso_utc,
    parse_dir_name,
    parse_summary,
    serial_for_dir,
    started_at_lower_bound,
)
from .store import DbRow, SqliteIndex

# ディレクトリ名と時刻とparse_summaryはresult_file.pyへ移した。既存の参照先として名前を残す。
__all__ = [
    "DIR_NAME_RE",
    "RESULT_FILE",
    "ResultStore",
    "ResultsMissingError",
    "ResultsPermissionError",
    "compact_utc",
    "epoch_from_iso",
    "file_digest",
    "iso_utc",
    "parse_dir_name",
    "parse_summary",
    "serial_for_dir",
    "started_at_lower_bound",
]


class ResultsMissingError(Exception):
    """結果ルートが存在しない、またはディレクトリでない。"""


class ResultsPermissionError(Exception):
    """結果ルートを読む権限がない。"""


class ResultStore:
    """結果ルートを走査し、result.jsonを安全に読んでキャッシュする。

    SQLite索引（index）があればcollect()がDBで候補を引くが、result.jsonは必ず読み直して正本とする。
    """

    def __init__(
        self, root: Path, *, index: SqliteIndex | None = None, max_failures: int = 10
    ) -> None:
        """結果ルートと索引を受け取り、キャッシュを空で始める。

        Args:
            root: 結果ルート（results_dir）。
            index: SQLite索引。Noneなら常にディレクトリ走査（scan）を使う。
            max_failures: result.jsonの読み込みが何回連続で失敗したら「結果破損」とみなすか。
        """
        self.root = root
        self.max_failures = max_failures
        self._index = index
        self._cache: dict[tuple[Path, float], ResultSummary] = {}
        self._failures: dict[Path, int] = {}

    def reset(self) -> None:
        """キャッシュと失敗回数を捨てる（F5）。"""
        self._cache.clear()
        self._failures.clear()

    def _load(self, path: Path, dir_name: str, mtime: float) -> ResultSummary | None:
        """result.jsonを読んで要約を返す。読めなければ失敗回数を加算してNone。"""
        key = (path, mtime)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        summary: ResultSummary | None = None
        try:
            raw = path.read_bytes()
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            data = None
        else:
            digest = hashlib.sha256(raw).hexdigest()
            summary = parse_summary(data, dir_name=dir_name, path=path, mtime=mtime, digest=digest)
        if summary is None:
            self._failures[path] = self._failures.get(path, 0) + 1
            return None
        self._failures.pop(path, None)
        self._cache[key] = summary
        return summary

    def _list_root(self) -> list[os.DirEntry[str]]:
        """結果ルートの直下を列挙する。不在・権限なしは専用の例外に変える。"""
        try:
            with os.scandir(self.root) as entries:
                return list(entries)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ResultsMissingError(str(self.root)) from exc
        except PermissionError as exc:
            raise ResultsPermissionError(str(self.root)) from exc

    def scan(self, min_mtime: float | None) -> list[ResultRecord]:
        """結果ルートを走査する。

        Args:
            min_mtime: これより古いresult.jsonは候補にしない。Noneなら走査だけして空を返す。

        Returns:
            名前順のレコード一覧。

        Raises:
            ResultsMissingError: 結果ルートが無い、またはディレクトリでない。
            ResultsPermissionError: 結果ルートを読む権限がない。
        """
        names = self._list_root()
        if min_mtime is None:
            return []
        records: list[ResultRecord] = []
        for entry in sorted(names, key=lambda entry: entry.name):
            parsed = parse_dir_name(entry.name)
            if parsed is None:
                continue
            path = Path(entry.path) / RESULT_FILE
            try:
                if not entry.is_dir():
                    continue
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime < min_mtime:
                continue
            summary = self._load(path, entry.name, mtime)
            broken = summary is None and self._failures.get(path, 0) >= self.max_failures
            records.append(ResultRecord(entry.name, mtime, parsed[1], summary, broken))
        return records

    def _record_from_row(self, row: DbRow) -> ResultRecord | None:
        """DBの1行からレコードを組む。result.jsonを読み直し、DBの値は結び付けの材料にだけ使う。

        output_dir列は使わず、inspection_idを結果ルート直下のディレクトリ名としてだけ扱う。
        形式外の名前や"/"を含む名前は捨てる。
        """
        name = row.inspection_id
        parsed = parse_dir_name(name)
        if parsed is None or "/" in name:
            return None
        serial_part = parsed[1]
        path = self.root / name / RESULT_FILE
        try:
            mtime: float | None = path.stat().st_mtime
        except (OSError, ValueError):
            mtime = None
        summary = self._load(path, name, mtime) if mtime is not None else None
        if mtime is not None and summary is not None:
            if row.result_digest is not None and row.result_digest != summary.digest:
                # 索引のダイジェストと実ファイルが食い違う。1回目から結果破損として扱う。
                return ResultRecord(name, mtime, serial_part, None, True)
            return ResultRecord(name, mtime, serial_part, summary, False)
        if row.state == "started":
            # 開始したが完了結果が無い。検査中か結果なしかはmapperがユニット状態で決める。
            started_epoch = epoch_from_iso(row.started_at)
            return ResultRecord(
                name,
                started_epoch if started_epoch is not None else 0.0,
                serial_part,
                None,
                False,
                started=True,
                port_path=row.port_path,
                kernel_name=row.kernel_name,
            )
        broken = mtime is not None and self._failures.get(path, 0) >= self.max_failures
        return ResultRecord(name, mtime if mtime is not None else 0.0, serial_part, None, broken)

    def collect(self, min_mtime: float | None) -> tuple[list[ResultRecord], str | None]:
        """SQLite索引で候補を引き、result.jsonを正本として読み直す。

        索引が無い・開けない・スキーマ版が新しいときは従来のscan()に戻り、理由キーを添える。

        Args:
            min_mtime: 接続時刻の最小値（エポック秒）。Noneなら列挙だけして空を返す。

        Returns:
            (名前順のレコード一覧、通知キー)。索引が使えたときの通知キーはNone。

        Raises:
            ResultsMissingError: 結果ルートが無い、またはディレクトリでない。
            ResultsPermissionError: 結果ルートを読む権限がない。
        """
        if self._index is None:
            return self.scan(min_mtime), None
        self._list_root()
        if min_mtime is None:
            return [], None
        rows, reason = self._index.query(started_at_lower_bound(min_mtime))
        if reason is not None:
            return self.scan(min_mtime), reason
        records: list[ResultRecord] = []
        for row in rows:
            record = self._record_from_row(row)
            if record is not None:
                records.append(record)
        records.sort(key=lambda record: record.name)
        return records, None
