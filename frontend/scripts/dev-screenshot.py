"""Launch the App in a --windowed style and take a screenshot after a fixed delay (plan.md D16).

Usage: uv run python scripts/dev-screenshot.py --fixtures OUT --scene a|b|c --select N --out PNG
       [--mode-scene none|scan_only|confirm|full|locked|lockout] [--code 3142]
systemctl is replaced with a fake runner that reports only the sdc unit as activating.
XDG_STATE_HOME is pointed at OUT/state so the language and strike files of the fixtures are
used. If screencapture fails, each Canvas (and the code dialog when shown) is dumped to
PostScript instead.
"""

import argparse
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

# Sibling helper module (scripts/ is not a package).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dev_mode_scenes import DEFAULT_CODE, SCENES, code_indices, run_scene  # noqa: E402

from usb_sentinel_display.libs.config import load_config  # noqa: E402
from usb_sentinel_display.libs.ui.app import App, AppOptions  # noqa: E402
from usb_sentinel_display.libs.units import SHOW_ARGV, CommandOutput  # noqa: E402

DEFAULT_FIXTURES = Path("/tmp/usb-sentinel-fixtures")  # noqa: S108 -- default input location, development machines only
INSPECTING_KERNELS = frozenset({"sdc"})
WINDOW_POSITION = "+100+100"


class ShowRunner:
    """Runner that fakes the output of systemctl show. Only the sdc unit is activating."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], *, timeout_s: float) -> CommandOutput:
        """Return an Id/ActiveState block for each unit name in argv."""
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
    """Parse the command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--scene", choices=["a", "b", "c"], default="a")
    parser.add_argument("--select", type=int, choices=[1, 2, 3, 4], default=1)
    parser.add_argument("--out", type=Path, required=True, help="output PNG")
    parser.add_argument(
        "--after-ms", type=int, default=12000, help="delay before taking the screenshot (ms)"
    )
    parser.add_argument("--geometry", default=None, help="WxH (default 480x320)")
    parser.add_argument("--results-dir", type=Path, default=None, help="override the results root")
    parser.add_argument("--sys-block", type=Path, default=None, help="override sys_block")
    parser.add_argument(
        "--unlink", default=None, help="remove the OUT/sys_block/KERNEL symlink after after_ms/2"
    )
    parser.add_argument(
        "--language",
        choices=["en", "ja"],
        default=None,
        help="display language at startup (the saved choice is not overwritten)",
    )
    parser.add_argument(
        "--qr-mode",
        type=int,
        choices=[0, 1, 2, 3],
        default=0,
        help="0 = summary QR, 1 to 3 = page of the detail QR",
    )
    parser.add_argument(
        "--mode-scene",
        choices=SCENES,
        default="none",
        help="what the operator did on the mode button before the shot (default none)",
    )
    parser.add_argument(
        "--code",
        default=DEFAULT_CODE,
        help="fixed 4-digit confirmation code of 1-4 shown by the dialog (default 3142)",
    )
    args = parser.parse_args(argv)
    code_indices(args.code)  # validate early; the App would raise later otherwise
    return args


def shoot(app: App, out: Path) -> None:
    """Capture the 480x320 window with screencapture; on failure dump to PostScript."""
    root = app.root
    root.update_idletasks()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    region = f"{x},{y},{root.winfo_width()},{root.winfo_height()}"
    try:
        # Call the macOS screencapture from PATH.
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
        saved = [str(left), str(right)]
        confirm = app.mode_ui.confirm
        if confirm.visible:
            # The code dialog is a third canvas that covers both; dump it too.
            dialog = Path(f"{out}.dialog.ps")
            confirm.canvas.postscript(file=str(dialog), colormode="color")
            saved.append(str(dialog))
        print(
            f"screencapture failed (exit code {code}, {size} bytes);"
            f" saved {', '.join(saved)} instead",
            file=sys.stderr,
        )
    else:
        print(f"saved: {out} ({size} bytes)")


def unlink_fixture(fixtures: Path, kernel: str) -> None:
    """Remove only the symlink created by the fixtures to simulate unplugging."""
    link = fixtures / "sys_block" / kernel
    if link.is_symlink():
        link.unlink()
        print(f"unlinked: {link}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)
    # Language and strike files live in the fixtures, not in the developer's ~/.local/state.
    os.environ["XDG_STATE_HOME"] = str(args.fixtures / "state")
    config = load_config(args.fixtures / "display.toml")
    config = replace(
        config,
        results_dir=args.results_dir or args.fixtures / "results",
        sys_block=args.sys_block or args.fixtures / "sys_block",
    )
    code = args.code
    app = App(
        config, AppOptions(windowed=True, kiosk=False), runner=ShowRunner(), make_code=lambda: code
    )
    # Pin the window position so it is not clipped at the screen edge.
    app.root.geometry(f"{args.geometry or '480x320'}{WINDOW_POSITION}")
    if args.language is not None:
        # Fix the language for this launch only, without touching the saved choice.
        app.language = args.language
    app.qr_mode = args.qr_mode
    app.select(args.select - 1)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    def finish() -> None:
        """Take the screenshot and close the window (called once after after_ms)."""
        shoot(app, args.out)
        app.root.destroy()

    if args.unlink:
        app.root.after(args.after_ms // 2, unlink_fixture, args.fixtures, args.unlink)
    # after(0) runs inside mainloop, that is after run()'s first tick.
    app.root.after(0, run_scene, app, args.mode_scene, args.code)
    app.root.after(args.after_ms, finish)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
