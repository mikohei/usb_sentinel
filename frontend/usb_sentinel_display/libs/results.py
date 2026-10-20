"""Scanning result directories and matching against the SQLite index (ResultStore).

Directory name parsing, time conversion, and result.json summaries (parse_summary) live in
result_file.py.
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
    drive_tag,
    epoch_from_iso,
    file_digest,
    iso_utc,
    parse_dir_name,
    parse_summary,
    serial_for_dir,
    started_at_lower_bound,
)
from .store import DbRow, SqliteIndex

# Directory names, times, and parse_summary moved to result_file.py. Names kept for old refs.
__all__ = [
    "DIR_NAME_RE",
    "RESULT_FILE",
    "ResultStore",
    "ResultsMissingError",
    "ResultsPermissionError",
    "compact_utc",
    "drive_tag",
    "epoch_from_iso",
    "file_digest",
    "iso_utc",
    "parse_dir_name",
    "parse_summary",
    "serial_for_dir",
    "started_at_lower_bound",
]


class ResultsMissingError(Exception):
    """The results root does not exist or is not a directory."""


class ResultsPermissionError(Exception):
    """No permission to read the results root."""


class ResultStore:
    """Scan the results root, read result.json safely, and cache it.

    With an SQLite index, collect() finds candidates via the DB, but result.json is always
    re-read as the source of truth.
    """

    def __init__(
        self, root: Path, *, index: SqliteIndex | None = None, max_failures: int = 10
    ) -> None:
        """Take the results root and index, and start with an empty cache.

        Args:
            root: Results root (results_dir).
            index: SQLite index. If None, always scan the directories (scan).
            max_failures: Consecutive result.json read failures that count as "result corrupt".
        """
        self.root = root
        self.max_failures = max_failures
        self._index = index
        self._cache: dict[tuple[Path, float], ResultSummary] = {}
        self._failures: dict[Path, int] = {}

    def reset(self) -> None:
        """Drop the cache and failure counts (F5)."""
        self._cache.clear()
        self._failures.clear()

    def _load(self, path: Path, dir_name: str, mtime: float) -> ResultSummary | None:
        """Read result.json and return the summary. If unreadable, count a failure, return None."""
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
        """List the results root entries. Missing/denied become dedicated exceptions."""
        try:
            with os.scandir(self.root) as entries:
                return list(entries)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ResultsMissingError(str(self.root)) from exc
        except PermissionError as exc:
            raise ResultsPermissionError(str(self.root)) from exc

    def scan(self, min_mtime: float | None) -> list[ResultRecord]:
        """Scan the results root.

        Args:
            min_mtime: result.json older than this is not a candidate. If None, return empty.

        Returns:
            Records sorted by name.

        Raises:
            ResultsMissingError: The results root does not exist or is not a directory.
            ResultsPermissionError: No permission to read the results root.
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
        """Build a record from one DB row. Re-read result.json; DB values are only binding inputs.

        The output_dir column is unused; inspection_id is treated only as a dir name under the root.
        Malformed names and names containing "/" are dropped.
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
                # The index digest and the real file disagree. Treat as corrupt from the first time.
                return ResultRecord(name, mtime, serial_part, None, True)
            return ResultRecord(name, mtime, serial_part, summary, False)
        if row.state == "started":
            # Started but no final result. The mapper picks inspecting vs no result by unit state.
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
        """Find candidates via the SQLite index and re-read result.json as the source of truth.

        If the index is missing, unopenable, or has a newer schema, fall back to scan() with a
        reason key.

        Args:
            min_mtime: Minimum connect time (epoch seconds). If None, just list and return empty.

        Returns:
            (records sorted by name, notice key). The notice key is None when the index was used.

        Raises:
            ResultsMissingError: The results root does not exist or is not a directory.
            ResultsPermissionError: No permission to read the results root.
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
