"""Behaviour of scripts/kiosk-lockdown.sh against a fake gsettings.

The fake answers `list-recursively <schema>` from fixture files in tmp_path (exit 1 when the
fixture is absent), records every `set` call in a file, fails `set` for keys listed in a `fail`
fixture, and in one variant drains its stdin first. The shell runs with `set -euo pipefail`
like install-all.sh, using the system bash (3.2 on macOS).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.test_run_docker import bash_path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "kiosk-lockdown.sh"
WM = "org.gnome.desktop.wm.keybindings"
SHELL = "org.gnome.shell.keybindings"
MUTTER = "org.gnome.mutter.keybindings"
WAYLAND = "org.gnome.mutter.wayland.keybindings"
MEDIA = "org.gnome.settings-daemon.plugins.media-keys"
DOCK = "org.gnome.shell.extensions.dash-to-dock"
FIVE_SCHEMAS = {WM, SHELL, MUTTER, WAYLAND, MEDIA}

PREAMBLE = """
set -euo pipefail
warn() { printf 'warning: %s\\n' "$*" >&2; }
fake_gs() {
    case "$1" in
        list-recursively)
            [[ -f "${FIXTURES}/$2" ]] || return 1
            cat "${FIXTURES}/$2"
            ;;
        set)
            printf 'set %s %s %s\\n' "$2" "$3" "$4" >> "${RECORD}"
            if [[ -f "${FIXTURES}/fail" ]] && grep -qx -- "$3" "${FIXTURES}/fail"; then
                return 1
            fi
            ;;
    esac
    return 0
}
fake_gs_drain() { cat >/dev/null; fake_gs "$@"; }
"""

# Fixed settings of design 5.2, as `set <schema> <key> <value>` record lines.
FIXED_SETTINGS = [
    "set org.gnome.desktop.lockdown disable-command-line true",
    "set org.gnome.desktop.lockdown disable-log-out true",
    "set org.gnome.desktop.lockdown disable-user-switching true",
    "set org.gnome.mutter overlay-key ''",
    "set org.gnome.mutter dynamic-workspaces false",
    "set org.gnome.desktop.wm.preferences num-workspaces 1",
    "set org.gnome.desktop.interface enable-hot-corners false",
    "set org.gnome.shell favorite-apps []",
    f"set {DOCK} hot-keys false",
    "set org.gnome.desktop.a11y.keyboard enable false",
    "set org.gnome.settings-daemon.plugins.power power-button-action 'nothing'",
]


class Fake:
    """Fixture directory plus record file for one test."""

    def __init__(self, tmp_path: Path) -> None:
        self.fixtures = tmp_path / "fixtures"
        self.fixtures.mkdir()
        self.record = tmp_path / "record"
        self.record.touch()

    def schema(self, name: str, lines: list[tuple[str, str]], prefix: str | None = None) -> None:
        """Write a list-recursively fixture; prefix overrides the schema in the lines."""
        schema = name if prefix is None else prefix
        text = "".join(f"{schema} {key} {value}\n" for key, value in lines)
        with (self.fixtures / name).open("a") as handle:
            handle.write(text)

    def fail(self, *keys: str) -> None:
        """Make `set` fail for these keys."""
        (self.fixtures / "fail").write_text("".join(f"{key}\n" for key in keys))

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Source the script and run a function with the fake available."""
        return self._bash('source "$1"; shift; "$@"', *args)

    def run_code(self, code: str) -> subprocess.CompletedProcess[str]:
        """Source the script and evaluate a short snippet (several function calls)."""
        return self._bash('source "$1"; shift; eval "$1"', code)

    def _bash(self, body: str, *args: str) -> subprocess.CompletedProcess[str]:
        argv = [bash_path(), "-c", PREAMBLE + body, "_", str(SCRIPT), *args]
        env = {**os.environ, "FIXTURES": str(self.fixtures), "RECORD": str(self.record)}
        return subprocess.run(argv, capture_output=True, text=True, check=False, env=env)

    def sets(self) -> list[str]:
        """Recorded set calls."""
        return self.record.read_text().splitlines()


@pytest.fixture
def fake(tmp_path: Path) -> Fake:
    """A fresh fake gsettings."""
    return Fake(tmp_path)


def test_script_parses() -> None:
    """bash -n accepts the script."""
    result = subprocess.run([bash_path(), "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_clears_every_string_list_whatever_the_key_name(fake: Fake) -> None:
    """Non-empty string lists are set to [] by value type, not by a copied key list."""
    fake.schema(
        WM,
        [
            ("switch-to-application-7", "['<Super>7']"),
            ("switch-to-workspace-down", "['<Super>Page_Down', '<Control><Alt>Down']"),
            ("future-shortcut-xyz", "['<Alt>F13']"),
        ],
    )
    result = fake.run("kiosk_clear_schema", "fake_gs", WM)
    assert result.returncode == 0, result.stderr
    assert fake.sets() == [
        f"set {WM} switch-to-application-7 []",
        f"set {WM} switch-to-workspace-down []",
        f"set {WM} future-shortcut-xyz []",
    ]
    assert f"GNOME: {WM}: 3 shortcuts cleared" in result.stdout
    assert "has no shortcut lists" not in result.stderr


def test_non_shortcut_values_are_never_set(fake: Fake) -> None:
    """Empty lists, strings, booleans, numbers and tuple lists are left alone."""
    fake.schema(
        WM,
        [
            ("empty-a", "@as []"),
            ("empty-b", "[]"),
            ("text", "'text'"),
            ("flag", "true"),
            ("number", "5"),
            ("sources", "[('xkb', 'us')]"),
        ],
    )
    result = fake.run("kiosk_clear_schema", "fake_gs", WM)
    assert result.returncode == 0, result.stderr
    assert fake.sets() == []
    assert f"GNOME: {WM}: 0 shortcuts cleared" in result.stdout
    # Empty lists count as shortcut lists, so no GNOME-version warning.
    assert "has no shortcut lists" not in result.stderr


def test_switch_to_session_kept_only_in_wayland_schema(fake: Fake) -> None:
    """The console switch exception applies to the wayland schema only."""
    fake.schema(
        WAYLAND,
        [
            ("switch-to-session-1", "['<Primary><Alt>F1']"),
            ("switch-to-session-12", "['<Primary><Alt>F12']"),
            ("restore-shortcuts", "['<Super>Escape']"),
        ],
    )
    fake.schema(MUTTER, [("switch-to-session-1", "['<Primary><Alt>F1']")])
    assert fake.run("kiosk_clear_schema", "fake_gs", WAYLAND).returncode == 0
    assert fake.run("kiosk_clear_schema", "fake_gs", MUTTER).returncode == 0
    assert fake.sets() == [
        f"set {WAYLAND} restore-shortcuts []",
        f"set {MUTTER} switch-to-session-1 []",
    ]


def test_child_schema_lines_are_ignored(fake: Fake) -> None:
    """Lines whose first field is another schema are skipped."""
    fake.schema(WM, [("close", "['<Alt>F4']")])
    fake.schema(WM, [("child-key", "['<Super>x']")], prefix=f"{WM}.child")
    result = fake.run("kiosk_clear_schema", "fake_gs", WM)
    assert result.returncode == 0
    assert fake.sets() == [f"set {WM} close []"]


def test_dock_regex_limits_the_keys(fake: Fake) -> None:
    """With KIOSK_DOCK_KEYS only the hotkey lists and shortcut are cleared."""
    fake.schema(
        DOCK,
        [
            ("app-hotkey-1", "['<Super>1']"),
            ("app-hotkey-10", "['<Super>0']"),
            ("app-shift-hotkey-3", "['<Shift><Super>3']"),
            ("app-ctrl-hotkey-2", "['<Control><Super>2']"),
            ("shortcut", "['<Super>q']"),
            ("scroll-switch-workspace-whole", "['one', 'two']"),
            ("hot-keys", "true"),
        ],
    )
    result = fake.run(
        "kiosk_clear_schema", "fake_gs", DOCK, "^(app-(shift-|ctrl-)?hotkey-[0-9]+|shortcut)$"
    )
    assert result.returncode == 0, result.stderr
    assert fake.sets() == [
        f"set {DOCK} app-hotkey-1 []",
        f"set {DOCK} app-hotkey-10 []",
        f"set {DOCK} app-shift-hotkey-3 []",
        f"set {DOCK} app-ctrl-hotkey-2 []",
        f"set {DOCK} shortcut []",
    ]
    assert f"GNOME: {DOCK}: 5 shortcuts cleared" in result.stdout


def test_missing_schema_warns_and_continues(fake: Fake) -> None:
    """A schema that gsettings does not know gives one warning, exit 0, next schema still runs."""
    fake.schema(SHELL, [("toggle-overview", "['<Super>s']")])
    result = fake.run_code(f"kiosk_clear_schema fake_gs {WM}; kiosk_clear_schema fake_gs {SHELL}")
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("warning:") == 1
    assert f"schema {WM} not found (skipped)" in result.stderr
    assert fake.sets() == [f"set {SHELL} toggle-overview []"]


def test_failing_set_warns_and_continues(fake: Fake) -> None:
    """A failing gsettings set is reported and the remaining keys are still cleared."""
    fake.schema(WM, [("close", "['<Alt>F4']"), ("minimize", "['<Super>h']")])
    fake.fail("close")
    result = fake.run("kiosk_clear_schema", "fake_gs", WM)
    assert result.returncode == 0, result.stderr
    assert result.stderr.splitlines() == [
        f"warning: gsettings: could not set {WM} close (kept default)"
    ]
    assert fake.sets() == [f"set {WM} close []", f"set {WM} minimize []"]


def test_stdin_draining_gsettings_still_processes_every_key(fake: Fake) -> None:
    """Each gsettings call reads stdin from /dev/null, so the here-string loop is not consumed."""
    fake.schema(
        WM, [("close", "['<Alt>F4']"), ("minimize", "['<Super>h']"), ("maximize", "['<Super>Up']")]
    )
    result = fake.run("kiosk_clear_schema", "fake_gs_drain", WM)
    assert result.returncode == 0, result.stderr
    assert len(fake.sets()) == 3


def test_plain_string_schema_warns_about_gnome_version(fake: Fake) -> None:
    """A schema without any list value triggers the GNOME-version warning."""
    fake.schema(WM, [("close", "'<Alt>F4'"), ("minimize", "'<Super>h'")])
    result = fake.run("kiosk_clear_schema", "fake_gs", WM)
    assert result.returncode == 0
    assert fake.sets() == []
    assert (
        f"warning: gsettings: {WM} has no shortcut lists; check the GNOME version" in result.stderr
    )


def test_kiosk_lockdown_applies_fixed_settings_and_clears_all_schemas(fake: Fake) -> None:
    """kiosk_lockdown sets the fixed table, then clears the five schemas and the dock."""
    for schema in FIVE_SCHEMAS:
        fake.schema(schema, [("some-shortcut", "['<Super>z']")])
    fake.schema(DOCK, [("app-hotkey-1", "['<Super>1']"), ("other-list", "['a']")])
    result = fake.run("kiosk_lockdown", "fake_gs")
    assert result.returncode == 0, result.stderr
    sets = fake.sets()
    assert sets[: len(FIXED_SETTINGS)] == FIXED_SETTINGS
    cleared = set(sets[len(FIXED_SETTINGS) :])
    assert cleared == {f"set {schema} some-shortcut []" for schema in FIVE_SCHEMAS} | {
        f"set {DOCK} app-hotkey-1 []"
    }
    assert result.stdout.splitlines()[-1] == (
        'GNOME: kiosk lockdown done (see README "Hardening against BadUSB")'
    )


def test_clear_schemas_variable_is_exactly_the_five(fake: Fake) -> None:
    """A removed or renamed schema in KIOSK_CLEAR_SCHEMAS fails the test."""
    result = fake.run_code('printf "%s\\n" $KIOSK_CLEAR_SCHEMAS')
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.split()) == FIVE_SCHEMAS
    assert len(result.stdout.split()) == 5
