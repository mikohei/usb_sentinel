"""Inspection records in SQLite (design doc section 5).

result.json is authoritative. The DB is an index for looking up, in one row, which inspection
ran when, on which port, and how it ended. Recording failures must not affect whether an
inspection succeeds. Public methods never raise; they return a failure reason string.
A connection is opened and closed per operation (not held for the hours an inspection takes).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from ..models import InspectionResult
from .schema import MIGRATIONS, SCHEMA_VERSION
from .sqlite_row import STEP_NAMES, VERDICT_CODES, row_from_result

# VERDICT_CODES, STEP_NAMES, and row_from_result moved to sqlite_row.py. The names are kept
# here for existing importers.
__all__ = [
    "DB_MODE",
    "STARTED_COLUMNS",
    "STEP_NAMES",
    "VERDICT_CODES",
    "SchemaTooNewError",
    "SqliteStore",
    "row_from_result",
]

# Mode of the main DB file. The directory is 2770 setgid, so the group becomes usb-sentinel.
DB_MODE = 0o660

# Columns written at start (state='started'). Verdict and step columns are filled on
# completion.
STARTED_COLUMNS = (
    "inspection_id",
    "state",
    "serial",
    "kernel_name",
    "port_path",
    "vendor",
    "model",
    "started_at",
    "started_compact",
    "output_dir",
    "created_at",
    "updated_at",
)


class SchemaTooNewError(RuntimeError):
    """The DB user_version is newer than this version's SCHEMA_VERSION (nothing is written)."""


def _insert_sql(columns: tuple[str, ...]) -> str:
    """Column names come from this module's constants, never external input. Values use named
    placeholders."""
    names = ", ".join(columns)
    params = ", ".join(f":{column}" for column in columns)
    return f"INSERT INTO inspections ({names}) VALUES ({params})"  # noqa: S608


def _update_sql(columns: tuple[str, ...]) -> str:
    """Update every column except inspection_id and created_at."""
    assignments = ", ".join(
        f"{column} = :{column}"
        for column in columns
        if column not in ("inspection_id", "created_at")
    )
    return f"UPDATE inspections SET {assignments} WHERE inspection_id = :inspection_id"  # noqa: S608


class SqliteStore:
    """Record inspection start and completion in SQLite. No connection is held."""

    def __init__(
        self, path: Path, *, connect: Callable[[str], sqlite3.Connection] = sqlite3.connect
    ) -> None:
        """Take the DB file path and a connect function. No connection is opened here.

        Args:
            path: Path of inspections.sqlite.
            connect: Function that opens a connection (replaced in tests).
        """
        self.path = path
        self._connect = connect

    def _chmod_db(self) -> str | None:
        """Apply 0o660 to the main inspections.sqlite only. SQLite copies its mode to -wal/-shm."""
        try:
            self.path.chmod(DB_MODE)
        except OSError as exc:
            return f"権限設定エラー: {exc}"
        return None

    def _open(self) -> sqlite3.Connection:
        """Open a connection, apply PRAGMAs and migrations, and return it. On failure, close it and
        re-raise."""
        conn = self._connect(str(self.path))
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA temp_store = MEMORY")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise SchemaTooNewError(
                    f"DBのスキーマ版{version}が本バージョン{SCHEMA_VERSION}より新しい"
                )
            if version < SCHEMA_VERSION:
                with conn:
                    conn.execute("BEGIN")
                    for target, statements in MIGRATIONS:
                        if target <= version:
                            continue
                        for sql in statements:
                            conn.execute(sql)
                        conn.execute(f"PRAGMA user_version = {int(target)}")
                if version == 0:
                    # First creation. The failure reason is returned again by _chmod_db()
                    # after each operation.
                    self._chmod_db()
        except BaseException:
            conn.close()
            raise
        return conn

    def _write(self, operate: Callable[[sqlite3.Connection], None]) -> str | None:
        """Open, write in one transaction, chmod, and close. Return a failure reason."""
        try:
            conn = self._open()
            try:
                with conn:
                    operate(conn)
            finally:
                conn.close()
        except SchemaTooNewError as exc:
            return str(exc)
        except sqlite3.Error as exc:
            return f"SQLiteエラー: {exc}"
        except OSError as exc:
            return f"ファイル操作エラー: {exc}"
        except ValueError as exc:
            return f"値の変換エラー: {exc}"
        chmod_error = self._chmod_db()
        if chmod_error is not None:
            # The row is already written, so return wording that is distinguishable from a
            # recording failure.
            return f"権限設定に失敗（記録は完了）: {chmod_error}"
        return None

    def record_started(self, result: InspectionResult) -> str | None:
        """Insert a row with state='started'.

        Returns:
            Failure reason. None on success.
        """

        def operate(conn: sqlite3.Connection) -> None:
            """INSERT only the start columns."""
            row = row_from_result(result, None)
            row["state"] = "started"
            conn.execute(_insert_sql(STARTED_COLUMNS), {c: row[c] for c in STARTED_COLUMNS})

        return self._write(operate)

    def record_completed(self, result: InspectionResult, digest: str) -> str | None:
        """Update all columns with state='completed'. Fall back to INSERT if no start row exists.

        Returns:
            Failure reason. None on success.
        """

        def operate(conn: sqlite3.Connection) -> None:
            """UPDATE all columns; INSERT if no row was affected."""
            row = row_from_result(result, digest)
            columns = tuple(row)
            cursor = conn.execute(_update_sql(columns), row)
            if cursor.rowcount == 0:
                conn.execute(_insert_sql(columns), row)

        return self._write(operate)
