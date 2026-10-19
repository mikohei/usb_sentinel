"""Appを--windowed相当で起動し、一定時間後にスクリーンショットを撮る（plan.md D16）。

使い方: uv run python scripts/dev-screenshot.py --fixtures OUT --scene a|b --select N --out PNG
systemctlはFakeRunnerで差し替え、sdcのユニットだけactivatingを返す。screencaptureが失敗
したら各Canvasのpostscriptに落とす。
"""

import argparse
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from usb_sentinel_display.libs.config import load_config
from usb_sentinel_display.libs.ui.app import App, AppOptions
from usb_sentinel_display.libs.units import SHOW_ARGV, CommandOutput

DEFAULT_FIXTURES = Path("/tmp/usb-sentinel-fixtures")  # noqa: S108 -- 開発機専用の既定入力先
INSPECTING_KERNELS = frozenset({"sdc"})
WINDOW_POSITION = "+100+100"


class ShowRunner:
    """systemctl showの出力を偽装するRunner。sdcのユニットだけactivating。"""

    def __init__(self) -> None:
        """呼び出し記録を空で始める。"""
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], *, timeout_s: float) -> CommandOutput:
        """argvに並んだユニット名ごとにId/ActiveStateのブロックを返す。"""
        self.calls.append(list(argv))
        if tuple(argv[: len(SHOW_ARGV)]) != SHOW_ARGV:
            return CommandOutput(1, "", False)
        blocks: list[str] = []
        for unit in argv[len(SHOW_ARGV) :]:
            kernel = unit.split("@", 1)[-1].removesuffix(".service")
            state = "activating" if kernel in INSPECTING_KERNELS else "inactive"
            blocks.append(f"Id={unit}\nActiveState={state}\n")
        return CommandOutput(0, "\n".join(blocks), False)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """コマンドライン引数を解析する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--scene", choices=["a", "b", "c"], default="a")
    parser.add_argument("--select", type=int, choices=[1, 2, 3, 4], default=1)
    parser.add_argument("--out", type=Path, required=True, help="出力PNG")
    parser.add_argument("--after-ms", type=int, default=12000, help="撮影までの待ち時間")
    parser.add_argument("--geometry", default=None, help="WxH（省略時480x320）")
    parser.add_argument("--results-dir", type=Path, default=None, help="結果ルートの上書き")
    parser.add_argument("--sys-block", type=Path, default=None, help="sys_blockの上書き")
    parser.add_argument(
        "--unlink", default=None, help="after_ms/2経過時にOUT/sys_block/KERNELのsymlinkを外す"
    )
    parser.add_argument(
        "--language",
        choices=["en", "ja"],
        default=None,
        help="起動時の表示言語（記録は上書きしない）",
    )
    parser.add_argument(
        "--qr-mode", type=int, choices=[0, 1, 2, 3], default=0, help="0=通常QR、1〜3=詳細QRの枚目"
    )
    return parser.parse_args(argv)


def shoot(app: App, out: Path) -> None:
    """ウィンドウの位置から480x320をscreencaptureで撮り、失敗ならpostscriptに落とす。"""
    root = app.root
    root.update_idletasks()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    region = f"{x},{y},{root.winfo_width()},{root.winfo_height()}"
    try:
        # macOS標準のscreencaptureをPATHから呼ぶ。
        argv = ["screencapture", "-x", "-R", region, str(out)]  # noqa: S607
        completed = subprocess.run(argv, check=False)
        code = completed.returncode
    except FileNotFoundError:
        code = 127
    size = out.stat().st_size if out.exists() else 0
    if code != 0 or size == 0:
        left = Path(f"{out}.left.ps")
        right = Path(f"{out}.right.ps")
        app.left_canvas.postscript(file=str(left), colormode="color")
        app.right_canvas.postscript(file=str(right), colormode="color")
        print(
            f"screencapture失敗（終了コード{code}、{size}バイト）。{left}と{right}に落とした",
            file=sys.stderr,
        )
    else:
        print(f"saved: {out} ({size} bytes)")


def unlink_fixture(fixtures: Path, kernel: str) -> None:
    """フィクスチャ内の自作symlinkだけを外して抜去を再現する。"""
    link = fixtures / "sys_block" / kernel
    if link.is_symlink():
        link.unlink()
        print(f"unlinked: {link}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。"""
    args = parse_args(argv)
    config = load_config(args.fixtures / "display.toml")
    config = replace(
        config,
        results_dir=args.results_dir or args.fixtures / "results",
        sys_block=args.sys_block or args.fixtures / "sys_block",
    )
    app = App(config, AppOptions(windowed=True, kiosk=False), runner=ShowRunner())
    # 画面端で切れないよう、ウィンドウの位置を固定する。
    app.root.geometry(f"{args.geometry or '480x320'}{WINDOW_POSITION}")
    if args.language is not None:
        # 記録ファイルに触らず、この起動だけ言語を固定する。
        app.language = args.language
    app.qr_mode = args.qr_mode
    app.select(args.select - 1)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    def finish() -> None:
        """撮影してウィンドウを閉じる（after_ms後に1回呼ばれる）。"""
        shoot(app, args.out)
        app.root.destroy()

    if args.unlink:
        app.root.after(args.after_ms // 2, unlink_fixture, args.fixtures, args.unlink)
    app.root.after(args.after_ms, finish)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
