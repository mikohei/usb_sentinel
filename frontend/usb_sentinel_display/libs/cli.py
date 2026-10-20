import argparse
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path

from .config import DisplayConfig, default_config_path, load_config
from .monitor import NOTICE_SYSFS_MISSING, NOTICE_SYSFS_PERMISSION
from .sysfs import SysfsMissingError, SysfsPermissionError, boot_time, list_usb_disks

# ui/__init__.py is empty, so importing i18n does not load the GUI library (Tk).
from .ui.i18n import text
from .units import Runner, SubprocessRunner

LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """Send diagnostics to stderr as "LEVEL logger: message" without a timestamp.

    journald adds the timestamp. basicConfig does nothing when the root logger already has
    handlers, so repeated calls and pytest's capture handlers keep their existing setup.
    """
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument definitions.

    Returns:
        A parser with the run and calibrate subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="usb-sentinel-display", description="USB Sentinel display frontend"
    )
    parser.add_argument(
        "--config", type=Path, default=None, help="Configuration file (display.toml)"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Show the display")
    run.add_argument("--results-dir", type=Path, default=None, help="Override the results root")
    run.add_argument("--sys-block", type=Path, default=None, help="Override /sys/block")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--windowed", action="store_true", help="Show in a 480x320 window")
    mode.add_argument("--kiosk", action="store_true", help="Full screen with Ctrl+Q disabled")
    run.add_argument("--no-systemctl", action="store_true", help="Do not call systemctl")

    calibrate = commands.add_parser(
        "calibrate", help="Print the port path of each connected USB drive"
    )
    calibrate.add_argument("--sys-block", type=Path, default=None, help="Override /sys/block")
    calibrate.add_argument("--interval", type=float, default=1.0, help="Polling interval (seconds)")
    return parser


def _apply_overrides(config: DisplayConfig, args: argparse.Namespace) -> DisplayConfig:
    """Override the config with values given on the CLI."""
    overrides: dict[str, Path] = {}
    if getattr(args, "results_dir", None) is not None:
        overrides["results_dir"] = args.results_dir
    if getattr(args, "sys_block", None) is not None:
        overrides["sys_block"] = args.sys_block
    return replace(config, **overrides) if overrides else config


def calibrate(config: DisplayConfig, interval: float) -> int:
    """Print "kernel-name port-path serial" each time a USB is inserted. Ends on Ctrl+C.

    Args:
        config: Uses sys_block.
        interval: Polling interval (seconds).

    Returns:
        Exit code (always 0).
    """
    seen: set[tuple[str, str, float]] = set()
    first_seen: dict[str, float] = {}

    def fallback_time(kernel_name: str) -> float:
        """Connect time when st_mtime is unavailable: boot time, else first-seen time."""
        booted = boot_time()
        return booted if booted is not None else first_seen.setdefault(kernel_name, time.time())

    try:
        while True:
            try:
                disks = list_usb_disks(config.sys_block, fallback_time=fallback_time)
            except SysfsMissingError:
                logger.warning(text(config.language, "notice." + NOTICE_SYSFS_MISSING))
                disks = []
            except SysfsPermissionError:
                logger.warning(text(config.language, "notice." + NOTICE_SYSFS_PERMISSION))
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
    """Entry point.

    Args:
        argv: Command-line arguments. Uses sys.argv if None.

    Returns:
        Exit code. 1 for config ValueError (including TOML syntax errors) and load OSError.
    """
    args = build_parser().parse_args(argv)
    configure_logging()
    config_path = args.config if args.config is not None else default_config_path()
    try:
        config = _apply_overrides(load_config(config_path), args)
    except (ValueError, OSError) as exc:
        logger.error("Configuration error: %s", exc)
        return 1
    if args.command == "calibrate":
        return calibrate(config, args.interval)
    # Lazily import the ui layer inside the run branch so calibrate does not load the GUI library.
    from .ui.app import AppOptions, run_app

    runner: Runner | None = None if args.no_systemctl else SubprocessRunner()
    return run_app(config, AppOptions(windowed=args.windowed, kiosk=args.kiosk), runner)
