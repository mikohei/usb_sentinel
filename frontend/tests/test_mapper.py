from pathlib import Path

from usb_sentinel_display.libs.config import PortMapping
from usb_sentinel_display.libs.mapper import bind_broken, bind_result, bind_started
from usb_sentinel_display.libs.models import (
    AttachedDisk,
    ResultRecord,
    ResultSummary,
    Slot,
    SlotState,
)
from usb_sentinel_display.libs.results import serial_for_dir

TEMPLATE = "usb-sentinel-docker@{kernel}.service"
PORTS = (
    PortMapping("P1", ("1-1.1", "2-1")),
    PortMapping("P2", ("1-1.2", "2-2")),
    PortMapping("P3", ("1-1.3",)),
    PortMapping("P4", ("1-1.4",)),
)


def disk(
    kernel: str = "sda", port: str = "1-1.1", serial: str = "ABC123", at: float = 1000.0
) -> AttachedDisk:
    return AttachedDisk(kernel, port, serial, at)


def summary(
    kernel: str = "sda",
    serial: str = "ABC123",
    port_path: str | None = None,
    mtime: float = 1100.0,
    name: str = "20250601T120000Z-ABC123",
) -> ResultSummary:
    return ResultSummary(
        dir_name=name,
        path=Path("/results") / name / "result.json",
        mtime=mtime,
        verdict="No Defect",
        serial=serial,
        vendor="V",
        model="M",
        kernel_name=kernel,
        port_path=port_path,
        started_at="2025-06-01T12:00:00+00:00",
        finished_at="",
        steps=(("malware", "pass"),),
        malware_count=0,
        digest="0" * 64,
    )


def record(
    kernel: str = "sda",
    serial: str = "ABC123",
    port_path: str | None = None,
    mtime: float = 1100.0,
    name: str | None = None,
    broken: bool = False,
) -> ResultRecord:
    dir_name = name or f"20250601T120000Z-{serial_for_dir(serial, kernel)}"
    serial_part = dir_name.split("-", 1)[1]
    if broken:
        return ResultRecord(dir_name, mtime, serial_part, None, True)
    return ResultRecord(
        dir_name, mtime, serial_part, summary(kernel, serial, port_path, mtime, dir_name), False
    )


def started(
    kernel: str = "sda",
    port_path: str | None = None,
    mtime: float = 1050.0,
    name: str = "20250601T120000Z-ABC123",
) -> ResultRecord:
    """A started-row record, which only the DB path produces."""
    return ResultRecord(
        name,
        mtime,
        name.split("-", 1)[1],
        None,
        False,
        started=True,
        port_path=port_path,
        kernel_name=kernel,
    )


def active(*kernels: str) -> dict[str, str]:
    return {TEMPLATE.format(kernel=k): "active" for k in kernels}


def inactive(*kernels: str) -> dict[str, str]:
    return {TEMPLATE.format(kernel=k): "inactive" for k in kernels}


def states(slots: list[Slot]) -> list[SlotState]:
    return [s.state for s in slots]


class TestBindResult:
    def test_kernel_name_key(self) -> None:
        """A result without port_path is bound by kernel_name."""
        assert bind_result(disk(), [record()]) is not None

    def test_older_than_attached_not_bound(self) -> None:
        """Results older than the connect time are not picked up."""
        assert bind_result(disk(at=2000.0), [record(mtime=1500.0)]) is None

    def test_rebooted_frontend_still_binds(self) -> None:
        """If attached_at precedes the result, it binds (even after a frontend restart)."""
        assert bind_result(disk(at=900.0), [record(mtime=1500.0)]) is not None

    def test_serial_mismatch_binds_by_kernel(self) -> None:
        """sysfs serial ABC 123 and directory name ABC_123 still bind via kernel_name."""
        found = bind_result(disk(serial="ABC 123"), [record(serial="ABC_123")])
        assert found is not None

    def test_port_path_preferred_over_kernel(self) -> None:
        """A result with port_path binds by port_path and is used even if kernel_name differs."""
        rec = record(kernel="sdb", port_path="1-1.1")
        assert bind_result(disk(kernel="sda", port="1-1.1"), [rec]) is rec

    def test_port_path_mismatch_not_bound(self) -> None:
        """A result with port_path is rejected if port_path differs, even with same kernel_name."""
        assert bind_result(disk(port="1-1.2"), [record(port_path="1-1.1")]) is None

    def test_prefers_matching_serial_then_newest(self) -> None:
        """With several candidates, prefer a dir-name serial match; on a tie, the max name."""
        other = record(serial="OTHER", mtime=1200.0, name="20250601T130000Z-OTHER")
        mine_old = record(mtime=1100.0, name="20250601T120000Z-ABC123")
        mine_new = record(mtime=1150.0, name="20250601T120500Z-ABC123")
        assert bind_result(disk(), [other, mine_old, mine_new]) is mine_new
        assert bind_result(disk(serial="ZZZ"), [other, mine_old]) is other

    def test_unreadable_records_skipped(self) -> None:
        assert bind_result(disk(), [record(broken=True)]) is None


class TestBindBroken:
    def test_matches_serial_part(self) -> None:
        rec = record(broken=True)
        assert bind_broken(disk(), [rec]) is rec

    def test_requires_mtime_and_serial(self) -> None:
        assert bind_broken(disk(at=2000.0), [record(broken=True, mtime=1500.0)]) is None
        assert bind_broken(disk(serial="X"), [record(broken=True)]) is None
        assert bind_broken(disk(), [record()]) is None


class TestBindStarted:
    def test_kernel_name_when_no_port_path(self) -> None:
        """A started row without port_path binds by kernel_name."""
        rec = started()
        assert bind_started(disk(), [rec]) is rec
        assert bind_started(disk(kernel="sdb"), [rec]) is None

    def test_port_path_mismatch_not_bound(self) -> None:
        """A started row with port_path does not bind on a port_path mismatch, even same kernel."""
        assert bind_started(disk(port="1-1.2"), [started(port_path="1-1.1")]) is None
        rec = started(kernel="sdz", port_path="1-1.1")
        assert bind_started(disk(port="1-1.1"), [rec]) is rec

    def test_requires_mtime_after_attached(self) -> None:
        """No binding if the start time precedes the connect time."""
        assert bind_started(disk(at=2000.0), [started(mtime=1500.0)]) is None

    def test_ignores_non_started_and_picks_newest(self) -> None:
        """Ignore anything but started; with several, take the max name."""
        assert bind_started(disk(), [record(), record(broken=True)]) is None
        old = started(name="20250601T120000Z-ABC123")
        new = started(name="20250601T120500Z-ABC123")
        assert bind_started(disk(), [old, new]) is new
