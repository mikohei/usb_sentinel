"""Behaviour of scripts/boot-video-hint.sh and its opt-in wiring in install-all.sh.

video_hint_append is sourced into a fresh bash (set -euo pipefail, bash 3.2 on macOS) with
CMDLINE_FILE pointing into tmp_path. The installer runs reuse the fake-binary harness of
tests/test_install_kiosk.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.test_install_kiosk import run_install_all_to_end
from tests.test_run_docker import bash_path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "boot-video-hint.sh"
ORIGINAL = "console=tty1 root=PARTUUID=x rootwait"
HINT = "video=HDMI-A-1:480x320@60"
PRELUDE = 'set -euo pipefail; warn(){ echo "warn: $*" >&2; }; source "$1"; shift; "$@"'


def append(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Call video_hint_append with CMDLINE_FILE=<tmp_path>/cmdline.txt."""
    return subprocess.run(
        [bash_path(), "-c", PRELUDE, "_", str(SCRIPT), "video_hint_append"],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "CMDLINE_FILE": str(tmp_path / "cmdline.txt")},
    )


def test_appends_once_with_backup(tmp_path: Path) -> None:
    """The first call appends the hint and keeps a .bak; the second call changes nothing."""
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text(ORIGINAL + "\n")
    first = append(tmp_path)
    assert first.returncode == 0, first.stderr
    assert "appended" in first.stdout
    assert cmdline.read_text() == f"{ORIGINAL} {HINT}\n"
    assert (tmp_path / "cmdline.txt.bak").read_text() == ORIGINAL + "\n"
    second = append(tmp_path)
    assert second.returncode == 0, second.stderr
    assert "already present" in second.stdout
    assert cmdline.read_text() == f"{ORIGINAL} {HINT}\n"
    assert (tmp_path / "cmdline.txt.bak").read_text() == ORIGINAL + "\n"


def test_missing_file_warns_and_returns_0(tmp_path: Path) -> None:
    """Without cmdline.txt the function warns and the caller continues."""
    result = append(tmp_path)
    assert result.returncode == 0
    assert "not found" in result.stderr
    assert not (tmp_path / "cmdline.txt").exists()
    assert not (tmp_path / "cmdline.txt.bak").exists()


def test_multi_line_file_is_left_alone(tmp_path: Path) -> None:
    """A cmdline.txt with two lines is not understood and stays untouched."""
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text(ORIGINAL + "\nquiet\n")
    result = append(tmp_path)
    assert result.returncode == 0
    assert "more than one line" in result.stderr
    assert cmdline.read_text() == ORIGINAL + "\nquiet\n"
    assert not (tmp_path / "cmdline.txt.bak").exists()


def test_foreign_connector_entry_is_left_alone(tmp_path: Path) -> None:
    """A different video=HDMI-A-1: mode is kept (duplicate connector entries are undefined)."""
    cmdline = tmp_path / "cmdline.txt"
    text = f"{ORIGINAL} video=HDMI-A-1:800x480@60\n"
    cmdline.write_text(text)
    result = append(tmp_path)
    assert result.returncode == 0
    assert "already has a video=HDMI-A-1:" in result.stderr
    assert cmdline.read_text() == text
    assert not (tmp_path / "cmdline.txt.bak").exists()


def test_file_without_trailing_newline(tmp_path: Path) -> None:
    """A cmdline.txt without a final newline still becomes one correct line."""
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text(ORIGINAL)
    result = append(tmp_path)
    assert result.returncode == 0, result.stderr
    assert cmdline.read_text() == f"{ORIGINAL} {HINT}\n"
    assert (tmp_path / "cmdline.txt.bak").read_text() == ORIGINAL


def test_sourcing_runs_nothing() -> None:
    """Sourcing the file only defines variables and functions."""
    result = subprocess.run(
        [bash_path(), "-c", 'source "$1"', "_", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == "" and result.stderr == ""


# ------------------------------------------------------------------ install-all.sh wiring


def test_installer_default_skips_video_hint(tmp_path: Path) -> None:
    """By default (INSTALL_VIDEO_HINT=0) cmdline.txt is untouched and the summary says so."""
    run = run_install_all_to_end(tmp_path, "", {"INSTALL_YES": "1"}, cmdline=ORIGINAL + "\n")
    assert run.code == 0, run.output
    assert run.cmdline.read_text() == ORIGINAL + "\n"
    assert not (tmp_path / "cmdline.txt.bak").exists()
    assert "boot video hint: skipped" in run.output


def test_installer_opt_in_appends_video_hint(tmp_path: Path) -> None:
    """INSTALL_VIDEO_HINT=1 appends the hint once, keeps a .bak and prints the undo line."""
    run = run_install_all_to_end(
        tmp_path,
        "",
        {"INSTALL_YES": "1", "INSTALL_VIDEO_HINT": "1"},
        cmdline=ORIGINAL + "\n",
    )
    assert run.code == 0, run.output
    assert run.cmdline.read_text() == f"{ORIGINAL} {HINT}\n"
    assert (tmp_path / "cmdline.txt.bak").read_text() == ORIGINAL + "\n"
    assert "appended" in run.output
    assert "cmdline.txt.bak" in run.output
