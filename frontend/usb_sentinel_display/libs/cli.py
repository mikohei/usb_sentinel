import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

from .config import DisplayConfig, default_config_path, load_config
from .monitor import NOTICE_SYSFS_MISSING, NOTICE_SYSFS_PERMISSION
from .sysfs import SysfsMissingError, SysfsPermissionError, boot_time, list_usb_disks

# ui/__init__.pyは空なので、i18nのimportでGUIライブラリ（tkinter）は読み込まれない。
from .ui.i18n import text
from .units import Runner, SubprocessRunner


def build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数の定義を組み立てる。

    Returns:
        run／calibrateサブコマンドを持つパーサ。
    """
    parser = argparse.ArgumentParser(
        prog="usb-sentinel-display", description="USB Sentinel表示フロントエンド"
    )
    parser.add_argument("--config", type=Path, default=None, help="設定ファイル（display.toml）")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="画面を表示する")
    run.add_argument("--results-dir", type=Path, default=None, help="結果ルートの上書き")
    run.add_argument("--sys-block", type=Path, default=None, help="/sys/blockの上書き")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--windowed", action="store_true", help="480x320のウィンドウで表示する")
    mode.add_argument("--kiosk", action="store_true", help="全画面でCtrl+Qを無効にする")
    run.add_argument("--no-systemctl", action="store_true", help="systemctlを呼ばない")

    calibrate = commands.add_parser("calibrate", help="接続したUSBのポートパスを印字する")
    calibrate.add_argument("--sys-block", type=Path, default=None, help="/sys/blockの上書き")
    calibrate.add_argument("--interval", type=float, default=1.0, help="確認間隔（秒）")
    return parser


def _apply_overrides(config: DisplayConfig, args: argparse.Namespace) -> DisplayConfig:
    """CLIで指定された値で設定を上書きする。"""
    overrides: dict[str, Path] = {}
    if getattr(args, "results_dir", None) is not None:
        overrides["results_dir"] = args.results_dir
    if getattr(args, "sys_block", None) is not None:
        overrides["sys_block"] = args.sys_block
    return replace(config, **overrides) if overrides else config


def calibrate(config: DisplayConfig, interval: float) -> int:
    """USBを挿すたびに「カーネル名 ポートパス シリアル」を印字する。Ctrl+Cで終わる。

    Args:
        config: sys_blockを使う。
        interval: 確認間隔（秒）。

    Returns:
        終了コード（常に0）。
    """
    seen: set[tuple[str, str, float]] = set()
    first_seen: dict[str, float] = {}

    def fallback_time(kernel_name: str) -> float:
        """st_mtimeが取れないときの接続時刻。起動時刻、無ければ初見時刻。"""
        booted = boot_time()
        return booted if booted is not None else first_seen.setdefault(kernel_name, time.time())

    try:
        while True:
            try:
                disks = list_usb_disks(config.sys_block, fallback_time=fallback_time)
            except SysfsMissingError:
                print(text(config.language, "notice." + NOTICE_SYSFS_MISSING), file=sys.stderr)
                disks = []
            except SysfsPermissionError:
                print(text(config.language, "notice." + NOTICE_SYSFS_PERMISSION), file=sys.stderr)
                disks = []
            for disk in disks:
                key = (disk.kernel_name, disk.port_path, disk.attached_at)
                if key not in seen:
                    seen.add(key)
                    print(f"{disk.kernel_name} {disk.port_path} {disk.serial}", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。

    Args:
        argv: コマンドライン引数。Noneならsys.argvを使う。

    Returns:
        終了コード。設定のValueError（TOML構文エラーを含む）と読み込みのOSErrorは1。
    """
    args = build_parser().parse_args(argv)
    config_path = args.config if args.config is not None else default_config_path()
    try:
        config = _apply_overrides(load_config(config_path), args)
    except (ValueError, OSError) as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 1
    if args.command == "calibrate":
        return calibrate(config, args.interval)
    # calibrateがGUIライブラリを読み込まないよう、ui層はrunの分岐内で遅延importする。
    from .ui.app import AppOptions, run_app

    runner: Runner | None = None if args.no_systemctl else SubprocessRunner()
    return run_app(config, AppOptions(windowed=args.windowed, kiosk=args.kiosk), runner)
