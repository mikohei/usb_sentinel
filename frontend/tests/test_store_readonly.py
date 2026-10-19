"""Prove SqliteIndex is read-only (bytes unchanged, mode=ro, query_only). Uses test_store data."""

import sqlite3
from pathlib import Path

import pytest

from tests.test_store import (
    NAME_1,
    NAME_2,
    STARTED_1,
    STARTED_2,
    KeepOpenConnection,
    db_row,
    make_db,
)
from usb_sentinel_display.libs.store import SqliteIndex


class TestReadOnly:
    def test_file_bytes_unchanged(self, tmp_path: Path) -> None:
        """The DB file bytes are identical before and after query() (proof of no writes)."""
        db = tmp_path / "db.sqlite"
        make_db(db, [db_row(NAME_1), db_row(NAME_2, started_at=STARTED_2, state="started")])
        before = db.read_bytes()
        rows, reason = SqliteIndex(db).query("2000-01-01T00:00:00.000000+00:00")
        assert reason is None and len(rows) == 2
        assert db.read_bytes() == before
        assert sorted(p.name for p in tmp_path.iterdir()) == ["db.sqlite"]

    def test_uri_requests_read_only_mode(self, tmp_path: Path) -> None:
        """The connection uses a file: URI that includes mode=ro."""
        db = tmp_path / "db.sqlite"
        make_db(db, [db_row(NAME_1)])
        seen: list[tuple[str, dict[str, object]]] = []

        def spy(database: str, **kwargs: object) -> sqlite3.Connection:
            seen.append((database, kwargs))
            return sqlite3.connect(database, uri=True, timeout=1.0)

        rows, reason = SqliteIndex(db, connect=spy).query(STARTED_1)
        assert reason is None and len(rows) == 1
        (uri, kwargs) = seen[0]
        assert uri.startswith("file:") and "mode=ro" in uri
        assert kwargs == {"uri": True, "timeout": 1.0}

    def test_query_only_blocks_insert(self, tmp_path: Path) -> None:
        """Even when opened without mode=ro, PRAGMA query_only=ON rejects INSERT."""
        db = tmp_path / "db.sqlite"
        make_db(db, [db_row(NAME_1)])
        opened: list[KeepOpenConnection] = []

        def plain(*args: object, **kwargs: object) -> sqlite3.Connection:
            conn = sqlite3.connect(db, factory=KeepOpenConnection)
            opened.append(conn)
            return conn

        rows, reason = SqliteIndex(db, connect=plain).query(STARTED_1)
        assert reason is None and len(rows) == 1
        (conn,) = opened
        try:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute(
                    "INSERT INTO inspections (inspection_id, state, serial, kernel_name,"
                    " started_at, started_compact, output_dir, created_at, updated_at)"
                    " VALUES ('x', 'started', 's', 'sda', 't', 'c', '/o', 't', 't')"
                )
            assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        finally:
            conn.really_close()
