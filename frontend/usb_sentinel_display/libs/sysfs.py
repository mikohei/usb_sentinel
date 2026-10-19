import os
import re
import time
from collections.abc import Callable
from pathlib import Path

from .models import AttachedDisk

# Port path of a USB device (e.g. 1-1.3). Take the element just before the interface (:1.0).
PORT_PATH_RE = re.compile(r"/(\d+-[\d.]+):\d+\.\d+/")
# Path element that marks being under a USB bus.
USB_BUS_RE = re.compile(r"/usb\d+/")
# Kernel names handled. USB mass storage is always sdX (partitions like sdX1 are excluded).
KERNEL_NAME_RE = re.compile(r"sd[a-z]{1,3}")
SERIAL_READ_LIMIT = 256


class SysfsMissingError(Exception):
    """sys_block does not exist or is not a directory."""


class SysfsPermissionError(Exception):
    """No permission to read sys_block."""


def sanitize_text(raw: bytes | str, limit: int = 256) -> str:
    """Sanitize a USB-derived string for display.

    Args:
        raw: Value read from sysfs or result.json.
        limit: Leading byte count for bytes, leading char count for str.

    Returns:
        The string with non-printable chars replaced by ? and surrounding whitespace stripped.
    """
    text = raw[:limit].decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw[:limit]
    return "".join(ch if ch.isprintable() else "?" for ch in text.strip())


def boot_time() -> float | None:
    """Return the system boot time (epoch seconds), or None where unavailable."""
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    if clock is not None:
        try:
            return time.time() - time.clock_gettime(clock)
        except OSError:
            pass
    try:
        with Path("/proc/stat").open("rb") as stream:
            for line in stream:
                if line.startswith(b"btime "):
                    return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _read_serial(port_dir: Path) -> str:
    """Read serial from the port directory. Empty string if absent."""
    try:
        with open(port_dir / "serial", "rb") as stream:  # noqa: PTH123 -- tests patch builtins.open
            return sanitize_text(stream.read(SERIAL_READ_LIMIT))
    except FileNotFoundError:
        return ""


def _disk_for(
    entry: os.DirEntry[str], fallback_time: Callable[[str], float]
) -> AttachedDisk | None:
    """Build an AttachedDisk from one /sys/block entry. None if not USB."""
    kernel_name = entry.name
    if not KERNEL_NAME_RE.fullmatch(kernel_name):
        return None
    real = os.path.realpath(entry.path, strict=True)
    if not USB_BUS_RE.search(real):
        return None
    match = PORT_PATH_RE.search(real)
    if match is None:
        return None
    port_path = str(match.group(1))
    # The part before the match is ".../1-1/1-1.3", which is normally the port directory as-is.
    prefix = Path(real[: match.start(0)])
    port_dir = prefix if prefix.name == port_path else prefix / port_path
    serial = _read_serial(port_dir)
    try:
        attached_at = os.stat(port_dir).st_mtime  # noqa: PTH116 -- tests patch os.stat
    except OSError:
        attached_at = fallback_time(kernel_name)
    return AttachedDisk(kernel_name, port_path, serial, attached_at)


def list_usb_disks(sys_block: Path, *, fallback_time: Callable[[str], float]) -> list[AttachedDisk]:
    """List /sys/block and return the disks attached via USB.

    Args:
        sys_block: Directory equivalent to /sys/block.
        fallback_time: Function that decides the connect time when st_mtime is unavailable.

    Returns:
        Disks sorted by kernel name. A per-entry OSError skips only that entry.

    Raises:
        SysfsMissingError: sys_block does not exist or is not a directory.
        SysfsPermissionError: No permission to read sys_block.
    """
    try:
        with os.scandir(sys_block) as entries:
            names = list(entries)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise SysfsMissingError(str(sys_block)) from exc
    except PermissionError as exc:
        raise SysfsPermissionError(str(sys_block)) from exc
    disks: list[AttachedDisk] = []
    for entry in names:
        try:
            disk = _disk_for(entry, fallback_time)
        except OSError:
            continue
        if disk is not None:
            disks.append(disk)
    return sorted(disks, key=lambda d: d.kernel_name)
