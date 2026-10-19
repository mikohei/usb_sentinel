"""Tests for ResultStore.collect() (matching against the SQLite index) and time conversion."""

from pathlib import Path

import pytest

from tests.conftest import make_result, sample_result
from tests.test_store import db_row, make_db
from usb_sentinel_display.libs.results import (
    ResultsMissingError,
    ResultStore,
    compact_utc,
    epoch_from_iso,
    file_digest,
    iso_utc,
    started_at_lower_bound,
)
from usb_sentinel_display.libs.store import NOTICE_DB_MISSING, SqliteIndex

NAME = "20250601T120000Z-ABC123"


class TestCompactUtc:
    def test_isoformat_with_microseconds(self) -> None:
        """Turn isoformat() output into YYYYmmddTHHMMSSZ."""
        assert compact_utc("2025-06-01T12:00:00.123456+00:00", NAME) == "20250601T120000Z"

    def test_offset_converted_to_utc(self) -> None:
        """+09:00 is converted to UTC."""
        assert compact_utc("2025-06-01T21:00:00+09:00", NAME) == "20250601T120000Z"

    def test_naive_treated_as_utc(self) -> None:
        """A time without tz is treated as UTC."""
        assert compact_utc("2025-06-01T12:00:00", NAME) == "20250601T120000Z"

    @pytest.mark.parametrize("value", ["not a date", "", 123, None])
    def test_fallback(self, value: object) -> None:
        """If unparsable, the first 16 chars of the directory name."""
        assert compact_utc(value, NAME) == "20250601T120000Z"


class TestIsoConversion:
    def test_iso_utc_matches_backend_format(self) -> None:
        """Epoch seconds in the same form as backend isoformat() (6-digit microseconds, +00:00)."""
        assert iso_utc(1748779200.123456) == "2025-06-01T12:00:00.123456+00:00"
        assert iso_utc(1748779200.0) == "2025-06-01T12:00:00.000000+00:00"

    def test_epoch_round_trip(self) -> None:
        """iso_utc -> epoch_from_iso returns the original value (microsecond precision)."""
        assert epoch_from_iso(iso_utc(1748779200.5)) == 1748779200.5
        assert epoch_from_iso("2025-06-01T21:00:00+09:00") == 1748779200.0
        assert epoch_from_iso("2025-06-01T12:00:00") == 1748779200.0

    @pytest.mark.parametrize("value", ["", "not a date", "2025-13-01T00:00:00"])
    def test_epoch_invalid(self, value: str) -> None:
        assert epoch_from_iso(value) is None

    def test_lower_bound_floors_to_seconds(self) -> None:
        """The bound is floored to seconds without microseconds (like backend rows at 0 us)."""
        assert started_at_lower_bound(1748779200.999999) == "2025-06-01T12:00:00+00:00"
        assert started_at_lower_bound(1748779200.0) == "2025-06-01T12:00:00+00:00"

    def test_lower_bound_orders_below_same_second_rows(self) -> None:
        """Same-second rows are >= the bound with or without microseconds; earlier are below."""
        bound = started_at_lower_bound(1748779200.5)
        assert bound <= "2025-06-01T12:00:00+00:00"
        assert bound <= "2025-06-01T12:00:00.000001+00:00"
        assert bound > "2025-06-01T11:59:59.999999+00:00"


STARTED = "2025-06-01T12:00:00.123456+00:00"
STARTED_EPOCH = 1748779200.123456


class TestCollect:
    """Find candidates via the SQLite index and re-read result.json as the source of truth."""

    def store(self, tmp_path: Path, rows: list[dict[str, object]] | None) -> ResultStore:
        """If rows is None, no DB file is created."""
        root = tmp_path / "results"
        root.mkdir(exist_ok=True)
        db = tmp_path / "inspections.sqlite"
        if rows is not None:
            make_db(db, rows)
        return ResultStore(root, index=SqliteIndex(db))

    def test_without_index_equals_scan(self, tmp_path: Path) -> None:
        """index=None gives the same records as scan() with no notice."""
        root = tmp_path / "results"
        make_result(root, NAME, sample_result(), 100.0)
        store = ResultStore(root)
        records, notice = store.collect(50.0)
        assert notice is None
        assert records == store.scan(50.0)
        assert store.collect(None) == ([], None)

    def test_completed_row_with_matching_json(self, tmp_path: Path) -> None:
        """A result.json whose digest matches a completed row has a summary."""
        root = tmp_path / "results"
        make_result(root, NAME, sample_result(), 100.0)
        digest = file_digest(root / NAME / "result.json")
        store = self.store(tmp_path, [db_row(NAME, started_at=STARTED, result_digest=digest)])
        records, notice = store.collect(50.0)
        assert notice is None
        (record,) = records
        assert record.summary is not None
        assert record.summary.digest == digest
        assert record.broken is False
        assert record.started is False
        assert record.mtime == 100.0

    def test_digest_mismatch_is_broken_immediately(self, tmp_path: Path) -> None:
        """If result_digest differs from the file, broken=True and summary None from the start."""
        root = tmp_path / "results"
        make_result(root, NAME, sample_result(), 100.0)
        store = self.store(tmp_path, [db_row(NAME, started_at=STARTED, result_digest="f" * 64)])
        (record,), _ = store.collect(50.0)
        assert record.broken is True
        assert record.summary is None
        assert record.mtime == 100.0

    def test_started_row_without_json(self, tmp_path: Path) -> None:
        """A started row without result.json: started=True, binding columns, mtime is start time."""
        store = self.store(
            tmp_path,
            [
                db_row(
                    NAME, state="started", started_at=STARTED, port_path="1-1.3", kernel_name="sdb"
                )
            ],
        )
        (record,), notice = store.collect(50.0)
        assert notice is None
        assert record.started is True
        assert record.summary is None and record.broken is False
        assert record.port_path == "1-1.3"
        assert record.kernel_name == "sdb"
        assert record.mtime == STARTED_EPOCH
        assert record.serial_part == "ABC123"

    def test_started_row_with_json_uses_json(self, tmp_path: Path) -> None:
        """A started row with a readable result.json (completion write failed) has a summary."""
        root = tmp_path / "results"
        make_result(root, NAME, sample_result(), 100.0)
        store = self.store(tmp_path, [db_row(NAME, state="started", started_at=STARTED)])
        (record,), _ = store.collect(50.0)
        assert record.summary is not None
        assert record.started is False

    @pytest.mark.parametrize("bad", ["..", "x/y", "20250601T120000Z-../x", "result.json", ""])
    def test_malformed_inspection_id_ignored(self, tmp_path: Path, bad: str) -> None:
        """Rows whose inspection_id is malformed or contains "/" are ignored (no output_dir)."""
        store = self.store(
            tmp_path,
            [
                db_row(bad, started_at=STARTED, output_dir=str(tmp_path)),
                db_row(NAME, state="started", started_at=STARTED),
            ],
        )
        records, _ = store.collect(50.0)
        assert [r.name for r in records] == [NAME]

    def test_missing_db_falls_back_to_scan(self, tmp_path: Path) -> None:
        """Without a DB, db_missing is added to the scan() result."""
        root = tmp_path / "results"
        make_result(root, NAME, sample_result(), 100.0)
        store = self.store(tmp_path, None)
        records, notice = store.collect(50.0)
        assert notice == NOTICE_DB_MISSING
        assert records == store.scan(50.0)
        assert len(records) == 1

    def test_results_dir_missing_raises(self, tmp_path: Path) -> None:
        """A missing results root raises ResultsMissingError as before (even with a DB)."""
        db = tmp_path / "inspections.sqlite"
        make_db(db, [db_row(NAME, started_at=STARTED)])
        store = ResultStore(tmp_path / "none", index=SqliteIndex(db))
        with pytest.raises(ResultsMissingError):
            store.collect(50.0)

    def test_none_min_mtime_does_not_query(self, tmp_path: Path) -> None:
        """If min_mtime is None, only list and do not query the DB."""
        store = self.store(tmp_path, None)
        assert store.collect(None) == ([], None)

    def test_lower_bound_passed_as_iso(self, tmp_path: Path) -> None:
        """min_mtime goes to the DB as ISO 8601; earlier rows are not candidates."""
        store = self.store(tmp_path, [db_row(NAME, state="started", started_at=STARTED)])
        records, _ = store.collect(STARTED_EPOCH + 1.0)
        assert records == []
        records, _ = store.collect(STARTED_EPOCH)
        assert len(records) == 1

    def test_lower_bound_keeps_row_without_microseconds(self, tmp_path: Path) -> None:
        """Rows at 0 us (backend omits ".000000") stay candidates with a same-second bound."""
        store = self.store(
            tmp_path,
            [db_row(NAME, state="started", started_at="2025-06-01T12:00:00+00:00")],
        )
        records, _ = store.collect(1748779200.0)
        assert [r.name for r in records] == [NAME]

    def test_completed_row_with_truncated_json_counts_failures(self, tmp_path: Path) -> None:
        """For a completed row with truncated JSON, broken uses the usual failure count."""
        root = tmp_path / "results"
        make_result(root, NAME, '{"device": {"kern', 100.0)
        store = self.store(tmp_path, [db_row(NAME, started_at=STARTED, result_digest="f" * 64)])
        store.max_failures = 2
        (record,), _ = store.collect(50.0)
        assert record.summary is None and record.broken is False
        (record,), _ = store.collect(50.0)
        assert record.broken is True

    def test_completed_row_without_json(self, tmp_path: Path) -> None:
        """A completed row with no result.json (deleted) has neither summary nor broken."""
        store = self.store(tmp_path, [db_row(NAME, started_at=STARTED)])
        (record,), _ = store.collect(50.0)
        assert record.summary is None
        assert record.broken is False
        assert record.started is False

    def test_records_sorted_by_name(self, tmp_path: Path) -> None:
        other = "20250601T110000Z-ZZZ"
        store = self.store(
            tmp_path,
            [
                db_row(NAME, state="started", started_at=STARTED),
                db_row(other, state="started", started_at="2025-06-01T12:30:00+00:00"),
            ],
        )
        records, _ = store.collect(50.0)
        assert [r.name for r in records] == [other, NAME]
