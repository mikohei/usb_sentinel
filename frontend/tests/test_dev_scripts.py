"""Tests for the development scripts' helper modules (scripts/fixtures_mode.py and
scripts/dev_mode_scenes.py) and for the display.toml written by make-fixtures.py.

The scripts are not packaged, so they are loaded from the scripts/ directory by path.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from usb_sentinel_display.libs import ceiling, trust
from usb_sentinel_display.libs.config import load_config

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name: str) -> ModuleType:
    """Import a module from scripts/ by file name (hyphenated names allowed)."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fixtures_mode() -> ModuleType:
    """The scripts/fixtures_mode.py module."""
    return load_script("fixtures_mode")


@pytest.fixture(scope="module")
def dev_mode_scenes() -> ModuleType:
    """The scripts/dev_mode_scenes.py module."""
    return load_script("dev_mode_scenes")


class TestFixturesMode:
    """write_mode_fixtures() creates the mode-toggle directories and files."""

    def test_default_enables_the_ceiling(self, fixtures_mode: ModuleType, tmp_path: Path) -> None:
        paths = fixtures_mode.write_mode_fixtures(tmp_path, ceiling=True, strikes=None)
        assert paths.control == tmp_path / "control"
        assert paths.host == tmp_path / "host"
        assert paths.inputs == tmp_path / "input"
        assert paths.state == tmp_path / "state"
        assert paths.control.is_dir()
        assert (paths.inputs / "input0").is_dir()
        assert paths.state.is_dir()
        assert "allow_destructive = true" in (paths.host / "config.toml").read_text("utf-8")
        assert "USB_SENTINEL_DEVICE_ACCESS=rw" in (paths.host / "docker.env").read_text("utf-8")
        # The real ceiling reader accepts the files as "erasing allowed".
        assert ceiling.read_ceiling(paths.host) is None
        assert not (paths.state / trust.STRIKE_DIR_NAME / trust.STRIKE_FILE_NAME).exists()

    def test_ceiling_off_writes_false_and_ro(
        self, fixtures_mode: ModuleType, tmp_path: Path
    ) -> None:
        paths = fixtures_mode.write_mode_fixtures(tmp_path, ceiling=False, strikes=None)
        assert "allow_destructive = false" in (paths.host / "config.toml").read_text("utf-8")
        assert "USB_SENTINEL_DEVICE_ACCESS=ro" in (paths.host / "docker.env").read_text("utf-8")
        assert ceiling.read_ceiling(paths.host) == ceiling.REASON_CEILING_OFF

    @pytest.mark.parametrize("strikes", [0, 3])
    def test_strikes_written_to_the_state_dir(
        self, fixtures_mode: ModuleType, tmp_path: Path, strikes: int
    ) -> None:
        paths = fixtures_mode.write_mode_fixtures(tmp_path, ceiling=True, strikes=strikes)
        strike_file = paths.state / trust.STRIKE_DIR_NAME / trust.STRIKE_FILE_NAME
        assert strike_file.read_text("utf-8") == f"{strikes}\n"
        assert trust.read_strikes(strike_file) == strikes

    def test_rerun_overwrites_without_error(
        self, fixtures_mode: ModuleType, tmp_path: Path
    ) -> None:
        fixtures_mode.write_mode_fixtures(tmp_path, ceiling=True, strikes=3)
        paths = fixtures_mode.write_mode_fixtures(tmp_path, ceiling=False, strikes=None)
        assert "USB_SENTINEL_DEVICE_ACCESS=ro" in (paths.host / "docker.env").read_text("utf-8")
        # An earlier strike file is left alone when --strikes is not given.
        strike_file = paths.state / trust.STRIKE_DIR_NAME / trust.STRIKE_FILE_NAME
        assert strike_file.read_text("utf-8") == "3\n"

    def test_negative_strikes_rejected(self, fixtures_mode: ModuleType, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="strikes"):
            fixtures_mode.write_mode_fixtures(tmp_path, ceiling=True, strikes=-1)


class TestMakeFixturesToml:
    """The display.toml written by make-fixtures.py carries the three mode keys."""

    def test_display_toml_loads_with_mode_keys(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        make_fixtures = load_script("make-fixtures")
        assert make_fixtures.main([str(tmp_path), "--scene", "a", "--strikes", "2"]) == 0
        config = load_config(tmp_path / "display.toml")
        assert config.control_dir == tmp_path / "control"
        assert config.host_config_dir == tmp_path / "host"
        assert config.sys_class_input == tmp_path / "input"
        assert config.sqlite_path is None
        assert (tmp_path / "state" / "usb-sentinel-display" / "erase-strikes").read_text(
            "utf-8"
        ) == "2\n"
        assert "fixtures:" in capsys.readouterr().out

    def test_ceiling_option(self, tmp_path: Path) -> None:
        make_fixtures = load_script("make-fixtures")
        assert make_fixtures.main([str(tmp_path), "--ceiling", "off"]) == 0
        assert ceiling.read_ceiling(tmp_path / "host") == ceiling.REASON_CEILING_OFF


class FakeApp:
    """Records the App calls made by a scene."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, int | None]] = []

    def toggle_mode(self) -> None:
        """Record the mode button."""
        self.calls.append(("toggle_mode", None))

    def select(self, index: int) -> None:
        """Record a tile tap / code digit."""
        self.calls.append(("select", index))


class TestDevModeScenes:
    """run_scene() drives the App exactly as plan.md Step 10 describes."""

    def test_scene_names_and_default_code(self, dev_mode_scenes: ModuleType) -> None:
        assert dev_mode_scenes.SCENES == (
            "none",
            "scan_only",
            "confirm",
            "full",
            "locked",
            "lockout",
        )
        assert dev_mode_scenes.DEFAULT_CODE == "3142"

    def test_code_indices(self, dev_mode_scenes: ModuleType) -> None:
        assert dev_mode_scenes.code_indices("3142") == (2, 0, 3, 1)
        assert dev_mode_scenes.code_indices("1111") == (0, 0, 0, 0)

    @pytest.mark.parametrize("code", ["", "314", "31425", "3140", "abcd", "3 42"])
    def test_code_indices_rejects_bad_codes(self, dev_mode_scenes: ModuleType, code: str) -> None:
        with pytest.raises(ValueError, match="code"):
            dev_mode_scenes.code_indices(code)

    @pytest.mark.parametrize("scene", ["none", "scan_only"])
    def test_quiet_scenes_do_nothing(self, dev_mode_scenes: ModuleType, scene: str) -> None:
        app = FakeApp()
        dev_mode_scenes.run_scene(app, scene, "3142")
        assert app.calls == []

    @pytest.mark.parametrize("scene", ["confirm", "locked", "lockout"])
    def test_toggle_only_scenes(self, dev_mode_scenes: ModuleType, scene: str) -> None:
        app = FakeApp()
        dev_mode_scenes.run_scene(app, scene, "3142")
        assert app.calls == [("toggle_mode", None)]

    def test_full_scene_enters_the_code(self, dev_mode_scenes: ModuleType) -> None:
        app = FakeApp()
        dev_mode_scenes.run_scene(app, "full", "3142")
        assert app.calls == [
            ("toggle_mode", None),
            ("select", 2),
            ("select", 0),
            ("select", 3),
            ("select", 1),
        ]

    def test_unknown_scene_rejected(self, dev_mode_scenes: ModuleType) -> None:
        with pytest.raises(ValueError, match="scene"):
            dev_mode_scenes.run_scene(FakeApp(), "erase", "3142")
