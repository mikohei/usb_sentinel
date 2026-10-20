import sqlite3
from pathlib import Path

import pytest

from usb_sentinel_display.libs.store import (
    EXPECTED_SCHEMA_VERSION,
    NOTICE_DB_MISSING,
    NOTICE_DB_OPEN_FAILED,
    NOTICE_DB_SCHEMA_NEW,
    QUERY_COLUMNS,
    DbRow,
    SqliteIndex,
)

# Copy of the CREATE TABLE statement from backend schema.py v1 (SCHEMA_VERSION = 1). frontend
# does not import backend, so it is pinned as a string. Update it when backend bumps the version.
CREATE_TABLE_V1 = """
CREATE TABLE IF NOT EXISTS inspections (
    inspection_id          TEXT PRIMARY KEY,
    state                  TEXT NOT NULL CHECK (state IN ('started', 'completed')),
    verdict                TEXT,
    verdict_code           TEXT,
    serial                 TEXT NOT NULL,
    kernel_name            TEXT NOT NULL,
    port_path              TEXT,
    vendor                 TEXT,
    model                  TEXT,
    started_at             TEXT NOT NULL,
    started_compact        TEXT NOT NULL,
    finished_at            TEXT,
    elapsed_s              REAL,
    result_digest          TEXT,
    step_malware           TEXT,
    step_hash_inventory    TEXT,
    step_capacity          TEXT,
    step_speed             TEXT,
    step_unmount           TEXT,
    malware_count          INTEGER,
    clamav_db_version      TEXT,
    clamav_db_date         TEXT,
    clamav_scanned_files   INTEGER,
    clamav_scan_time_s     REAL,
    nominal_bytes          INTEGER,
    advertised_bytes       INTEGER,
    actual_bytes           INTEGER,
    advertised_over        INTEGER,
    capacity_kind          TEXT,
    counterfeit_reasons    TEXT,
    lost_ratio             REAL,
    probe_verdict_line     TEXT,
    link_speed_mbps        INTEGER,
    device_class           TEXT,
    device_class_basis     TEXT,
    read_mb_s              REAL,
    write_mb_s             REAL,
    threshold_read_mb_s    REAL,
    threshold_write_mb_s   REAL,
    speed_basis_read       TEXT,
    speed_basis_write      TEXT,
    speed_severity         TEXT,
    reason_codes           TEXT,
    reasons                TEXT,
    hash_inventory_digest  TEXT,
    hash_inventory_lines   INTEGER,
    output_dir             TEXT NOT NULL,
    warnings               TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
)
"""
# Copy of the v2 migration from backend schema.py (SCHEMA_VERSION = 2): the inspection mode.
ALTER_TABLE_V2 = "ALTER TABLE inspections ADD COLUMN mode TEXT"

STARTED_1 = "2025-06-01T12:00:00.123456+00:00"
STARTED_2 = "2025-06-01T12:30:00.000001+00:00"
STARTED_OLD = "2025-05-31T23:59:59.999999+00:00"
NAME_1 = "20250601T120000Z-ABC123"
NAME_2 = "20250601T123000Z-XYZ789"
NAME_OLD = "20250531T235959Z-OLD001"
DIGEST = "a" * 64

# Defaults for the NOT NULL columns that make_db() fills.
_DEFAULTS: dict[str, object] = {
    "state": "completed",
    "serial": "ABC123",
    "kernel_name": "sda",
    "port_path": None,
    "started_at": STARTED_1,
    "result_digest": None,
    "output_dir": "/var/lib/usb-sentinel/results/x",
    "created_at": STARTED_1,
    "updated_at": STARTED_1,
}


def db_row(inspection_id: str, **overrides: object) -> dict[str, object]:
    """Row dict to INSERT. NOT NULL columns get defaults; started_compact comes from started_at."""
    row: dict[str, object] = {"inspection_id": inspection_id, **_DEFAULTS, **overrides}
    started_at = row["started_at"]
    if "started_compact" not in row:
        row["started_compact"] = (
            started_at[:10].replace("-", "") + "T" + started_at[11:19].replace(":", "") + "Z"
            if isinstance(started_at, str)
            else "00000000T000000Z"
        )
    return row


def make_db(path: Path, rows: list[dict[str, object]], user_version: int = 2) -> None:
    """Create the v1 table (plus the v2 column when user_version >= 2) and insert rows."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(CREATE_TABLE_V1)
        if user_version >= 2:
            conn.execute(ALTER_TABLE_V2)
        conn.execute(f"PRAGMA user_version = {int(user_version)}")
        for row in rows:
            columns = ", ".join(row)
            params = ", ".join(f":{c}" for c in row)
            conn.execute(f"INSERT INTO inspections ({columns}) VALUES ({params})", row)  # noqa: S608
        conn.commit()
    finally:
        conn.close()


class KeepOpenConnection(sqlite3.Connection):
    """Connection that ignores close() and stays open (to inspect it after query())."""

    def close(self) -> None:
        pass

    def really_close(self) -> None:
        sqlite3.Connection.close(self)


class TestSchemaVersion:
    def test_expected_version_is_two(self) -> None:
        """The readable version equals backend SCHEMA_VERSION (2): v1 table plus the mode column."""
        assert EXPECTED_SCHEMA_VERSION == 2
        assert "CREATE TABLE IF NOT EXISTS inspections" in CREATE_TABLE_V1
        assert ALTER_TABLE_V2 == "ALTER TABLE inspections ADD COLUMN mode TEXT"

    def test_make_db_v2_has_mode_column(self, tmp_path: Path) -> None:
        db = tmp_path / "db.sqlite"
        make_db(db, [db_row(NAME_1, mode="full")])
        conn = sqlite3.connect(db)
        try:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(inspections)")]
            assert "mode" in columns
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        finally:
            conn.close()

    def test_v1_db_still_read(self, tmp_path: Path) -> None:
        """A database left by the old backend (user_version 1, no mode column) is still read."""
        db = tmp_path / "db.sqlite"
        make_db(db, [db_row(NAME_1)], user_version=1)
        conn = sqlite3.connect(db)
        try:
            assert "mode" not in [row[1] for row in conn.execute("PRAGMA table_info(inspections)")]
        finally:
            conn.close()
        rows, reason = SqliteIndex(db).query(STARTED_1)
        assert reason is None
        assert [r.inspection_id for r in rows] == [NAME_1]

    def test_query_columns_match_dbrow(self) -> None:
        """The SELECT column order matches the DbRow field order."""
        assert tuple(DbRow.__dataclass_fields__) == QUERY_COLUMNS


class TestQuery:
    def test_rows_at_or_after_lower_bound_in_order(self, tmp_path: Path) -> None:
        """Return rows at/above the started_at bound, by started_at, with all DbRow columns."""
        db = tmp_path / "inspections.sqlite"
        make_db(
            db,
            [
                db_row(NAME_2, started_at=STARTED_2, state="started", serial="XYZ789"),
                db_row(NAME_OLD, started_at=STARTED_OLD),
                db_row(
                    NAME_1,
                    started_at=STARTED_1,
                    port_path="1-1.3",
                    result_digest=DIGEST,
                    kernel_name="sdb",
                ),
            ],
        )
        rows, reason = SqliteIndex(db).query(STARTED_1)
        assert reason is None
        assert rows == [
            DbRow(NAME_1, "completed", "ABC123", "sdb", "1-1.3", STARTED_1, DIGEST),
            DbRow(NAME_2, "started", "XYZ789", "sda", None, STARTED_2, None),
        ]

    def test_lower_bound_excludes_earlier(self, tmp_path: Path) -> None:
        """A row even 1 microsecond before the bound is not returned."""
        db = tmp_path / "db.sqlite"
        make_db(db, [db_row(NAME_1, started_at=STARTED_1)])
        rows, _ = SqliteIndex(db).query("2025-06-01T12:00:00.123457+00:00")
        assert rows == []

    def test_null_columns_become_none(self, tmp_path: Path) -> None:
        """NULL port_path and result_digest become None."""
        db = tmp_path / "db.sqlite"
        make_db(db, [db_row(NAME_1)])
        (row,), _ = SqliteIndex(db).query("2000-01-01T00:00:00.000000+00:00")
        assert row.port_path is None
        assert row.result_digest is None

    def test_wrong_typed_row_dropped(self, tmp_path: Path) -> None:
        """Rows with unexpected column types (BLOB serial) are dropped; other rows remain."""
        db = tmp_path / "db.sqlite"
        make_db(db, [db_row(NAME_1, serial=b"\x00\x01"), db_row(NAME_2, started_at=STARTED_2)])
        rows, reason = SqliteIndex(db).query("2000-01-01T00:00:00.000000+00:00")
        assert reason is None
        assert [r.inspection_id for r in rows] == [NAME_2]

    def test_missing_db(self, tmp_path: Path) -> None:
        """db_missing if the DB file does not exist."""
        assert SqliteIndex(tmp_path / "none.sqlite").query(STARTED_1) == ([], NOTICE_DB_MISSING)

    def test_newer_schema(self, tmp_path: Path) -> None:
        """user_version 3 gives db_schema_new (falls back to scanning even if columns grew)."""
        db = tmp_path / "db.sqlite"
        make_db(db, [db_row(NAME_1)], user_version=3)
        assert SqliteIndex(db).query(STARTED_1) == ([], NOTICE_DB_SCHEMA_NEW)

    def test_empty_file_is_missing(self, tmp_path: Path) -> None:
        """An empty file without tables (user_version 0) gives db_missing."""
        db = tmp_path / "db.sqlite"
        db.write_bytes(b"")
        assert SqliteIndex(db).query(STARTED_1) == ([], NOTICE_DB_MISSING)

    def test_garbage_file_is_open_failed(self, tmp_path: Path) -> None:
        """A non-SQLite file gives db_open_failed (no exception escapes)."""
        db = tmp_path / "db.sqlite"
        db.write_bytes(b"garbage" * 100)
        assert SqliteIndex(db).query(STARTED_1) == ([], NOTICE_DB_OPEN_FAILED)

    def test_version_ok_but_table_missing(self, tmp_path: Path) -> None:
        """A DB with user_version 1 but no table gives db_open_failed."""
        db = tmp_path / "db.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        assert SqliteIndex(db).query(STARTED_1) == ([], NOTICE_DB_OPEN_FAILED)

    def test_unsearchable_dir_is_open_failed(self, tmp_path: Path) -> None:
        """db_open_failed too when the DB dir has no exec permission (exists() PermissionError)."""
        dbdir = tmp_path / "db"
        dbdir.mkdir()
        db = dbdir / "inspections.sqlite"
        make_db(db, [db_row(NAME_1)])
        dbdir.chmod(0)
        try:
            try:
                db.stat()
            except PermissionError:
                pass
            else:
                pytest.skip("chmod 0 does not deny access here")
            assert SqliteIndex(db).query(STARTED_1) == ([], NOTICE_DB_OPEN_FAILED)
        finally:
            dbdir.chmod(0o700)

    def test_connect_failure_is_open_failed(self, tmp_path: Path) -> None:
        """db_open_failed even when the connection itself fails."""
        db = tmp_path / "db.sqlite"
        make_db(db, [])

        def failing(*args: object, **kwargs: object) -> sqlite3.Connection:
            raise sqlite3.OperationalError("unable to open database file")

        assert SqliteIndex(db, connect=failing).query(STARTED_1) == ([], NOTICE_DB_OPEN_FAILED)
