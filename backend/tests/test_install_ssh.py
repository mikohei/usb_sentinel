"""Behaviour of scripts/ssh-access.sh (the opt-in SSH step of install-all.sh) and its wiring.

ssh_setup is sourced into a fresh bash with fake apt-get/systemctl/sshd/hostname/ufw binaries
on PATH that log their argv. The current user stands in for the sudo caller, the admin home and
the sshd drop-in live under tmp_path.
"""

from __future__ import annotations

import getpass
import os
import stat
import subprocess
from pathlib import Path

import pytest

from tests.test_run_docker import bash_path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
SSH_SCRIPT = SCRIPTS / "ssh-access.sh"
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFake you@laptop"
PRELUDE = (
    'warn() { printf "warning: %s\\n" "$*" >&2; }; '
    'die() { printf "error: %s\\n" "$*" >&2; exit 1; }; '
    'source "$1"; shift; "$@"'
)
FAKE_BINARIES = ("apt-get", "systemctl", "sshd", "hostname", "ufw")


def _make_fakes(tmp_path: Path, *, ufw: bool = True) -> tuple[Path, Path]:
    """Create fake binaries that append their name and argv to a log file."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in FAKE_BINARIES:
        if name == "ufw" and not ufw:
            continue
        body = f'#!/bin/sh\necho "{name} $*" >> "{log}"\n'
        if name == "hostname":
            body += "echo '192.168.1.50 10.0.0.7'\n"
        if name == "ufw":
            body += '[ "$1" = status ] && echo "Status: active"\n'
        body += "exit 0\n"
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    return bin_dir, log


def run_setup(
    tmp_path: Path, env: dict[str, str], *, ufw: bool = True
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run ssh_setup for the current user with fakes on PATH; return the result and the log."""
    bin_dir, log = _make_fakes(tmp_path, ufw=ufw)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    full_env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", str(home)),
        "SSHD_DROPIN": str(tmp_path / "sshd_config.d" / "90-usb-sentinel.conf"),
        **env,
    }
    argv = [
        bash_path(),
        "-c",
        PRELUDE,
        "_",
        str(SSH_SCRIPT),
        "ssh_setup",
        getpass.getuser(),
        str(home),
    ]
    result = subprocess.run(argv, capture_output=True, text=True, check=False, env=full_env)
    return result, log


def test_script_parses_and_sourcing_runs_nothing() -> None:
    """bash -n accepts the script and sourcing it only defines functions."""
    parse = subprocess.run([bash_path(), "-n", str(SSH_SCRIPT)], capture_output=True, text=True)
    assert parse.returncode == 0, parse.stderr
    sourced = subprocess.run(
        [bash_path(), "-c", 'source "$1"', "_", str(SSH_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert sourced.returncode == 0
    assert sourced.stdout == "" and sourced.stderr == ""


def test_without_key_dies_before_installing_anything(tmp_path: Path) -> None:
    """INSTALL_SSH=1 without a key: exit 1, clear message, no apt/systemctl call, no drop-in."""
    result, log = run_setup(tmp_path, {})
    assert result.returncode == 1
    assert "no SSH public key" in result.stderr
    assert "SSH_AUTHORIZED_KEY=" in result.stderr
    assert "Password login is never enabled" in result.stderr
    assert not log.exists()
    assert not (tmp_path / "sshd_config.d").exists()


def test_with_key_writes_dropin_installs_key_and_validates(tmp_path: Path) -> None:
    """The drop-in hardens sshd for the admin, the key is installed 0600, sshd -t runs first."""
    result, log = run_setup(tmp_path, {"SSH_AUTHORIZED_KEY": KEY})
    assert result.returncode == 0, result.stderr
    admin = getpass.getuser()
    dropin = (tmp_path / "sshd_config.d" / "90-usb-sentinel.conf").read_text().splitlines()
    for line in (
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        "PermitRootLogin no",
        "PubkeyAuthentication yes",
        f"AllowUsers {admin}",
        "X11Forwarding no",
    ):
        assert line in dropin
    auth_keys = tmp_path / "home" / ".ssh" / "authorized_keys"
    assert auth_keys.read_text() == KEY + "\n"
    assert stat.S_IMODE(auth_keys.stat().st_mode) == 0o600
    assert stat.S_IMODE(auth_keys.parent.stat().st_mode) == 0o700
    calls = log.read_text().splitlines()
    assert "apt-get install -y --no-install-recommends openssh-server" in calls
    assert calls.index("sshd -t") < calls.index("systemctl enable --now ssh")
    assert "ufw allow 22/tcp" in calls
    assert f"ssh {admin}@192.168.1.50" in result.stdout
    assert f"ssh {admin}@10.0.0.7" in result.stdout
    assert "sudo systemctl disable --now ssh" in result.stdout


def test_second_run_does_not_duplicate_key(tmp_path: Path) -> None:
    """Re-running with the same key leaves a single line in authorized_keys."""
    first, _ = run_setup(tmp_path, {"SSH_AUTHORIZED_KEY": KEY})
    assert first.returncode == 0, first.stderr
    (tmp_path / "bin").rename(tmp_path / "bin.first")
    second, _ = run_setup(tmp_path, {"SSH_AUTHORIZED_KEY": KEY})
    assert second.returncode == 0, second.stderr
    assert "key already present" in second.stdout
    auth_keys = tmp_path / "home" / ".ssh" / "authorized_keys"
    assert auth_keys.read_text() == KEY + "\n"


def test_key_file_and_existing_authorized_keys(tmp_path: Path) -> None:
    """SSH_AUTHORIZED_KEY_FILE works, and an existing non-empty authorized_keys needs no env."""
    key_file = tmp_path / "id.pub"
    key_file.write_text(f"# comment\n{KEY}\n")
    result, _ = run_setup(tmp_path, {"SSH_AUTHORIZED_KEY_FILE": str(key_file)})
    assert result.returncode == 0, result.stderr
    (tmp_path / "bin").rename(tmp_path / "bin.first")
    again, _ = run_setup(tmp_path, {})
    assert again.returncode == 0, again.stderr


def test_malformed_key_is_rejected(tmp_path: Path) -> None:
    """A value that is not an OpenSSH public key line is refused."""
    result, log = run_setup(tmp_path, {"SSH_AUTHORIZED_KEY": "hunter2"})
    assert result.returncode == 1
    assert "does not look like an OpenSSH public key" in result.stderr
    assert not log.exists()


def test_without_ufw_firewall_is_left_alone(tmp_path: Path) -> None:
    """When ufw is not installed nothing is installed and no ufw command runs."""
    result, log = run_setup(tmp_path, {"SSH_AUTHORIZED_KEY": KEY}, ufw=False)
    assert result.returncode == 0, result.stderr
    assert "ufw not installed" in result.stdout
    assert not any(line.startswith("ufw") for line in log.read_text().splitlines())
    assert "ufw" not in "".join(
        line for line in log.read_text().splitlines() if line.startswith("apt-get")
    )


# ------------------------------------------------------------------ install-all.sh wiring


def test_install_all_ssh_is_opt_in() -> None:
    """install-all.sh defaults INSTALL_SSH to 0 and only calls ssh_setup inside the guard."""
    text = (SCRIPTS / "install-all.sh").read_text()
    assert 'readonly INSTALL_SSH="${INSTALL_SSH:-0}"' in text
    assert 'readonly SSH_ADMIN_USER="${SSH_ADMIN_USER:-${SUDO_USER:-}}"' in text
    assert (
        "openssh-server"
        not in text.split('source "${REPO_DIR}/scripts/ssh-access.sh"')[0].split(
            "set -euo pipefail"
        )[1]
    )
    lines = text.splitlines()
    guard = lines.index('if [[ "${INSTALL_SSH}" == 1 ]]; then')
    setup = next(i for i, line in enumerate(lines) if line.strip().startswith("ssh_setup "))
    close = lines.index("fi", guard)
    assert guard < setup < close
    assert 'ssh_setup "${SSH_ADMIN_USER}"' in lines[setup]
    assert "DISPLAY_USER" not in lines[setup]


@pytest.mark.parametrize("step", ["1/10", "2/10", "7/10 SSH", "8/10", "9/10", "10/10"])
def test_install_all_steps_renumbered(step: str) -> None:
    """Every log header uses the new /10 numbering and no /9 header remains."""
    text = (SCRIPTS / "install-all.sh").read_text()
    assert f'log "{step}' in text
    assert "/9 " not in text
