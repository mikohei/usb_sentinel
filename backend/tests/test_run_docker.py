"""Behaviour of the bash functions in scripts/run-docker.sh (device access and mode file).

The functions are sourced into a fresh bash and called through a fixed argv list, so the test
runs with the system bash (3.2 on macOS) and BSD find/head/tail/wc. The control directory and
the mode file live under tmp_path; the current user and the group of tmp_path stand in for
root and usb-sentinel.
"""

from __future__ import annotations

import getpass
import grp
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run-docker.sh"
NOW = 1760850000
TTL = 20

# Reason texts printed by inspection_mode on stderr (design 5.1).
DIR_BAD = "control directory missing or a symlink"
DIR_PERM = "control directory has wrong owner, group or permissions"
NO_FILE = "no mode file"
NOT_REGULAR = "mode file is not a regular file"
FILE_PERM = "mode file has wrong group, permissions or size"
INVALID = "mode file content invalid"


def bash_path() -> str:
    """Return the system bash or skip the test when none is installed."""
    path = shutil.which("bash")
    if path is None:
        pytest.skip("bash is not available")
    return path


def run_fn(fn: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Source the script and call one of its functions with the given arguments."""
    argv = [bash_path(), "-c", 'source "$1"; shift; "$@"', "_", str(SCRIPT), fn, *args]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def test_script_parses() -> None:
    """bash -n accepts the script."""
    result = subprocess.run([bash_path(), "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_sourcing_runs_nothing() -> None:
    """Sourcing the script defines functions only: no usage message, exit 0."""
    result = subprocess.run(
        [bash_path(), "-c", 'source "$1"', "_", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


# ------------------------------------------------------------------ device_access_mode


@pytest.mark.parametrize("requested", ["rw", "ro"])
@pytest.mark.parametrize("config_kind", ["active", "commented", "missing"])
def test_device_access_mode_matrix(tmp_path: Path, requested: str, config_kind: str) -> None:
    """Only docker.env rw plus an active allow_destructive = true line gives rw."""
    config = tmp_path / "config.toml"
    if config_kind == "active":
        config.write_text("allow_destructive = true\n")
    elif config_kind == "commented":
        config.write_text("# allow_destructive = true\n")
    result = run_fn("device_access_mode", requested, str(config))
    assert result.returncode == 0
    expected = "rw" if (requested, config_kind) == ("rw", "active") else "r"
    assert result.stdout == f"{expected}\n"
    if expected == "rw":
        assert "host allows read-write (docker.env rw + allow_destructive = true)" in result.stderr


# ------------------------------------------------------------------ inspection_mode


def owner_and_group(path: Path) -> tuple[str, str]:
    """Return the current user and the group name of the given path."""
    return getpass.getuser(), grp.getgrgid(path.stat().st_gid).gr_name


def control_dir(tmp_path: Path) -> Path:
    """Create a control directory that passes the owner, group and permission check."""
    directory = tmp_path / "usb-sentinel-control"
    directory.mkdir(mode=0o770)
    directory.chmod(0o770)
    return directory


def mode_result(mode_file: Path, group: str, now: int = NOW) -> subprocess.CompletedProcess[str]:
    """Call inspection_mode for the given mode file."""
    owner = getpass.getuser()
    return run_fn("inspection_mode", str(mode_file), str(now), owner, group, str(TTL))


def check(result: subprocess.CompletedProcess[str], expected: str, reason: str | None) -> None:
    """Assert stdout is exactly one word, the reason is on stderr and no NUL warning appears."""
    assert result.returncode == 0
    assert result.stdout == f"{expected}\n"
    assert "null byte" not in result.stderr
    if reason is None:
        assert "notice: inspection mode scan_only:" not in result.stderr
    else:
        notices = [line for line in result.stderr.splitlines() if line.startswith("notice:")]
        assert notices == [f"notice: inspection mode scan_only: {reason}"]


CONTENT_CASES: list[tuple[str, bytes, str, str | None]] = [
    ("fresh", f"full {NOW}\n".encode(), "full", None),
    ("ttl_edge", f"full {NOW - TTL}\n".encode(), "full", None),
    ("stale", f"full {NOW - TTL - 1}\n".encode(), "scan_only", "mode file stale (age 21s)"),
    ("future", f"full {NOW + 1}\n".encode(), "scan_only", "mode file stale (age -1s)"),
    ("scan_only_zero", b"scan_only 0\n", "scan_only", None),
    ("no_stamp", b"full\n", "scan_only", INVALID),
    ("upper_case", f"FULL {NOW}\n".encode(), "scan_only", INVALID),
    ("trailing_space", f"full {NOW} \n".encode(), "scan_only", INVALID),
    ("crlf", f"full {NOW}\r\n".encode(), "scan_only", INVALID),
    ("no_newline", f"full {NOW}".encode(), "scan_only", INVALID),
    ("two_newlines", f"full {NOW}\n\n".encode(), "scan_only", INVALID),
    ("three_newlines", f"full {NOW}\n\n\n".encode(), "scan_only", INVALID),
    ("nul_inside", f"fu\0ll {NOW}\n".encode(), "scan_only", INVALID),
    ("nul_before_newline", f"full {NOW}\0\n".encode(), "scan_only", INVALID),
    ("nul_instead_of_newline", f"full {NOW}\0".encode(), "scan_only", INVALID),
    ("nul_inside_no_newline", f"fu\0ll {NOW}".encode(), "scan_only", INVALID),
    ("two_lines", f"full {NOW}\nscan_only 0\n".encode(), "scan_only", INVALID),
    ("leading_zero_11_digits", b"full 01760850000\n", "full", None),
    ("13_digits", b"full 0001760850000\n", "scan_only", INVALID),
    ("empty", b"", "scan_only", INVALID),
]


@pytest.mark.parametrize(
    ("content", "expected", "reason"),
    [case[1:] for case in CONTENT_CASES],
    ids=[case[0] for case in CONTENT_CASES],
)
def test_inspection_mode_content(
    tmp_path: Path, content: bytes, expected: str, reason: str | None
) -> None:
    """Exactly `<mode> <epoch>\\n` with a fresh stamp gives full; everything else scan_only."""
    directory = control_dir(tmp_path)
    mode_file = directory / "mode"
    mode_file.write_bytes(content)
    _, group = owner_and_group(directory)
    result = mode_result(mode_file, group)
    check(result, expected, reason)
    if expected == "full":
        assert "notice: display requested full inspection (age " in result.stderr


def test_scan_only_content_notice(tmp_path: Path) -> None:
    """A valid scan_only line is reported as the display's choice."""
    directory = control_dir(tmp_path)
    mode_file = directory / "mode"
    mode_file.write_bytes(b"scan_only 5\n")
    result = mode_result(mode_file, owner_and_group(directory)[1])
    check(result, "scan_only", None)
    assert "notice: inspection mode scan_only (display)" in result.stderr


def test_missing_directory(tmp_path: Path) -> None:
    """No control directory means scan_only."""
    mode_file = tmp_path / "missing" / "mode"
    check(mode_result(mode_file, owner_and_group(tmp_path)[1]), "scan_only", DIR_BAD)


def test_symlinked_directory(tmp_path: Path) -> None:
    """A control directory reached through a symlink is refused even with a fresh file."""
    directory = control_dir(tmp_path)
    (directory / "mode").write_bytes(f"full {NOW}\n".encode())
    link = tmp_path / "link"
    link.symlink_to(directory, target_is_directory=True)
    check(mode_result(link / "mode", owner_and_group(directory)[1]), "scan_only", DIR_BAD)


def test_world_writable_directory(tmp_path: Path) -> None:
    """A world-writable control directory is refused."""
    directory = control_dir(tmp_path)
    directory.chmod(0o777)
    (directory / "mode").write_bytes(f"full {NOW}\n".encode())
    check(mode_result(directory / "mode", owner_and_group(directory)[1]), "scan_only", DIR_PERM)


def test_wrong_directory_group(tmp_path: Path) -> None:
    """A control directory owned by another group is refused."""
    directory = control_dir(tmp_path)
    (directory / "mode").write_bytes(f"full {NOW}\n".encode())
    own_gid = directory.stat().st_gid
    others = [gid for gid in os.getgroups() if gid != own_gid]
    if not others:
        pytest.skip("no second group available")
    other = grp.getgrgid(others[0]).gr_name
    check(mode_result(directory / "mode", other), "scan_only", DIR_PERM)


def test_missing_file(tmp_path: Path) -> None:
    """A control directory without a mode file means scan_only."""
    directory = control_dir(tmp_path)
    check(mode_result(directory / "mode", owner_and_group(directory)[1]), "scan_only", NO_FILE)


def test_symlink_file(tmp_path: Path) -> None:
    """A symlink in place of the mode file is refused."""
    directory = control_dir(tmp_path)
    target = tmp_path / "target"
    target.write_bytes(f"full {NOW}\n".encode())
    (directory / "mode").symlink_to(target)
    check(mode_result(directory / "mode", owner_and_group(directory)[1]), "scan_only", NOT_REGULAR)


def test_directory_in_place_of_file(tmp_path: Path) -> None:
    """A directory in place of the mode file is refused."""
    directory = control_dir(tmp_path)
    (directory / "mode").mkdir()
    check(mode_result(directory / "mode", owner_and_group(directory)[1]), "scan_only", NOT_REGULAR)


def test_world_writable_file(tmp_path: Path) -> None:
    """A world-writable mode file is refused."""
    directory = control_dir(tmp_path)
    mode_file = directory / "mode"
    mode_file.write_bytes(f"full {NOW}\n".encode())
    mode_file.chmod(0o666)
    check(mode_result(mode_file, owner_and_group(directory)[1]), "scan_only", FILE_PERM)


def test_file_too_large(tmp_path: Path) -> None:
    """A 65-byte mode file is refused before it is read."""
    directory = control_dir(tmp_path)
    mode_file = directory / "mode"
    mode_file.write_bytes(f"full {NOW}".encode().ljust(64, b"0") + b"\n")
    assert mode_file.stat().st_size == 65
    check(mode_result(mode_file, owner_and_group(directory)[1]), "scan_only", FILE_PERM)
