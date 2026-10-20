from collections.abc import Callable
from pathlib import Path

import pytest

from tests.conftest import FakeRunner, make_result, sample_result
from tests.test_store import db_row, make_db
from usb_sentinel_display.libs.config import DisplayConfig, PortMapping
from usb_sentinel_display.libs.mapper import Mapper
from usb_sentinel_display.libs.models import AttachedDisk, SlotState
from usb_sentinel_display.libs.monitor import (
    NOTICE_RESULTS_MISSING,
    NOTICE_RESULTS_PERMISSION,
    NOTICE_SYSFS_MISSING,
    NOTICE_SYSFS_PERMISSION,
    Monitor,
)
from usb_sentinel_display.libs.results import (
    ResultsMissingError,
    ResultsPermissionError,
    ResultStore,
)
from usb_sentinel_display.libs.store import (
    NOTICE_DB_MISSING,
    NOTICE_DB_OPEN_FAILED,
    NOTICE_DB_SCHEMA_NEW,
    SqliteIndex,
)
from usb_sentinel_display.libs.sysfs import SysfsMissingError, SysfsPermissionError

TEMPLATE = "usb-sentinel-docker@{kernel}.service"
ListDisks = Callable[..., list[AttachedDisk]]


def raising(exc: Exception) -> ListDisks:
    def list_disks(sys_block: Path, *, fallback_time: Callable[[str], float]) -> list[AttachedDisk]:
        raise exc

    return list_disks


def static(disks: list[AttachedDisk]) -> ListDisks:
    def list_disks(sys_block: Path, *, fallback_time: Callable[[str], float]) -> list[AttachedDisk]:
        return disks

    return list_disks


def build(
    tmp_path: Path,
    *,
    list_disks: ListDisks,
    runner: FakeRunner | None = None,
    results_dir: Path | None = None,
    now: float = 5000.0,
    ports: tuple[PortMapping, ...] = (PortMapping("P1", ("1-1.3",)), PortMapping("P2", ("2-1",))),
) -> Monitor:
    root = results_dir if results_dir is not None else tmp_path / "results"
    config = DisplayConfig(results_dir=root, sys_block=tmp_path / "sys_block", ports=ports)
    return Monitor(
        config,
        store=ResultStore(root),
        runner=runner,
        mapper=Mapper(ports, retain_s=3600.0),
        list_disks=list_disks,
        now=lambda: now,
    )


class TestNotices:
    def test_sysfs_missing(self, tmp_path: Path) -> None:
        (tmp_path / "results").mkdir()
        snap = build(tmp_path, list_disks=raising(SysfsMissingError("x"))).tick()
        assert snap.notice == NOTICE_SYSFS_MISSING
        assert snap.error is None
        assert [s.state for s in snap.slots] == [SlotState.EMPTY] * 4

    def test_sysfs_permission(self, tmp_path: Path) -> None:
        (tmp_path / "results").mkdir()
        snap = build(tmp_path, list_disks=raising(SysfsPermissionError("x"))).tick()
        assert snap.notice == NOTICE_SYSFS_PERMISSION

    def test_results_missing(self, tmp_path: Path) -> None:
        snap = build(tmp_path, list_disks=static([])).tick()
        assert snap.notice == NOTICE_RESULTS_MISSING
        assert snap.error is None

    def test_results_permission(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monitor = build(tmp_path, list_disks=static([]))

        def deny(min_mtime: float | None) -> list[object]:
            raise ResultsPermissionError("x")

        monkeypatch.setattr(monitor.store, "scan", deny)
        assert monitor.tick().notice == NOTICE_RESULTS_PERMISSION

    def test_results_missing_error_type_from_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monitor = build(tmp_path, list_disks=static([]))

        def missing(min_mtime: float | None) -> list[object]:
            raise ResultsMissingError("x")

        monkeypatch.setattr(monitor.store, "scan", missing)
        assert monitor.tick().notice == NOTICE_RESULTS_MISSING

    def test_no_notice_when_healthy(self, tmp_path: Path) -> None:
        (tmp_path / "results").mkdir()
        snap = build(tmp_path, list_disks=static([])).tick()
        assert snap.notice is None
        assert snap.tick_count == 1

    def test_db_notice_from_collect(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """db_missing from store.collect() becomes the notice; scan records are used as-is."""
        root = tmp_path / "results"
        make_result(root, "20250601T120000Z-ABC123", sample_result(port_path="1-1.3"), 1100.0)
        disks = [AttachedDisk("sda", "1-1.3", "ABC123", 1000.0)]
        monitor = build(tmp_path, list_disks=static(disks))
        original = monitor.store.collect

        def collect(min_mtime: float | None) -> tuple[list[object], str | None]:
            records, _ = original(min_mtime)
            return list(records), NOTICE_DB_MISSING

        monkeypatch.setattr(monitor.store, "collect", collect)
        snap = monitor.tick()
        assert snap.notice == NOTICE_DB_MISSING
        assert snap.error is None
        assert snap.slots[0].state is SlotState.DONE

    def test_results_missing_wins_over_db_notice(self, tmp_path: Path) -> None:
        """If the results directory is missing, results_missing wins even without a DB."""
        db = tmp_path / "none.sqlite"
        store = ResultStore(tmp_path / "results", index=SqliteIndex(db))
        monitor = build(tmp_path, list_disks=static([]))
        monitor.store = store
        assert monitor.tick().notice == NOTICE_RESULTS_MISSING

    @pytest.mark.parametrize("kind", ["missing", "garbage", "schema3"])
    def test_broken_index_falls_back_without_exception(self, tmp_path: Path, kind: str) -> None:
        """Missing, corrupt, or schema-v3 DB: tick() never raises; returns records and a notice."""
        root = tmp_path / "results"
        make_result(root, "20250601T120000Z-ABC123", sample_result(port_path="1-1.3"), 1100.0)
        db = tmp_path / "inspections.sqlite"
        if kind == "garbage":
            db.write_bytes(b"garbage" * 100)
        elif kind == "schema3":
            make_db(db, [], user_version=3)
        disks = [AttachedDisk("sda", "1-1.3", "ABC123", 1000.0)]
        monitor = build(tmp_path, list_disks=static(disks))
        monitor.store = ResultStore(root, index=SqliteIndex(db))
        snap = monitor.tick()
        assert snap.error is None
        assert (
            snap.notice
            == {
                "missing": NOTICE_DB_MISSING,
                "garbage": NOTICE_DB_OPEN_FAILED,
                "schema3": NOTICE_DB_SCHEMA_NEW,
            }[kind]
        )
        assert snap.slots[0].state is SlotState.DONE

    def test_index_used_when_healthy(self, tmp_path: Path) -> None:
        """With a DB there is no notice, and a started row feeds the INSPECTING state."""
        root = tmp_path / "results"
        root.mkdir()
        db = tmp_path / "inspections.sqlite"
        make_db(
            db,
            [
                db_row(
                    "20250601T120000Z-ABC123",
                    state="started",
                    started_at="2025-06-01T12:00:00.123456+00:00",
                    port_path="1-1.3",
                )
            ],
        )
        disks = [AttachedDisk("sda", "1-1.3", "ABC123", 1748779200.0)]
        monitor = build(tmp_path, list_disks=static(disks), now=1748779200.0 + 600.0)
        monitor.store = ResultStore(root, index=SqliteIndex(db))
        snap = monitor.tick()
        assert snap.notice is None
        assert snap.slots[0].state is SlotState.INSPECTING


class TestErrors:
    def make_failing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Monitor:
        (tmp_path / "results").mkdir()
        monitor = build(tmp_path, list_disks=static([]))

        def boom(*args: object, **kwargs: object) -> object:
            raise RuntimeError("boom")

        monkeypatch.setattr(monitor.mapper, "update", boom)
        return monitor

    def test_mapper_exception_contained(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Mapper errors stay in tick(): the class name goes to error and prior slots are kept."""
        (tmp_path / "results").mkdir()
        monitor = build(tmp_path, list_disks=static([]))
        first = monitor.tick()
        assert first.error is None

        def boom(*args: object, **kwargs: object) -> object:
            raise RuntimeError("boom")

        monkeypatch.setattr(monitor.mapper, "update", boom)
        snap = monitor.tick()
        assert snap.error == "RuntimeError"
        assert snap.error_repeated is False
        assert snap.slots == first.slots
        assert snap.overflow == first.overflow
        assert "RuntimeError: boom" in capsys.readouterr().err

    def test_repeated_errors_change_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monitor = self.make_failing(tmp_path, monkeypatch)
        for _ in range(29):
            snap = monitor.tick()
            assert (snap.error, snap.error_repeated) == ("RuntimeError", False)
        snap = monitor.tick()
        assert (snap.error, snap.error_repeated) == ("RuntimeError", True)
        capsys.readouterr()

    def test_different_class_resets_counter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monitor = self.make_failing(tmp_path, monkeypatch)
        for _ in range(29):
            monitor.tick()

        def other(*args: object, **kwargs: object) -> object:
            raise KeyError("k")

        monkeypatch.setattr(monitor.mapper, "update", other)
        snap = monitor.tick()
        assert (snap.error, snap.error_repeated) == ("KeyError", False)
        for _ in range(27):
            monitor.tick()
        snap = monitor.tick()
        assert (snap.error, snap.error_repeated) == ("KeyError", False)
        snap = monitor.tick()
        assert (snap.error, snap.error_repeated) == ("KeyError", True)
        capsys.readouterr()

    def test_success_resets_counter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "results").mkdir()
        monitor = build(tmp_path, list_disks=static([]))
        original = monitor.mapper.update

        def boom(*args: object, **kwargs: object) -> object:
            raise RuntimeError("boom")

        monkeypatch.setattr(monitor.mapper, "update", boom)
        for _ in range(29):
            monitor.tick()
        monkeypatch.setattr(monitor.mapper, "update", original)
        assert monitor.tick().error is None
        monkeypatch.setattr(monitor.mapper, "update", boom)
        snap = monitor.tick()
        assert (snap.error, snap.error_repeated) == ("RuntimeError", False)
        capsys.readouterr()
