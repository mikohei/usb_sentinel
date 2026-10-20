"""kiosk_unlock and the autostart toggle of scripts/kiosk-lockdown.sh.

Uses the fake gsettings of test_kiosk_lockdown.py; it records `reset` and `reset-recursively`
calls in the same file as `set` calls, so one run of lockdown followed by unlock shows exactly
which keys each side touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_kiosk_lockdown import (
    DOCK,
    FIVE_SCHEMAS,
    FIXED_RESETS,
    WAYLAND,
    WM,
    Fake,
)

CLEAR_ORDER = [
    "org.gnome.desktop.wm.keybindings",
    "org.gnome.shell.keybindings",
    "org.gnome.mutter.keybindings",
    "org.gnome.mutter.wayland.keybindings",
    "org.gnome.settings-daemon.plugins.media-keys",
]


@pytest.fixture
def fake(tmp_path: Path) -> Fake:
    """A fresh fake gsettings."""
    return Fake(tmp_path)


def _split_record(lines: list[str]) -> tuple[list[str], list[str]]:
    """Return (set lines, reset lines): everything before the first reset, and from it on."""
    first = next(i for i, line in enumerate(lines) if line.startswith("reset"))
    return lines[:first], lines[first:]


def _touched_by_sets(sets: list[str]) -> set[tuple[str, str]]:
    return {(parts[1], parts[2]) for parts in (line.split(" ") for line in sets)}


def _covered_by_resets(resets: list[str], sets: list[str]) -> set[tuple[str, str]]:
    """Expand `reset-recursively S` to every key of S that a set line touched."""
    covered: set[tuple[str, str]] = set()
    for line in resets:
        parts = line.split(" ")
        if parts[0] == "reset":
            covered.add((parts[1], parts[2]))
        else:
            covered |= {pair for pair in _touched_by_sets(sets) if pair[0] == parts[1]}
    return covered


def test_kiosk_unlock_resets_exactly_what_lockdown_set(fake: Fake) -> None:
    """Every (schema, key) that lockdown set is reset, and nothing else is."""
    for schema in FIVE_SCHEMAS:
        fake.schema(schema, [("some-shortcut", "['<Super>z']")])
    fake.schema(
        DOCK,
        [
            ("app-hotkey-1", "['<Super>1']"),
            ("shortcut", "['<Super>q']"),
            ("other-list", "['a']"),
            ("hot-keys", "true"),
        ],
    )
    result = fake.run_code("kiosk_lockdown fake_gs; kiosk_unlock fake_gs")
    assert result.returncode == 0, result.stderr
    sets, resets = _split_record(fake.sets())
    assert all(line.startswith("set ") for line in sets)
    assert _touched_by_sets(sets) == _covered_by_resets(resets, sets)
    assert f"reset {DOCK} other-list" not in resets
    assert f"reset-recursively {DOCK}" not in resets


def test_kiosk_unlock_order_matches_lockdown(fake: Fake) -> None:
    """Fixed resets first (lockdown order), then the five schemas in order, then the dock."""
    for schema in FIVE_SCHEMAS:
        fake.schema(schema, [("some-shortcut", "['<Super>z']")])
    fake.schema(DOCK, [("app-hotkey-1", "['<Super>1']"), ("shortcut", "['<Super>q']")])
    result = fake.run("kiosk_unlock", "fake_gs")
    assert result.returncode == 0, result.stderr
    resets = fake.sets()
    n = len(FIXED_RESETS)
    assert resets[:n] == FIXED_RESETS
    assert resets[n : n + 5] == [f"reset-recursively {schema}" for schema in CLEAR_ORDER]
    assert resets[n + 5 :] == [f"reset {DOCK} app-hotkey-1", f"reset {DOCK} shortcut"]
    assert result.stdout.splitlines()[-1] == "GNOME: kiosk lockdown undone"


def test_kiosk_unlock_missing_schema_warns_and_continues(fake: Fake) -> None:
    """An unknown schema gives one warning and exit 0; the other schemas are still reset."""
    for schema in FIVE_SCHEMAS - {WM}:
        fake.schema(schema, [("some-shortcut", "['<Super>z']")])
    fake.schema(DOCK, [("hot-keys", "true")])
    result = fake.run("kiosk_unlock", "fake_gs")
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("warning:") == 1
    assert f"schema {WM} not found (skipped)" in result.stderr
    recursive = [line for line in fake.sets() if line.startswith("reset-recursively")]
    assert recursive == [f"reset-recursively {schema}" for schema in CLEAR_ORDER]


def test_kiosk_reset_dock_keys_uses_regex(fake: Fake) -> None:
    """Only the hotkey lists and shortcut are reset; appearance keys and hot-keys stay."""
    fake.schema(
        DOCK,
        [
            ("app-hotkey-1", "['<Super>1']"),
            ("app-shift-hotkey-3", "['<Shift><Super>3']"),
            ("shortcut", "['<Super>q']"),
            ("scroll-switch-workspace-whole", "['one', 'two']"),
            ("hot-keys", "true"),
        ],
    )
    result = fake.run("kiosk_reset_dock_keys", "fake_gs")
    assert result.returncode == 0, result.stderr
    assert fake.sets() == [
        f"reset {DOCK} app-hotkey-1",
        f"reset {DOCK} app-shift-hotkey-3",
        f"reset {DOCK} shortcut",
    ]


def test_wayland_reset_is_recursive(fake: Fake) -> None:
    """The wayland schema is reset wholesale; switch-to-session-N were never changed."""
    fake.schema(WAYLAND, [("switch-to-session-1", "['<Primary><Alt>F1']")])
    result = fake.run("kiosk_reset_schema", "fake_gs", WAYLAND)
    assert result.returncode == 0, result.stderr
    assert fake.sets() == [f"reset-recursively {WAYLAND}"]


DESKTOP_LINES = ["[Desktop Entry]", "Type=Application", "Exec=true"]


def test_autostart_hide_is_idempotent(fake: Fake, tmp_path: Path) -> None:
    """Two hide calls leave exactly one Hidden=true line after the original lines."""
    entry = tmp_path / "entry.desktop"
    entry.write_text("".join(f"{line}\n" for line in DESKTOP_LINES))
    for _ in range(2):
        result = fake.run("kiosk_autostart_hide", str(entry))
        assert result.returncode == 0, result.stderr
    assert entry.read_text().splitlines() == [*DESKTOP_LINES, "Hidden=true"]


def test_autostart_show_removes_hidden(fake: Fake, tmp_path: Path) -> None:
    """Every Hidden= line goes, no .bak is left, and a second call is a no-op."""
    entry = tmp_path / "entry.desktop"
    entry.write_text(
        "".join(f"{line}\n" for line in [*DESKTOP_LINES, "Hidden=true", "Hidden=false"])
    )
    result = fake.run("kiosk_autostart_show", str(entry))
    assert result.returncode == 0, result.stderr
    assert entry.read_text().splitlines() == DESKTOP_LINES
    assert not list(tmp_path.glob("*.bak"))
    again = fake.run("kiosk_autostart_show", str(entry))
    assert again.returncode == 0, again.stderr
    assert entry.read_text().splitlines() == DESKTOP_LINES


@pytest.mark.parametrize("function", ["kiosk_autostart_hide", "kiosk_autostart_show"])
def test_autostart_helpers_warn_on_missing_file(fake: Fake, tmp_path: Path, function: str) -> None:
    """A missing entry is reported once and does not fail the caller."""
    result = fake.run(function, str(tmp_path / "absent.desktop"))
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("warning:") == 1
    assert "autostart entry" in result.stderr and "missing" in result.stderr
