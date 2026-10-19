"""Industry minimum speeds per device class (per the SOP).

`config.py` uses this module to validate `device_class`, so this module imports nothing but
`dataclasses` (importing from `steps/` would create a `config` -> `steps` -> `config` cycle).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Floor:
    """Minimum speeds for one device class.

    Attributes:
        read_mb_s: Minimum read speed (MB/s, 10^6 bytes/s).
        write_mb_s: Minimum write speed (MB/s).
        label: Japanese name used in the summary.
        code: Two-character code for the QR code and SQLite.
    """

    read_mb_s: float
    write_mb_s: float
    label: str
    code: str


@dataclass(frozen=True)
class Reference:
    """One reference value compared in the speed judgment (read or write).

    Attributes:
        value: Reference value to compare against (MB/s). None if absent.
        basis: Basis of the reference: "nominal" (nominal value) / "class" (class minimum) /
            "none".
        device_class: Resolved device class key. None if unknown.
    """

    value: float | None
    basis: str
    device_class: str | None


# USB 2.0 High Speed link speed (Mbps). Only this exact value is inferred as usb2_flash.
USB2_HIGH_SPEED_MBPS = 480
# Lower bound of USB 3.x SuperSpeed link speeds (Mbps). At or above this, infer usb3_flash.
USB3_SUPER_SPEED_MBPS = 5000
# USB 3.2 Gen2 and above use 128b/132b encoding; below that (Gen1 and lower) use 8b/10b.
GEN2_MBPS = 10000


# microSD has the same values as SD, so it has no separate key; use sd_c10 and sd_u3.
DEVICE_FLOORS: dict[str, Floor] = {
    "hdd_5400": Floor(80.0, 80.0, "HDD 5400rpm", "h5"),
    "hdd_7200": Floor(120.0, 120.0, "HDD 7200rpm", "h7"),
    "sata_ssd": Floor(400.0, 400.0, "SATA SSD", "ss"),
    "nvme_ssd": Floor(500.0, 500.0, "NVMe/USB3.2 SSD", "ns"),
    "sd_c10": Floor(25.0, 10.0, "SD Class10/U1", "s1"),
    "sd_u3": Floor(90.0, 30.0, "SD U3/V30", "s3"),
    "usb2_flash": Floor(15.0, 5.0, "USB2.0フラッシュ", "u2"),
    "usb3_flash": Floor(50.0, 15.0, "USB3.0フラッシュ", "u3"),
}


def resolve_device_class(
    configured: str | None, link_speed_mbps: int | None
) -> tuple[str | None, str]:
    """Return (class key, basis). The basis is "config" / "link_speed" / "unknown".

    A configured value takes precedence. Otherwise the class is inferred from the USB link
    speed: 480 Mbps (USB 2.0 High Speed) is usb2_flash, and 5000 Mbps or more (USB 3.x) is
    usb3_flash. USB 1.1 (0 < s < 480), non-standard values (480 < s < 5000), and unknown (None)
    are not inferred.

    Args:
        configured: Value of `[speed] device_class`. None if not set.
        link_speed_mbps: Link speed read from sysfs (Mbps). None if unreadable.

    Returns:
        Pair of the class key (a DEVICE_FLOORS key or None) and the basis.
    """
    if configured is not None:
        return configured, "config"
    if link_speed_mbps is None:
        return None, "unknown"
    if link_speed_mbps == USB2_HIGH_SPEED_MBPS:
        return "usb2_flash", "link_speed"
    if link_speed_mbps >= USB3_SUPER_SPEED_MBPS:
        return "usb3_flash", "link_speed"
    return None, "unknown"


def effective_mb_s(link_speed_mbps: int) -> float:
    """Compute the effective ceiling (MB/s) from the link speed (Mbps), minus encoding loss.

    Gen1 and below (under 10000 Mbps) use 8b/10b encoding, so divide by 10. Gen2 and above use
    128b/132b encoding, so multiply by 128/132/8.

    Args:
        link_speed_mbps: Link speed (Mbps).

    Returns:
        Effective ceiling (MB/s, 10^6 bytes/s).
    """
    if link_speed_mbps < GEN2_MBPS:
        return link_speed_mbps / 10
    return link_speed_mbps * 128 / 132 / 8
