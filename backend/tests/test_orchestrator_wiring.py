"""Wiring between inspect() and SQLite recording, scan_executed(), and result directory names.

Helpers (full_runner, _statuses) are borrowed from test_orchestrator.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tests import test_orchestrator
from tests.test_orchestrator import _statuses
from usb_sentinel.libs import (
    Config,
    DeviceInfo,
    FakeRunner,
    InspectionResult,
    StepStatus,
    Verdict,
    inspect,
    result_dir_for,
)
from usb_sentinel.libs.models import StepResult
from usb_sentinel.libs.orchestrator import scan_executed

full_runner = test_orchestrator.full_runner


class RecordingStore:
    """Fake store that records calls and returns the given failure string."""

    def __init__(self, started_failure: str | None = None) -> None:
        self.started_failure = started_failure
        self.started: list[InspectionResult] = []
        self.completed: list[tuple[InspectionResult, str]] = []

    def record_started(self, result: InspectionResult) -> str | None:
        self.started.append(result)
        return self.started_failure

    def record_completed(self, result: InspectionResult, digest: str) -> str | None:
        self.completed.append((result, digest))
        return None


class TestStoreWiring:
    def test_record_started_called_once_with_output_dir(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        """inspect() calls the start record exactly once after deciding output_dir. It does not
        call the completion record."""
        store = RecordingStore()

        result = inspect(full_runner, config, "/dev/sda", store)

        assert len(store.started) == 1
        assert store.started[0] is result
        assert store.started[0].output_dir == result.output_dir
        assert Path(result.output_dir).is_dir()
        assert store.completed == []
        assert result.warnings == []

    def test_started_failure_becomes_warning_and_inspection_continues(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        store = RecordingStore(started_failure="SQLiteエラー: database is locked")

        result = inspect(full_runner, config, "/dev/sda", store)

        assert result.warnings == ["SQLite記録失敗（開始）: SQLiteエラー: database is locked"]
        assert _statuses(result)["malware"] is StepStatus.PASS
        assert result.verdict is Verdict.INCONCLUSIVE

    def test_store_none_is_accepted(self, full_runner: FakeRunner, config: Config) -> None:
        result = inspect(full_runner, config, "/dev/sda", None)

        assert result.warnings == []
        assert _statuses(result)["malware"] is StepStatus.PASS


class TestScanExecuted:
    def test_true_when_detections_key_present(self) -> None:
        """detections is set only after clamscan --recursive returns (empty still means it
        ran)."""
        step = StepResult("malware", StepStatus.ERROR, details={"detections": []})
        assert scan_executed(step) is True

    def test_false_without_detections_key(self) -> None:
        step = StepResult("malware", StepStatus.ERROR, details={"database": {}})
        assert scan_executed(step) is False


def test_result_dir_sanitizes_serial() -> None:
    """Keep the directory name safe even if the serial contains unusable characters."""
    device = DeviceInfo(
        path="/dev/sda",
        kernel_name="sda",
        advertised_bytes=1,
        serial="AB/CD:12",
    )
    started = datetime(2024, 3, 15, 4, 5, 6, tzinfo=UTC)

    path = result_dir_for(Path("/var/lib/x"), started, device)

    assert path.name == "20240315T040506Z-AB_CD_12"


def test_result_dir_without_serial() -> None:
    device = DeviceInfo(path="/dev/sda", kernel_name="sda", advertised_bytes=1)
    started = datetime(2024, 3, 15, 4, 5, 6, tzinfo=UTC)

    assert result_dir_for(Path("/x"), started, device).name.endswith("sda-unknown-serial")
