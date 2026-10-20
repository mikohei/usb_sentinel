"""Read-only use of the backend SQLite index (inspections.sqlite) (doc/qr-payload.md section 8).

result.json is the source of truth; the DB is only an index for finding candidates fast. Only
SELECT runs here, and writes are blocked twice: a mode=ro URI and PRAGMA query_only=ON. If the DB
is missing, cannot be opened, or has a newer schema, a reason key is returned and the caller
(results.ResultStore) falls back to scanning the result directories.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Highest readable schema version. Mirrors backend schema.SCHEMA_VERSION (2: v1 plus a `mode`
# column that this reader does not query; the mode comes from result.json).
EXPECTED_SCHEMA_VERSION = 2

# Info strip notice keys (text is "notice." + key in ui/i18n.py).
NOTICE_DB_MISSING = "db_missing"
NOTICE_DB_OPEN_FAILED = "db_open_failed"
NOTICE_DB_SCHEMA_NEW = "db_schema_new"

# Columns read by SELECT (in DbRow field order).
QUERY_COLUMNS: tuple[str, ...] = (
    "inspection_id",
    "state",
    "serial",
    "kernel_name",
    "port_path",
    "started_at",
    "result_digest",
)
# Column names are fixed strings like the constants above, with no external input.
# Values are passed only via placeholders.
_QUERY_SQL = (
    "SELECT inspection_id, state, serial, kernel_name, port_path, started_at, result_digest"
    " FROM inspections WHERE started_at >= ? ORDER BY started_at"
)
# Connect wait (seconds) and busy_timeout (ms). Give up after 1 second if a backend write overlaps.
_CONNECT_TIMEOUT_S = 1.0
_BUSY_TIMEOUT_MS = 1000


@dataclass(frozen=True)
class DbRow:
    """Only the columns of one inspections row that are used for binding."""

    inspection_id: str
    state: str
    serial: str
    kernel_name: str
    port_path: str | None
    started_at: str
    result_digest: str | None


def _row_from_tuple(values: tuple[object, ...]) -> DbRow | None:
    """Turn one SELECT row into a DbRow. Rows with unexpected column types give None (dropped)."""
    if len(values) != len(QUERY_COLUMNS):
        return None
    inspection_id, state, serial, kernel_name, port_path, started_at, result_digest = values
    if not all(isinstance(v, str) for v in (inspection_id, state, serial, kernel_name, started_at)):
        return None
    if port_path is not None and not isinstance(port_path, str):
        return None
    if result_digest is not None and not isinstance(result_digest, str):
        return None
    return DbRow(
        inspection_id=str(inspection_id),
        state=str(state),
        serial=str(serial),
        kernel_name=str(kernel_name),
        port_path=port_path if isinstance(port_path, str) else None,
        started_at=str(started_at),
        result_digest=result_digest if isinstance(result_digest, str) else None,
    )


class SqliteIndex:
    """Open inspections.sqlite read-only and fetch candidate rows by a start-time lower bound."""

    def __init__(
        self,
        path: Path,
        *,
        connect: Callable[..., sqlite3.Connection] = sqlite3.connect,
    ) -> None:
        """
        Args:
            path: Path to inspections.sqlite.
            connect: Creates the connection (swapped in tests). Takes the sqlite3.connect args.
        """
        self.path = path
        self._connect = connect

    def query(self, min_started_at: str) -> tuple[list[DbRow], str | None]:
        """Return rows with started_at >= min_started_at, in started_at order.

        Args:
            min_started_at: ISO 8601 (UTC) lower bound. Compared as strings; use the backend format.

        Returns:
            (rows, notice key). If the notice key is not None, rows is empty and the caller rescans.
        """
        try:
            # stat() rather than Path.exists(): since Python 3.14 exists() returns False for any
            # OSError, which would report EACCES (parent lacks exec permission) as missing.
            try:
                self.path.stat()
            except FileNotFoundError:
                return [], NOTICE_DB_MISSING
            uri = f"{self.path.resolve().as_uri()}?mode=ro"
            conn = self._connect(uri, uri=True, timeout=_CONNECT_TIMEOUT_S)
        except (sqlite3.Error, OSError, ValueError):
            return [], NOTICE_DB_OPEN_FAILED
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > EXPECTED_SCHEMA_VERSION:
                return [], NOTICE_DB_SCHEMA_NEW
            if version == 0:
                # A file before the table was created (backend has never written to it).
                return [], NOTICE_DB_MISSING
            fetched = conn.execute(_QUERY_SQL, (min_started_at,)).fetchall()
        except (sqlite3.Error, OSError, ValueError):
            return [], NOTICE_DB_OPEN_FAILED
        finally:
            conn.close()
        rows: list[DbRow] = []
        for values in fetched:
            row = _row_from_tuple(tuple(values))
            if row is not None:
                rows.append(row)
        return rows, None
