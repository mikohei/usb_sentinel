import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from usb_sentinel_display.libs.units import CommandOutput

PCIE = "devices/platform/scb/fd500000.pcie/pci0000:00/0000:00:00.0/0000:01:00.0"
SDA_REL = f"{PCIE}/usb1/1-1/1-1.3/1-1.3:1.0/host0/target0:0:0/0:0:0:0/block/sda"
SDB_REL = f"{PCIE}/usb2/2-1/2-1:1.0/host1/target1:0:0/1:0:0:0/block/sdb"
MMC_REL = "devices/platform/emmc2bus/fe340000.mmc/mmc_host/mmc0/mmc0:0001/block/mmcblk0"


@dataclass(frozen=True)
class FakeSysfs:
    """Main paths of the fake sysfs tree."""

    root: Path
    sys_block: Path
    sda_port_dir: Path
    sdb_port_dir: Path


@pytest.fixture
def fake_sysfs(tmp_path: Path) -> FakeSysfs:
    """Create a fake Raspberry Pi 4-like sysfs tree under tmp_path.

    sda is at 1-1.3 on bus usb1, sdb at 2-1 on bus usb2, and mmcblk0 on a non-USB path.
    """
    root = tmp_path
    for rel in (SDA_REL, SDB_REL, MMC_REL):
        (root / rel).mkdir(parents=True)
    sys_block = root / "sys_block"
    sys_block.mkdir()
    (sys_block / "sda").symlink_to(root / SDA_REL)
    (sys_block / "sdb").symlink_to(root / SDB_REL)
    (sys_block / "mmcblk0").symlink_to(root / MMC_REL)
    sda_port_dir = root / PCIE / "usb1/1-1/1-1.3"
    sdb_port_dir = root / PCIE / "usb2/2-1"
    (sda_port_dir / "serial").write_bytes(b"ABC123\n")
    (sdb_port_dir / "serial").write_bytes(b"XYZ789\n")
    return FakeSysfs(root, sys_block, sda_port_dir, sdb_port_dir)


def make_result(root: Path, name: str, data: dict[str, object] | str, mtime: float) -> Path:
    """Create a result directory and result.json, and pin its mtime.

    Args:
        root: Results root.
        name: Directory name (<timestamp>-<serial>).
        data: A dict is written as JSON; a str is written as-is (to reproduce truncated JSON).
        mtime: mtime to set on result.json.

    Returns:
        Path of the created directory.
    """
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "result.json"
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return directory


def sample_result(
    *,
    kernel_name: str = "sda",
    serial: str = "ABC123",
    verdict: str = "No Defect",
    port_path: str | None = None,
    steps: list[dict[str, object]] | None = None,
    started_at: str = "2025-06-01T12:00:00.123456+00:00",
) -> dict[str, object]:
    """Build a dict equivalent to the backend's result.json."""
    device: dict[str, object] = {
        "kernel_name": kernel_name,
        "serial": serial,
        "vendor": "Vendor",
        "model": "Model",
        "path": f"/dev/{kernel_name}",
    }
    if port_path is not None:
        device["port_path"] = port_path
    if steps is None:
        steps = [
            {"name": "malware", "status": "pass", "details": {"detections": []}},
            {"name": "hash_inventory", "status": "pass", "details": {}},
            {"name": "capacity", "status": "skipped", "details": {}},
            {"name": "speed", "status": "skipped", "details": {}},
        ]
    return {
        "device": device,
        "verdict": verdict,
        "steps": steps,
        "started_at": started_at,
        "finished_at": "2025-06-01T12:05:00.000000+00:00",
    }


def phase2_result(
    *,
    kernel_name: str = "sda",
    serial: str = "ABC123",
    verdict: str = "Spec Mismatch",
    port_path: str | None = None,
) -> dict[str, object]:
    """Build a dict like a backend phase-2 result.json (with all detail-QR inputs)."""
    steps: list[dict[str, object]] = [
        {
            "name": "malware",
            "status": "pass",
            "details": {
                "detections": [],
                "database": {"db_version": "27650", "db_date": "2025-06-01T08:00:00+00:00"},
            },
        },
        {
            "name": "hash_inventory",
            "status": "pass",
            "details": {"digest": "abcdef0123456789" * 4, "lines": 1234},
        },
        {
            "name": "capacity",
            "status": "fail",
            "details": {
                "nominal_bytes": 32_000_000_000,
                "actual_bytes": 29_500_000_000,
                "kind": "short",
                "lost_ratio": 0.0123,
                "counterfeit_reasons": [],
            },
        },
        {
            "name": "speed",
            "status": "fail",
            "details": {
                "read": {"mb_s": 45.67},
                "write": {"mb_s": 12.34},
                "threshold_read_mb_s": 50.0,
                "threshold_write_mb_s": 15.0,
                "basis_read": "class",
                "basis_write": "class",
                "severity": "minor",
                "device_class": "usb3_flash",
            },
        },
    ]
    data = sample_result(
        kernel_name=kernel_name, serial=serial, verdict=verdict, port_path=port_path, steps=steps
    )
    device = data["device"]
    if isinstance(device, dict):
        device["advertised_bytes"] = 32_000_000_000
        device["link_speed_mbps"] = 5000
    data["advertised_check"] = {"nominal_bytes": 32_000_000_000, "exceeded": False}
    data["elapsed_s"] = 312.5
    data["reason_codes"] = ["c:short", "s:minor"]
    return data


class FakeRunner:
    """Fake Runner that returns outputs registered by argv prefix."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.registered: dict[tuple[str, ...], CommandOutput] = {}

    def register(
        self,
        argv: list[str],
        *,
        returncode: int = 0,
        stdout: str = "",
        timed_out: bool = False,
    ) -> None:
        """Register output for an argv prefix."""
        self.registered[tuple(argv)] = CommandOutput(returncode, stdout, timed_out)

    def run(self, argv: list[str], *, timeout_s: float) -> CommandOutput:
        """Return the longest-matching registered output. AssertionError if none is registered."""
        args = tuple(argv)
        self.calls.append(args)
        matches = [key for key in self.registered if args[: len(key)] == key]
        if not matches:
            raise AssertionError(f"unregistered: {argv}")
        return replace(self.registered[max(matches, key=len)])
