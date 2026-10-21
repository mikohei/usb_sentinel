"""Behaviour of scripts/clock-floor.sh and its wiring (unit file, install-docker.sh).

The Raspberry Pi 4 has no RTC and the station runs offline, so every boot starts at the same old
time. clock-floor.sh moves the clock forward to just after the newest saved state at boot. The
functions are sourced into the system bash (3.2 on macOS) like test_run_docker.py.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.test_run_docker import bash_path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "clock-floor.sh"
UNIT = ROOT / "deploy" / "usb-sentinel-clock.service"
NOW = 1760850000


def run_fn(fn: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Source the script and call one of its functions with the given arguments."""
    argv = [bash_path(), "-c", 'source "$1"; shift; "$@"', "_", str(SCRIPT), fn, *args]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def touch(path: Path, mtime: int, *, directory: bool = False) -> Path:
    """Create a file or directory and set its mtime."""
    if directory:
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    os.utime(path, (mtime, mtime))
    return path


def test_sourcing_runs_nothing() -> None:
    """Sourcing the script defines functions only and never touches the clock."""
    result = subprocess.run(
        [bash_path(), "-c", 'source "$1"', "_", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_newest_mtime_reads_paths_and_their_newest_child(tmp_path: Path) -> None:
    """A directory counts with its newest direct child; missing paths are ignored."""
    stamp = touch(tmp_path / "clock", NOW - 300)
    results = touch(tmp_path / "results", NOW - 200, directory=True)
    touch(results / "20260727T204530Z-a", NOW - 100, directory=True)
    touch(results / "20260727T205110Z-b", NOW - 150, directory=True)
    os.utime(results, (NOW - 200, NOW - 200))
    result = run_fn("newest_mtime", str(stamp), str(results), str(tmp_path / "missing"))
    assert (result.returncode, result.stdout) == (0, f"{NOW - 100}\n"), result.stderr


def test_newest_mtime_prints_nothing_without_paths(tmp_path: Path) -> None:
    """No existing path: empty output, exit 0 (a first boot has nothing to restore)."""
    empty = touch(tmp_path / "empty", NOW, directory=True)
    result = run_fn("newest_mtime", str(tmp_path / "missing"))
    assert (result.returncode, result.stdout) == (0, "")
    result = run_fn("newest_mtime", str(empty))
    assert (result.returncode, result.stdout) == (0, f"{NOW}\n")


def test_clock_target_moves_only_forward() -> None:
    """The target is one second after the floor, and only when the clock is not already later."""
    cases = [
        (str(NOW), str(NOW - 1), ""),
        (str(NOW), str(NOW), f"{NOW + 1}\n"),
        (str(NOW - 7355787), str(NOW), f"{NOW + 1}\n"),
        (str(NOW), "", ""),
    ]
    for now, floor, expected in cases:
        result = run_fn("clock_target", now, floor)
        assert (result.returncode, result.stdout) == (0, expected), (now, floor, result.stderr)


def test_unit_restores_at_boot_and_saves_at_shutdown() -> None:
    """The oneshot unit runs before Docker and the time-set target, and saves on stop."""
    lines = UNIT.read_text().splitlines()
    assert "ExecStart=/opt/usb-sentinel/scripts/clock-floor.sh restore" in lines
    assert "ExecStop=/opt/usb-sentinel/scripts/clock-floor.sh save" in lines
    assert "RemainAfterExit=yes" in lines
    assert "DefaultDependencies=no" in lines
    before = next(line for line in lines if line.startswith("Before="))
    for target in ("time-set.target", "docker.service", "shutdown.target"):
        assert target in before.split("=", 1)[1].split()
    assert "WantedBy=sysinit.target" in lines


def test_install_docker_installs_and_enables_the_clock_unit() -> None:
    """install-docker.sh copies the script and unit, then enables the unit."""
    text = (ROOT / "scripts" / "install-docker.sh").read_text()
    script = (
        'install -m 0755 "${REPO_DIR}/scripts/clock-floor.sh" "${APP_DIR}/scripts/clock-floor.sh"'
    )
    unit = (
        'install -m 0644 "${REPO_DIR}/deploy/usb-sentinel-clock.service" '
        "/etc/systemd/system/usb-sentinel-clock.service"
    )
    enable = "systemctl enable --now usb-sentinel-clock.service"
    assert script in text
    assert unit in text
    assert text.index(unit) < text.index("systemctl daemon-reload") < text.index(enable)
