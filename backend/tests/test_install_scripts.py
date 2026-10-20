"""Static checks of the host install scripts and the tmpfiles entry (no root needed)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.test_run_docker import bash_path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
TMPFILES = ROOT / "deploy" / "usb-sentinel.tmpfiles.conf"
TMPFILES_LINE = "d /run/usb-sentinel-control 2770 root usb-sentinel -"


DESKTOP_TEMPLATE = ROOT / "frontend" / "deploy" / "usb-sentinel-display.desktop"
DISPLAY_USER_LINE = 'readonly DISPLAY_USER="${DISPLAY_USER:-${SUDO_USER:-}}"'


def _script(name: str) -> str:
    return (SCRIPTS / name).read_text()


def _ordered(text: str, *needles: str) -> None:
    """Assert every needle occurs, each after the previous one."""
    position = -1
    for needle in needles:
        found = text.find(needle, position + 1)
        assert found > position, f"{needle!r} not found after position {position}"
        position = found


@pytest.mark.parametrize(
    "name",
    [
        "install-all.sh",
        "install-docker.sh",
        "kiosk-lockdown.sh",
        "kiosk-enable.sh",
        "kiosk-disable.sh",
        "run-docker.sh",
        "ssh-access.sh",
    ],
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


def test_install_all_never_calls_kiosk_lockdown() -> None:
    """install-all.sh does not arm the lockdown; the final message points at kiosk-enable.sh."""
    lines = _script("install-all.sh").splitlines()
    assert not any(re.match(r"^\s*kiosk_lockdown\b", line) for line in lines)
    start = lines.index("cat <<EOF")
    end = lines.index("EOF", start)
    message = "\n".join(lines[start:end])
    assert "kiosk-enable.sh" in message
    assert "kiosk-disable.sh" in message
    assert "Ctrl+Alt+F3" in message
    assert "pkill -f usb-sentinel-display" in message


def test_install_all_writes_autostart_hidden() -> None:
    """The autostart entry is hidden right after it is written and chmod-ed."""
    lines = _script("install-all.sh").splitlines()
    written = next(i for i, line in enumerate(lines) if line.endswith('> "${DESKTOP_FILE}"'))
    chmod = lines.index('chmod 0644 "${DESKTOP_FILE}"')
    hidden = lines.index('kiosk_autostart_hide "${DESKTOP_FILE}"')
    assert written < chmod < hidden


def test_desktop_template_substitutes_and_logs() -> None:
    """The sed replacements of install-all.sh leave a logging Exec line without <user>."""
    text = (
        DESKTOP_TEMPLATE.read_text()
        .replace("/home/<user>/.local/bin/uv", "/home/kiosk/.local/bin/uv")
        .replace("/home/<user>/usb_sentinel/frontend", "/opt/usb_sentinel/frontend")
        .replace("<user>", "kiosk")
    )
    assert "<user>" not in text
    lines = text.splitlines()
    execs = [line for line in lines if line.startswith("Exec=")]
    assert len(execs) == 1
    exec_line = execs[0]
    assert exec_line.startswith("Exec=sh -c '") and exec_line.endswith("'")
    for needle in (
        "--kiosk",
        "--offline --frozen --no-sync",
        "/opt/usb_sentinel/frontend",
        "/home/kiosk/.local/bin/uv",
        'autostart.log" 2>&1',
    ):
        assert needle in exec_line
    assert "X-GNOME-Autostart-Delay=5" in lines
    assert "X-GNOME-Autostart-enabled=true" in lines
    assert not any(line.startswith("Hidden=") for line in lines)


def test_kiosk_enable_structure() -> None:
    """kiosk-enable.sh checks the resolution, confirms, locks down, then shows the entry."""
    text = _script("kiosk-enable.sh")
    _ordered(
        text,
        'source "${REPO_DIR}/scripts/kiosk-lockdown.sh"',
        "KIOSK_FORCE",
        'confirm "',
        "kiosk_lockdown gsettings_user",
        'kiosk_autostart_show "${DESKTOP_FILE}"',
    )
    assert "gsettings_user() {" in text
    assert "/run/user/${uid}/bus" in text
    assert "dbus-run-session" in text


def test_kiosk_disable_structure() -> None:
    """kiosk-disable.sh hides the entry, stops the app and unlocks without prompting."""
    text = _script("kiosk-disable.sh")
    assert 'kiosk_autostart_hide "${DESKTOP_FILE}"' in text
    assert 'pkill -u "${DISPLAY_USER}" -f usb-sentinel-display' in text
    assert "kiosk_unlock gsettings_user" in text
    assert 'confirm "' not in text


def test_kiosk_scripts_resolve_display_user_like_install_all() -> None:
    """Both scripts reuse the DISPLAY_USER (and INSTALL_YES) pattern of install-all.sh."""
    assert DISPLAY_USER_LINE in _script("install-all.sh")
    assert DISPLAY_USER_LINE in _script("kiosk-enable.sh")
    assert DISPLAY_USER_LINE in _script("kiosk-disable.sh")
    assert 'readonly INSTALL_YES="${INSTALL_YES:-0}"' in _script("kiosk-enable.sh")


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
