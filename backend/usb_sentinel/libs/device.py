import json
import os
import re
from pathlib import Path
from typing import Any

from .models import DeviceInfo
from .runner import CommandRunner

SYS_BLOCK = Path("/sys/block")
# `/sys/bus/usb/devices/<port_path>/speed`にリンク速度（Mbps）が10進文字列で入っている。
SYS_USB_DEVICES = Path("/sys/bus/usb/devices")
# `/sys/block/sda`のrealpath中の`.../1-1.3/1-1.3:1.0/...`からUSBポートパス`1-1.3`を取る。
PORT_PATH_RE = re.compile(r"/(\d+-[\d.]+):\d+\.\d+/")


class UnsafeDeviceError(RuntimeError):
    pass


def port_path_for(kernel_name: str, sys_block: Path | None = None) -> str | None:
    """カーネル名からUSBポートパス（例: `1-1.3`）を求める。USB経由でなければNone。

    sys_blockがNoneなら呼び出し時点のモジュール変数SYS_BLOCKを使う（テストの差し替えを効かせるため）。
    """
    root = SYS_BLOCK if sys_block is None else sys_block
    try:
        real = os.path.realpath(str(root / kernel_name))
    except OSError:
        return None
    match = PORT_PATH_RE.search(real)
    return str(match.group(1)) if match else None


def link_speed_for(port_path: str | None, sys_usb: Path | None = None) -> int | None:
    """ポートパスからUSBリンク速度（Mbps）を読む。読めなければNone。

    値は`480`・`5000`・`10000`などで、USB1.1は`12`または`1.5`。小数はint(float)で切り捨てる。
    外部コマンドを呼ばないのでCommandRunnerは通さない（port_path_for()と同じ扱い）。

    Args:
        port_path: `port_path_for()`が返すポートパス（例: `1-1.3`）。Noneなら読まない。
        sys_usb: `/sys/bus/usb/devices`の差し替え先。Noneなら呼び出し時点のSYS_USB_DEVICES。

    Returns:
        リンク速度（Mbps）。ポートパス不明、ファイル不在、数値でない場合はNone。
    """
    if port_path is None:
        return None
    root = SYS_USB_DEVICES if sys_usb is None else sys_usb
    try:
        return int(float((root / port_path / "speed").read_text().strip()))
    except (OSError, ValueError):
        return None


def block_info(runner: CommandRunner, path: str) -> dict[str, Any]:
    """lsblkでデバイスの情報（子パーティション込み）を取る。

    Args:
        runner: コマンド実行器。
        path: 対象デバイスのパス（例: `/dev/sda`）。

    Returns:
        lsblkの`blockdevices`先頭要素。

    Raises:
        RuntimeError: lsblkが失敗したとき（CommandResult.check()）。
        UnsafeDeviceError: 該当するブロックデバイスが無いとき。
        ValueError, KeyError: lsblkの出力がJSONでない、または`blockdevices`キーが無いとき。
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
    """lsblk・udevadm・sysfsからDeviceInfoを組む。デバイスには書き込まない。

    Args:
        runner: コマンド実行器。
        path: 対象デバイスのパス。

    Returns:
        デバイス情報（容量、ベンダ、シリアル、バス、ポートパス、リンク速度）。

    Raises:
        UnsafeDeviceError: ディスク全体（type=disk）でないとき。
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
    """検査対象として安全なデバイスか確かめる安全ガード。条件は緩めない。

    USBバス経由・リムーバブル・容量が非ゼロ・ルートファイルシステムを含まない、の4条件を
    すべて満たさないデバイスを拒否する。

    Args:
        runner: コマンド実行器（findmntでルートの元デバイスを調べる）。
        device: probe()が返したデバイス情報。

    Raises:
        UnsafeDeviceError: いずれかの条件を満たさないとき。
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
    """lsblkの木からファイルシステム（fstypeあり）を持つデバイスパスを深さ優先で集める。

    Args:
        info: block_info()の戻り値（または子ノード）。

    Returns:
        デバイスパスの一覧（lsblkの並び順）。
    """
    paths = [str(info["path"])] if info.get("fstype") else []
    for child in info.get("children", []):
        paths.extend(filesystems(child))
    return paths


def is_mounted(info: dict[str, Any]) -> bool:
    """デバイス本体または子パーティションのどれかがマウント中か。

    Args:
        info: block_info()の戻り値（または子ノード）。

    Returns:
        いずれかにmountpointがあればTrue。
    """
    return bool(info.get("mountpoint")) or any(
        is_mounted(child) for child in info.get("children", [])
    )
