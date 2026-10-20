"""Mode-toggle part of make-fixtures.py (plan.md Step 10).

Creates the directories the inspection-mode button needs on a development machine:
control/ (the mode file goes here), host/ (config.toml and docker.env that set the ceiling),
input/input0/ (a fake /sys/class/input) and state/ (XDG_STATE_HOME for the language and the
strike file). Kept out of make-fixtures.py so that script stays under 300 lines.
"""

from dataclasses import dataclass
from pathlib import Path

CONFIG_TOML = "# Fixture for the display's ceiling check.\nallow_destructive = {allow}\n"
DOCKER_ENV = "USB_SENTINEL_DEVICE_ACCESS={access}\n"
STRIKE_DIR_NAME = "usb-sentinel-display"
STRIKE_FILE_NAME = "erase-strikes"

# Lines appended to the generated display.toml (keys of DisplayConfig).
MODE_TOML = """\
# Inspection-mode toggle (development copies of /run/usb-sentinel-control,
# /etc/usb-sentinel and /sys/class/input). Point XDG_STATE_HOME at {state} to
# keep the language and strike files here too (dev-screenshot.py does).
control_dir = "{control}"
host_config_dir = "{host}"
sys_class_input = "{inputs}"
"""


@dataclass(frozen=True)
class ModePaths:
    """Directories created under the fixtures root."""

    control: Path
    host: Path
    inputs: Path
    state: Path

    def toml(self) -> str:
        """The display.toml lines for the three mode keys."""
        return MODE_TOML.format(
            control=self.control, host=self.host, inputs=self.inputs, state=self.state
        )


def write_mode_fixtures(out: Path, *, ceiling: bool, strikes: int | None) -> ModePaths:
    """Create control/, host/, input/input0/ and state/ under OUT.

    Args:
        out: Fixtures root.
        ceiling: True writes allow_destructive = true and USB_SENTINEL_DEVICE_ACCESS=rw
            (erasing allowed by the host); False writes false and ro (button locked).
        strikes: When given, write this wrong-code count to state/usb-sentinel-display/
            erase-strikes. None leaves an existing strike file alone.

    Returns:
        The created directories.

    Raises:
        ValueError: strikes is negative.
    """
    if strikes is not None and strikes < 0:
        raise ValueError(f"strikes must be 0 or more, got {strikes}")
    paths = ModePaths(out / "control", out / "host", out / "input", out / "state")
    paths.control.mkdir(parents=True, exist_ok=True)
    paths.host.mkdir(parents=True, exist_ok=True)
    (paths.inputs / "input0").mkdir(parents=True, exist_ok=True)
    paths.state.mkdir(parents=True, exist_ok=True)
    allow = "true" if ceiling else "false"
    access = "rw" if ceiling else "ro"
    (paths.host / "config.toml").write_text(CONFIG_TOML.format(allow=allow), encoding="utf-8")
    (paths.host / "docker.env").write_text(DOCKER_ENV.format(access=access), encoding="utf-8")
    if strikes is not None:
        strike_dir = paths.state / STRIKE_DIR_NAME
        strike_dir.mkdir(parents=True, exist_ok=True)
        (strike_dir / STRIKE_FILE_NAME).write_text(f"{strikes}\n", encoding="utf-8")
    return paths
