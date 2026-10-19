"""Backend failure handling that matters when triggered without an operator."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.conftest import (
    CLAMSCAN_VERSION,
    F3READ_OK,
    F3WRITE_OK,
    LSBLK_DISK,
    fio_json,
    make_statvfs,
)
from usb_sentinel.libs import Config, FakeRunner, StepContext, StepStatus, Verdict, inspect
from usb_sentinel.libs import device as device_mod
from usb_sentinel.libs.config import load_config
from usb_sentinel.libs.mounter import mounted
from usb_sentinel.libs.steps.capacity import CapacityStep
from usb_sentinel.libs.steps.capacity_fs import rw_mounted
from usb_sentinel.libs.steps.malware import MalwareScanStep
from usb_sentinel.libs.steps.speed import SpeedStep

LSBLK_UNMOUNTED = (
    '{"blockdevices": [{"name": "sda", "path": "/dev/sda", "type": "disk", "mountpoint": null,'
    ' "children": [{"path": "/dev/sda1", "fstype": "vfat", "mountpoint": null}]}]}'
)


def test_configuration_rejects_boolean_strings(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('allow_destructive = "false"\n')
    with pytest.raises(ValueError, match="TOML boolean"):
        load_config(path)


def test_missing_database_cannot_pass(ctx: StepContext, runner: FakeRunner) -> None:
    runner.register(["clamscan", "--version"], stdout="ClamAV 1.4.3")
    result = MalwareScanStep().run(ctx)
    assert result.status is StepStatus.ERROR
    assert not any("--recursive" in args for args in runner.calls)


def test_partial_mount_failure_unmounts_previous_partition(
    ctx: StepContext, runner: FakeRunner
) -> None:
    runner.register(
        ["lsblk"],
        stdout='{"blockdevices": [{"children": ['
        '{"path": "/dev/sda1", "fstype": "vfat"},'
        '{"path": "/dev/sda2", "fstype": "ext4"}]}]}',
    )
    runner.register(["mount"])
    runner.register(
        ["mount", "-o", "ro,noexec,nosuid,nodev", "/dev/sda2"],
        returncode=32,
        stderr="failed",
    )
    runner.register(["umount"])
    with pytest.raises(RuntimeError, match="failed"), mounted(ctx):
        pytest.fail("inspection must not start")
    assert len([args for args in runner.calls if args[0] == "umount"]) == 1


def test_nonboolean_flags_rejected(config: Config) -> None:
    with pytest.raises(ValueError):
        replace(config, emit_qr="false")


def test_rw_mount_always_uses_noexec_nosuid_nodev(ctx: StepContext, runner: FakeRunner) -> None:
    """Even the capacity check's rw mount leaves no room to execute infected files."""
    runner.register(["lsblk"], stdout=LSBLK_UNMOUNTED)
    runner.register(["mount"])
    runner.register(["umount"])
    runner.register(["f3write"], stdout=F3WRITE_OK)
    runner.register(["f3read"], stdout=F3READ_OK)

    CapacityStep(statvfs=lambda _p: make_statvfs(), monotonic=lambda: 0.0).run(ctx)

    mounts = [c for c in runner.calls if c[0] == "mount"]
    assert mounts, "mountが呼ばれていない"
    for argv in mounts:
        assert argv[1:4] == ("-w", "-o", "noexec,nosuid,nodev")


def test_rw_mount_unmounts_even_when_body_raises(ctx: StepContext, runner: FakeRunner) -> None:
    """The rw mount of the sample is always released, even on exception paths (a leftover mount
    stops the next inspection at the gate)."""
    runner.register(["mount"])
    runner.register(["umount"])
    with (
        pytest.raises(RuntimeError, match="boom"),
        rw_mounted(ctx, "/dev/sda1", 0, lambda _p: make_statvfs(), [], []),
    ):
        raise RuntimeError("boom")
    assert runner.calls[-1][0] == "umount"


@pytest.fixture
def infected_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    """FakeRunner for a full inspection sequence that detects EICAR (destructive commands are
    registered too, to confirm they are never called)."""
    sys_block = tmp_path / "sys_block"
    (sys_block / "sda").mkdir(parents=True)
    (sys_block / "sda" / "removable").write_text("1\n")
    monkeypatch.setattr(device_mod, "SYS_BLOCK", sys_block)
    fake = FakeRunner()
    fake.register(["lsblk"], stdout=json.dumps(LSBLK_DISK))
    fake.register(["udevadm", "info"], stdout="ID_BUS=usb\n")
    fake.register(["findmnt"], stdout="/dev/mmcblk0p2\n")
    fake.register(["clamscan", "--version"], stdout=CLAMSCAN_VERSION)
    fake.register(
        ["clamscan", "--recursive"],
        returncode=1,
        stdout="/mnt/sda1/eicar.com: Eicar-Signature FOUND\n",
    )
    fake.register(["mount"])
    fake.register(["umount"])
    fake.register(["f3probe"])
    fake.register(["f3write"], stdout=F3WRITE_OK)
    fake.register(["f3read"], stdout=F3READ_OK)
    fake.register(["fio"], stdout=fio_json("read"))
    return fake


def test_permitted_gate_skips_destructive_steps_after_malware(
    infected_runner: FakeRunner, config: Config
) -> None:
    """Even with malware FAIL + hash_inventory PASS, capacity and speed are SKIPPED (regression
    test for permitted)."""
    result = inspect(infected_runner, replace(config, allow_destructive=True), "/dev/sda")

    statuses = {s.name: s.status for s in result.steps}
    assert statuses["malware"] is StepStatus.FAIL
    assert statuses["hash_inventory"] is StepStatus.PASS
    assert statuses["capacity"] is StepStatus.SKIPPED
    assert statuses["speed"] is StepStatus.SKIPPED
    assert result.verdict is Verdict.MALWARE_DETECTED


def test_no_write_commands_after_malware_detection(
    infected_runner: FakeRunner, config: Config
) -> None:
    """After infection is detected, the only external commands called are ro mount, umount, and
    queries."""
    inspect(infected_runner, replace(config, allow_destructive=True), "/dev/sda")

    allowed = {"lsblk", "udevadm", "findmnt", "clamscan", "mount", "umount"}
    assert {c[0] for c in infected_runner.calls} <= allowed
    for argv in infected_runner.calls:
        if argv[0] == "mount":
            assert "-w" not in argv
            assert "ro,noexec,nosuid,nodev" in argv


def test_fio_read_pass_is_readonly(ctx: StepContext, runner: FakeRunner) -> None:
    """For read measurement, fio itself is also made to refuse writes."""
    runner.register(["lsblk"], stdout=LSBLK_UNMOUNTED)
    runner.register(["fio", "--name=seq_read"], stdout=fio_json("read"))
    runner.register(["fio", "--name=seq_write"], stdout=fio_json("write"))

    SpeedStep(monotonic=lambda: 0.0).run(ctx)

    read = next(c for c in runner.calls if c[:2] == ("fio", "--name=seq_read"))
    assert "--readonly" in read
    assert "--readonly" not in next(c for c in runner.calls if c[:2] == ("fio", "--name=seq_write"))
