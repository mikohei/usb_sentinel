"""Inspection records in SQLite (libs/store/).

Recording must not affect inspection success. Verify that even with a corrupt DB or a DB with a
newer schema version, no exception escapes and a reason string is returned. The DB is created as
a file under tmp_path (:memory: cannot verify WAL or chmod).
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from usb_sentinel.libs import Config, DeviceInfo, InspectionResult, StepResult, StepStatus, Verdict
from usb_sentinel.libs.store import NullStore, SqliteStore, store_for
from usb_sentinel.libs.store.schema import SCHEMA_VERSION

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="file modes are POSIX only")

STARTED_AT = "2024-03-15T04:05:06+00:00"
INSPECTION_ID = "20240315T040506Z-ABC123456789"


def _device() -> DeviceInfo:
    return DeviceInfo(
        path="/dev/sda",
        kernel_name="sda",
        advertised_bytes=32_000_000_000,
        vendor="Generic",
        model="Flash Disk",
        serial="ABC123456789",
        removable=True,
        bus="usb",
        port_path="1-1.3",
        link_speed_mbps=5000,
    )


def _started(tmp_path: Path, inspection_id: str = INSPECTION_ID) -> InspectionResult:
    """Result at the point inspect() calls the start record (no steps, advertised_check only)."""
    result = InspectionResult(_device(), STARTED_AT, output_dir=str(tmp_path / inspection_id))
    result.advertised_check = {
        "nominal_bytes": 29_000_000_000,
        "advertised_bytes": 32_000_000_000,
        "over_ratio": 1.07,
        "over_threshold_bytes": 31_030_000_000,
        "advertised_ratio": 1.1034,
        "exceeded": True,
        "summary": "advertised capacity 32.0GB > limit 31.0GB (110.3% of nominal 29.0GB)",
    }
    return result


def _completed(tmp_path: Path) -> InspectionResult:
    """Completed result with every step filled in."""
    result = _started(tmp_path)
    result.steps = [
        StepResult(
            "malware",
            StepStatus.PASS,
            "No malware",
            details={
                "database": {
                    "engine_version": "1.0.3",
                    "db_version": "27200",
                    "db_date": "2024-02-12T09:23:12",
                    "stale": False,
                },
                "detections": [],
                "scan_summary": {"scanned_files": 1234, "time_s": 42.5, "infected_files": 0},
            },
            warnings=["ClamAV definition DB is 31 days old"],
            elapsed_s=42.51,
        ),
        StepResult(
            "hash_inventory",
            StepStatus.PASS,
            "1234 files",
            details={"lines": 1234, "digest": "ab" * 32},
            elapsed_s=3.0,
        ),
        StepResult(
            "capacity",
            StepStatus.FAIL,
            "Suspected counterfeit: ...",
            details={
                "actual_bytes": 30_200_000_000,
                "kind": "counterfeit",
                "counterfeit_reasons": ["over_advertised", "lost_ratio"],
                "lost_ratio": 0.7381,
                "probe_verdict_line": None,
            },
            elapsed_s=100.0,
        ),
        StepResult(
            "speed",
            StepStatus.FAIL,
            "Speed shortfall (minor): ...",
            details={
                "device_class": "usb3_flash",
                "device_class_basis": "link_speed",
                "read": {"mb_s": 85.3},
                "write": {"mb_s": 20.1},
                "threshold_read_mb_s": 40.0,
                "threshold_write_mb_s": 12.0,
                "basis_read": "class",
                "basis_write": "class",
                "severity": "minor",
            },
            warnings=["Device class not set; judged with USB flash defaults"],
            elapsed_s=60.0,
        ),
    ]
    result.verdict = Verdict.COUNTERFEIT_SUSPECTED
    result.reasons = [
        "advertised capacity 32.0GB > limit 31.0GB (110.3% of nominal 29.0GB)",
        "capacity: Suspected counterfeit",
    ]
    result.reason_codes = ["c:over", "c:lost", "s:minor"]
    result.warnings = ["inspection-wide warning"]
    result.finished_at = "2024-03-15T04:35:06+00:00"
    result.elapsed_s = 1800.0
    return result


def _rows(path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM inspections ORDER BY inspection_id")]
    finally:
        conn.close()


def _pragma(path: Path, name: str) -> int | str:
    conn = sqlite3.connect(str(path))
    try:
        value = conn.execute(f"PRAGMA {name}").fetchone()[0]
    finally:
        conn.close()
    assert isinstance(value, int | str)
    return value


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "db" / "inspections.sqlite"


@pytest.fixture
def store(db_path: Path) -> SqliteStore:
    db_path.parent.mkdir()
    return SqliteStore(db_path)


class TestSchema:
    def test_first_open_creates_schema(
        self, store: SqliteStore, db_path: Path, tmp_path: Path
    ) -> None:
        """The first connection sets user_version=1 and creates the inspections table and five
        indexes."""
        assert store.record_started(_started(tmp_path)) is None

        assert _pragma(db_path, "user_version") == SCHEMA_VERSION == 1
        conn = sqlite3.connect(str(db_path))
        try:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            indexes = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
                )
            }
        finally:
            conn.close()
        assert tables == {"inspections"}
        assert indexes == {
            "idx_inspections_started",
            "idx_inspections_serial",
            "idx_inspections_port",
            "idx_inspections_state",
            "idx_inspections_digest",
        }

    def test_journal_mode_is_wal(self, store: SqliteStore, db_path: Path, tmp_path: Path) -> None:
        store.record_started(_started(tmp_path))

        assert _pragma(db_path, "journal_mode") == "wal"

    def test_newer_schema_is_not_written(
        self, store: SqliteStore, db_path: Path, tmp_path: Path
    ) -> None:
        """Do not write to a DB with a newer user_version; return a reason string."""
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA user_version = 2")
        conn.close()

        failure = store.record_started(_started(tmp_path))

        assert failure is not None
        assert "schema version 2" in failure
        conn = sqlite3.connect(str(db_path))
        try:
            assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
        finally:
            conn.close()

    def test_corrupt_file_returns_reason_without_raising(
        self, store: SqliteStore, db_path: Path, tmp_path: Path
    ) -> None:
        db_path.write_bytes(b"garbage" * 100)

        failure_started = store.record_started(_started(tmp_path))
        failure_completed = store.record_completed(_completed(tmp_path), "0" * 64)

        assert failure_started is not None and "SQLite" in failure_started
        assert failure_completed is not None and "SQLite" in failure_completed
        assert db_path.read_bytes() == b"garbage" * 100


class TestStoreFor:
    def test_null_store_returns_none(self, tmp_path: Path) -> None:
        store = NullStore()

        assert store.record_started(_started(tmp_path)) is None
        assert store.record_completed(_completed(tmp_path), "0" * 64) is None

    def test_store_for_selects_by_sqlite_path(self, tmp_path: Path) -> None:
        config = Config(results_dir=tmp_path / "r", mount_root=tmp_path / "m")

        assert isinstance(store_for(replace(config, sqlite_path=None)), NullStore)
        chosen = store_for(replace(config, sqlite_path=tmp_path / "x.sqlite"))
        assert isinstance(chosen, SqliteStore)
        assert chosen.path == tmp_path / "x.sqlite"
