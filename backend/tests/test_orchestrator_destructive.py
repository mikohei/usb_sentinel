"""Gate for the destructive steps (capacity, speed) in the inspection sequence, and the
non-destructive advertised-capacity check.

Helpers (full_runner, _statuses, _assert_nothing_destructive) are borrowed from
test_orchestrator.py.
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
from usb_sentinel.libs import Config, FakeRunner, InspectionMode, StepStatus, Verdict, inspect
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
        """Do not proceed to writing when unmounting has failed."""
        still_mounted = json.loads(json.dumps(LSBLK_DISK))
        still_mounted["blockdevices"][0]["children"][0]["mountpoint"] = "/mnt/sda1"
        full_runner.register(["lsblk"], stdout=json.dumps(still_mounted))

        result = inspect(
            full_runner,
            replace(config, allow_destructive=True),
            "/dev/sda",
            mode=InspectionMode.FULL,
        )

        capacity = result.step("capacity")
        assert capacity is not None
        assert capacity.status is StepStatus.SKIPPED
        assert "Still mounted" in capacity.summary
        assert _statuses(result)["speed"] is StepStatus.SKIPPED
        _assert_nothing_destructive(full_runner)

    def test_destructive_steps_run_when_enabled_and_unmounted(
        self, full_runner: FakeRunner, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When conditions are met, the capacity and speed steps run, and if both PASS the
        verdict is No Defect."""
        monkeypatch.setattr(capacity_mod, "DEFAULT_STATVFS", lambda _p: make_statvfs())
        enabled = replace(
            config,
            allow_destructive=True,
            speed=SpeedConfig(nominal_read_mb_s=50.0, nominal_write_mb_s=10.0),
        )

        result = inspect(full_runner, enabled, "/dev/sda", mode=InspectionMode.FULL)

        assert _statuses(result)["capacity"] is StepStatus.PASS
        assert _statuses(result)["speed"] is StepStatus.PASS
        assert result.verdict is Verdict.NO_DEFECT
        capacity = result.step("capacity")
        assert capacity is not None
        # The injected statvfs value is used, not the dev machine's disk usage.
        assert capacity.details["used_bytes"] == STATVFS_USED_BYTES
        rw_mounts = _rw_mount_calls(full_runner)
        assert len(rw_mounts) == 1
        assert "noexec,nosuid,nodev" in rw_mounts[0]
        # The read fio runs before the write fio.
        fio_calls = [c for c in full_runner.calls if c[0] == "fio"]
        assert [c[1] for c in fio_calls] == ["--name=seq_read", "--name=seq_write"]

    def test_elapsed_s_recorded_for_executed_steps(
        self, full_runner: FakeRunner, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Executed steps get an elapsed time (seconds, non-negative float), and so does the
        whole run."""
        monkeypatch.setattr(capacity_mod, "DEFAULT_STATVFS", lambda _p: make_statvfs())
        enabled = replace(
            config,
            allow_destructive=True,
            speed=SpeedConfig(nominal_read_mb_s=50.0, nominal_write_mb_s=10.0),
        )

        result = inspect(full_runner, enabled, "/dev/sda", mode=InspectionMode.FULL)

        assert [s.name for s in result.steps] == ["malware", "hash_inventory", "capacity", "speed"]
        for step in result.steps:
            assert isinstance(step.elapsed_s, float), step.name
            assert step.elapsed_s >= 0.0, step.name
        assert isinstance(result.elapsed_s, float)
        assert result.elapsed_s >= 0.0

    def test_skipped_steps_have_no_elapsed_s(self, full_runner: FakeRunner, config: Config) -> None:
        """A StepResult built as SKIPPED keeps an elapsed time of None."""
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
        """If the capacity step fails to umount, the speed step protects itself and does not
        start fio."""
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

        result = inspect(
            full_runner,
            replace(config, allow_destructive=True),
            "/dev/sda",
            mode=InspectionMode.FULL,
        )

        capacity = result.step("capacity")
        speed = result.step("speed")
        assert capacity is not None and speed is not None
        assert capacity.status is StepStatus.ERROR
        assert capacity.summary.startswith("umount failed")
        assert speed.status is StepStatus.ERROR
        assert "Still mounted" in speed.summary
        assert not any(c[0] == "fio" for c in full_runner.calls)
        assert result.verdict is Verdict.INCONCLUSIVE


def _with_nominal(config: Config, nominal: int | None, **overrides: object) -> Config:
    return replace(config, capacity=replace(config.capacity, nominal_bytes=nominal), **overrides)


class TestAdvertisedCheck:
    """Non-destructive advertised-capacity check (right after probe(), before mounting) and
    Counterfeit Suspected."""

    def test_exceeded_without_destructive_steps(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        """Even with allow_destructive=false, advertised 32 GB over 107% of nominal 29 GB means
        suspected counterfeit."""
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
        """An overrun does not affect the destructive gate (permitted unchanged). Writes come
        after the ro mount."""
        monkeypatch.setattr(capacity_mod, "DEFAULT_STATVFS", lambda _p: make_statvfs())
        enabled = _with_nominal(config, 29_000_000_000, allow_destructive=True)

        result = inspect(full_runner, enabled, "/dev/sda", mode=InspectionMode.FULL)

        assert _statuses(result)["capacity"] is StepStatus.FAIL
        # The speed step also runs (no nominal speed is set, so the verdict is SKIPPED, but fio
        # runs).
        assert any(c[0] == "f3write" for c in full_runner.calls)
        assert any(c[0] == "fio" for c in full_runner.calls)
        assert result.verdict is Verdict.COUNTERFEIT_SUSPECTED
        capacity = result.step("capacity")
        assert capacity is not None
        assert "over_advertised" in capacity.details["counterfeit_reasons"]
        # It appears twice in reasons, recording that both the non-destructive check and the
        # step confirmed it.
        assert sum("advertised capacity" in r for r in result.reasons) == 2
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
