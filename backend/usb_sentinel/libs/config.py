import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .speed_floor import DEVICE_FLOORS

# fioのサイズ表記（例: 512、4k、1M、2G）。
BLOCK_SIZE_PATTERN = re.compile(r"[1-9][0-9]*[kKmMgG]?")


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


def _require_ratio(name: str, value: object) -> None:
    """0 < value <= 1の数値であることを確認する。"""
    number = _require_number(name, value)
    if not 0 < number <= 1:
        raise ValueError(f"{name} must be in (0, 1]")


def _require_positive(name: str, value: object) -> None:
    """正の数値であることを確認する。"""
    if _require_number(name, value) <= 0:
        raise ValueError(f"{name} must be positive")


def _require_positive_int(name: str, value: object) -> None:
    """boolでない1以上の整数であることを確認する。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be an integer >= 1")


@dataclass(frozen=True)
class MalwareConfig:
    max_db_age_days: int = 30
    timeout_s: float = 3600
    exclude_patterns: tuple[str, ...] = ()
    # 定義DBの鮮度超過をERRORにする（真ならスキャンを実行しない）。既定は警告のみ。
    stale_db_is_error: bool = False

    def __post_init__(self) -> None:
        """設定値の型を検証する。"""
        if not isinstance(self.stale_db_is_error, bool):
            raise ValueError("malware.stale_db_is_error must be a TOML boolean")


@dataclass(frozen=True)
class CapacityConfig:
    pass_ratio: float = 0.93
    use_probe: bool = False
    timeout_s: float = 14400
    # 商品ページの公称容量（バイト、10進表記）。未設定ならlsblkの申告値を分母にする。
    nominal_bytes: int | None = None
    # 容量超過の上限比率（公称×over_ratioを超えたら偽装疑い）。
    over_ratio: float = 1.07

    def __post_init__(self) -> None:
        """設定値の範囲を検証する。"""
        _require_ratio("capacity.pass_ratio", self.pass_ratio)
        _require_positive("capacity.timeout_s", self.timeout_s)
        if not isinstance(self.use_probe, bool):
            raise ValueError("capacity.use_probe must be a TOML boolean")
        if self.nominal_bytes is not None:
            _require_positive_int("capacity.nominal_bytes", self.nominal_bytes)
        if _require_number("capacity.over_ratio", self.over_ratio) <= 1.0:
            raise ValueError("capacity.over_ratio must be greater than 1.0")


@dataclass(frozen=True)
class SpeedConfig:
    block_size: str = "1M"
    queue_depth: int = 8
    duration_s: int = 30
    pass_ratio: float = 0.80
    timeout_s: float = 900
    # 公称速度（MB/s、10^6バイト/秒）。未設定（None）なら判定しない。
    nominal_read_mb_s: float | None = None
    nominal_write_mb_s: float | None = None
    # デバイス種別（speed_floor.DEVICE_FLOORSのキー）。公称速度が無いときの判定基準に使う。
    device_class: str | None = None

    def __post_init__(self) -> None:
        """設定値の範囲を検証する。"""
        if not isinstance(self.block_size, str) or not BLOCK_SIZE_PATTERN.fullmatch(
            self.block_size
        ):
            raise ValueError("speed.block_size must look like 512, 4k, 1M or 2G")
        _require_positive_int("speed.queue_depth", self.queue_depth)
        _require_positive_int("speed.duration_s", self.duration_s)
        _require_ratio("speed.pass_ratio", self.pass_ratio)
        _require_positive("speed.timeout_s", self.timeout_s)
        for name in ("nominal_read_mb_s", "nominal_write_mb_s"):
            value = getattr(self, name)
            if value is not None:
                _require_positive(f"speed.{name}", value)
        if self.device_class is not None and self.device_class not in DEVICE_FLOORS:
            known = ", ".join(sorted(DEVICE_FLOORS))
            raise ValueError(f"speed.device_class must be one of: {known}")


@dataclass(frozen=True)
class Config:
    results_dir: Path = Path("/var/lib/usb-sentinel/results")
    mount_root: Path = Path("/run/usb-sentinel/mnt")
    allow_destructive: bool = False
    hash_inventory: bool = True
    # ハッシュ台帳作成の時間上限（秒）。hash_inventoryがboolなのでテーブルにはしない。
    hash_inventory_timeout_s: float = 3600.0
    # SQLite記録先。Noneなら記録しない（TOMLでは "" を指定）。
    sqlite_path: Path | None = Path("/var/lib/usb-sentinel/db/inspections.sqlite")
    emit_qr: bool = True
    malware: MalwareConfig = field(default_factory=MalwareConfig)
    capacity: CapacityConfig = field(default_factory=CapacityConfig)
    speed: SpeedConfig = field(default_factory=SpeedConfig)

    def __post_init__(self) -> None:
        """真偽値キーの型と、ハッシュ台帳の時間上限が正であることを確かめる。"""
        for key in ("allow_destructive", "hash_inventory", "emit_qr"):
            if not isinstance(getattr(self, key), bool):
                raise ValueError(f"{key} must be a TOML boolean")
        _require_positive("hash_inventory_timeout_s", self.hash_inventory_timeout_s)


def load_config(path: Path) -> Config:
    """config.tomlを読んでConfigにする。ファイルが無ければ既定値。

    Args:
        path: 設定ファイルのパス（既定は`/etc/usb-sentinel/config.toml`）。

    Returns:
        各節（malware／capacity／speed）を対応するdataclassに変換した設定。

    Raises:
        ValueError: 値の型や範囲が不正なとき（各dataclassの`__post_init__`）。
        TypeError: 未知のキーがあるとき（dataclassの引数エラー）。
    """
    if not path.exists():
        return Config()
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    for key in ("results_dir", "mount_root"):
        if key in data:
            data[key] = Path(data[key])
    if "sqlite_path" in data:
        data["sqlite_path"] = Path(data["sqlite_path"]) if data["sqlite_path"] else None
    for key, cls in (
        ("malware", MalwareConfig),
        ("capacity", CapacityConfig),
        ("speed", SpeedConfig),
    ):
        section = data.pop(key, {})
        if key == "malware" and "exclude_patterns" in section:
            section["exclude_patterns"] = tuple(section["exclude_patterns"])
        data[key] = cls(**section)
    return Config(**data)
