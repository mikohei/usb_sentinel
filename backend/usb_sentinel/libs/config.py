import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .speed_floor import DEVICE_FLOORS

# fio size notation (e.g. 512, 4k, 1M, 2G).
BLOCK_SIZE_PATTERN = re.compile(r"[1-9][0-9]*[kKmMgG]?")


def _require_number(name: str, value: object) -> float:
    """Check that the value is a number other than bool, and return it.

    Args:
        name: Item name used in the error message.
        value: Value to validate.

    Returns:
        The value as a number.

    Raises:
        ValueError: If the value is a bool or not a number.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number")
    return value


def _require_ratio(name: str, value: object) -> None:
    """Check that the value is a number with 0 < value <= 1."""
    number = _require_number(name, value)
    if not 0 < number <= 1:
        raise ValueError(f"{name} must be in (0, 1]")


def _require_positive(name: str, value: object) -> None:
    """Check that the value is a positive number."""
    if _require_number(name, value) <= 0:
        raise ValueError(f"{name} must be positive")


def _require_positive_int(name: str, value: object) -> None:
    """Check that the value is an integer >= 1 other than bool."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be an integer >= 1")


@dataclass(frozen=True)
class MalwareConfig:
    max_db_age_days: int = 30
    timeout_s: float = 3600
    exclude_patterns: tuple[str, ...] = ()
    # Treat a stale definition DB as ERROR (if true, the scan is not run). Default: warn only.
    stale_db_is_error: bool = False

    def __post_init__(self) -> None:
        """Validate the types of the settings."""
        if not isinstance(self.stale_db_is_error, bool):
            raise ValueError("malware.stale_db_is_error must be a TOML boolean")


@dataclass(frozen=True)
class CapacityConfig:
    pass_ratio: float = 0.93
    use_probe: bool = False
    timeout_s: float = 14400
    # Nominal capacity from the product page (bytes, decimal). If unset, the size reported by
    # lsblk is used as the denominator.
    nominal_bytes: int | None = None
    # Upper ratio for excess capacity (exceeding nominal x over_ratio suggests counterfeit).
    over_ratio: float = 1.07

    def __post_init__(self) -> None:
        """Validate the ranges of the settings."""
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
    # Nominal speeds (MB/s, 10^6 bytes/s). Not judged when unset (None).
    nominal_read_mb_s: float | None = None
    nominal_write_mb_s: float | None = None
    # Device class (a speed_floor.DEVICE_FLOORS key). Used as the reference when no nominal
    # speed is set.
    device_class: str | None = None

    def __post_init__(self) -> None:
        """Validate the ranges of the settings."""
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
    # Time limit for building the hash inventory (seconds). Not a table because
    # hash_inventory is a bool.
    hash_inventory_timeout_s: float = 3600.0
    # SQLite destination. None disables recording (set "" in TOML).
    sqlite_path: Path | None = Path("/var/lib/usb-sentinel/db/inspections.sqlite")
    emit_qr: bool = True
    malware: MalwareConfig = field(default_factory=MalwareConfig)
    capacity: CapacityConfig = field(default_factory=CapacityConfig)
    speed: SpeedConfig = field(default_factory=SpeedConfig)

    def __post_init__(self) -> None:
        """Check the types of boolean keys and that the hash inventory time limit is positive."""
        for key in ("allow_destructive", "hash_inventory", "emit_qr"):
            if not isinstance(getattr(self, key), bool):
                raise ValueError(f"{key} must be a TOML boolean")
        _require_positive("hash_inventory_timeout_s", self.hash_inventory_timeout_s)


def load_config(path: Path) -> Config:
    """Read config.toml into a Config. Defaults are used when the file does not exist.

    Args:
        path: Path to the config file (default `/etc/usb-sentinel/config.toml`).

    Returns:
        Config with each section (malware / capacity / speed) converted to its dataclass.

    Raises:
        ValueError: If a value has an invalid type or range (each dataclass's `__post_init__`).
        TypeError: If there is an unknown key (dataclass argument error).
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
