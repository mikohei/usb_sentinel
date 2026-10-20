"""Static checks of the host install scripts and the tmpfiles entry (no root needed)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.test_run_docker import bash_path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
TMPFILES = ROOT / "deploy" / "usb-sentinel.tmpfiles.conf"
TMPFILES_LINE = "d /run/usb-sentinel-control 2770 root usb-sentinel -"


@pytest.mark.parametrize(
    "name", ["install-all.sh", "install-docker.sh", "kiosk-lockdown.sh", "run-docker.sh"]
)
def test_scripts_parse(name: str) -> None:
    """bash -n accepts every host script."""
    result = subprocess.run(
        [bash_path(), "-n", str(SCRIPTS / name)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_install_all_sources_lockdown_after_gsettings_user() -> None:
    """install-all.sh sources kiosk-lockdown.sh once gsettings_user exists."""
    lines = (SCRIPTS / "install-all.sh").read_text().splitlines()
    defined = next(i for i, line in enumerate(lines) if line.startswith("gsettings_user()"))
    sourced = lines.index('source "${REPO_DIR}/scripts/kiosk-lockdown.sh"')
    assert defined < sourced


def test_install_all_calls_lockdown_inside_gsettings_block() -> None:
    """kiosk_lockdown gsettings_user runs inside the `if command -v gsettings` block."""
    lines = (SCRIPTS / "install-all.sh").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("if command -v gsettings"))
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("else"))
    block = lines[start:end]
    assert "    kiosk_lockdown gsettings_user" in block
    assert block.index("    kiosk_lockdown gsettings_user") > block.index(
        "    gsettings_user set org.gnome.desktop.screensaver lock-enabled false"
    )


def test_install_docker_installs_tmpfiles_entry() -> None:
    """install-docker.sh requires systemd-tmpfiles, installs the entry and creates the dir."""
    text = (SCRIPTS / "install-docker.sh").read_text()
    assert "systemd-tmpfiles" in next(
        line for line in text.splitlines() if "for command in" in line
    )
    install = (
        'install -m 0644 "${REPO_DIR}/deploy/usb-sentinel.tmpfiles.conf" '
        "/etc/tmpfiles.d/usb-sentinel.conf"
    )
    assert install in text
    assert "systemd-tmpfiles --create /etc/tmpfiles.d/usb-sentinel.conf" in text
    assert text.index("groupadd --system usb-sentinel") < text.index(install)


def test_tmpfiles_entry_is_the_single_control_directory_line() -> None:
    """The only non-comment line creates the control directory as root:usb-sentinel 2770."""
    lines = [
        line for line in TMPFILES.read_text().splitlines() if line and not line.startswith("#")
    ]
    assert lines == [TMPFILES_LINE]
