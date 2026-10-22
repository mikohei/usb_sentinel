"""exFAT format at the end of a full inspection, and evidence kept when it must not run.

Helpers (full_runner, _assert_nothing_destructive) are borrowed from test_orchestrator.py.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tests import test_orchestrator
from tests.conftest import LSBLK_DISK, make_statvfs
from tests.test_orchestrator import _assert_nothing_destructive
from usb_sentinel.libs import (
    Config,
    FakeRunner,
    InspectionMode,
    InspectionResult,
    StepStatus,
    Verdict,
    inspect,
)
from usb_sentinel.libs.config import CapacityConfig, SpeedConfig
from usb_sentinel.libs.report.json_report import to_json
from usb_sentinel.libs.report.text import render_text
from usb_sentinel.libs.runner import CommandResult
from usb_sentinel.libs.steps import capacity as capacity_mod
from usb_sentinel.libs.steps.format_fs import format_argvs

full_runner = test_orchestrator.full_runner

FORMAT = [("wipefs", "--all", "--force", "/dev/sda"), ("mkfs.exfat", "/dev/sda")]


@pytest.fixture
def enabled(config: Config, monkeypatch: pytest.MonkeyPatch) -> Config:
    """Config that lets a full inspection run the destructive steps and pass them."""
    monkeypatch.setattr(capacity_mod, "DEFAULT_STATVFS", lambda _p: make_statvfs())
    return replace(
        config,
        allow_destructive=True,
        speed=SpeedConfig(nominal_read_mb_s=50.0, nominal_write_mb_s=10.0),
    )


def _probe_only(config: Config) -> Config:
    """f3probe mode: the only format is the one at the end."""
    return replace(config, capacity=CapacityConfig(use_probe=True))


def _full(runner: FakeRunner, config: Config) -> InspectionResult:
    return inspect(runner, config, "/dev/sda", mode=InspectionMode.FULL)


class TestReformat:
    def test_full_inspection_ends_with_exfat_format(
        self, full_runner: FakeRunner, enabled: Config
    ) -> None:
        result = _full(full_runner, enabled)

        assert result.verdict is Verdict.NO_DEFECT
        # Formatted once before f3write and once more after fio wrote to the raw device.
        assert full_runner.calls[-2:] == FORMAT
        last_fio = max(i for i, c in enumerate(full_runner.calls) if c[0] == "fio")
        assert last_fio < len(full_runner.calls) - 2
        assert result.reformat["status"] == "done"
        assert result.reformat["commands"] == [list(argv) for argv in FORMAT]
        assert json.loads(to_json(result))["reformat"]["status"] == "done"
        assert "Reformat: done (Formatted as exfat)" in render_text(result)

    def test_malware_leaves_the_drive_untouched(
        self, full_runner: FakeRunner, enabled: Config
    ) -> None:
        """Evidence: a drive with malware is never written, even in full mode."""
        full_runner.register(
            ["clamscan", "--recursive"],
            returncode=1,
            stdout="/mnt/sda1/eicar.com: Eicar-Signature FOUND\n",
        )

        result = _full(full_runner, enabled)

        assert result.verdict is Verdict.MALWARE_DETECTED
        assert result.reformat == {}
        _assert_nothing_destructive(full_runner)

    def test_unfinished_scan_leaves_the_drive_untouched(
        self, full_runner: FakeRunner, enabled: Config
    ) -> None:
        full_runner.register(["clamscan", "--recursive"], returncode=2, stderr="LibClamAV Error")

        result = _full(full_runner, enabled)

        assert result.reformat == {}
        _assert_nothing_destructive(full_runner)

    def test_scan_only_leaves_the_drive_untouched(
        self, full_runner: FakeRunner, enabled: Config
    ) -> None:
        result = inspect(full_runner, enabled, "/dev/sda")

        assert result.reformat == {}
        _assert_nothing_destructive(full_runner)

    def test_disabled_by_config(self, full_runner: FakeRunner, enabled: Config) -> None:
        result = _full(full_runner, replace(_probe_only(enabled), reformat=False))

        assert result.reformat == {}
        assert not any(c[0] == "mkfs.exfat" for c in full_runner.calls)

    def test_failure_is_a_warning_and_keeps_the_verdict(
        self, full_runner: FakeRunner, enabled: Config
    ) -> None:
        full_runner.register(["mkfs.exfat"], returncode=1, stderr="cannot open /dev/sda")

        result = _full(full_runner, _probe_only(enabled))

        assert result.verdict is Verdict.NO_DEFECT
        assert result.reformat["status"] == "failed"
        assert result.warnings == [
            "Drive not reformatted: Format failed: mkfs.exfat: cannot open /dev/sda"
        ]

    def test_skipped_while_still_mounted(self, enabled: Config, full_runner: FakeRunner) -> None:
        class MountedAfterProbe(FakeRunner):
            """lsblk shows a mount once f3probe has run (a mount left behind)."""

            def run(self, argv: list[str], *, timeout_s: float | None = None) -> CommandResult:
                if argv[0] == "lsblk" and any(c[0] == "f3probe" for c in self.calls):
                    self.calls.append(tuple(argv))
                    disk = json.loads(json.dumps(LSBLK_DISK))
                    disk["blockdevices"][0]["children"][0]["mountpoint"] = "/mnt/x"
                    return CommandResult(tuple(argv), stdout=json.dumps(disk))
                return super().run(argv, timeout_s=timeout_s)

        runner = MountedAfterProbe()
        runner.registered = full_runner.registered

        result = _full(runner, _probe_only(enabled))

        assert result.reformat["status"] == "skipped"
        assert result.reformat["summary"] == "Still mounted"
        assert "Drive not reformatted: Still mounted" in result.warnings
        assert not any(c[0] in ("wipefs", "mkfs.exfat") for c in runner.calls)
        speed = result.step("speed")
        assert speed is not None and speed.status is StepStatus.ERROR


def test_format_argvs_target_the_whole_disk() -> None:
    assert format_argvs("/dev/sdb") == [
        ["wipefs", "--all", "--force", "/dev/sdb"],
        ["mkfs.exfat", "/dev/sdb"],
    ]
