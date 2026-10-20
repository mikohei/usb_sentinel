"""Composition of the docker argv in scripts/run-docker.sh.

Covers inspection_access, the pure docker_argv, the plan_docker_argv matrix (docker.env x
config.toml x requested mode) and the static wiring of main. Functions are called through the
same fixed bash argv as test_run_docker.py.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.test_run_docker import SCRIPT, run_fn

IMAGE = "usb-sentinel:local"
CONFIG = "/etc/usb-sentinel/config.toml"
DATABASE = "/var/lib/clamav"
RESULTS = "/var/lib/usb-sentinel/results"
DB_DIR = "/var/lib/usb-sentinel/db"
PATHS = ["/dev/sdb", "/dev/sdb1"]

HEAD = [
    "run", "--rm", "--pull=never", "--init",
    "--name", "usb-sentinel-sdb",
    "--network", "none", "--read-only",
    "--cap-drop", "ALL", "--cap-add", "SYS_ADMIN", "--cap-add", "DAC_OVERRIDE",
    "--security-opt", "no-new-privileges", "--security-opt", "apparmor=unconfined",
    "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",  # noqa: S108 (container tmpfs)
    "--tmpfs", "/run:rw,nosuid,nodev,noexec,size=16m",
]  # fmt: skip


def mounts(config: str) -> list[str]:
    """The six fixed --mount values, unchanged from the pre-feature script."""
    return [
        "type=bind,src=/sys,dst=/sys,readonly",
        "type=bind,src=/run/udev,dst=/run/udev,readonly",
        f"type=bind,src={config},dst=/etc/usb-sentinel/config.toml,readonly",
        f"type=bind,src={DATABASE},dst=/var/lib/clamav,readonly",
        f"type=bind,src={RESULTS},dst=/var/lib/usb-sentinel/results",
        f"type=bind,src={DB_DIR},dst=/var/lib/usb-sentinel/db",
    ]


def expected_argv(access: str, mode: str, config: str = CONFIG) -> list[str]:
    """Full expected argv for /dev/sdb with partitions PATHS."""
    argv = list(HEAD)
    for path in PATHS:
        argv += ["--device", f"{path}:{path}:{access}"]
    for mount in mounts(config):
        argv += ["--mount", mount]
    return [*argv, IMAGE, "inspect", "--mode", mode, "/dev/sdb"]


def docker_argv(
    access: str,
    mode: str,
    *,
    device: str = "/dev/sdb",
    image: str = IMAGE,
    config: str = CONFIG,
    paths: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Call docker_argv with the standard fixed arguments."""
    paths = PATHS if paths is None else paths
    fixed = [device, image, config, DATABASE, RESULTS, DB_DIR]
    return run_fn("docker_argv", access, mode, *fixed, *paths)


# ------------------------------------------------------------------ inspection_access


@pytest.mark.parametrize(
    ("ceiling", "requested", "expected"),
    [
        ("rw", "full", "rw full"),
        ("rw", "scan_only", "r scan_only"),
        ("rw", "garbage", "r scan_only"),
        ("r", "full", "r scan_only"),
        ("r", "scan_only", "r scan_only"),
    ],
)
def test_inspection_access(ceiling: str, requested: str, expected: str) -> None:
    """Only exactly rw + full gives a writable device."""
    result = run_fn("inspection_access", ceiling, requested)
    assert result.returncode == 0
    assert result.stdout == f"{expected}\n"
    if ceiling == "rw" and requested != "full":
        assert "device stays read-only: inspection mode is scan_only" in result.stderr


# ------------------------------------------------------------------ docker_argv


@pytest.mark.parametrize(("access", "mode"), [("r", "scan_only"), ("rw", "full")])
def test_docker_argv_success(access: str, mode: str) -> None:
    """The two consistent pairs print exactly the expected argv, one word per line."""
    result = docker_argv(access, mode)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == expected_argv(access, mode)


@pytest.mark.parametrize(
    ("access", "mode"), [("r", "full"), ("rw", "scan_only"), ("RW", "full"), ("rw", "full ")]
)
def test_docker_argv_inconsistent_pair(access: str, mode: str) -> None:
    """Any other (access, mode) pair is refused with an error and empty stdout."""
    result = docker_argv(access, mode)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error:")


def test_docker_argv_rejects_path_outside_dev() -> None:
    """A path outside /dev/ is refused."""
    result = docker_argv("r", "scan_only", paths=["/dev/sdb", "/etc/passwd"])
    assert (result.returncode, result.stdout) == (1, "")


def test_docker_argv_rejects_device_with_space() -> None:
    """A device path containing a space is refused."""
    result = docker_argv("r", "scan_only", device="/dev/sd b")
    assert (result.returncode, result.stdout) == (1, "")


def test_docker_argv_rejects_no_paths() -> None:
    """At least one --device path is required."""
    result = docker_argv("r", "scan_only", paths=[])
    assert (result.returncode, result.stdout) == (1, "")


def test_docker_argv_rejects_empty_image() -> None:
    """An empty image name is refused."""
    result = docker_argv("r", "scan_only", image="")
    assert (result.returncode, result.stdout) == (1, "")


def test_docker_argv_rejects_newline_in_config() -> None:
    """A config path with a newline would split into two argv words; refused."""
    result = docker_argv("r", "scan_only", config="/etc/a\n--privileged")
    assert (result.returncode, result.stdout) == (1, "")


# ------------------------------------------------------------------ plan_docker_argv


@pytest.mark.parametrize("requested", ["full", "scan_only", "garbage", ""])
@pytest.mark.parametrize("config_kind", ["active", "commented", "missing"])
@pytest.mark.parametrize("device_access", ["rw", "ro"])
def test_plan_docker_argv_matrix(
    tmp_path: Path, device_access: str, config_kind: str, requested: str
) -> None:
    """Only rw + active allow_destructive + requested full gives :rw and --mode full."""
    config = tmp_path / "config.toml"
    if config_kind == "active":
        config.write_text("allow_destructive = true\n")
    elif config_kind == "commented":
        config.write_text("# allow_destructive = true\n")
    fixed = [requested, "/dev/sdb", IMAGE, DATABASE, RESULTS, DB_DIR]
    result = run_fn("plan_docker_argv", device_access, str(config), *fixed, *PATHS)
    assert result.returncode == 0, result.stderr
    argv = result.stdout.splitlines()
    writable = (device_access, config_kind, requested) == ("rw", "active", "full")
    access, mode = ("rw", "full") if writable else ("r", "scan_only")
    assert argv == expected_argv(access, mode, str(config))
    assert all("usb-sentinel-control" not in word for word in argv)
    mount_values = [argv[i + 1] for i, word in enumerate(argv) if word == "--mount"]
    assert mount_values == mounts(str(config))


# ------------------------------------------------------------------ main wiring (static)


def main_text() -> str:
    """Return the body of main() from the script text."""
    text = SCRIPT.read_text()
    match = re.search(r"^main\(\) \{\n(.*?)^\}", text, re.MULTILINE | re.DOTALL)
    assert match is not None, "main() not found"
    return str(match.group(1))


def test_main_reads_mode_before_udevadm_settle() -> None:
    """The mode snapshot is taken at insertion, before the up-to-30 s settle wait."""
    body = main_text()
    assert 0 < body.index("inspection_mode") < body.index("udevadm settle")


def test_main_passes_requested_mode_to_plan() -> None:
    """main composes the argv through plan_docker_argv with the snapshot only."""
    body = main_text()
    assert re.search(r'plan_docker_argv .*"\$\{requested_mode\}"', body)
    assert "ceiling=" not in body
    assert "inspection_access" not in body


def test_main_calls_docker_once_via_argv() -> None:
    """The only docker invocation is exec docker with the composed argv."""
    code_lines = [
        line.strip() for line in main_text().splitlines() if not line.strip().startswith("#")
    ]
    docker_lines = [line for line in code_lines if re.search(r"(?:^|\s)docker(?:\s|$)", line)]
    assert docker_lines == ['exec docker "${argv[@]}"']


def test_mode_ttl_constant_on_its_own_line() -> None:
    """The frontend test parses this exact line to keep HOST_MODE_TTL_S in sync."""
    assert "\nreadonly MODE_TTL_S=20\n" in SCRIPT.read_text()
    assert "\nreadonly CONTROL_FILE=/run/usb-sentinel-control/mode\n" in SCRIPT.read_text()
