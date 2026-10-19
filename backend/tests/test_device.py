"""Device identification and safety guards.

Destructive checks follow, so a wrong pass here causes real damage. Cover every case that
must be rejected.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from usb_sentinel.libs import DeviceInfo, FakeRunner, UnsafeDeviceError
from usb_sentinel.libs import device as device_mod

LSBLK_USB = {
    "blockdevices": [
        {
            "name": "sda",
            "size": 32_000_000_000,
            "vendor": "Generic ",
            "model": "Flash Disk ",
            "serial": "ABC123456789",
            "rm": True,
            "type": "disk",
            "mountpoint": None,
        }
    ]
}

UDEVADM_USB = "DEVNAME=/dev/sda\nID_BUS=usb\nID_MODEL=Flash_Disk\n"


@pytest.fixture
def probe_runner() -> FakeRunner:
    """FakeRunner with the commands probe() needs registered."""
    fake = FakeRunner()
    fake.register(["lsblk"], stdout=json.dumps(LSBLK_USB))
    fake.register(["udevadm", "info"], stdout=UDEVADM_USB)
    return fake


@pytest.fixture
def sys_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Replace `/sys/block` with tmp_path (it does not exist on macOS)."""
    root = tmp_path / "sys_block"
    (root / "sda").mkdir(parents=True)
    (root / "sda" / "removable").write_text("1\n")
    # Root disk mmcblk0 and its partitions.
    (root / "mmcblk0" / "mmcblk0p2").mkdir(parents=True)
    (root / "mmcblk0" / "removable").write_text("0\n")
    monkeypatch.setattr(device_mod, "SYS_BLOCK", root)
    monkeypatch.setattr(device_mod, "SYS_USB_DEVICES", tmp_path / "sys_usb")
    return root


USB_SDA = (
    "devices/platform/scb/fd500000.pcie/pci0000:00/0000:00:00.0/0000:01:00.0"
    "/usb1/1-1/1-1.3/1-1.3:1.0/host0/target0:0:0/0:0:0:0/block/sda"
)
USB_SDB = (
    "devices/platform/scb/fd500000.pcie/pci0000:00/0000:00:00.0/0000:01:00.0"
    "/usb2/2-1/2-1:1.0/host1/target1:0:0/1:0:0:0/block/sdb"
)
MMC_BLK0 = "devices/platform/emmc2bus/fe340000.mmc/mmc_host/mmc0/mmc0:0001/block/mmcblk0"


@pytest.fixture
def sysfs_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fake tree of symlinks to device paths, like /sys/block on the real device."""
    root = tmp_path / "sys_block"
    root.mkdir()
    for name, rel in (("sda", USB_SDA), ("sdb", USB_SDB), ("mmcblk0", MMC_BLK0)):
        real = tmp_path / rel
        real.mkdir(parents=True)
        (root / name).symlink_to(real)
    monkeypatch.setattr(device_mod, "SYS_BLOCK", root)
    monkeypatch.setattr(device_mod, "SYS_USB_DEVICES", tmp_path / "sys_usb")
    return root


def _write_speed(sys_usb: Path, port_path: str, text: str) -> None:
    """Create a fake /sys/bus/usb/devices/<port_path>/speed."""
    (sys_usb / port_path).mkdir(parents=True, exist_ok=True)
    (sys_usb / port_path / "speed").write_text(text)


class TestLinkSpeed:
    def test_reads_integer(self, tmp_path: Path) -> None:
        _write_speed(tmp_path, "1-1.3", "5000\n")

        assert device_mod.link_speed_for("1-1.3", tmp_path) == 5000

    def test_missing_directory_is_none(self, tmp_path: Path) -> None:
        assert device_mod.link_speed_for("1-1.3", tmp_path) is None

    def test_non_numeric_is_none(self, tmp_path: Path) -> None:
        _write_speed(tmp_path, "1-1.3", "fast\n")

        assert device_mod.link_speed_for("1-1.3", tmp_path) is None

    def test_fraction_is_truncated(self, tmp_path: Path) -> None:
        """USB 1.1 Low Speed `1.5` becomes 1 via int(float)."""
        _write_speed(tmp_path, "1-1.3", "1.5\n")

        assert device_mod.link_speed_for("1-1.3", tmp_path) == 1

    def test_none_port_path_is_none(self, tmp_path: Path) -> None:
        assert device_mod.link_speed_for(None, tmp_path) is None

    def test_probe_reads_link_speed(self, probe_runner: FakeRunner, sysfs_links: Path) -> None:
        """probe() copies the speed of port path 1-1.3 into DeviceInfo.link_speed_mbps."""
        _write_speed(sysfs_links.parent / "sys_usb", "1-1.3", "480\n")

        info = device_mod.probe(probe_runner, "/dev/sda")

        assert info.port_path == "1-1.3"
        assert info.link_speed_mbps == 480

    def test_probe_without_speed_file_is_none(
        self, probe_runner: FakeRunner, sysfs_links: Path
    ) -> None:
        info = device_mod.probe(probe_runner, "/dev/sda")

        assert info.port_path == "1-1.3"
        assert info.link_speed_mbps is None


class TestPortPath:
    def test_extracts_nested_port(self, sysfs_links: Path) -> None:
        assert device_mod.port_path_for("sda") == "1-1.3"

    def test_extracts_root_port(self, sysfs_links: Path) -> None:
        assert device_mod.port_path_for("sdb") == "2-1"

    def test_non_usb_device_is_none(self, sysfs_links: Path) -> None:
        assert device_mod.port_path_for("mmcblk0") is None

    def test_missing_device_is_none(self, sysfs_links: Path) -> None:
        assert device_mod.port_path_for("sdz") is None

    def test_explicit_sys_block(self, sysfs_links: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Passing sys_block explicitly avoids depending on the module variable."""
        monkeypatch.setattr(device_mod, "SYS_BLOCK", Path("/nonexistent"))

        assert device_mod.port_path_for("sda", sysfs_links) == "1-1.3"
        assert device_mod.port_path_for("sdb", sys_block=sysfs_links) == "2-1"
        assert device_mod.port_path_for("mmcblk0", sysfs_links) is None


class TestProbe:
    def test_port_path_from_sysfs(self, probe_runner: FakeRunner, sysfs_links: Path) -> None:
        assert device_mod.probe(probe_runner, "/dev/sda").port_path == "1-1.3"

    def test_port_path_none_without_sysfs(self, probe_runner: FakeRunner, sys_block: Path) -> None:
        """If sys_block has no link, port_path is None."""
        assert device_mod.probe(probe_runner, "/dev/sda").port_path is None

    def test_reads_device_attributes(self, probe_runner: FakeRunner) -> None:
        info = device_mod.probe(probe_runner, "/dev/sda")

        assert info.kernel_name == "sda"
        assert info.advertised_bytes == 32_000_000_000
        # Leading and trailing whitespace is stripped from lsblk values.
        assert info.vendor == "Generic"
        assert info.model == "Flash Disk"
        assert info.serial == "ABC123456789"
        assert info.removable is True
        assert info.bus == "usb"

    def test_rejects_partition(self, probe_runner: FakeRunner) -> None:
        """Reject a partition, since it is not a whole disk."""
        payload = json.loads(json.dumps(LSBLK_USB))
        payload["blockdevices"][0]["type"] = "part"
        probe_runner.register(["lsblk"], stdout=json.dumps(payload))

        with pytest.raises(UnsafeDeviceError, match="whole disk"):
            device_mod.probe(probe_runner, "/dev/sda1")

    def test_rejects_missing_device(self, probe_runner: FakeRunner) -> None:
        probe_runner.register(["lsblk"], stdout=json.dumps({"blockdevices": []}))

        with pytest.raises(UnsafeDeviceError, match="no such block device"):
            device_mod.probe(probe_runner, "/dev/sdz")

    def test_bus_is_none_when_udevadm_fails(self, probe_runner: FakeRunner) -> None:
        """probe itself succeeds even without udevadm (ensure_safe makes the decision)."""
        probe_runner.register(["udevadm", "info"], returncode=1, stderr="not found")

        assert device_mod.probe(probe_runner, "/dev/sda").bus is None


class TestEnsureSafe:
    def _runner_with_root(self, source: str = "/dev/mmcblk0p2") -> FakeRunner:
        fake = FakeRunner()
        fake.register(["findmnt"], stdout=f"{source}\n")
        return fake

    def test_accepts_usb_removable_device(self, device: DeviceInfo, sys_block: Path) -> None:
        device_mod.ensure_safe(self._runner_with_root(), device)

    def test_rejects_non_usb_bus(self, device: DeviceInfo, sys_block: Path) -> None:
        with pytest.raises(UnsafeDeviceError, match="not on the USB bus"):
            device_mod.ensure_safe(self._runner_with_root(), replace(device, bus="nvme"))

    def test_rejects_non_removable(self, device: DeviceInfo, sys_block: Path) -> None:
        """Reject if lsblk RM is 0 and /sys/block also says 0."""
        (sys_block / "sda" / "removable").write_text("0\n")

        with pytest.raises(UnsafeDeviceError, match="not a removable device"):
            device_mod.ensure_safe(self._runner_with_root(), replace(device, removable=False))

    def test_sysfs_removable_overrides_lsblk(self, device: DeviceInfo, sys_block: Path) -> None:
        """Allow if sysfs says 1 even when lsblk reports RM=0 (seen with USB HDDs and
        similar)."""
        device_mod.ensure_safe(self._runner_with_root(), replace(device, removable=False))

    def test_rejects_root_disk(self, sys_block: Path) -> None:
        """Never inspect the disk that holds the root filesystem."""
        root_device = DeviceInfo(
            path="/dev/mmcblk0",
            kernel_name="mmcblk0",
            advertised_bytes=64_000_000_000,
            removable=True,
            bus="usb",
        )

        with pytest.raises(UnsafeDeviceError, match="root filesystem"):
            device_mod.ensure_safe(self._runner_with_root(), root_device)

    def test_rejects_zero_capacity(self, device: DeviceInfo, sys_block: Path) -> None:
        with pytest.raises(UnsafeDeviceError, match="zero capacity"):
            device_mod.ensure_safe(self._runner_with_root(), replace(device, advertised_bytes=0))

    def test_passes_when_root_disk_undeterminable(
        self, device: DeviceInfo, sys_block: Path
    ) -> None:
        """Continue if it is removable USB, even if findmnt fails."""
        fake = FakeRunner()
        fake.register(["findmnt"], returncode=1)

        device_mod.ensure_safe(fake, device)
