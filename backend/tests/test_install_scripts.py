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
        "boot-video-hint.sh",
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


def test_install_all_prompts_only_in_preflight() -> None:
    """Every confirm/read call sits before step 2; steps 3 and 8 only read the stored answers."""
    lines = _script("install-all.sh").splitlines()
    step2 = lines.index('log "2/10 Installing apt packages"')
    prompt_re = r"\bconfirm(_default_yes)? \"|\bread -r\b"
    prompts = [i for i, line in enumerate(lines) if re.search(prompt_re, line)]
    assert prompts, "the preflight must still ask the questions"
    assert all(i < step2 for i in prompts), [lines[i] for i in prompts if i >= step2]
    text = "\n".join(lines)
    assert "No more questions; the rest runs unattended" in text
    assert "INSTALL_YES=1 skips the questions" in text
    assert 'if [[ "${RUN_UV_INSTALLER}" == 0 ]]; then' in text
    assert 'if [[ "${RUN_FRESHCLAM}" == 1 ]]; then' in text
    # The SHA-256 is shown before the uv question, and both questions precede apt-get.
    _ordered(
        text,
        'curl -LsSf "${UV_INSTALLER_URL}"',
        'echo "SHA-256:',
        'confirm "Run this installer as',
        'confirm "Run freshclam',
        'confirm_default_yes "Arm the kiosk',
        'log "2/10',
        "apt-get update",
    )


ROOT_CHECK = '[[ ${EUID} -eq 0 ]] || die "run with sudo"'

FAKE_BINARIES = {
    # uname -s -> Linux, uname -m -> aarch64
    "uname": 'echo "uname $*" >> "$FAKE_LOG"\ncase "$1" in -s) echo Linux;; *) echo aarch64;; esac',
    "id": 'echo "id $*" >> "$FAKE_LOG"\n[ "$1" = -u ] && echo 1000\nexit 0',
    "getent": (
        'echo "getent $*" >> "$FAKE_LOG"\n'
        'case "$1" in passwd) echo "$2:x:1000:1000::$FAKE_HOME:/bin/bash";;'
        ' *) echo "1.2.3.4 $2";; esac'
    ),
    "curl": (
        'echo "curl $*" >> "$FAKE_LOG"\n'
        'while [ $# -gt 1 ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
        'printf "#!/bin/sh\\necho fake uv installer\\n" > "$out"'
    ),
    "sha256sum": 'echo "sha256sum $*" >> "$FAKE_LOG"\necho "deadbeef  $1"',
    # The run stops at the first apt-get so nothing after step 2 is exercised. Whatever is
    # still unread on stdin is logged: prompts consumed before this point leave it empty.
    "apt-get": 'echo "apt-get $* stdin=[$(cat)]" >> "$FAKE_LOG"\nexit 99',
}


def _run_install_all(tmp_path: Path, stdin: str, env_extra: dict[str, str]) -> tuple[int, str]:
    """Run install-all.sh with fake binaries; return the exit code and the fake call log."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in FAKE_BINARIES.items():
        path = bindir / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "calls.log"
    log.touch()
    # EUID is a readonly bash variable, so the sudo check is disabled in a scratch copy. The
    # copy lives under <tmp>/scripts/ next to the helper files it sources before step 1.
    original = _script("install-all.sh")
    assert ROOT_CHECK in original
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for helper in ("kiosk-lockdown.sh", "boot-video-hint.sh"):
        (scripts / helper).write_text(_script(helper))
    script = scripts / "install-all.sh"
    script.write_text(original.replace(ROOT_CHECK, "true"))
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "HOME": str(home),
        "FAKE_LOG": str(log),
        "FAKE_HOME": str(home),
        "DISPLAY_USER": "kiosk",
        **env_extra,
    }
    result = subprocess.run(
        [bash_path(), str(script)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result.returncode, log.read_text() + result.stdout + result.stderr


def test_install_all_consumes_all_answers_before_apt_get(tmp_path: Path) -> None:
    """With INSTALL_YES unset, all three y answers on stdin are read before the first apt-get."""
    code, log = _run_install_all(tmp_path, "y\ny\ny\n", {})
    assert code == 99, log
    _ordered(
        log,
        "curl -LsSf https://astral.sh/uv/install.sh",
        "sha256sum ",
        "apt-get update",
    )
    # read -p prints nothing when stdin is a pipe, so the proof is the drained stdin.
    assert "apt-get update stdin=[]" in log
    assert "No more questions" in log


def test_install_all_rejecting_uv_dies_before_apt_get(tmp_path: Path) -> None:
    """Answering N to the uv question exits in the preflight; apt-get never runs."""
    code, log = _run_install_all(tmp_path, "n\ny\ny\n", {})
    assert code == 1
    assert "uv installation cancelled" in log
    assert "apt-get" not in log


def test_install_all_yes_env_asks_nothing(tmp_path: Path) -> None:
    """INSTALL_YES=1 reaches apt-get without reading stdin (the n answers stay unread)."""
    code, log = _run_install_all(tmp_path, "n\nn\nn\n", {"INSTALL_YES": "1"})
    assert code == 99, log
    assert "apt-get update stdin=[n\nn\nn]" in log


def test_install_all_sources_lockdown_before_preflight() -> None:
    """Both helpers are sourced before step 1; gsettings_user stays only in kiosk-disable.sh."""
    lines = _script("install-all.sh").splitlines()
    preflight = lines.index('log "1/10 Preflight checks"')
    assert lines.index('source "${REPO_DIR}/scripts/kiosk-lockdown.sh"') < preflight
    assert lines.index('source "${REPO_DIR}/scripts/boot-video-hint.sh"') < preflight
    assert "gsettings_user() {" not in _script("install-all.sh")
    assert "gsettings_user() {" not in _script("kiosk-enable.sh")
    assert "gsettings_user() {" in _script("kiosk-disable.sh")


def test_install_all_arms_only_in_step_10() -> None:
    """kiosk_arm runs once, in step 10, inside the ARM_KIOSK and gsettings guards."""
    lines = _script("install-all.sh").splitlines()
    assert not any(re.match(r"^\s*kiosk_lockdown\b", line) for line in lines)
    arms = [i for i, line in enumerate(lines) if re.match(r"^\s*kiosk_arm\b", line)]
    assert len(arms) == 1
    arm = arms[0]
    step10 = next(
        i for i, line in enumerate(lines) if line.strip().startswith('log "10/10 Arming the kiosk')
    )
    assert step10 < arm
    guard = max(
        i for i, line in enumerate(lines[:arm]) if line == 'if [[ "${ARM_KIOSK}" == 1 ]]; then'
    )
    gsettings = max(
        i
        for i, line in enumerate(lines[:arm])
        if line.strip() == "if command -v gsettings >/dev/null; then"
    )
    assert guard < gsettings < arm
    start = lines.index("cat <<EOF")
    end = lines.index("EOF", start)
    message = "\n".join(lines[start:end])
    for needle in (
        "KIOSK_STATE",
        "Ctrl+Alt+F3",
        "kiosk-disable.sh",
        "pkill -f usb-sentinel-display",
        "calibrate",
        "P?",
    ):
        assert needle in message


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
    """kiosk-enable.sh hints at the resolution, confirms, then arms through kiosk_arm."""
    text = _script("kiosk-enable.sh")
    _ordered(
        text,
        'source "${REPO_DIR}/scripts/kiosk-lockdown.sh"',
        'kiosk_resolution_hint "${USER_HOME}"',
        "KIOSK_FORCE",
        'confirm "',
        'kiosk_arm "${DISPLAY_USER}" "${DESKTOP_FILE}"',
    )
    assert 'die "fix the resolution' not in text
    force_lines = [
        line for line in text.splitlines() if "KIOSK_FORCE" in line and not line.startswith("#")
    ]
    assert force_lines
    for line in force_lines:
        assert "warn" in line or line.strip().startswith('if [[ "${KIOSK_FORCE:-0}"'), line
    # The shared gsettings wrapper moved to kiosk-lockdown.sh.
    lockdown = _script("kiosk-lockdown.sh")
    assert "kiosk_gsettings_user() {" in lockdown
    assert "/run/user/${uid}/bus" in lockdown
    assert "dbus-run-session" in lockdown
    assert "gsettings_user() {" not in text


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
