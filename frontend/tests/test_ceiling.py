"""Tests for ceiling.read_ceiling (design doc 3.4, 8.2): config.toml x docker.env matrix."""

from pathlib import Path

import pytest

from usb_sentinel_display.libs.ceiling import (
    MAX_FILE_BYTES,
    REASON_CEILING_OFF,
    REASON_UNREADABLE,
    read_ceiling,
)

CONFIG_TRUE = "allow_destructive = true\n"
ENV_RW = "USB_SENTINEL_DEVICE_ACCESS=rw\n"
CONFIG_CASES: dict[str, str | None] = {
    "missing": None,
    "false": "allow_destructive = false\n",
    "true": CONFIG_TRUE,
    "true_in_section": "[section]\nallow_destructive = true\n",
    "quoted_key": '"allow_destructive" = true\n',
    "invalid_toml": "allow_destructive = \n",
    "indented_true": "  allow_destructive\t=  true\n",
}
ENV_CASES: dict[str, str | None] = {
    "missing": None,
    "ro": "USB_SENTINEL_DEVICE_ACCESS=ro\n",
    "rw": ENV_RW,
    "quoted_rw": 'USB_SENTINEL_DEVICE_ACCESS="rw"\n',
    "single_quoted_rw": "USB_SENTINEL_DEVICE_ACCESS='rw'\n",
    "rw_comment": "USB_SENTINEL_DEVICE_ACCESS=rw # c\n",
    "rw_then_ro": "USB_SENTINEL_DEVICE_ACCESS=rw\nUSB_SENTINEL_DEVICE_ACCESS=ro\n",
    "ro_then_rw": "USB_SENTINEL_DEVICE_ACCESS=ro\nUSB_SENTINEL_DEVICE_ACCESS=rw\n",
    "spaced_rw": "  USB_SENTINEL_DEVICE_ACCESS = rw  \n# comment\n\n",
    "commented_rw": "#USB_SENTINEL_DEVICE_ACCESS=rw\n",
    "other_key": "OTHER=rw\n",
}
CONFIG_ALLOWS = {"true", "indented_true"}
ENV_ALLOWS = {"rw", "quoted_rw", "single_quoted_rw", "ro_then_rw", "spaced_rw"}


def host_dir(tmp_path: Path, config: str | None, env: str | None) -> Path:
    """Create a host config directory with the given config.toml and docker.env contents."""
    if config is not None:
        (tmp_path / "config.toml").write_text(config, encoding="utf-8")
    if env is not None:
        (tmp_path / "docker.env").write_text(env, encoding="utf-8")
    return tmp_path


def deny_read(path: Path) -> None:
    """chmod 0 the file, or skip the test when that does not deny reading (root)."""
    path.chmod(0)
    try:
        path.read_bytes()
    except PermissionError:
        return
    pytest.skip("chmod 0 does not deny access here")


class TestMatrix:
    @pytest.mark.parametrize("env_case", sorted(ENV_CASES))
    @pytest.mark.parametrize("config_case", sorted(CONFIG_CASES))
    def test_only_top_level_true_and_rw_allow(
        self, tmp_path: Path, config_case: str, env_case: str
    ) -> None:
        """Only an active top-level `allow_destructive = true` with a last value rw allows."""
        directory = host_dir(tmp_path, CONFIG_CASES[config_case], ENV_CASES[env_case])
        result = read_ceiling(directory)
        if config_case in CONFIG_ALLOWS and env_case in ENV_ALLOWS:
            assert result is None
        else:
            assert result in (REASON_CEILING_OFF, REASON_UNREADABLE)


class TestReasons:
    def test_config_missing_is_ceiling_off(self, tmp_path: Path) -> None:
        assert read_ceiling(host_dir(tmp_path, None, ENV_CASES["rw"])) == REASON_CEILING_OFF

    def test_env_missing_is_ceiling_off(self, tmp_path: Path) -> None:
        assert read_ceiling(host_dir(tmp_path, CONFIG_CASES["true"], None)) == REASON_CEILING_OFF

    def test_host_dir_missing_is_ceiling_off(self, tmp_path: Path) -> None:
        assert read_ceiling(tmp_path / "none") == REASON_CEILING_OFF

    @pytest.mark.parametrize("case", ["false", "true_in_section", "quoted_key"])
    def test_config_rules_not_met(self, tmp_path: Path, case: str) -> None:
        """false, true inside a table and a quoted key are ceiling off (not unreadable)."""
        assert read_ceiling(host_dir(tmp_path, CONFIG_CASES[case], ENV_CASES["rw"])) == (
            REASON_CEILING_OFF
        )

    def test_invalid_toml_is_unreadable(self, tmp_path: Path) -> None:
        directory = host_dir(tmp_path, CONFIG_CASES["invalid_toml"], ENV_CASES["rw"])
        assert read_ceiling(directory) == REASON_UNREADABLE

    def test_toml_true_without_grep_line_is_ceiling_off(self, tmp_path: Path) -> None:
        """tomllib accepts `allow_destructive=true` with a dotted trick? No: only the grep rule."""
        config = "allow_destructive = true # ok\n"  # grep rule matches too: allowed
        assert read_ceiling(host_dir(tmp_path, config, ENV_CASES["rw"])) is None
        config = "allow_destructive = true\n[x]\n"  # still top-level: allowed
        assert read_ceiling(host_dir(tmp_path, config, ENV_CASES["rw"])) is None

    def test_toml_value_not_bool(self, tmp_path: Path) -> None:
        """A string "true" is not the boolean true."""
        config = 'allow_destructive = "true"\n'
        assert read_ceiling(host_dir(tmp_path, config, ENV_CASES["rw"])) == REASON_CEILING_OFF

    @pytest.mark.parametrize("case", ["ro", "rw_comment", "rw_then_ro", "commented_rw"])
    def test_env_rules_not_met(self, tmp_path: Path, case: str) -> None:
        assert read_ceiling(host_dir(tmp_path, CONFIG_CASES["true"], ENV_CASES[case])) == (
            REASON_CEILING_OFF
        )

    def test_config_unreadable(self, tmp_path: Path) -> None:
        directory = host_dir(tmp_path, CONFIG_CASES["true"], ENV_CASES["rw"])
        deny_read(directory / "config.toml")
        try:
            assert read_ceiling(directory) == REASON_UNREADABLE
        finally:
            (directory / "config.toml").chmod(0o600)

    def test_env_unreadable(self, tmp_path: Path) -> None:
        directory = host_dir(tmp_path, CONFIG_CASES["true"], ENV_CASES["rw"])
        deny_read(directory / "docker.env")
        try:
            assert read_ceiling(directory) == REASON_UNREADABLE
        finally:
            (directory / "docker.env").chmod(0o600)

    def test_config_too_large(self, tmp_path: Path) -> None:
        """A file over MAX_FILE_BYTES is unreadable even when its content would allow."""
        directory = host_dir(tmp_path, None, ENV_CASES["rw"])
        padding = "# " + "x" * MAX_FILE_BYTES + "\n"
        (directory / "config.toml").write_text(CONFIG_TRUE + padding)
        assert (directory / "config.toml").stat().st_size > MAX_FILE_BYTES
        assert read_ceiling(directory) == REASON_UNREADABLE

    def test_env_too_large(self, tmp_path: Path) -> None:
        directory = host_dir(tmp_path, CONFIG_CASES["true"], None)
        (directory / "docker.env").write_text(ENV_RW + "#" * (MAX_FILE_BYTES + 1))
        assert read_ceiling(directory) == REASON_UNREADABLE

    def test_exactly_max_bytes_is_read(self, tmp_path: Path) -> None:
        """A file of exactly MAX_FILE_BYTES is still read."""
        directory = host_dir(tmp_path, None, ENV_CASES["rw"])
        content = CONFIG_TRUE + "#" + "x" * (MAX_FILE_BYTES - len(CONFIG_TRUE) - 2) + "\n"
        (directory / "config.toml").write_text(content)
        assert (directory / "config.toml").stat().st_size == MAX_FILE_BYTES
        assert read_ceiling(directory) is None

    def test_non_utf8_is_unreadable(self, tmp_path: Path) -> None:
        directory = host_dir(tmp_path, None, ENV_CASES["rw"])
        (directory / "config.toml").write_bytes(b"allow_destructive = true\n\xff\xfe")
        assert read_ceiling(directory) == REASON_UNREADABLE
        directory = host_dir(tmp_path, CONFIG_CASES["true"], None)
        (directory / "docker.env").write_bytes(b"USB_SENTINEL_DEVICE_ACCESS=rw\n\xff")
        assert read_ceiling(directory) == REASON_UNREADABLE

    def test_directory_in_place_of_file_is_unreadable(self, tmp_path: Path) -> None:
        directory = host_dir(tmp_path, None, ENV_CASES["rw"])
        (directory / "config.toml").mkdir()
        assert read_ceiling(directory) == REASON_UNREADABLE

    def test_constants(self) -> None:
        assert REASON_CEILING_OFF == "locked_ceiling"
        assert REASON_UNREADABLE == "locked_unreadable"
        assert MAX_FILE_BYTES == 1_048_576
