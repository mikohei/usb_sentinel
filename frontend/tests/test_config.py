from pathlib import Path

import pytest

from usb_sentinel_display.libs.config import (
    DisplayConfig,
    PortMapping,
    default_config_path,
    load_config,
)


def write(tmp_path: Path, text: str) -> Path:
    """TOML文字列をファイルに書いてパスを返す。"""
    path = tmp_path / "display.toml"
    path.write_text(text, encoding="utf-8")
    return path


class TestDefaults:
    def test_default_values(self) -> None:
        """既定値が設計書5.2のとおりである。"""
        config = DisplayConfig()
        assert config.results_dir == Path("/var/lib/usb-sentinel/results")
        assert config.sys_block == Path("/sys/block")
        assert config.unit_template == "usb-sentinel-docker@{kernel}.service"
        assert config.poll_interval_s == 1.0
        assert config.retain_minutes == 60
        assert config.font_family == "Noto Sans CJK JP"
        assert config.language == "en"
        assert config.sqlite_path == Path("/var/lib/usb-sentinel/db/inspections.sqlite")
        assert config.ports == ()

    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        """ファイルが無ければ既定値で動く。"""
        assert load_config(tmp_path / "none.toml") == DisplayConfig()

    def test_default_config_path_is_expanded(self) -> None:
        """既定の設定パスは~が展開されている。"""
        path = default_config_path()
        assert "~" not in str(path)
        assert path.name == "display.toml"


class TestValidation:
    @pytest.mark.parametrize("value", [0.1, 11])
    def test_poll_interval_out_of_range(self, value: float) -> None:
        """poll_interval_sは0.2〜10の範囲外でValueError。"""
        with pytest.raises(ValueError, match="poll_interval_s"):
            DisplayConfig(poll_interval_s=value)

    def test_poll_interval_bounds_are_inclusive(self) -> None:
        """境界値0.2と10は許容される。"""
        DisplayConfig(poll_interval_s=0.2)
        DisplayConfig(poll_interval_s=10)

    def test_retain_minutes_zero(self) -> None:
        """retain_minutesは1以上。"""
        with pytest.raises(ValueError, match="retain_minutes"):
            DisplayConfig(retain_minutes=0)

    def test_unit_template_without_kernel(self) -> None:
        """unit_templateに{kernel}が無いとValueError。"""
        with pytest.raises(ValueError, match="kernel"):
            DisplayConfig(unit_template="usb-sentinel@.service")

    @pytest.mark.parametrize(
        "template", ["x@{kernel}{0}.service", "{kernel.__class__}@{kernel}", "x@{kernel}}"]
    )
    def test_unit_template_with_other_braces(self, template: str) -> None:
        """{kernel}以外の波括弧があると起動時にValueError。"""
        with pytest.raises(ValueError, match="kernel"):
            DisplayConfig(unit_template=template)

    def test_duplicate_labels(self) -> None:
        """labelの重複はValueError。"""
        with pytest.raises(ValueError, match="label"):
            DisplayConfig(ports=(PortMapping("P1", ("1-1.1",)), PortMapping("P1", ("1-1.2",))))

    def test_duplicate_paths_across_entries(self) -> None:
        """別エントリ間のpaths重複はValueError。"""
        with pytest.raises(ValueError, match="paths"):
            DisplayConfig(ports=(PortMapping("P1", ("1-1.1",)), PortMapping("P2", ("1-1.1",))))

    def test_five_ports(self) -> None:
        """[[ports]]は4件まで。"""
        ports = tuple(PortMapping(f"P{i}", (f"1-1.{i}",)) for i in range(1, 6))
        with pytest.raises(ValueError, match="ports"):
            DisplayConfig(ports=ports)

    @pytest.mark.parametrize("label", ["", "P1234", "ポ1"])
    def test_invalid_label(self, label: str) -> None:
        """labelは1〜4文字の印字可能ASCII。"""
        with pytest.raises(ValueError, match="label"):
            PortMapping(label, ("1-1.1",))

    def test_poll_interval_string(self, tmp_path: Path) -> None:
        """文字列のpoll_interval_sはValueError。"""
        path = write(tmp_path, 'poll_interval_s = "1"\n')
        with pytest.raises(ValueError, match="poll_interval_s"):
            load_config(path)

    def test_retain_minutes_bool(self, tmp_path: Path) -> None:
        """boolのretain_minutesはValueError。"""
        path = write(tmp_path, "retain_minutes = true\n")
        with pytest.raises(ValueError, match="retain_minutes"):
            load_config(path)

    @pytest.mark.parametrize("value", ["fr", "", "JA"])
    def test_language_invalid(self, value: str) -> None:
        """languageはenかjaだけ。"""
        with pytest.raises(ValueError, match="language"):
            DisplayConfig(language=value)

    def test_language_from_toml(self, tmp_path: Path) -> None:
        """TOMLのlanguage = "ja"を読む。数値はValueError。"""
        assert load_config(write(tmp_path, 'language = "ja"\n')).language == "ja"
        with pytest.raises(ValueError, match="language"):
            load_config(write(tmp_path, "language = 1\n"))

    def test_sqlite_path_from_toml(self, tmp_path: Path) -> None:
        """TOMLの文字列はPathに、空文字はNone（索引を使わない）になる。"""
        path = write(tmp_path, 'sqlite_path = "/srv/db/inspections.sqlite"\n')
        assert load_config(path).sqlite_path == Path("/srv/db/inspections.sqlite")
        assert load_config(write(tmp_path, 'sqlite_path = ""\n')).sqlite_path is None

    def test_sqlite_path_wrong_type(self, tmp_path: Path) -> None:
        """文字列以外のsqlite_pathはValueError。"""
        with pytest.raises(ValueError, match="sqlite_path"):
            load_config(write(tmp_path, "sqlite_path = 1\n"))
        with pytest.raises(ValueError, match="sqlite_path"):
            DisplayConfig(sqlite_path="x")  # ty: ignore[invalid-argument-type] -- 型違いを渡したときの検証を確かめる

    def test_sqlite_path_none_allowed(self) -> None:
        DisplayConfig(sqlite_path=None)

    def test_unknown_key(self, tmp_path: Path) -> None:
        """未知のキーはValueErrorで知らせる。"""
        path = write(tmp_path, "unknown = 1\n")
        with pytest.raises(ValueError, match="unknown"):
            load_config(path)


class TestLoad:
    def test_two_ports_in_order(self, tmp_path: Path) -> None:
        """[[ports]]が2件なら出現順に2つだけ入る。"""
        path = write(
            tmp_path,
            """
results_dir = "/srv/results"
sys_block = "/srv/sys_block"
poll_interval_s = 2
retain_minutes = 5
[[ports]]
label = "P2"
paths = ["1-1.2", "2-2"]
[[ports]]
label = "P1"
paths = ["1-1.1"]
""",
        )
        config = load_config(path)
        assert config.results_dir == Path("/srv/results")
        assert config.sys_block == Path("/srv/sys_block")
        assert config.poll_interval_s == 2
        assert config.retain_minutes == 5
        assert len(config.ports) == 2
        assert config.ports[0] == PortMapping("P2", ("1-1.2", "2-2"))
        assert config.ports[1] == PortMapping("P1", ("1-1.1",))

    def test_paths_must_be_strings(self, tmp_path: Path) -> None:
        """pathsの要素が文字列でないとValueError。"""
        path = write(tmp_path, '[[ports]]\nlabel = "P1"\npaths = [1]\n')
        with pytest.raises(ValueError, match="paths"):
            load_config(path)
