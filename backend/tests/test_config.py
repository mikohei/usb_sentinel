"""Validation of config values (__post_init__ of CapacityConfig / SpeedConfig).

TOML true/false become bool in Python, a subclass of int, so verify they are rejected even
when they slip into numeric fields.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from usb_sentinel.libs.config import (
    CapacityConfig,
    Config,
    MalwareConfig,
    SpeedConfig,
    load_config,
)


class TestCapacityConfig:
    @pytest.mark.parametrize("ratio", [0, 1.5, True, "0.9"])
    def test_rejects_invalid_pass_ratio(self, ratio: object) -> None:
        with pytest.raises(ValueError, match="capacity.pass_ratio"):
            CapacityConfig(pass_ratio=ratio)  # ty: ignore[invalid-argument-type]  intentionally invalid type

    @pytest.mark.parametrize("timeout", [-1, True])
    def test_rejects_invalid_timeout(self, timeout: object) -> None:
        with pytest.raises(ValueError, match="capacity.timeout_s"):
            CapacityConfig(timeout_s=timeout)  # ty: ignore[invalid-argument-type]  intentionally invalid type

    def test_timeout_defaults_to_no_limit(self) -> None:
        assert CapacityConfig().timeout_s == 0
        assert CapacityConfig(timeout_s=0).timeout_s == 0

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("batch_files", 0),
            ("batch_files", 1.5),
            ("slow_batches", 0),
            ("slow_batches", True),
            ("stall_mb_s", 0),
            ("stall_mb_s", -1.0),
        ],
    )
    def test_rejects_invalid_batch_settings(self, key: str, value: object) -> None:
        with pytest.raises(ValueError, match=f"capacity.{key}"):
            CapacityConfig(**{key: value})  # ty: ignore[invalid-argument-type]  intentionally invalid type

    def test_rejects_string_probe_first(self) -> None:
        with pytest.raises(ValueError, match="probe_first"):
            CapacityConfig(probe_first="true")  # ty: ignore[invalid-argument-type]  intentionally invalid type

    def test_rejects_string_use_probe(self) -> None:
        with pytest.raises(ValueError, match="use_probe"):
            CapacityConfig(use_probe="false")  # ty: ignore[invalid-argument-type]  intentionally invalid type

    def test_boundary_ratio_one_is_accepted(self) -> None:
        # pass_ratio = 1.0 is the boundary value that requires "equal to nominal capacity".
        assert CapacityConfig(pass_ratio=1.0).pass_ratio == 1.0

    @pytest.mark.parametrize("nominal", [-1, 0, True, 32.0])
    def test_rejects_invalid_nominal_bytes(self, nominal: object) -> None:
        """Nominal capacity accepts only non-bool integers of 1 or more."""
        with pytest.raises(ValueError, match="capacity.nominal_bytes"):
            CapacityConfig(nominal_bytes=nominal)  # ty: ignore[invalid-argument-type]  intentionally invalid type

    def test_nominal_bytes_defaults_to_none(self) -> None:
        assert CapacityConfig().nominal_bytes is None
        assert CapacityConfig(nominal_bytes=32_000_000_000).nominal_bytes == 32_000_000_000

    @pytest.mark.parametrize("ratio", [1.0, 0.99, 0, True, "1.07"])
    def test_rejects_over_ratio_not_above_one(self, ratio: object) -> None:
        """The capacity-overrun ratio is a number greater than 1.0 (default 1.07)."""
        with pytest.raises(ValueError, match="capacity.over_ratio"):
            CapacityConfig(over_ratio=ratio)  # ty: ignore[invalid-argument-type]  intentionally invalid type

    def test_over_ratio_default(self) -> None:
        assert CapacityConfig().over_ratio == 1.07


class TestMalwareConfig:
    def test_rejects_string_stale_db_is_error(self) -> None:
        with pytest.raises(ValueError, match="stale_db_is_error"):
            MalwareConfig(stale_db_is_error="true")  # ty: ignore[invalid-argument-type]  intentionally invalid type

    def test_stale_db_is_error_defaults_to_false(self) -> None:
        assert MalwareConfig().stale_db_is_error is False


class TestSpeedConfig:
    @pytest.mark.parametrize("block_size", ["abc", "0M", "1MB", 1024, ""])
    def test_rejects_invalid_block_size(self, block_size: object) -> None:
        with pytest.raises(ValueError, match="block_size"):
            SpeedConfig(block_size=block_size)  # ty: ignore[invalid-argument-type]  intentionally invalid type

    @pytest.mark.parametrize("block_size", ["4k", "1M", "512", "2G"])
    def test_accepts_fio_size_notation(self, block_size: str) -> None:
        assert SpeedConfig(block_size=block_size).block_size == block_size

    @pytest.mark.parametrize("depth", [0, True, 1.5])
    def test_rejects_invalid_queue_depth(self, depth: object) -> None:
        with pytest.raises(ValueError, match="queue_depth"):
            SpeedConfig(queue_depth=depth)  # ty: ignore[invalid-argument-type]  intentionally invalid type

    def test_rejects_zero_duration(self) -> None:
        with pytest.raises(ValueError, match="duration_s"):
            SpeedConfig(duration_s=0)

    @pytest.mark.parametrize("ratio", [0, 1.5, True])
    def test_rejects_invalid_pass_ratio(self, ratio: object) -> None:
        with pytest.raises(ValueError, match="speed.pass_ratio"):
            SpeedConfig(pass_ratio=ratio)  # ty: ignore[invalid-argument-type]  intentionally invalid type

    def test_rejects_zero_timeout(self) -> None:
        with pytest.raises(ValueError, match="speed.timeout_s"):
            SpeedConfig(timeout_s=0)

    @pytest.mark.parametrize("nominal", [0, -5, True])
    def test_rejects_invalid_nominal_speed(self, nominal: object) -> None:
        with pytest.raises(ValueError, match="nominal_read_mb_s"):
            SpeedConfig(nominal_read_mb_s=nominal)  # ty: ignore[invalid-argument-type]  intentionally invalid type
        with pytest.raises(ValueError, match="nominal_write_mb_s"):
            SpeedConfig(nominal_write_mb_s=nominal)  # ty: ignore[invalid-argument-type]  intentionally invalid type

    def test_nominal_defaults_to_none(self) -> None:
        config = SpeedConfig()
        assert config.nominal_read_mb_s is None
        assert config.nominal_write_mb_s is None

    def test_accepts_positive_nominal(self) -> None:
        config = SpeedConfig(nominal_read_mb_s=90.0, nominal_write_mb_s=20)
        assert config.nominal_read_mb_s == 90.0
        assert config.nominal_write_mb_s == 20

    @pytest.mark.parametrize("device_class", ["usb4_flash", "", 3])
    def test_rejects_unknown_device_class(self, device_class: object) -> None:
        """The device kind is limited to keys of speed_floor.DEVICE_FLOORS."""
        with pytest.raises(ValueError, match="speed.device_class"):
            SpeedConfig(device_class=device_class)  # ty: ignore[invalid-argument-type]  intentionally invalid type

    def test_accepts_known_device_class(self) -> None:
        assert SpeedConfig(device_class="usb3_flash").device_class == "usb3_flash"
        assert SpeedConfig().device_class is None


class TestConfig:
    @pytest.mark.parametrize("timeout", [0, -1, True])
    def test_rejects_invalid_hash_inventory_timeout(self, timeout: object) -> None:
        with pytest.raises(ValueError, match="hash_inventory_timeout_s"):
            Config(hash_inventory_timeout_s=timeout)  # ty: ignore[invalid-argument-type]  intentionally invalid type

    def test_defaults(self) -> None:
        config = Config()
        assert config.hash_inventory_timeout_s == 3600.0
        assert config.sqlite_path == Path("/var/lib/usb-sentinel/db/inspections.sqlite")


class TestLoadConfig:
    def test_empty_sqlite_path_disables_store(self, tmp_path: Path) -> None:
        """Setting sqlite_path = "" in TOML disables SQLite recording (None)."""
        path = tmp_path / "config.toml"
        path.write_text('sqlite_path = ""\n')
        assert load_config(path).sqlite_path is None

    def test_sqlite_path_becomes_path(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        db = tmp_path / "db" / "inspections.sqlite"
        path.write_text(f'sqlite_path = "{db}"\n')
        assert load_config(path).sqlite_path == db

    def test_reads_new_keys_from_toml(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(
            "hash_inventory_timeout_s = 120\n"
            "[malware]\nstale_db_is_error = true\n"
            "[capacity]\nnominal_bytes = 32000000000\nover_ratio = 1.10\n"
            '[speed]\ndevice_class = "sd_u3"\n'
        )
        config = load_config(path)
        assert config.hash_inventory_timeout_s == 120
        assert config.malware.stale_db_is_error is True
        assert config.capacity.nominal_bytes == 32_000_000_000
        assert config.capacity.over_ratio == 1.10
        assert config.speed.device_class == "sd_u3"

    def test_invalid_stale_db_is_error_in_toml(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text('[malware]\nstale_db_is_error = "true"\n')
        with pytest.raises(ValueError, match="stale_db_is_error"):
            load_config(path)

    def test_reads_nominal_speed_from_toml(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text("[speed]\nnominal_read_mb_s = 90.0\n")
        config = load_config(path)
        assert config.speed.nominal_read_mb_s == 90.0
        assert config.speed.nominal_write_mb_s is None

    def test_unknown_key_is_type_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text("[speed]\nunknown = 1\n")
        with pytest.raises(TypeError):
            load_config(path)

    def test_invalid_value_in_toml_is_value_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text("[speed]\nqueue_depth = true\n")
        with pytest.raises(ValueError, match="queue_depth"):
            load_config(path)
