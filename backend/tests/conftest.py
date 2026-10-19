"""テスト共通のフィクスチャ。

実機の Linux コマンドは `FakeRunner` で置き換えるため、macOS 開発機でも全テストが
通る。`FakeRunner.register()` は argv の前置一致で、より長い prefix を優先する。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from usb_sentinel.libs import Config, DeviceInfo, FakeRunner, StepContext
from usb_sentinel.libs.steps.capacity import CapacityStep

# clamscan --version の実出力に合わせた形式。
CLAMSCAN_VERSION = "ClamAV 1.0.3/27200/Mon Feb 12 09:23:12 2024"

# DB 日付（2024-02-12）に対して十分に近い「現在時刻」。鮮度警告が出ない。
FIXED_NOW = "2024-02-20T10:00:00"

# f3write 8.x の stdout（公開ドキュメントの形式。実機確認後に差し替える前提）。
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

# f3read 8.x の stdout。Data OK は 30.2GB 相当（58984375 セクタ）。
F3READ_OK = (
    "F3 read 8.0\n"
    "Copyright (C) 2010 Digirati Internet LTDA.\n"
    "This is free software; see the source for copying conditions.\n"
    "\n"
    "                  SECTORS      ok/corrupted/changed/overwritten\n"
    "Validating file 1.h2w ... 2097152/        0/      0/      0\n"
    "Validating file 29.h2w ... 2097152/        0/      0/      0\n"
    "\n"
    "  Data OK: 30.20 GB (58984375 sectors)\n"
    "Data LOST: 0.00 Byte (0 sectors)\n"
    "\t       Corrupted: 0.00 Byte (0 sectors)\n"
    "\tSlightly changed: 0.00 Byte (0 sectors)\n"
    "\t     Overwritten: 0.00 Byte (0 sectors)\n"
    "Average reading speed: 62.90 MB/s\n"
)

# f3read の stdout で Data OK のセクタ数だけ差し替えたい場合に使う。
F3READ_OK_SECTORS = 58984375


def f3read_output(ok_sectors: int, lost_sectors: int = 0, corrupted_sectors: int = 0) -> str:
    """セクタ数を指定した f3read の stdout を組み立てる。"""
    return (
        f"  Data OK: {ok_sectors * 512 / 1e9:.2f} GB ({ok_sectors} sectors)\n"
        f"Data LOST: {lost_sectors * 512 / 1e9:.2f} GB ({lost_sectors} sectors)\n"
        f"\t       Corrupted: 0.00 Byte ({corrupted_sectors} sectors)\n"
        "\tSlightly changed: 0.00 Byte (0 sectors)\n"
        "\t     Overwritten: 0.00 Byte (0 sectors)\n"
        "Average reading speed: 62.90 MB/s\n"
    )


def fio_json(
    mode: str,
    bw_bytes: int | None = 85_300_000,
    *,
    bw: int | None = None,
    error: int | None = 0,
    prefix: str = "",
) -> str:
    """fio --output-format=json の stdout を組み立てる。

    Args:
        mode: "read" または "write"。
        bw_bytes: バイト/秒。None なら bw_bytes キーを省く。
        bw: KiB/秒。bw_bytes が無いときのフォールバック用。
        error: jobs[0].error。None ならキーを省く。
        prefix: JSON の前に出る警告行。
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


# make_statvfs() の既定値での既使用バイト数（(7_812_500 - 7_568_359) * 4096）。
STATVFS_USED_BYTES = 244_141 * 4096


def make_statvfs(
    *,
    f_flag: int = 0,
    f_frsize: int = 4096,
    f_blocks: int = 7_812_500,
    f_bfree: int = 7_568_359,
    f_bavail: int | None = None,
) -> os.statvfs_result:
    """注入用の os.statvfs_result を作る。既定は 32GB 中 1GB 使用。"""
    bavail = f_bfree if f_bavail is None else f_bavail
    # 並びは f_bsize, f_frsize, f_blocks, f_bfree, f_bavail, f_files, f_ffree, f_favail,
    # f_flag, f_namemax。
    return os.statvfs_result((f_frsize, f_frsize, f_blocks, f_bfree, bavail, 0, 0, 0, f_flag, 255))


@pytest.fixture
def device() -> DeviceInfo:
    """典型的な検査対象 USB メモリ（32GB）。"""
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
    """テスト用設定。出力先は tmp_path 配下に閉じ込める。"""
    return Config(
        results_dir=tmp_path / "results",
        mount_root=tmp_path / "mnt",
        allow_destructive=False,
    )


@pytest.fixture
def runner() -> FakeRunner:
    """clamscan --version だけ登録済みの FakeRunner。"""
    fake = FakeRunner()
    fake.register(["clamscan", "--version"], stdout=CLAMSCAN_VERSION)
    return fake


# 容量・速度ステップのテストで使う、未マウントの vfat パーティション 1 つを持つ 32GB ディスク。
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
    """容量ステップ（f3write モード）が正常 PASS する経路に必要なコマンドを登録した FakeRunner。"""
    runner.register(["lsblk"], stdout=json.dumps(LSBLK_DISK))
    runner.register(["mount"])
    runner.register(["umount"])
    runner.register(["f3write"], stdout=F3WRITE_OK)
    runner.register(["f3read"], stdout=F3READ_OK)
    return runner


def capacity_step(stat: os.statvfs_result | None = None) -> CapacityStep:
    """statvfs と monotonic を固定した CapacityStep。"""
    return CapacityStep(statvfs=lambda _p: stat or make_statvfs(), monotonic=lambda: 0.0)


@pytest.fixture
def ctx(
    runner: FakeRunner,
    config: Config,
    device: DeviceInfo,
    tmp_path: Path,
) -> StepContext:
    """マウント済みパーティション 1 つを持つ StepContext。"""
    mountpoint = tmp_path / "mnt" / "sda1"
    mountpoint.mkdir(parents=True, exist_ok=True)
    return StepContext(
        runner=runner,
        config=config,
        device=device,
        mountpoints=[mountpoint],
        output_dir=tmp_path / "out",
    )
