"""検査シーケンスの破壊的ステップ（容量・速度）のゲートと申告容量の非破壊チェック。

材料（full_runner、_statuses、_assert_nothing_destructive）はtest_orchestrator.pyから借りる。
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tests import test_orchestrator
from tests.conftest import LSBLK_DISK, STATVFS_USED_BYTES, make_statvfs
from tests.test_orchestrator import (
    DESTRUCTIVE_COMMANDS,
    _assert_nothing_destructive,
    _rw_mount_calls,
    _statuses,
)
from usb_sentinel.libs import Config, FakeRunner, StepStatus, Verdict, inspect
from usb_sentinel.libs.config import SpeedConfig
from usb_sentinel.libs.runner import CommandResult
from usb_sentinel.libs.steps import capacity as capacity_mod

full_runner = test_orchestrator.full_runner


class TestInspectDestructive:
    def test_destructive_steps_skipped_when_disabled(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        assert config.allow_destructive is False

        result = inspect(full_runner, config, "/dev/sda")

        capacity = result.step("capacity")
        assert capacity is not None
        assert capacity.status is StepStatus.SKIPPED
        assert "allow_destructive" in capacity.summary
        assert _statuses(result)["speed"] is StepStatus.SKIPPED
        _assert_nothing_destructive(full_runner)

    def test_destructive_steps_blocked_while_mounted(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        """アンマウントに失敗した状態では書き込みに進まない。"""
        still_mounted = json.loads(json.dumps(LSBLK_DISK))
        still_mounted["blockdevices"][0]["children"][0]["mountpoint"] = "/mnt/sda1"
        full_runner.register(["lsblk"], stdout=json.dumps(still_mounted))

        result = inspect(full_runner, replace(config, allow_destructive=True), "/dev/sda")

        capacity = result.step("capacity")
        assert capacity is not None
        assert capacity.status is StepStatus.SKIPPED
        assert "マウント" in capacity.summary
        assert _statuses(result)["speed"] is StepStatus.SKIPPED
        _assert_nothing_destructive(full_runner)

    def test_destructive_steps_run_when_enabled_and_unmounted(
        self, full_runner: FakeRunner, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """条件を満たせば容量・速度ステップが実行され、両方PASSならNo Defectになる。"""
        monkeypatch.setattr(capacity_mod, "DEFAULT_STATVFS", lambda _p: make_statvfs())
        enabled = replace(
            config,
            allow_destructive=True,
            speed=SpeedConfig(nominal_read_mb_s=50.0, nominal_write_mb_s=10.0),
        )

        result = inspect(full_runner, enabled, "/dev/sda")

        assert _statuses(result)["capacity"] is StepStatus.PASS
        assert _statuses(result)["speed"] is StepStatus.PASS
        assert result.verdict is Verdict.NO_DEFECT
        capacity = result.step("capacity")
        assert capacity is not None
        # 開発機のディスク使用量が混入せず、注入したstatvfsの値になる。
        assert capacity.details["used_bytes"] == STATVFS_USED_BYTES
        rw_mounts = _rw_mount_calls(full_runner)
        assert len(rw_mounts) == 1
        assert "noexec,nosuid,nodev" in rw_mounts[0]
        # 読み取りfioは書き込みfioより先。
        fio_calls = [c for c in full_runner.calls if c[0] == "fio"]
        assert [c[1] for c in fio_calls] == ["--name=seq_read", "--name=seq_write"]

    def test_elapsed_s_recorded_for_executed_steps(
        self, full_runner: FakeRunner, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """実行したステップには所要時間（秒、非負のfloat）が入り、全体にも入る。"""
        monkeypatch.setattr(capacity_mod, "DEFAULT_STATVFS", lambda _p: make_statvfs())
        enabled = replace(
            config,
            allow_destructive=True,
            speed=SpeedConfig(nominal_read_mb_s=50.0, nominal_write_mb_s=10.0),
        )

        result = inspect(full_runner, enabled, "/dev/sda")

        assert [s.name for s in result.steps] == ["malware", "hash_inventory", "capacity", "speed"]
        for step in result.steps:
            assert isinstance(step.elapsed_s, float), step.name
            assert step.elapsed_s >= 0.0, step.name
        assert isinstance(result.elapsed_s, float)
        assert result.elapsed_s >= 0.0

    def test_skipped_steps_have_no_elapsed_s(self, full_runner: FakeRunner, config: Config) -> None:
        """SKIPPEDとして組み立てたStepResultの所要時間はNoneのまま。"""
        result = inspect(full_runner, config, "/dev/sda")

        capacity = result.step("capacity")
        speed = result.step("speed")
        malware = result.step("malware")
        assert capacity is not None and speed is not None and malware is not None
        assert capacity.status is StepStatus.SKIPPED
        assert capacity.elapsed_s is None
        assert speed.elapsed_s is None
        assert isinstance(malware.elapsed_s, float)

    def test_capacity_umount_failure_blocks_fio(
        self, full_runner: FakeRunner, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """容量ステップがumountに失敗したら、速度ステップは自衛してfioを起動しない。"""
        monkeypatch.setattr(capacity_mod, "DEFAULT_STATVFS", lambda _p: make_statvfs())
        still_mounted = json.loads(json.dumps(LSBLK_DISK))
        still_mounted["blockdevices"][0]["children"][0]["mountpoint"] = "/run/x/rw/0"
        state = {"rw_umount_failed": False}
        original_run = full_runner.run

        def run(argv: list[str], *, timeout_s: float | None = None) -> CommandResult:
            if argv[0] == "umount" and "/rw/" in argv[1]:
                state["rw_umount_failed"] = True
                full_runner.calls.append(tuple(argv))
                return CommandResult(tuple(argv), 32, stderr="target is busy")
            if argv[0] == "lsblk" and state["rw_umount_failed"]:
                full_runner.calls.append(tuple(argv))
                return CommandResult(tuple(argv), 0, stdout=json.dumps(still_mounted))
            return original_run(argv, timeout_s=timeout_s)

        monkeypatch.setattr(full_runner, "run", run)

        result = inspect(full_runner, replace(config, allow_destructive=True), "/dev/sda")

        capacity = result.step("capacity")
        speed = result.step("speed")
        assert capacity is not None and speed is not None
        assert capacity.status is StepStatus.ERROR
        assert capacity.summary.startswith("umount失敗")
        assert speed.status is StepStatus.ERROR
        assert "マウントが残っている" in speed.summary
        assert not any(c[0] == "fio" for c in full_runner.calls)
        assert result.verdict is Verdict.INCONCLUSIVE


def _with_nominal(config: Config, nominal: int | None, **overrides: object) -> Config:
    return replace(config, capacity=replace(config.capacity, nominal_bytes=nominal), **overrides)


class TestAdvertisedCheck:
    """申告容量の非破壊チェック（probe()直後、マウント前）とCounterfeit Suspected。"""

    def test_exceeded_without_destructive_steps(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        """allow_destructive=falseでも申告32GBが公称29GBの107%超なら偽装疑いになる。"""
        result = inspect(full_runner, _with_nominal(config, 29_000_000_000), "/dev/sda")

        assert result.advertised_check["exceeded"] is True
        assert result.advertised_check["over_threshold_bytes"] == 31_030_000_000
        assert result.verdict is Verdict.COUNTERFEIT_SUSPECTED
        assert result.reasons[0] == result.advertised_check["summary"]
        assert result.reason_codes == ["c:over", "c:skipped", "s:skipped"]
        assert _statuses(result)["capacity"] is StepStatus.SKIPPED
        assert _statuses(result)["speed"] is StepStatus.SKIPPED
        _assert_nothing_destructive(full_runner)

    def test_malware_beats_exceeded_but_check_remains(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        full_runner.register(
            ["clamscan", "--recursive"],
            returncode=1,
            stdout="/mnt/sda1/eicar.com: Eicar-Signature FOUND\n",
        )

        result = inspect(full_runner, _with_nominal(config, 29_000_000_000), "/dev/sda")

        assert result.verdict is Verdict.MALWARE_DETECTED
        assert result.advertised_check["exceeded"] is True
        assert result.reason_codes[:2] == ["c:over", "m:found"]
        _assert_nothing_destructive(full_runner)

    def test_exceeded_does_not_block_destructive_steps(
        self, full_runner: FakeRunner, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """超過は破壊的ゲートに影響しない（permitted不変）。書き込みはroマウントより後。"""
        monkeypatch.setattr(capacity_mod, "DEFAULT_STATVFS", lambda _p: make_statvfs())
        enabled = _with_nominal(config, 29_000_000_000, allow_destructive=True)

        result = inspect(full_runner, enabled, "/dev/sda")

        assert _statuses(result)["capacity"] is StepStatus.FAIL
        # 速度ステップも実行される（公称速度未設定なので判定はSKIPPEDだがfioは走る）。
        assert any(c[0] == "f3write" for c in full_runner.calls)
        assert any(c[0] == "fio" for c in full_runner.calls)
        assert result.verdict is Verdict.COUNTERFEIT_SUSPECTED
        capacity = result.step("capacity")
        assert capacity is not None
        assert "over_advertised" in capacity.details["counterfeit_reasons"]
        # 非破壊チェックとステップの両方で確認した記録として、reasonsには2回出る。
        assert sum("申告容量" in r for r in result.reasons) == 2
        assert result.reason_codes.count("c:over") == 1
        first_ro_mount = next(
            i for i, c in enumerate(full_runner.calls) if c[0] == "mount" and c[1] != "-w"
        )
        writes = [
            i
            for i, c in enumerate(full_runner.calls)
            if c[0] in DESTRUCTIVE_COMMANDS or c[:2] == ("mount", "-w")
        ]
        assert writes and min(writes) > first_ro_mount

    def test_unset_nominal_is_not_exceeded(self, full_runner: FakeRunner, config: Config) -> None:
        result = inspect(full_runner, config, "/dev/sda")

        assert result.advertised_check["exceeded"] is False
        assert result.advertised_check["nominal_bytes"] is None
        assert result.advertised_check["advertised_bytes"] == 32_000_000_000
        assert result.advertised_check["summary"] == ""
        assert "c:over" not in result.reason_codes
