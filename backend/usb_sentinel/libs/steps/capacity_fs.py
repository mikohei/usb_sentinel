"""rw mount and f3 output parsers for the capacity step (f3write mode).

The rw mount is only for the capacity test and must not be brought into `mounter.py`
(read-only only). The `noexec,nosuid,nodev` options must not be removed.
"""

import os
import re
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import StepContext

# f3 sectors are always 512 bytes.
SECTOR_BYTES = 512

# Factors converting f3 speed notation (decimal) to MB/s.
SPEED_UNIT_TO_MB = {"Byte": 1e-6, "KB": 1e-3, "MB": 1.0, "GB": 1e3, "TB": 1e6}

# File names created by f3write. Unrelated files on the sample are never deleted.
H2W_PATTERN = re.compile(r"^\d+\.h2w$")

_SPEED_UNITS = r"(Byte|KB|MB|GB|TB)/s"
_WRITE_SPEED = re.compile(r"Average writing speed: ([\d.]+) " + _SPEED_UNITS)
_READ_SPEED = re.compile(r"Average reading speed: ([\d.]+) " + _SPEED_UNITS)
_DATA_OK = re.compile(r"Data OK: .*\((\d+) sectors\)")
_DATA_LOST = re.compile(r"Data LOST: .*\((\d+) sectors\)")
_CORRUPTED = re.compile(r"Corrupted: .*\((\d+) sectors\)")
_SLIGHTLY_CHANGED = re.compile(r"Slightly changed: .*\((\d+) sectors\)")
_OVERWRITTEN = re.compile(r"Overwritten: .*\((\d+) sectors\)")


class MountpointError(RuntimeError):
    """Creating (mkdir) or inspecting (statvfs) the mount point failed."""


class MountError(RuntimeError):
    """mount -w failed. The message is stderr. umount has not been called."""


class NotWritableError(RuntimeError):
    """The mount succeeded but ST_RDONLY is set (cannot be mounted writable)."""


class UnmountError(RuntimeError):
    """umount failed. The mount remains."""


@dataclass
class F3ReadSummary:
    """Summary extracted from f3read stdout."""

    ok_sectors: int
    lost_sectors: int
    corrupted_sectors: int = 0
    slightly_changed_sectors: int = 0
    overwritten_sectors: int = 0
    read_mb_s: float | None = None

    @property
    def bad_sectors(self) -> int:
        """Total of non-OK sectors."""
        return (
            self.lost_sectors
            + self.corrupted_sectors
            + self.slightly_changed_sectors
            + self.overwritten_sectors
        )


def _speed_mb_s(pattern: re.Pattern[str], text: str) -> float | None:
    """Find the speed line and convert it to MB/s. None if absent."""
    match = pattern.search(text)
    if match is None:
        return None
    return float(match.group(1)) * SPEED_UNIT_TO_MB[match.group(2)]


def _optional_sectors(pattern: re.Pattern[str], text: str) -> int:
    """Read an optional sector-count line. 0 if absent."""
    match = pattern.search(text)
    return int(match.group(1)) if match else 0


def parse_f3write(text: str) -> float | None:
    """Get the write speed (MB/s) from f3write stdout. None if there is no speed line."""
    return _speed_mb_s(_WRITE_SPEED, text)


def parse_f3read(text: str) -> F3ReadSummary:
    """Parse f3read stdout.

    Raises:
        ValueError: If the `Data OK` or `Data LOST` line is missing.
    """
    ok = _DATA_OK.search(text)
    lost = _DATA_LOST.search(text)
    if ok is None or lost is None:
        raise ValueError("f3readの出力を解析できない")
    return F3ReadSummary(
        ok_sectors=int(ok.group(1)),
        lost_sectors=int(lost.group(1)),
        corrupted_sectors=_optional_sectors(_CORRUPTED, text),
        slightly_changed_sectors=_optional_sectors(_SLIGHTLY_CHANGED, text),
        overwritten_sectors=_optional_sectors(_OVERWRITTEN, text),
        read_mb_s=_speed_mb_s(_READ_SPEED, text),
    )


def usage(statvfs: Callable[[str], os.statvfs_result], target: Path) -> tuple[int, int]:
    """Return (used bytes, free bytes) of the mount point.

    Raises:
        MountpointError: If statvfs raises OSError.
    """
    try:
        stat = statvfs(str(target))
    except OSError as exc:
        raise MountpointError("マウントポイントを調べられない") from exc
    return (stat.f_blocks - stat.f_bfree) * stat.f_frsize, stat.f_bavail * stat.f_frsize


def remove_h2w(target: Path, warnings: list[str]) -> int:
    """Delete <digits>.h2w directly under target and return the count.

    An OSError while deleting is recorded in warnings and processing continues. The warning
    that previous files were deleted is not emitted here (the caller's responsibility).
    """
    removed = 0
    try:
        entries = sorted(target.iterdir())
    except OSError as exc:
        warnings.append(f"{target}を走査できない: {exc.strerror}")
        return 0
    for path in entries:
        if not H2W_PATTERN.match(path.name):
            continue
        try:
            path.unlink()
        except OSError as exc:
            warnings.append(f"{path.name}を削除できない: {exc.strerror}")
            continue
        removed += 1
    return removed


@contextmanager
def rw_mounted(
    ctx: StepContext,
    device_path: str,
    index: int,
    statvfs: Callable[[str], os.statvfs_result],
    warnings: list[str],
    commands: list[list[str]],
) -> Generator[Path]:
    """rw-mount device_path and yield the mount point.

    warnings and commands are the caller's (run()) lists, received as-is and appended to.
    The mount / umount argv is recorded in commands right before runner.run() is called.
    After a successful mount, cleanup always runs in the order .h2w deletion -> umount, whether
    the body exits via an exception or a return. A umount failure propagates as UnmountError,
    with any in-flight exception kept in __cause__.
    """
    target = ctx.config.mount_root / ctx.device.kernel_name / "rw" / str(index)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MountpointError(f"マウントポイントを作れない: {exc.strerror}") from exc
    mount_argv = ["mount", "-w", "-o", "noexec,nosuid,nodev", device_path, str(target)]
    commands.append(mount_argv)
    mount = ctx.runner.run(mount_argv)
    if not mount.ok:
        raise MountError(mount.stderr or f"終了コード{mount.returncode}")
    pending: BaseException | None = None
    try:
        try:
            flags = statvfs(str(target)).f_flag
        except OSError as exc:
            raise MountpointError("マウントポイントを調べられない") from exc
        if flags & os.ST_RDONLY:
            raise NotWritableError("書き込み可能でマウントできない")
        yield target
    except BaseException as exc:
        # contextmanager throws the with-body's exception in at the yield. Keep it for chaining
        # on umount failure; it is always re-raised, so nothing is swallowed.
        pending = exc
        raise
    finally:
        remove_h2w(target, warnings)
        umount_argv = ["umount", str(target)]
        commands.append(umount_argv)
        umount = ctx.runner.run(umount_argv)
        if not umount.ok:
            raise UnmountError(umount.stderr or f"終了コード{umount.returncode}") from pending
