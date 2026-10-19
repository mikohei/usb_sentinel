"""容量ステップ（f3writeモード）のrwマウントとf3出力のパーサ。

rwマウントは容量検査専用で、`mounter.py`（read-only専用）には持ち込まない。
オプション`noexec,nosuid,nodev`は外さない。
"""

import os
import re
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import StepContext

# f3のセクタは512バイト固定。
SECTOR_BYTES = 512

# f3の速度表記（十進）をMB/sに揃える係数。
SPEED_UNIT_TO_MB = {"Byte": 1e-6, "KB": 1e-3, "MB": 1.0, "GB": 1e3, "TB": 1e6}

# f3writeが作るファイル名。検体の無関係なファイルは消さない。
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
    """マウントポイントの作成（mkdir）または調査（statvfs）に失敗した。"""


class MountError(RuntimeError):
    """mount -wが失敗した。メッセージはstderr。umountは呼ばれていない。"""


class NotWritableError(RuntimeError):
    """マウントはできたがST_RDONLYが立っている（書き込み可能でマウントできない）。"""


class UnmountError(RuntimeError):
    """umountに失敗した。マウントが残っている。"""


@dataclass
class F3ReadSummary:
    """f3readのstdoutから取り出した集計。"""

    ok_sectors: int
    lost_sectors: int
    corrupted_sectors: int = 0
    slightly_changed_sectors: int = 0
    overwritten_sectors: int = 0
    read_mb_s: float | None = None

    @property
    def bad_sectors(self) -> int:
        """非OKセクタの合計。"""
        return (
            self.lost_sectors
            + self.corrupted_sectors
            + self.slightly_changed_sectors
            + self.overwritten_sectors
        )


def _speed_mb_s(pattern: re.Pattern[str], text: str) -> float | None:
    """速度行を探してMB/sに換算する。行が無ければNone。"""
    match = pattern.search(text)
    if match is None:
        return None
    return float(match.group(1)) * SPEED_UNIT_TO_MB[match.group(2)]


def _optional_sectors(pattern: re.Pattern[str], text: str) -> int:
    """任意のセクタ数行を取る。無ければ0。"""
    match = pattern.search(text)
    return int(match.group(1)) if match else 0


def parse_f3write(text: str) -> float | None:
    """f3writeのstdoutから書き込み速度（MB/s）を取る。速度行が無ければNone。"""
    return _speed_mb_s(_WRITE_SPEED, text)


def parse_f3read(text: str) -> F3ReadSummary:
    """f3readのstdoutを解析する。

    Raises:
        ValueError: `Data OK`行または`Data LOST`行が無いとき。
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
    """マウントポイントの（既使用バイト数, 空きバイト数）を返す。

    Raises:
        MountpointError: statvfsがOSErrorを出したとき。
    """
    try:
        stat = statvfs(str(target))
    except OSError as exc:
        raise MountpointError("マウントポイントを調べられない") from exc
    return (stat.f_blocks - stat.f_bfree) * stat.f_frsize, stat.f_bavail * stat.f_frsize


def remove_h2w(target: Path, warnings: list[str]) -> int:
    """target直下の<数字>.h2wを削除して件数を返す。

    削除時のOSErrorはwarningsに残して続行する。「前回のファイルを削除した」旨の
    警告はここでは出さない（呼び出し側の責務）。
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
    """device_pathをrwマウントしてマウントポイントを返す。

    warningsとcommandsは呼び出し側（run()）のリストをそのまま受け取り、追記する。
    commandsにはmount / umountのargvを、runner.run()を呼ぶ直前に記録する。
    mount成功後は、本体が例外で抜けてもreturnで抜けても、.h2w削除→umountの順で
    必ず後片付けする。umount失敗はUnmountErrorとして伝え、進行中の例外は__cause__に残す。
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
        # contextmanagerはwith本体の例外をyield位置へ送り込む。umount失敗時の連鎖用に保持し、
        # 必ず再送出するので握りつぶしにはならない。
        pending = exc
        raise
    finally:
        remove_h2w(target, warnings)
        umount_argv = ["umount", str(target)]
        commands.append(umount_argv)
        umount = ctx.runner.run(umount_argv)
        if not umount.ok:
            raise UnmountError(umount.stderr or f"終了コード{umount.returncode}") from pending
