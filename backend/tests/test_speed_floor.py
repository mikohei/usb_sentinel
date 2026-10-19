"""Industry minimum speed table and device kind resolution (speed_floor).

The table values are fixed by the SOP. When changing them, update this test's expected values
as well.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from usb_sentinel.libs import speed_floor
from usb_sentinel.libs.speed_floor import (
    DEVICE_FLOORS,
    Floor,
    Reference,
    effective_mb_s,
    resolve_device_class,
)


class TestFloors:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("hdd_5400", Floor(80.0, 80.0, "HDD 5400rpm", "h5")),
            ("hdd_7200", Floor(120.0, 120.0, "HDD 7200rpm", "h7")),
            ("sata_ssd", Floor(400.0, 400.0, "SATA SSD", "ss")),
            ("nvme_ssd", Floor(500.0, 500.0, "NVMe/USB3.2 SSD", "ns")),
            ("sd_c10", Floor(25.0, 10.0, "SD Class10/U1", "s1")),
            ("sd_u3", Floor(90.0, 30.0, "SD U3/V30", "s3")),
            ("usb2_flash", Floor(15.0, 5.0, "USB2.0 flash", "u2")),
            ("usb3_flash", Floor(50.0, 15.0, "USB3.0 flash", "u3")),
        ],
    )
    def test_table_matches_sop(self, key: str, expected: Floor) -> None:
        """Read, write, label, and code for all 8 kinds match section 4.2 of the design doc."""
        assert DEVICE_FLOORS[key] == expected

    def test_exactly_eight_classes(self) -> None:
        assert len(DEVICE_FLOORS) == 8

    def test_codes_are_unique_two_letters(self) -> None:
        codes = [floor.code for floor in DEVICE_FLOORS.values()]
        assert len(set(codes)) == len(codes)
        assert all(len(code) == 2 for code in codes)


class TestResolveDeviceClass:
    def test_config_takes_priority(self) -> None:
        """If configured, use the setting regardless of link speed."""
        assert resolve_device_class("nvme_ssd", 480) == ("nvme_ssd", "config")
        assert resolve_device_class("sd_c10", None) == ("sd_c10", "config")

    @pytest.mark.parametrize(
        ("link", "expected"),
        [
            (480, ("usb2_flash", "link_speed")),
            (5000, ("usb3_flash", "link_speed")),
            (10000, ("usb3_flash", "link_speed")),
            (20000, ("usb3_flash", "link_speed")),
        ],
    )
    def test_link_speed_infers_flash(self, link: int, expected: tuple[str, str]) -> None:
        assert resolve_device_class(None, link) == expected

    @pytest.mark.parametrize("link", [None, 12, 1, 0, 481, 4999])
    def test_unknown_when_no_basis(self, link: int | None) -> None:
        """USB 1.1, non-standard values, and unknown all map to (None, "unknown")."""
        assert resolve_device_class(None, link) == (None, "unknown")


class TestEffectiveMbS:
    def test_gen1_uses_8b10b(self) -> None:
        assert effective_mb_s(5000) == 500.0
        assert effective_mb_s(480) == 48.0

    def test_gen2_uses_128b132b(self) -> None:
        assert abs(effective_mb_s(10000) - 1212.12) < 0.1
        assert abs(effective_mb_s(20000) - 2424.24) < 0.1


class TestReference:
    def test_is_frozen_and_hashable(self) -> None:
        """Being frozen, equal values compare equal and are hashable (usable as dict keys)."""
        ref = Reference(50.0, "class", "usb3_flash")
        assert ref == Reference(50.0, "class", "usb3_flash")
        assert hash(ref) == hash(Reference(50.0, "class", "usb3_flash"))

    def test_fields(self) -> None:
        ref = Reference(None, "none", None)
        assert (ref.value, ref.basis, ref.device_class) == (None, "none", None)


def test_module_imports_only_dataclasses() -> None:
    """Referenced from config.py, so it must not import other modules in usb_sentinel."""
    tree = ast.parse(inspect.getsource(speed_floor))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports are not allowed"
            imported.append(node.module or "")
    assert imported
    for name in imported:
        assert name == "dataclasses", f"disallowed import: {name}"
