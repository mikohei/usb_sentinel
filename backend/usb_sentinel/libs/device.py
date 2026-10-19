import json
import os
import re
from pathlib import Path
from typing import Any

from .models import DeviceInfo
from .runner import CommandRunner

SYS_BLOCK = Path("/sys/block")
# `/sys/bus/usb/devices/<port_path>/speed` holds the link speed (Mbps) as a decimal string.
SYS_USB_DEVICES = Path("/sys/bus/usb/devices")
# Extract the USB port path `1-1.3` from `.../1-1.3/1-1.3:1.0/...` in the realpath of
# `/sys/block/sda`.
PORT_PATH_RE = re.compile(r"/(\d+-[\d.]+):\d+\.\d+/")


class UnsafeDeviceError(RuntimeError):
    pass


def port_path_for(kernel_name: str, sys_block: Path | None = None) -> str | None:
    """Find the USB port path (e.g. `1-1.3`) from the kernel name. None if not on USB.

    If sys_block is None, the module variable SYS_BLOCK is read at call time (so that test
    patches take effect).
    """
    root = SYS_BLOCK if sys_block is None else sys_block
    try:
        real = os.path.realpath(str(root / kernel_name))
    except OSError:
        return None
    match = PORT_PATH_RE.search(real)
    return str(match.group(1)) if match else None


def link_speed_for(port_path: str | None, sys_usb: Path | None = None) -> int | None:
    """Read the USB link speed (Mbps) from the port path. None if unreadable.

    Values are like `480`, `5000`, `10000`; USB 1.1 is `12` or `1.5`. Fractions are truncated
    with int(float). No external command is run, so CommandRunner is not used (same as
    port_path_for()).

    Args:
        port_path: Port path returned by `port_path_for()` (e.g. `1-1.3`). Not read if None.
        sys_usb: Replacement for `/sys/bus/usb/devices`. If None, SYS_USB_DEVICES at call time.

    Returns:
        Link speed (Mbps). None if the port path is unknown, the file is missing, or the value
        is not numeric.
    """
    if port_path is None:
        return None
    root = SYS_USB_DEVICES if sys_usb is None else sys_usb
    try:
        return int(float((root / port_path / "speed").read_text().strip()))
    except (OSError, ValueError):
        return None


def block_info(runner: CommandRunner, path: str) -> dict[str, Any]:
    """Get device information (including child partitions) with lsblk.

    Args:
        runner: Command runner.
        path: Path of the target device (e.g. `/dev/sda`).

    Returns:
        The first element of lsblk's `blockdevices`.

    Raises:
        RuntimeError: If lsblk fails (CommandResult.check()).
        UnsafeDeviceError: If there is no matching block device.
        ValueError, KeyError: If lsblk output is not JSON or lacks the `blockdevices` key.
    """
    output = runner.run(
        [
            "lsblk",
            "--json",
            "--bytes",
            "--output",
            "NAME,PATH,SIZE,VENDOR,MODEL,SERIAL,RM,TYPE,FSTYPE,MOUNTPOINT",
            path,
        ]
    ).check()
    devices: list[dict[str, Any]] = json.loads(output.stdout)["blockdevices"]
    if not devices:
        raise UnsafeDeviceError("no such block device")
    return devices[0]


def probe(runner: CommandRunner, path: str) -> DeviceInfo:
    """Build DeviceInfo from lsblk, udevadm, and sysfs. Nothing is written to the device.

    Args:
        runner: Command runner.
        path: Path of the target device.

    Returns:
        Device information (capacity, vendor, serial, bus, port path, link speed).

    Raises:
        UnsafeDeviceError: If it is not a whole disk (type=disk).
    """
    info = block_info(runner, path)
    if info["type"] != "disk":
        raise UnsafeDeviceError("must be a whole disk")
    props = runner.run(["udevadm", "info", "--query=property", f"--name={path}"])
    properties = (
        dict(line.split("=", 1) for line in props.stdout.splitlines() if "=" in line)
        if props.ok
        else {}
    )
    port_path = port_path_for(info["name"])
    return DeviceInfo(
        path=path,
        kernel_name=info["name"],
        advertised_bytes=int(info.get("size") or 0),
        vendor=(info.get("vendor") or "").strip(),
        model=(info.get("model") or "").strip(),
        serial=(info.get("serial") or "").strip(),
        removable=bool(info.get("rm")),
        bus=properties.get("ID_BUS"),
        port_path=port_path,
        link_speed_mbps=link_speed_for(port_path),
    )


def ensure_safe(runner: CommandRunner, device: DeviceInfo) -> None:
    """Safety guard that checks whether the device is safe to inspect. Never relax it.

    Rejects any device that does not meet all four conditions: on the USB bus, removable,
    non-zero capacity, and not containing the root filesystem.

    Args:
        runner: Command runner (findmnt is used to find the root's source device).
        device: Device information returned by probe().

    Raises:
        UnsafeDeviceError: If any condition is not met.
    """
    if device.bus != "usb":
        raise UnsafeDeviceError("not on the USB bus")
    removable = SYS_BLOCK / device.kernel_name / "removable"
    if not device.removable and not (removable.exists() and removable.read_text().strip() == "1"):
        raise UnsafeDeviceError("not a removable device")
    if device.advertised_bytes <= 0:
        raise UnsafeDeviceError("zero capacity")
    root = runner.run(["findmnt", "--noheadings", "--output", "SOURCE", "/"])
    if root.ok:
        source = root.stdout.strip()
        if source == device.path or (SYS_BLOCK / device.kernel_name / Path(source).name).exists():
            raise UnsafeDeviceError("device contains root filesystem")


def filesystems(info: dict[str, Any]) -> list[str]:
    """Collect, depth first, device paths with a filesystem (fstype set) from the lsblk tree.

    Args:
        info: Return value of block_info() (or a child node).

    Returns:
        List of device paths (in lsblk order).
    """
    paths = [str(info["path"])] if info.get("fstype") else []
    for child in info.get("children", []):
        paths.extend(filesystems(child))
    return paths


def is_mounted(info: dict[str, Any]) -> bool:
    """Whether the device itself or any child partition is mounted.

    Args:
        info: Return value of block_info() (or a child node).

    Returns:
        True if any of them has a mountpoint.
    """
    return bool(info.get("mountpoint")) or any(
        is_mounted(child) for child in info.get("children", [])
    )
