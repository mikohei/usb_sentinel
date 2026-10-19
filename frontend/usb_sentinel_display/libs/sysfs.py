import os
import re
import time
from collections.abc import Callable
from pathlib import Path

from .models import AttachedDisk

# USBデバイスのポートパス（例: 1-1.3）。インタフェース（:1.0）の直前の要素を取る。
PORT_PATH_RE = re.compile(r"/(\d+-[\d.]+):\d+\.\d+/")
# USBバス配下であることを示すパス要素。
USB_BUS_RE = re.compile(r"/usb\d+/")
# 扱うカーネル名。USB大容量ストレージは常にsdX（パーティションのsdX1は対象外）。
KERNEL_NAME_RE = re.compile(r"sd[a-z]{1,3}")
SERIAL_READ_LIMIT = 256


class SysfsMissingError(Exception):
    """sys_blockが存在しない、またはディレクトリでない。"""


class SysfsPermissionError(Exception):
    """sys_blockを読む権限がない。"""


def sanitize_text(raw: bytes | str, limit: int = 256) -> str:
    """USB由来の文字列を表示用に無害化する。

    Args:
        raw: sysfsやresult.jsonから読んだ値。
        limit: bytesなら先頭バイト数、strなら先頭文字数。

    Returns:
        印字可能でない文字を?に置き換え、前後の空白を落とした文字列。
    """
    text = raw[:limit].decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw[:limit]
    return "".join(ch if ch.isprintable() else "?" for ch in text.strip())


def boot_time() -> float | None:
    """システム起動時刻（エポック秒）を返す。取れない環境ではNone。"""
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
    """ポートディレクトリのserialを読む。無ければ空文字。"""
    try:
        with open(port_dir / "serial", "rb") as stream:  # noqa: PTH123 -- テストでbuiltins.openを差し替えるため
            return sanitize_text(stream.read(SERIAL_READ_LIMIT))
    except FileNotFoundError:
        return ""


def _disk_for(
    entry: os.DirEntry[str], fallback_time: Callable[[str], float]
) -> AttachedDisk | None:
    """/sys/blockの1エントリからAttachedDiskを作る。USBでなければNone。"""
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
    # 一致位置の直前までが「.../1-1/1-1.3」なので、通常はそのままポートディレクトリになる。
    prefix = Path(real[: match.start(0)])
    port_dir = prefix if prefix.name == port_path else prefix / port_path
    serial = _read_serial(port_dir)
    try:
        attached_at = os.stat(port_dir).st_mtime  # noqa: PTH116 -- テストでos.statを差し替えるため
    except OSError:
        attached_at = fallback_time(kernel_name)
    return AttachedDisk(kernel_name, port_path, serial, attached_at)


def list_usb_disks(sys_block: Path, *, fallback_time: Callable[[str], float]) -> list[AttachedDisk]:
    """/sys/blockを列挙してUSB経由のディスクを返す。

    Args:
        sys_block: /sys/block相当のディレクトリ。
        fallback_time: ポートディレクトリのst_mtimeが取れないときに接続時刻を決める関数。

    Returns:
        カーネル名順のディスク一覧。エントリ単位のOSErrorはそのエントリだけ無視する。

    Raises:
        SysfsMissingError: sys_blockが無い、またはディレクトリでない。
        SysfsPermissionError: sys_blockを読む権限がない。
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
