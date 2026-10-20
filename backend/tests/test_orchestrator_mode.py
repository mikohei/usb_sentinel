"""Inspection mode (scan_only / full) as the third condition of the destructive gate.

Matrix: allow_destructive {False, True} x mode {SCAN_ONLY, FULL} x malware {pass, found, error}
plus an inventory error. Destructive steps run only for (True, FULL, clean scan, inventory
pass). Fixtures are borrowed from test_orchestrator.py.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests import test_orchestrator
from tests.conftest import make_statvfs
from tests.test_orchestrator import _assert_nothing_destructive, _statuses
from usb_sentinel.libs import (
    Config,
    FakeRunner,
    InspectionMode,
    InspectionResult,
    StepContext,
    StepResult,
    StepStatus,
    inspect,
)
from usb_sentinel.libs import orchestrator as orchestrator_mod
from usb_sentinel.libs.steps import capacity as capacity_mod

full_runner = test_orchestrator.full_runner

MALWARE_CASES = ("pass", "found", "error")
REASON_CEILING = "allow_destructive = false"
REASON_MODE = "Inspection mode is scan_only"


def _arm(runner: FakeRunner, malware: str) -> None:
    """Register the clamscan outcome for the matrix case."""
    if malware == "found":
        runner.register(
            ["clamscan", "--recursive"],
            returncode=1,
            stdout="/mnt/sda1/eicar.com: Eicar-Signature FOUND\n",
        )
    elif malware == "error":
        # No definition DB -> ERROR before any scan runs.
        runner.register(["clamscan", "--version"], stdout="ClamAV 1.4.3")


def _failing_inventory(ctx: StepContext) -> StepResult:
    return StepResult("hash_inventory", StepStatus.ERROR, "1 unreadable files")


@pytest.fixture(autouse=True)
def _statvfs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capacity_mod, "DEFAULT_STATVFS", lambda _p: make_statvfs())


class TestModeMatrix:
    @pytest.mark.parametrize("malware", MALWARE_CASES)
    @pytest.mark.parametrize("mode", list(InspectionMode))
    @pytest.mark.parametrize("allow_destructive", [False, True])
    def test_destructive_steps_only_for_full_and_clean(
        self,
        full_runner: FakeRunner,
        config: Config,
        allow_destructive: bool,
        mode: InspectionMode,
        malware: str,
    ) -> None:
        _arm(full_runner, malware)
        cfg = replace(config, allow_destructive=allow_destructive)

        result = inspect(full_runner, cfg, "/dev/sda", mode=mode)

        statuses = _statuses(result)
        expected_full = allow_destructive and mode is InspectionMode.FULL
        assert (result.mode is InspectionMode.FULL) == expected_full
        if expected_full and malware == "pass":
            assert statuses["capacity"] is StepStatus.PASS
            assert statuses["speed"] in (StepStatus.PASS, StepStatus.SKIPPED)
            assert any(c[0] == "f3write" for c in full_runner.calls)
            assert any(c[0] == "fio" for c in full_runner.calls)
            return
        assert statuses["capacity"] is StepStatus.SKIPPED
        assert statuses["speed"] is StepStatus.SKIPPED
        _assert_nothing_destructive(full_runner)

    @pytest.mark.parametrize("malware", MALWARE_CASES)
    @pytest.mark.parametrize("mode", list(InspectionMode))
    def test_ceiling_off_reason_wins(
        self, full_runner: FakeRunner, config: Config, mode: InspectionMode, malware: str
    ) -> None:
        """With allow_destructive=false the reason is the ceiling, whatever the mode or scan."""
        _arm(full_runner, malware)

        result = inspect(full_runner, config, "/dev/sda", mode=mode)

        capacity = result.step("capacity")
        speed = result.step("speed")
        assert capacity is not None and speed is not None
        assert capacity.summary == REASON_CEILING
        assert speed.summary == REASON_CEILING
        assert result.mode is InspectionMode.SCAN_ONLY

    @pytest.mark.parametrize("malware", MALWARE_CASES)
    def test_scan_only_reason_when_ceiling_on(
        self, full_runner: FakeRunner, config: Config, malware: str
    ) -> None:
        """allow_destructive=true with the default mode skips with the mode reason."""
        _arm(full_runner, malware)
        cfg = replace(config, allow_destructive=True)

        result = inspect(full_runner, cfg, "/dev/sda")

        capacity = result.step("capacity")
        speed = result.step("speed")
        assert capacity is not None and speed is not None
        assert capacity.summary == REASON_MODE
        assert speed.summary == REASON_MODE
        assert result.mode is InspectionMode.SCAN_ONLY
        _assert_nothing_destructive(full_runner)

    @pytest.mark.parametrize("malware", ["found", "error"])
    def test_full_mode_with_bad_scan_keeps_scan_reason(
        self, full_runner: FakeRunner, config: Config, malware: str
    ) -> None:
        """In full mode with the ceiling on, a failed scan keeps the existing reason."""
        _arm(full_runner, malware)
        cfg = replace(config, allow_destructive=True)

        result = inspect(full_runner, cfg, "/dev/sda", mode=InspectionMode.FULL)

        capacity = result.step("capacity")
        assert capacity is not None
        assert capacity.summary == "Scan and hashing not completed"
        assert result.mode is InspectionMode.FULL
        _assert_nothing_destructive(full_runner)

    @pytest.mark.parametrize("mode", list(InspectionMode))
    @pytest.mark.parametrize("allow_destructive", [False, True])
    def test_inventory_error_blocks_destructive_steps(
        self,
        full_runner: FakeRunner,
        config: Config,
        monkeypatch: pytest.MonkeyPatch,
        allow_destructive: bool,
        mode: InspectionMode,
    ) -> None:
        monkeypatch.setattr(orchestrator_mod, "inventory", _failing_inventory)
        cfg = replace(config, allow_destructive=allow_destructive)

        result = inspect(full_runner, cfg, "/dev/sda", mode=mode)

        statuses = _statuses(result)
        assert statuses["hash_inventory"] is StepStatus.ERROR
        assert statuses["capacity"] is StepStatus.SKIPPED
        assert statuses["speed"] is StepStatus.SKIPPED
        _assert_nothing_destructive(full_runner)


class TestMountedLookup:
    def test_is_mounted_checked_only_in_full_mode(
        self, full_runner: FakeRunner, config: Config
    ) -> None:
        """The post-gate lsblk lookup happens only when the ceiling is on and the mode is full.
        A found scan keeps both runs free of destructive steps, so the lsblk counts differ by
        exactly the gate lookup."""
        _arm(full_runner, "found")
        cfg = replace(config, allow_destructive=True)

        inspect(full_runner, cfg, "/dev/sda", mode=InspectionMode.SCAN_ONLY)
        scan_only_lsblk = sum(c[0] == "lsblk" for c in full_runner.calls)
        full_runner.calls.clear()
        inspect(full_runner, cfg, "/dev/sda", mode=InspectionMode.FULL)
        full_lsblk = sum(c[0] == "lsblk" for c in full_runner.calls)

        assert full_lsblk == scan_only_lsblk + 1


class TestModeDefault:
    def test_default_mode_is_scan_only(self, full_runner: FakeRunner, config: Config) -> None:
        result = inspect(full_runner, replace(config, allow_destructive=True), "/dev/sda")

        assert result.mode is InspectionMode.SCAN_ONLY

    def test_mode_is_set_before_start_record(self, full_runner: FakeRunner, config: Config) -> None:
        """The start record already sees the effective mode."""
        seen: list[InspectionMode] = []

        class Spy:
            def record_started(self, result: InspectionResult) -> str | None:
                seen.append(result.mode)
                return None

            def record_completed(self, result: InspectionResult, digest: str) -> str | None:
                return None

        cfg = replace(config, allow_destructive=True)
        inspect(full_runner, cfg, "/dev/sda", store=Spy(), mode=InspectionMode.FULL)

        assert seen == [InspectionMode.FULL]
