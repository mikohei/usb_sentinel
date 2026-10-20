"""End-to-end runs of scripts/install-all.sh with every external binary faked (no root).

The scripts and frontend/deploy are copied under tmp_path/repo so REPO_DIR resolves there;
install-docker.sh is stubbed, the sudo check patched to `true`, and uv is pre-installed in the
fake home so the preflight asks only the kiosk question. CLAMAV_DB_DIR points at a temp dir
that already holds a complete signature database, so step 8 passes with the fake docker.
Fakes append their argv to $FAKE_LOG; `install` replaces BSD install (which fails on -o/-g
as non-root). bash 3.2.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.test_install_scripts import FAKE_BINARIES as PREFLIGHT_FAKES
from tests.test_install_scripts import ROOT_CHECK
from tests.test_run_docker import bash_path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
DEPLOY = ROOT / "frontend" / "deploy"
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests test@laptop"
LOCKDOWN_SET = "gsettings set org.gnome.desktop.lockdown disable-command-line true"
LOG = 'echo "{name} $*" >> "$FAKE_LOG"'

FAKES = {
    "uname": PREFLIGHT_FAKES["uname"],
    "id": PREFLIGHT_FAKES["id"],
    "getent": PREFLIGHT_FAKES["getent"],
    "curl": PREFLIGHT_FAKES["curl"],
    "sha256sum": PREFLIGHT_FAKES["sha256sum"],
    # Drains stdin so unread answers are provable; every prompt precedes step 2.
    "apt-get": 'echo "apt-get $* stdin=[$(cat)]" >> "$FAKE_LOG"\nexit 0',
    "systemctl": LOG.format(name="systemctl") + "\nexit 0",
    "usermod": LOG.format(name="usermod") + "\nexit 0",
    "chown": LOG.format(name="chown") + "\nexit 0",
    "docker": LOG.format(name="docker") + "\nexit 0",
    "sudo": (
        LOG.format(name="sudo")
        + '\nwhile [ $# -gt 0 ]; do case "$1" in -u) shift 2;; -H) shift;; *) break;; esac; done'
        '\nexec "$@"'
    ),
    "dbus-run-session": (
        LOG.format(name="dbus-run-session")
        + '\nwhile [ "$1" != -- ]; do shift; done; shift\nexec "$@"'
    ),
    "gsettings": (
        LOG.format(name="gsettings")
        + '\nif [ "$1" = list-recursively ]; then echo "$2 some-key [\'<Super>a\']"; fi\nexit 0'
    ),
    "install": (
        LOG.format(name="install") + '\nmkdir_mode=0; args=""'
        "\nwhile [ $# -gt 0 ]; do"
        '\n  case "$1" in -o|-g|-m) shift;; -d) mkdir_mode=1;; *) args="$args $1";; esac; shift'
        "\ndone"
        "\nset -- $args"
        '\nif [ "$mkdir_mode" = 1 ]; then mkdir -p "$@"; else cp "$1" "$2"; fi'
    ),
}
UV_FAKE = '#!/bin/sh\necho "uv $*" >> "$FAKE_LOG"\n[ "$1" = --version ] && echo "uv 0.0"\nexit 0\n'


@dataclass
class InstallRun:
    """Result of one faked install-all.sh run."""

    code: int
    output: str
    log: str
    desktop: Path
    cmdline: Path
    home: Path
    repo: Path
    bindir: Path


def hidden_lines(desktop: Path) -> list[str]:
    """Return the Hidden= lines of the autostart entry (the template comment does not count)."""
    return [line for line in desktop.read_text().splitlines() if line.startswith("Hidden=")]


def write_fakes(bindir: Path, fakes: dict[str, str]) -> None:
    """Write every fake as an executable /bin/sh script into bindir."""
    bindir.mkdir(exist_ok=True)
    for name, body in fakes.items():
        path = bindir / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)


def prepare(
    tmp_path: Path, extra_fakes: dict[str, str] | None = None, uv_fake: str = UV_FAKE
) -> InstallRun:
    """Copy the repo pieces, write the fakes and the fake home; return the paths (code -1)."""
    repo = tmp_path / "repo"
    shutil.copytree(SCRIPTS, repo / "scripts")
    shutil.copytree(DEPLOY, repo / "frontend" / "deploy")
    (repo / "scripts" / "install-docker.sh").write_text("#!/bin/sh\nexit 0\n")
    for name in ("install-all.sh", "kiosk-enable.sh"):
        script = repo / "scripts" / name
        text = script.read_text()
        assert ROOT_CHECK in text
        script.write_text(text.replace(ROOT_CHECK, "true"))
    bindir = tmp_path / "bin"
    write_fakes(bindir, {**FAKES, **(extra_fakes or {})})
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    uv = home / ".local" / "bin" / "uv"
    uv.write_text(uv_fake)
    uv.chmod(0o755)
    (tmp_path / "calls.log").touch()
    clamav = tmp_path / "clamav"
    clamav.mkdir()
    for name in ("main.cvd", "daily.cld", "bytecode.cvd"):
        (clamav / name).write_text("signatures")
    desktop = home / ".config" / "autostart" / "usb-sentinel-display.desktop"
    return InstallRun(-1, "", "", desktop, tmp_path / "cmdline.txt", home, repo, bindir)


def base_env(run: InstallRun, env: dict[str, str]) -> dict[str, str]:
    """Environment for the scripts: fakes first on PATH, fake home, log, display user."""
    return {
        "PATH": f"{run.bindir}:/usr/bin:/bin",
        "HOME": str(run.home),
        "FAKE_LOG": str(run.home.parent / "calls.log"),
        "FAKE_HOME": str(run.home),
        "DISPLAY_USER": "kiosk",
        "CMDLINE_FILE": str(run.cmdline),
        "CLAMAV_DB_DIR": str(run.home.parent / "clamav"),
        "CLAMAV_RETRY_DELAY": "0",
        "CONSOLE_SETUP_FILE": str(run.home.parent / "console-setup"),
        **env,
    }


def run_install_all_to_end(
    tmp_path: Path,
    stdin: str,
    env: dict[str, str],
    *,
    cmdline: str | None = None,
    extra_fakes: dict[str, str] | None = None,
    uv_fake: str = UV_FAKE,
    before: Callable[[InstallRun], None] | None = None,
) -> InstallRun:
    """Run the copied install-all.sh to its end and collect code, output, log and paths.

    before, if given, runs on the prepared sandbox just before the script starts."""
    run = prepare(tmp_path, extra_fakes, uv_fake)
    if before is not None:
        before(run)
    if cmdline is not None:
        run.cmdline.write_text(cmdline)
    result = subprocess.run(
        [bash_path(), str(run.repo / "scripts" / "install-all.sh")],
        input=stdin,
        capture_output=True,
        text=True,
        env=base_env(run, env),
        check=False,
    )
    run.code = result.returncode
    run.output = result.stdout + result.stderr
    run.log = (tmp_path / "calls.log").read_text()
    return run


def test_default_run_arms_kiosk(tmp_path: Path) -> None:
    """INSTALL_YES=1 ends with autostart enabled, the lockdown applied and the escape routes."""
    run = run_install_all_to_end(tmp_path, "", {"INSTALL_YES": "1"})
    assert run.code == 0, run.output
    assert not hidden_lines(run.desktop)
    assert LOCKDOWN_SET in run.log
    assert "gsettings set org.gnome.shell favorite-apps []" in run.log
    for needle in (
        "10/10 Arming the kiosk",
        "Kiosk: armed",
        "Ctrl+Alt+F3",
        "kiosk-disable.sh",
        "calibrate",
        "P?",
    ):
        assert needle in run.output


def test_answer_n_leaves_hidden(tmp_path: Path) -> None:
    """Answering n to the kiosk question keeps Hidden=true and applies no lockdown."""
    run = run_install_all_to_end(tmp_path, "n\n", {})
    assert run.code == 0, run.output
    assert hidden_lines(run.desktop) == ["Hidden=true"]
    assert "gsettings set org.gnome.desktop.lockdown" not in run.log
    assert "kiosk NOT armed" in run.output
    assert "kiosk-enable.sh" in run.output


def test_answer_no_word_also_declines(tmp_path: Path) -> None:
    """The word no declines like n."""
    run = run_install_all_to_end(tmp_path, "no\n", {})
    assert run.code == 0, run.output
    assert hidden_lines(run.desktop) == ["Hidden=true"]
    assert "gsettings set org.gnome.desktop.lockdown" not in run.log
    assert "kiosk NOT armed" in run.output


def test_install_kiosk_0_overrides_yes(tmp_path: Path) -> None:
    """INSTALL_KIOSK=0 beats INSTALL_YES=1 and asks nothing (the y stays unread)."""
    run = run_install_all_to_end(tmp_path, "y\n", {"INSTALL_YES": "1", "INSTALL_KIOSK": "0"})
    assert run.code == 0, run.output
    assert hidden_lines(run.desktop) == ["Hidden=true"]
    assert "gsettings set org.gnome.desktop.lockdown" not in run.log
    assert "INSTALL_KIOSK=0" in run.output
    assert "apt-get update stdin=[y]" in run.log


def test_install_kiosk_1_skips_prompt(tmp_path: Path) -> None:
    """INSTALL_KIOSK=1 arms without asking; the n stays unread."""
    run = run_install_all_to_end(tmp_path, "n\n", {"INSTALL_KIOSK": "1"})
    assert run.code == 0, run.output
    assert not hidden_lines(run.desktop)
    assert LOCKDOWN_SET in run.log
    assert "apt-get update stdin=[n]" in run.log


def test_ssh_hint_in_final_message(tmp_path: Path) -> None:
    """With INSTALL_SSH=1 the escape routes name the admin user and the key is installed."""
    extra: dict[str, str] = {
        "sshd": LOG.format(name="sshd") + "\nexit 0",
        "hostname": LOG.format(name="hostname") + "\necho 192.168.1.50\nexit 0",
        "ufw": LOG.format(name="ufw") + '\n[ "$1" = status ] && echo "Status: active"\nexit 0',
    }
    env = {
        "INSTALL_YES": "1",
        "INSTALL_SSH": "1",
        "SSH_ADMIN_USER": "admin",
        "SSH_AUTHORIZED_KEY": KEY,
        "SSHD_DROPIN": str(tmp_path / "sshd_config.d" / "90-usb-sentinel.conf"),
    }
    run = run_install_all_to_end(tmp_path, "", env, extra_fakes=extra)
    assert run.code == 0, run.output
    assert "SSH as admin (enabled in step 7/10" in run.output
    assert KEY in (run.home / ".ssh" / "authorized_keys").read_text()
    assert "Kiosk: armed" in run.output


ARM_PRELUDE = (
    "set -euo pipefail; "
    'warn(){ echo "warn: $*" >&2; }; die(){ echo "error: $*" >&2; exit 1; }; '
    'source "$1"; shift; "$@"'
)


def test_kiosk_arm_function(tmp_path: Path) -> None:
    """kiosk_arm removes Hidden=true and applies the lockdown; a missing entry dies first."""
    run = prepare(tmp_path)
    run.desktop.parent.mkdir(parents=True)
    run.desktop.write_text("[Desktop Entry]\nExec=true\nHidden=true\n")
    argv = [bash_path(), "-c", ARM_PRELUDE, "_", str(SCRIPTS / "kiosk-lockdown.sh"), "kiosk_arm"]
    env = base_env(run, {})
    ok = subprocess.run(
        [*argv, "kiosk", str(run.desktop)], capture_output=True, text=True, env=env, check=False
    )
    assert ok.returncode == 0, ok.stderr
    assert "Hidden=" not in run.desktop.read_text()
    assert LOCKDOWN_SET in (tmp_path / "calls.log").read_text()
    (tmp_path / "calls.log").write_text("")
    missing = subprocess.run(
        [*argv, "kiosk", str(tmp_path / "absent.desktop")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert missing.returncode == 1
    assert "autostart entry missing" in missing.stderr
    assert "gsettings" not in (tmp_path / "calls.log").read_text()


@pytest.mark.parametrize("force", [None, "1"])
def test_kiosk_enable_no_longer_dies(tmp_path: Path, force: str | None) -> None:
    """Without monitors.xml kiosk-enable.sh warns and arms; KIOSK_FORCE only adds a warning."""
    run = prepare(tmp_path)
    run.desktop.parent.mkdir(parents=True)
    run.desktop.write_text("[Desktop Entry]\nExec=true\nHidden=true\n")
    env = base_env(run, {"INSTALL_YES": "1"})
    if force is not None:
        env["KIOSK_FORCE"] = force
    result = subprocess.run(
        [bash_path(), str(run.repo / "scripts" / "kiosk-enable.sh")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "could not confirm a 480x320" in result.stderr
    assert ("no longer needed" in result.stderr) is (force is not None)
    assert "Hidden=" not in run.desktop.read_text()
    assert LOCKDOWN_SET in (tmp_path / "calls.log").read_text()
