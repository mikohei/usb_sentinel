import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("~/.config/usb-sentinel/display.toml")
MAX_PORTS = 4
MAX_LABEL_LEN = 4
# 画面の言語。ui/i18n.pyのLANGUAGESと同じ組（config.pyはui/をimportしないので自前で持つ）。
LANGUAGES: tuple[str, ...] = ("en", "ja")


def _require_number(name: str, value: object) -> float:
    """boolでない数値であることを確認して返す。

    Args:
        name: エラーメッセージに使う項目名。
        value: 検証する値。

    Returns:
        数値としての値。

    Raises:
        ValueError: boolまたは数値以外のとき。
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number")
    return value


def _require_str(name: str, value: object) -> str:
    """文字列であることを確認して返す。"""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


@dataclass(frozen=True)
class PortMapping:
    """物理ポートの表示名と、そのポートに現れるポートパスの一覧。"""

    label: str
    paths: tuple[str, ...]

    def __post_init__(self) -> None:
        """labelとpathsの形式を検証する。"""
        label = _require_str("ports.label", self.label)
        if not 1 <= len(label) <= MAX_LABEL_LEN or not label.isascii() or not label.isprintable():
            raise ValueError("ports.label must be 1-4 printable ASCII characters")
        if not isinstance(self.paths, tuple) or not all(isinstance(p, str) for p in self.paths):
            raise ValueError("ports.paths must be an array of strings")


@dataclass(frozen=True)
class DisplayConfig:
    """display.tomlの内容（設計書5.2）。"""

    results_dir: Path = Path("/var/lib/usb-sentinel/results")
    sys_block: Path = Path("/sys/block")
    unit_template: str = "usb-sentinel-docker@{kernel}.service"
    poll_interval_s: float = 1.0
    retain_minutes: int = 60
    font_family: str = "Noto Sans CJK JP"
    language: str = "en"
    # backendのSQLite索引。Noneなら索引を使わず結果ディレクトリだけを走査する（TOMLでは""）。
    sqlite_path: Path | None = Path("/var/lib/usb-sentinel/db/inspections.sqlite")
    ports: tuple[PortMapping, ...] = ()

    def __post_init__(self) -> None:
        """型と範囲を検証する。"""
        if not isinstance(self.results_dir, Path):
            raise ValueError("results_dir must be a string")
        if not isinstance(self.sys_block, Path):
            raise ValueError("sys_block must be a string")
        if self.sqlite_path is not None and not isinstance(self.sqlite_path, Path):
            raise ValueError("sqlite_path must be a string")
        template = _require_str("unit_template", self.unit_template)
        if "{kernel}" not in template:
            raise ValueError("unit_template must contain {kernel}")
        if "{" in template.replace("{kernel}", "") or "}" in template.replace("{kernel}", ""):
            raise ValueError("unit_template may only contain the {kernel} placeholder")
        if not 0.2 <= _require_number("poll_interval_s", self.poll_interval_s) <= 10:
            raise ValueError("poll_interval_s must be in [0.2, 10]")
        if (
            isinstance(self.retain_minutes, bool)
            or not isinstance(self.retain_minutes, int)
            or self.retain_minutes < 1
        ):
            raise ValueError("retain_minutes must be an integer >= 1")
        _require_str("font_family", self.font_family)
        if self.language not in LANGUAGES:
            raise ValueError('language must be "en" or "ja"')
        if not isinstance(self.ports, tuple) or not all(
            isinstance(p, PortMapping) for p in self.ports
        ):
            raise ValueError("ports must be an array of tables")
        if len(self.ports) > MAX_PORTS:
            raise ValueError(f"ports must have at most {MAX_PORTS} entries")
        labels = [p.label for p in self.ports]
        if len(set(labels)) != len(labels):
            raise ValueError("ports.label must be unique")
        paths = [path for p in self.ports for path in p.paths]
        if len(set(paths)) != len(paths):
            raise ValueError("ports.paths must be unique across entries")


def default_config_path() -> Path:
    """既定の設定ファイルパス（~を展開したもの）を返す。"""
    return DEFAULT_CONFIG_PATH.expanduser()


def _parse_ports(raw: object) -> tuple[PortMapping, ...]:
    """[[ports]]の配列をPortMappingの組に変換する。"""
    if not isinstance(raw, list):
        raise ValueError("ports must be an array of tables")
    ports: list[PortMapping] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("ports must be an array of tables")
        unknown = set(entry) - {"label", "paths"}
        if unknown:
            raise ValueError(f"unknown key in ports: {sorted(unknown)}")
        paths = entry.get("paths", [])
        if not isinstance(paths, list):
            raise ValueError("ports.paths must be an array of strings")
        ports.append(PortMapping(entry.get("label", ""), tuple(paths)))
    return tuple(ports)


def load_config(path: Path) -> DisplayConfig:
    """設定ファイルを読む。無ければ既定値を返す。

    Args:
        path: display.tomlのパス。

    Returns:
        検証済みの設定。

    Raises:
        ValueError: 型違い、範囲外、未知のキーがあるとき。
    """
    if not path.exists():
        return DisplayConfig()
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    known = {f.name for f in DisplayConfig.__dataclass_fields__.values()}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown key: {sorted(unknown)}")
    for key in ("results_dir", "sys_block"):
        if key in data:
            data[key] = Path(_require_str(key, data[key]))
    if "sqlite_path" in data:
        text = _require_str("sqlite_path", data["sqlite_path"])
        data["sqlite_path"] = Path(text) if text else None
    if "ports" in data:
        data["ports"] = _parse_ports(data["ports"])
    return DisplayConfig(**data)
