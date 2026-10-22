"""Shared test fixtures.

Real Linux commands are replaced by `FakeRunner`, so every test passes on a macOS dev
machine too. `FakeRunner.register()` matches argv by prefix and prefers longer prefixes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from usb_sentinel.libs import Config, DeviceInfo, FakeRunner, StepContext
from usb_sentinel.libs.config import CapacityConfig
from usb_sentinel.libs.steps.capacity import CapacityStep

# Format matching real clamscan --version output.
CLAMSCAN_VERSION = "ClamAV 1.0.3/27200/Mon Feb 12 09:23:12 2024"

# A "current time" close enough to the DB date (2024-02-12) that no staleness warning appears.
FIXED_NOW = "2024-02-20T10:00:00"

# stdout of f3write 8.x (format from public docs; to be replaced after checking on real
# hardware).
F3WRITE_OK = (
    "F3 write 8.0\n"
    "Copyright (C) 2010 Digirati Internet LTDA.\n"
    "This is free software; see the source for copying conditions.\n"
    "\n"
    "Free space: 30.20 GB\n"
    "Creating file 1.h2w ... OK!\n"
    "Creating file 29.h2w ... OK!\n"
    "Free space: 0.00 Byte\n"
    "Average writing speed: 18.20 MB/s\n"
)

# Files written by F3WRITE_OK (f3 writes 1 GiB per file; the last one is partial).
F3_FILES = range(1, 30)


def validating_lines(files: range = F3_FILES) -> str:
    """The per-file lines f3read prints (one per file it read)."""
    return "".join(
        f"Validating file {n}.h2w ... 2097152/        0/      0/      0\n" for n in files
    )


# stdout of f3read 8.x. Data OK is about 30.2 GB (58984375 sectors).
F3READ_OK = (
    "F3 read 8.0\n"
    "Copyright (C) 2010 Digirati Internet LTDA.\n"
    "This is free software; see the source for copying conditions.\n"
    "\n"
    "                  SECTORS      ok/corrupted/changed/overwritten\n" + validating_lines() + "\n"
    "  Data OK: 30.20 GB (58984375 sectors)\n"
    "Data LOST: 0.00 Byte (0 sectors)\n"
    "\t       Corrupted: 0.00 Byte (0 sectors)\n"
    "\tSlightly changed: 0.00 Byte (0 sectors)\n"
    "\t     Overwritten: 0.00 Byte (0 sectors)\n"
    "Average reading speed: 62.90 MB/s\n"
)

# Use when only the Data OK sector count in the f3read stdout needs changing.
F3READ_OK_SECTORS = 58984375


def f3read_output(
    ok_sectors: int,
    corrupted_sectors: int = 0,
    overwritten_sectors: int = 0,
    *,
    files: range = F3_FILES,
    changed_sectors: int = 0,
) -> str:
    """Build f3read stdout with the given sector counts.

    As in f3read, `Data LOST` is the sum of corrupted, slightly changed and overwritten.
    """
    lost = corrupted_sectors + changed_sectors + overwritten_sectors
    return validating_lines(files) + (
        f"  Data OK: {ok_sectors * 512 / 1e9:.2f} GB ({ok_sectors} sectors)\n"
        f"Data LOST: {lost * 512 / 1e9:.2f} GB ({lost} sectors)\n"
        f"\t       Corrupted: 0.00 Byte ({corrupted_sectors} sectors)\n"
        f"\tSlightly changed: 0.00 Byte ({changed_sectors} sectors)\n"
        f"\t     Overwritten: 0.00 Byte ({overwritten_sectors} sectors)\n"
        "Average reading speed: 62.90 MB/s\n"
    )


# stdout of f3probe 8.x for a genuine 32 GB drive and for a counterfeit one.
F3PROBE_GENUINE = (
    "F3 probe 8.0\n"
    "Copyright (C) 2010 Digirati Internet LTDA.\n"
    "\n"
    "WARNING: Probing normally takes from a few seconds to 15 minutes.\n"
    "Probing /dev/sda: done\n"
    "\n"
    "Good news: The device `/dev/sda' is the real thing\n"
    "\n"
    "Device geometry:\n"
    "\t         *Usable* size: 29.80 GB (62500000 blocks)\n"
    "\t        Announced size: 29.80 GB (62500000 blocks)\n"
    "\t                Module: 32.00 GB (2^35 Bytes)\n"
    "\tApproximate cache size: 0.00 Byte (0 blocks), need-reset=no\n"
    "\t   Physical block size: 512.00 Byte (2^9 Bytes)\n"
    "\n"
    "Probe time: 1'10\"\n"
)

F3PROBE_FAKE = (
    "Bad news: The device `/dev/sda' is a counterfeit of type limbo\n"
    "\n"
    "Device geometry:\n"
    "\t         *Usable* size: 7.50 GB (15625000 blocks)\n"
    "\t        Announced size: 29.80 GB (62500000 blocks)\n"
    "\t   Physical block size: 512.00 Byte (2^9 Bytes)\n"
)


def fio_json(
    mode: str,
    bw_bytes: int | None = 85_300_000,
    *,
    bw: int | None = None,
    error: int | None = 0,
    prefix: str = "",
) -> str:
    """Build fio --output-format=json stdout.

    Args:
        mode: "read" or "write".
        bw_bytes: Bytes per second. If None, the bw_bytes key is omitted.
        bw: KiB per second. Fallback used when bw_bytes is absent.
        error: jobs[0].error. If None, the key is omitted.
        prefix: Warning lines printed before the JSON.
    """
    io: dict[str, Any] = {"iops": 81.3, "io_bytes": 2_559_000_000, "runtime": 30001}
    if bw_bytes is not None:
        io["bw_bytes"] = bw_bytes
    if bw is not None:
        io["bw"] = bw
    job: dict[str, Any] = {"jobname": f"seq_{mode}", mode: io}
    if error is not None:
        job["error"] = error
    return prefix + json.dumps({"fio version": "fio-3.33", "jobs": [job]})


# Used bytes with make_statvfs() defaults ((7_812_500 - 7_568_359) * 4096).
STATVFS_USED_BYTES = 244_141 * 4096


def make_statvfs(
    *,
    f_flag: int = 0,
    f_frsize: int = 4096,
    f_blocks: int = 7_812_500,
    f_bfree: int = 7_568_359,
    f_bavail: int | None = None,
) -> os.statvfs_result:
    """Create an os.statvfs_result for injection. Default: 1 GB used of 32 GB."""
    bavail = f_bfree if f_bavail is None else f_bavail
    # Order: f_bsize, f_frsize, f_blocks, f_bfree, f_bavail, f_files, f_ffree, f_favail,
    # f_flag, f_namemax.
    return os.statvfs_result((f_frsize, f_frsize, f_blocks, f_bfree, bavail, 0, 0, 0, f_flag, 255))


@pytest.fixture
def device() -> DeviceInfo:
    """A typical USB flash drive under inspection (32 GB)."""
    return DeviceInfo(
        path="/dev/sda",
        kernel_name="sda",
        advertised_bytes=32_000_000_000,
        vendor="Generic",
        model="Flash Disk",
        serial="ABC123456789",
        removable=True,
        bus="usb",
    )


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """Test config. Output is confined under tmp_path.

    batch_files covers every file of F3WRITE_OK, so f3write and f3read run once each (the
    batching itself is tested in test_capacity_batches.py).
    """
    return Config(
        results_dir=tmp_path / "results",
        mount_root=tmp_path / "mnt",
        allow_destructive=False,
        capacity=CapacityConfig(batch_files=64),
    )


@pytest.fixture
def runner() -> FakeRunner:
    """FakeRunner with only clamscan --version registered."""
    fake = FakeRunner()
    fake.register(["clamscan", "--version"], stdout=CLAMSCAN_VERSION)
    return fake


# 32 GB disk with one unmounted vfat partition, used by capacity and speed step tests.
LSBLK_DISK = {
    "blockdevices": [
        {
            "name": "sda",
            "path": "/dev/sda",
            "size": 32_000_000_000,
            "vendor": "Generic",
            "model": "Flash Disk",
            "serial": "ABC123456789",
            "rm": True,
            "type": "disk",
            "mountpoint": None,
            "children": [
                {"path": "/dev/sda1", "fstype": "vfat", "type": "part", "mountpoint": None}
            ],
        }
    ]
}


@pytest.fixture
def f3_runner(runner: FakeRunner) -> FakeRunner:
    """FakeRunner with the commands needed for the capacity step (f3write mode) to PASS."""
    runner.register(["lsblk"], stdout=json.dumps(LSBLK_DISK))
    runner.register(["f3probe"], stdout=F3PROBE_GENUINE)
    runner.register(["wipefs"])
    runner.register(["mkfs.exfat"])
    runner.register(["mount"])
    runner.register(["umount"])
    runner.register(["f3write"], stdout=F3WRITE_OK)
    runner.register(["f3read"], stdout=F3READ_OK)
    return runner


def capacity_step(stat: os.statvfs_result | None = None) -> CapacityStep:
    """CapacityStep with statvfs and monotonic fixed."""
    return CapacityStep(statvfs=lambda _p: stat or make_statvfs(), monotonic=lambda: 0.0)


@pytest.fixture
def ctx(
    runner: FakeRunner,
    config: Config,
    device: DeviceInfo,
    tmp_path: Path,
) -> StepContext:
    """StepContext with one mounted partition."""
    mountpoint = tmp_path / "mnt" / "sda1"
    mountpoint.mkdir(parents=True, exist_ok=True)
    return StepContext(
        runner=runner,
        config=config,
        device=device,
        mountpoints=[mountpoint],
        output_dir=tmp_path / "out",
    )
