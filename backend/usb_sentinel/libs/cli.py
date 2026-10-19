import argparse
import sys
from pathlib import Path

from .config import load_config
from .device import UnsafeDeviceError
from .models import Verdict
from .orchestrator import inspect
from .report import render_text, write_all
from .report.json_report import digest
from .runner import SubprocessRunner
from .store import store_for


def main() -> int:
    """`usb-sentinel [--config PATH] {selftest | inspect DEVICE}`の入口。

    Returns:
        終了コード。inspectは判定がNo Defectなら0、それ以外は1。検査拒否（UnsafeDeviceError）は2、
        実行エラーは3。selftestは必要なコマンドとClamAV定義DBが揃っていれば0、欠けていれば3。
    """
    parser = argparse.ArgumentParser(description="USB 検査バックエンド")
    parser.add_argument("--config", type=Path, default=Path("/etc/usb-sentinel/config.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("selftest")
    inspection = commands.add_parser("inspect")
    inspection.add_argument("device")
    args = parser.parse_args()
    runner = SubprocessRunner()
    try:
        if args.command == "selftest":
            missing = False
            for command in (
                "clamscan",
                "f3probe",
                "f3write",
                "f3read",
                "fio",
                "mount",
                "umount",
                "lsblk",
                "udevadm",
                "findmnt",
            ):
                found = runner.which(command)
                print(f"{' OK ' if found else 'MISS'} {command}")
                missing |= found is None
            version = runner.run(["clamscan", "--version"])
            print(version.stdout or version.stderr)
            missing |= not version.ok or "/" not in version.stdout
            return 3 if missing else 0
        config = load_config(args.config)
        # 開始記録（inspect()内）と完了記録で同じstoreを使う。
        store = store_for(config)
        result = inspect(runner, config, args.device, store)
        output = Path(result.output_dir)
        try:
            write_all(result, output, emit_qr=config.emit_qr)
        finally:
            # 正本result.jsonが書けていれば、QR生成の成否を問わず完了記録を残す。
            if (output / "result.json").exists():
                failure = store.record_completed(result, digest(result))
                if failure:
                    print(f"SQLite記録失敗（完了）: {failure}", file=sys.stderr)
        print(render_text(result))
        return 0 if result.verdict == Verdict.NO_DEFECT else 1
    except UnsafeDeviceError as exc:
        print(f"検査拒否: {exc}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        print(f"実行エラー: {exc}", file=sys.stderr)
        return 3
