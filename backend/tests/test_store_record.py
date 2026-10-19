"""SqliteStoreの開始・完了記録、同時書き込み、ファイルモード。

材料（db_path、store、_started、_completed、_rows）はtest_store.pyから借りる。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from tests import test_store
from tests.test_store import INSPECTION_ID, POSIX_ONLY, STARTED_AT, _completed, _rows, _started
from usb_sentinel.libs.report.json_report import digest
from usb_sentinel.libs.store import SqliteStore

db_path = test_store.db_path
store = test_store.store


class TestRecord:
    def test_record_started_inserts_started_row(
        self, store: SqliteStore, db_path: Path, tmp_path: Path
    ) -> None:
        assert store.record_started(_started(tmp_path)) is None

        rows = _rows(db_path)
        assert len(rows) == 1
        row = rows[0]
        assert row["inspection_id"] == INSPECTION_ID
        assert row["state"] == "started"
        assert row["serial"] == "ABC123456789"
        assert row["kernel_name"] == "sda"
        assert row["port_path"] == "1-1.3"
        assert row["started_at"] == STARTED_AT
        assert row["started_compact"] == "20240315T040506Z"
        assert row["output_dir"] == str(tmp_path / INSPECTION_ID)
        assert row["verdict"] is None
        assert row["result_digest"] is None
        assert row["created_at"] == row["updated_at"]

    def test_record_completed_updates_all_columns(
        self, store: SqliteStore, db_path: Path, tmp_path: Path
    ) -> None:
        result = _completed(tmp_path)
        store.record_started(_started(tmp_path))

        assert store.record_completed(result, digest(result)) is None

        rows = _rows(db_path)
        assert len(rows) == 1
        row = rows[0]
        assert row["state"] == "completed"
        assert row["verdict"] == "Counterfeit Suspected"
        assert row["verdict_code"] == "C"
        assert row["result_digest"] == digest(result)
        assert row["elapsed_s"] == 1800.0
        assert row["finished_at"] == "2024-03-15T04:35:06+00:00"
        assert row["step_malware"] == "pass"
        assert row["step_hash_inventory"] == "pass"
        assert row["step_capacity"] == "fail"
        assert row["step_speed"] == "fail"
        assert row["step_unmount"] is None
        assert row["malware_count"] == 0
        assert row["clamav_db_version"] == "27200"
        assert row["clamav_db_date"] == "2024-02-12"
        assert row["clamav_scanned_files"] == 1234
        assert row["clamav_scan_time_s"] == 42.5
        assert row["nominal_bytes"] == 29_000_000_000
        assert row["advertised_bytes"] == 32_000_000_000
        assert row["actual_bytes"] == 30_200_000_000
        assert row["advertised_over"] == 1
        assert row["capacity_kind"] == "counterfeit"
        # 非破壊チェックの"over_advertised"とステップの理由の順序保持和集合（重複なし）。
        assert json.loads(row["counterfeit_reasons"]) == ["over_advertised", "lost_ratio"]
        assert row["lost_ratio"] == 0.7381
        assert row["probe_verdict_line"] is None
        assert row["link_speed_mbps"] == 5000
        assert row["device_class"] == "usb3_flash"
        assert row["device_class_basis"] == "link_speed"
        assert row["read_mb_s"] == 85.3
        assert row["write_mb_s"] == 20.1
        assert row["threshold_read_mb_s"] == 40.0
        assert row["threshold_write_mb_s"] == 12.0
        assert row["speed_basis_read"] == "class"
        assert row["speed_basis_write"] == "class"
        assert row["speed_severity"] == "minor"
        assert json.loads(row["reason_codes"]) == ["c:over", "c:lost", "s:minor"]
        assert json.loads(row["reasons"]) == result.reasons
        assert row["hash_inventory_digest"] == "ab" * 32
        assert row["hash_inventory_lines"] == 1234
        assert json.loads(row["warnings"]) == [
            "ClamAV 定義 DB が 31 日前のもの",
            "種別未設定のためUSBフラッシュ既定値で判定",
            "全体の警告",
        ]
        assert row["created_at"] <= row["updated_at"]

    def test_record_completed_without_started_inserts(
        self, store: SqliteStore, db_path: Path, tmp_path: Path
    ) -> None:
        """開始記録が失敗していても完了行は残す（UPDATE影響0行→INSERT）。"""
        result = _completed(tmp_path)

        assert store.record_completed(result, digest(result)) is None

        rows = _rows(db_path)
        assert len(rows) == 1
        assert rows[0]["state"] == "completed"
        assert rows[0]["inspection_id"] == INSPECTION_ID

    def test_concurrent_record_started_keeps_both_rows(
        self, store: SqliteStore, db_path: Path, tmp_path: Path
    ) -> None:
        """2スレッドが同時に開始記録しても両行が残る（busy_timeoutで待つ）。"""
        results = [_started(tmp_path, f"{INSPECTION_ID}-{i}") for i in range(2)]
        failures: list[str | None] = [None, None]
        barrier = threading.Barrier(2)

        def work(index: int) -> None:
            barrier.wait()
            failures[index] = store.record_started(results[index])

        threads = [threading.Thread(target=work, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert failures == [None, None]
        assert [r["inspection_id"] for r in _rows(db_path)] == [
            f"{INSPECTION_ID}-0",
            f"{INSPECTION_ID}-1",
        ]


class TestFileMode:
    @POSIX_ONLY
    def test_db_file_mode_is_0660(self, store: SqliteStore, db_path: Path, tmp_path: Path) -> None:
        """record_started()後の本体はumaskに関係なく0o660（chmod後の値を見る）。"""
        store.record_started(_started(tmp_path))

        assert db_path.stat().st_mode & 0o777 == 0o660

    def test_chmod_failure_is_reported_separately_from_record(
        self,
        store: SqliteStore,
        db_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """chmodに失敗しても行は書けているので、記録失敗と区別できる文言を返す。"""

        def fail_chmod(self: Path, mode: int) -> None:
            raise PermissionError(1, "Operation not permitted", str(self))

        monkeypatch.setattr(Path, "chmod", fail_chmod)

        reason = store.record_started(_started(tmp_path))

        assert reason is not None
        assert "記録は完了" in reason
        assert "権限設定" in reason
        rows = _rows(db_path)
        assert len(rows) == 1
        assert rows[0]["state"] == "started"

    def test_sidecars_are_removed_after_close(
        self, store: SqliteStore, db_path: Path, tmp_path: Path
    ) -> None:
        """接続を操作ごとに閉じるので、戻った時点で-wal・-shmは無い。"""
        store.record_started(_started(tmp_path))

        assert db_path.exists()
        assert not db_path.with_name(db_path.name + "-wal").exists()
        assert not db_path.with_name(db_path.name + "-shm").exists()

    @POSIX_ONLY
    def test_shm_copies_db_mode_while_another_connection_is_open(
        self, store: SqliteStore, db_path: Path, tmp_path: Path
    ) -> None:
        """別接続が開いている間は-shmが残り、本体のモード0o660を写して作られる。"""
        store.record_started(_started(tmp_path))
        other = sqlite3.connect(str(db_path))
        try:
            other.execute("SELECT count(*) FROM inspections").fetchone()
            store.record_started(_started(tmp_path, f"{INSPECTION_ID}-2"))

            shm = db_path.with_name(db_path.name + "-shm")
            assert shm.exists()
            assert shm.stat().st_mode & 0o777 == db_path.stat().st_mode & 0o777 == 0o660
        finally:
            other.close()
