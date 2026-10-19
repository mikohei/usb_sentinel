"""Tests for Monitor.tick(): systemctl calls, result binding, and connect-time fallback.

Assembly helpers such as build() and static() are borrowed from test_monitor.
"""

import json
from pathlib import Path

import pytest

from tests.conftest import FakeRunner, FakeSysfs, make_result, sample_result
from tests.test_monitor import TEMPLATE, build, static
from usb_sentinel_display.libs import results as results_mod
from usb_sentinel_display.libs.config import DisplayConfig
from usb_sentinel_display.libs.mapper import Mapper
from usb_sentinel_display.libs.models import AttachedDisk, SlotState
from usb_sentinel_display.libs.monitor import Monitor, Snapshot
from usb_sentinel_display.libs.results import ResultStore
from usb_sentinel_display.libs.units import SHOW_ARGV


class TestSystemctl:
    def test_runner_none_does_not_query(self, tmp_path: Path) -> None:
        (tmp_path / "results").mkdir()
        disks = [AttachedDisk("sda", "1-1.3", "ABC123", 1000.0)]
        snap = build(tmp_path, list_disks=static(disks), runner=None).tick()
        assert snap.slots[0].unit_state is None
        assert snap.slots[0].state is SlotState.NO_RESULT

    def test_no_disks_no_call(self, tmp_path: Path) -> None:
        (tmp_path / "results").mkdir()
        runner = FakeRunner()
        build(tmp_path, list_disks=static([]), runner=runner).tick()
        assert runner.calls == []

    def test_activating_is_inspecting(self, tmp_path: Path) -> None:
        (tmp_path / "results").mkdir()
        runner = FakeRunner()
        unit = TEMPLATE.format(kernel="sda")
        runner.register(list(SHOW_ARGV), stdout=f"Id={unit}\nActiveState=activating\n")
        disks = [AttachedDisk("sda", "1-1.3", "ABC123", 1000.0)]
        snap = build(tmp_path, list_disks=static(disks), runner=runner).tick()
        assert snap.slots[0].state is SlotState.INSPECTING
        assert runner.calls == [tuple([*SHOW_ARGV, unit])]


class TestResultsFlow:
    def test_result_bound_from_store(self, tmp_path: Path) -> None:
        """result.json under the results root is bound to the slot."""
        root = tmp_path / "results"
        make_result(root, "20250601T120000Z-ABC123", sample_result(port_path="1-1.3"), 1100.0)
        disks = [AttachedDisk("sda", "1-1.3", "ABC123", 1000.0)]
        snap = build(tmp_path, list_disks=static(disks)).tick()
        assert snap.slots[0].state is SlotState.DONE
        assert snap.slots[0].result is not None

    def test_force_reload_resets_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "results"
        make_result(root, "20250601T120000Z-ABC123", sample_result(), 1100.0)
        disks = [AttachedDisk("sda", "1-1.3", "ABC123", 1000.0)]
        monitor = build(tmp_path, list_disks=static(disks))
        calls = [0]
        original = json.loads

        def counting(data: bytes | str) -> object:
            calls[0] += 1
            return original(data)

        monkeypatch.setattr(results_mod.json, "loads", counting)
        monitor.tick()
        monitor.tick()
        assert calls == [1]
        monitor.force_reload()
        monitor.tick()
        assert calls == [2]

    def test_min_mtime_is_min_attached(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "results").mkdir()
        disks = [
            AttachedDisk("sda", "1-1.3", "A", 1500.0),
            AttachedDisk("sdb", "2-1", "B", 1200.0),
        ]
        monitor = build(tmp_path, list_disks=static(disks))
        seen: list[float | None] = []

        def scan(min_mtime: float | None) -> list[object]:
            seen.append(min_mtime)
            return []

        monkeypatch.setattr(monitor.store, "scan", scan)
        monitor.tick()
        assert seen == [1200.0]

    def test_real_sysfs_listing(self, fake_sysfs: FakeSysfs) -> None:
        """The default list_usb_disks reads two disks from the fake tree."""
        root = fake_sysfs.root / "results"
        root.mkdir()
        config = DisplayConfig(results_dir=root, sys_block=fake_sysfs.sys_block)
        monitor = Monitor(
            config, store=ResultStore(root), runner=None, mapper=Mapper((), retain_s=60.0)
        )
        snap = monitor.tick()
        assert isinstance(snap, Snapshot)
        assert [s.disk.kernel_name for s in snap.slots if s.disk] == ["sda", "sdb"]


class TestFallbackTime:
    def test_uses_boot_time_or_first_seen(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without boot_time, return the first-seen time; it is stable for the same kernel name."""
        (tmp_path / "results").mkdir()
        monitor = build(tmp_path, list_disks=static([]), now=4242.0)
        import usb_sentinel_display.libs.monitor as monitor_mod

        monkeypatch.setattr(monitor_mod, "boot_time", lambda: None)
        assert monitor._fallback_time("sda") == 4242.0
        assert monitor._fallback_time("sda") == 4242.0
        monkeypatch.setattr(monitor_mod, "boot_time", lambda: 100.0)
        assert monitor._fallback_time("sdb") == 100.0
