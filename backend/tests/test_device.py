"""デバイス識別と安全ガード。

破壊的検査を行うため、ここが誤って通ると実害が出る。拒否すべきケースを網羅する。
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
    """probe() が必要とするコマンドを登録した FakeRunner。"""
    fake = FakeRunner()
    fake.register(["lsblk"], stdout=json.dumps(LSBLK_USB))
    fake.register(["udevadm", "info"], stdout=UDEVADM_USB)
    return fake


@pytest.fixture
def sys_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`/sys/block` を tmp_path に差し替える（macOS には存在しないため）。"""
    root = tmp_path / "sys_block"
    (root / "sda").mkdir(parents=True)
    (root / "sda" / "removable").write_text("1\n")
    # ルートディスク mmcblk0 とそのパーティション。
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
    """実機の/sys/blockと同じく、デバイスパスへのシンボリックリンクを並べた偽ツリー。"""
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
    """偽の/sys/bus/usb/devices/<port_path>/speedを作る。"""
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
        """USB1.1 Low Speedの`1.5`はint(float)で1になる。"""
        _write_speed(tmp_path, "1-1.3", "1.5\n")

        assert device_mod.link_speed_for("1-1.3", tmp_path) == 1

    def test_none_port_path_is_none(self, tmp_path: Path) -> None:
        assert device_mod.link_speed_for(None, tmp_path) is None

    def test_probe_reads_link_speed(self, probe_runner: FakeRunner, sysfs_links: Path) -> None:
        """probe()はポートパス1-1.3のspeedをDeviceInfo.link_speed_mbpsに写す。"""
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
        """sys_blockを明示すればモジュール変数に依らない。"""
        monkeypatch.setattr(device_mod, "SYS_BLOCK", Path("/nonexistent"))

        assert device_mod.port_path_for("sda", sysfs_links) == "1-1.3"
        assert device_mod.port_path_for("sdb", sys_block=sysfs_links) == "2-1"
        assert device_mod.port_path_for("mmcblk0", sysfs_links) is None


class TestProbe:
    def test_port_path_from_sysfs(self, probe_runner: FakeRunner, sysfs_links: Path) -> None:
        assert device_mod.probe(probe_runner, "/dev/sda").port_path == "1-1.3"

    def test_port_path_none_without_sysfs(self, probe_runner: FakeRunner, sys_block: Path) -> None:
        """sys_blockにリンクが無ければport_pathはNone。"""
        assert device_mod.probe(probe_runner, "/dev/sda").port_path is None

    def test_reads_device_attributes(self, probe_runner: FakeRunner) -> None:
        info = device_mod.probe(probe_runner, "/dev/sda")

        assert info.kernel_name == "sda"
        assert info.advertised_bytes == 32_000_000_000
        # lsblk の値は前後の空白が落ちる。
        assert info.vendor == "Generic"
        assert info.model == "Flash Disk"
        assert info.serial == "ABC123456789"
        assert info.removable is True
        assert info.bus == "usb"

    def test_rejects_partition(self, probe_runner: FakeRunner) -> None:
        """パーティションを渡された場合はディスク全体でないため拒否する。"""
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
        """udevadm が使えない環境でも probe 自体は成功させる（判定は ensure_safe 側）。"""
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
        """lsblk の RM が 0 で、/sys/block 側も 0 なら拒否する。"""
        (sys_block / "sda" / "removable").write_text("0\n")

        with pytest.raises(UnsafeDeviceError, match="not a removable device"):
            device_mod.ensure_safe(self._runner_with_root(), replace(device, removable=False))

    def test_sysfs_removable_overrides_lsblk(self, device: DeviceInfo, sys_block: Path) -> None:
        """lsblk が RM=0 を報告しても sysfs が 1 なら通す（USB HDD 等の実例がある）。"""
        device_mod.ensure_safe(self._runner_with_root(), replace(device, removable=False))

    def test_rejects_root_disk(self, sys_block: Path) -> None:
        """ルートファイルシステムを載せたディスクは絶対に検査させない。"""
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
        """findmnt が失敗してもリムーバブル USB であれば続行する。"""
        fake = FakeRunner()
        fake.register(["findmnt"], returncode=1)

        device_mod.ensure_safe(fake, device)
