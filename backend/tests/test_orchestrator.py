"""検査シーケンスの駆動と判定分類。

とくに「マルウェア検知時に破壊的検査へ進まない」ことは、感染ドライブの証跡保全に
直結するため確実に検証する。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.conftest import (
    F3READ_OK,
    F3WRITE_OK,
    LSBLK_DISK,
    fio_json,
)
from usb_sentinel.libs import (
    Config,
    FakeRunner,
    InspectionResult,
    StepStatus,
    Verdict,
    inspect,
)
from usb_sentinel.libs import device as device_mod

# 破壊的ステップで呼ばれてはいけないコマンド（ゲート不通過時の検証に使う）。
DESTRUCTIVE_COMMANDS = {"f3probe", "f3write", "f3read", "fio"}

CLAMSCAN_VERSION = "ClamAV 1.0.3/27200/Mon Feb 12 09:23:12 2024"


@pytest.fixture
def full_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    """検査シーケンス全体を通すのに必要なコマンドを登録したFakeRunner。"""
    sys_block = tmp_path / "sys_block"
    (sys_block / "sda").mkdir(parents=True)
    (sys_block / "sda" / "removable").write_text("1\n")
    monkeypatch.setattr(device_mod, "SYS_BLOCK", sys_block)
    monkeypatch.setattr(device_mod, "SYS_USB_DEVICES", tmp_path / "sys_usb")

    fake = FakeRunner()
    fake.register(["lsblk"], stdout=json.dumps(LSBLK_DISK))
    fake.register(["udevadm", "info"], stdout="ID_BUS=usb\n")
    fake.register(["findmnt"], stdout="/dev/mmcblk0p2\n")
    fake.register(["clamscan", "--version"], stdout=CLAMSCAN_VERSION)
    fake.register(["clamscan", "--recursive"], returncode=0, stdout="")
    fake.register(["mount"], returncode=0)
    fake.register(["umount"], returncode=0)
    fake.register(["f3write"], stdout=F3WRITE_OK)
    fake.register(["f3read"], stdout=F3READ_OK)
    fake.register(["fio", "--name=seq_read"], stdout=fio_json("read", bw_bytes=85_300_000))
    fake.register(["fio", "--name=seq_write"], stdout=fio_json("write", bw_bytes=20_100_000))
    return fake


def _rw_mount_calls(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [c for c in runner.calls if c[:2] == ("mount", "-w")]


def _assert_nothing_destructive(runner: FakeRunner) -> None:
    assert {c[0] for c in runner.calls}.isdisjoint(DESTRUCTIVE_COMMANDS)
    assert _rw_mount_calls(runner) == []


def _statuses(result: InspectionResult) -> dict[str, StepStatus]:
    return {s.name: s.status for s in result.steps}


class TestInspect:
    def test_clean_drive_runs_all_steps_in_order(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        result = inspect(full_runner, config, "/dev/sda")

        assert [s.name for s in result.steps] == [
            "malware",
            "hash_inventory",
            "capacity",
            "speed",
        ]
        assert _statuses(result)["malware"] is StepStatus.PASS
        assert _statuses(result)["hash_inventory"] is StepStatus.PASS

    def test_mounts_read_only_and_unmounts(self, full_runner: FakeRunner, config: Config) -> None:
        inspect(full_runner, config, "/dev/sda")

        mount_call = next(c for c in full_runner.calls if c[0] == "mount")
        assert "ro,noexec,nosuid,nodev" in mount_call
        assert "/dev/sda1" in mount_call
        assert any(c[0] == "umount" for c in full_runner.calls)

    def test_malware_detection_still_builds_hash_inventory(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        """感染検知時も台帳は作るが破壊的検査には進まない。"""
        full_runner.register(
            ["clamscan", "--recursive"],
            returncode=1,
            stdout="/mnt/sda1/eicar.com: Eicar-Signature FOUND\n",
        )

        result = inspect(full_runner, config, "/dev/sda")
        statuses = _statuses(result)

        assert statuses["malware"] is StepStatus.FAIL
        assert statuses["hash_inventory"] is StepStatus.PASS
        assert statuses["capacity"] is StepStatus.SKIPPED
        assert statuses["speed"] is StepStatus.SKIPPED
        assert result.verdict is Verdict.MALWARE_DETECTED
        _assert_nothing_destructive(full_runner)

    def test_malware_timeout_still_builds_hash_inventory(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        """clamscanがタイムアウト（部分スキャン）でもスキャンは実行済みなので台帳を作る。"""
        full_runner.register(["clamscan", "--recursive"], returncode=0, timed_out=True)

        result = inspect(full_runner, config, "/dev/sda")
        statuses = _statuses(result)

        assert statuses["malware"] is StepStatus.ERROR
        assert "detections" in result.steps[0].details
        assert statuses["hash_inventory"] is StepStatus.PASS
        assert statuses["capacity"] is StepStatus.SKIPPED
        assert statuses["speed"] is StepStatus.SKIPPED
        _assert_nothing_destructive(full_runner)

    def test_missing_clamscan_skips_hash_inventory(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        """clamscanが1ファイルも開いていない経路では台帳のためにファイルを開かない。"""
        full_runner.register(["clamscan", "--version"], returncode=127, stderr="not found")

        result = inspect(full_runner, config, "/dev/sda")
        statuses = _statuses(result)
        hash_step = result.step("hash_inventory")

        assert statuses["malware"] is StepStatus.ERROR
        assert hash_step is not None
        assert hash_step.status is StepStatus.SKIPPED
        assert hash_step.summary == "スキャン未実行のため作成しない"
        assert not (Path(result.output_dir) / "hash-inventory.tsv").exists()
        assert not any("--recursive" in c for c in full_runner.calls)
        _assert_nothing_destructive(full_runner)

    def test_missing_database_skips_hash_inventory(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        full_runner.register(["clamscan", "--version"], stdout="ClamAV 1.4.3")

        result = inspect(full_runner, config, "/dev/sda")
        hash_step = result.step("hash_inventory")

        assert _statuses(result)["malware"] is StepStatus.ERROR
        assert hash_step is not None
        assert hash_step.status is StepStatus.SKIPPED
        assert hash_step.summary == "スキャン未実行のため作成しない"
        assert not (Path(result.output_dir) / "hash-inventory.tsv").exists()

    def test_stale_database_as_error_skips_hash_inventory(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        """stale_db_is_error=trueで鮮度超過がERRORになる経路では、clamscan未実行なので台帳も作らない。

        CLAMSCAN_VERSIONのDB日付（2024-02-12）は実時刻に対して必ず鮮度超過になる。
        """
        strict = replace(config, malware=replace(config.malware, stale_db_is_error=True))

        result = inspect(full_runner, strict, "/dev/sda")
        hash_step = result.step("hash_inventory")

        assert _statuses(result)["malware"] is StepStatus.ERROR
        assert hash_step is not None
        assert hash_step.status is StepStatus.SKIPPED
        assert hash_step.summary == "スキャン未実行のため作成しない"
        assert not (Path(result.output_dir) / "hash-inventory.tsv").exists()
        assert not any("--recursive" in c for c in full_runner.calls)
        _assert_nothing_destructive(full_runner)

    def test_hash_inventory_disabled_by_config(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        result = inspect(full_runner, replace(config, hash_inventory=False), "/dev/sda")
        hash_step = result.step("hash_inventory")

        assert hash_step is not None
        assert hash_step.status is StepStatus.SKIPPED
        assert hash_step.summary == "無効（hash_inventory = false）"
        assert not (Path(result.output_dir) / "hash-inventory.tsv").exists()

    def test_malware_detection_writes_nothing_to_device(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        """感染時はf3/fioを一切呼ばない（証跡保全）。"""
        full_runner.register(
            ["clamscan", "--recursive"],
            returncode=1,
            stdout="/mnt/sda1/eicar.com: Eicar-Signature FOUND\n",
        )
        full_runner.register(["f3probe"])

        inspect(full_runner, config, "/dev/sda")

        _assert_nothing_destructive(full_runner)

    def test_no_filesystem_reports_error_without_mounting(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        no_fs = json.loads(json.dumps(LSBLK_DISK))
        no_fs["blockdevices"][0]["children"] = []
        full_runner.register(["lsblk"], stdout=json.dumps(no_fs))

        result = inspect(full_runner, config, "/dev/sda")

        assert _statuses(result)["malware"] is StepStatus.ERROR
        assert not any(c[0] == "mount" for c in full_runner.calls)

    def test_mount_failure_is_reported(self, full_runner: FakeRunner, config: Config) -> None:
        full_runner.register(["mount"], returncode=32, stderr="wrong fs type")

        result = inspect(full_runner, config, "/dev/sda")

        malware = result.step("malware")
        assert malware is not None
        assert malware.status is StepStatus.ERROR
        assert "マウント" in malware.summary

    def test_records_output_dir_and_creates_it(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        result = inspect(full_runner, config, "/dev/sda")

        output_dir = Path(result.output_dir)
        assert output_dir.is_dir()
        assert output_dir.parent == config.results_dir
        assert "ABC123456789" in output_dir.name

    def test_unsafe_device_raises_before_touching_it(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        from usb_sentinel.libs import UnsafeDeviceError

        full_runner.register(["udevadm", "info"], stdout="ID_BUS=nvme\n")

        with pytest.raises(UnsafeDeviceError):
            inspect(full_runner, config, "/dev/sda")

        assert not any(c[0] in {"mount", "clamscan"} for c in full_runner.calls)
