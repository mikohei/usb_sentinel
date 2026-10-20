import argparse
import logging
import sys
from pathlib import Path

from .config import load_config
from .device import UnsafeDeviceError
from .models import InspectionMode, Verdict
from .orchestrator import inspect
from .report import render_text, write_all
from .report.json_report import digest
from .runner import SubprocessRunner
from .store import store_for

LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """Send diagnostics to stderr as "LEVEL logger: message" without a timestamp.

    journald adds the timestamp. basicConfig does nothing when the root logger already has
    handlers, so repeated calls and pytest's capture handlers keep their existing setup.
    """
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stderr)


def main() -> int:
    """Entry point for `usb-sentinel [--config PATH] {selftest | inspect [--mode M] DEVICE}`.

    Returns:
        Exit code. inspect returns 0 when the verdict is No Defect, otherwise 1. A refused
        inspection (UnsafeDeviceError) returns 2, and an execution error returns 3. selftest
        returns 0 when all required commands and the ClamAV definition DB are present, else 3.
    """
    parser = argparse.ArgumentParser(description="USB inspection backend")
    parser.add_argument("--config", type=Path, default=Path("/etc/usb-sentinel/config.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("selftest")
    inspection = commands.add_parser("inspect")
    inspection.add_argument("device")
    inspection.add_argument(
        "--mode",
        choices=[m.value for m in InspectionMode],
        default=InspectionMode.SCAN_ONLY.value,
        help="Inspection mode; destructive steps need full and allow_destructive = true",
    )
    args = parser.parse_args()
    configure_logging()
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
        # Use the same store for the start record (inside inspect()) and the completion record.
        store = store_for(config)
        result = inspect(runner, config, args.device, store, mode=InspectionMode(args.mode))
        output = Path(result.output_dir)
        try:
            write_all(result, output, emit_qr=config.emit_qr)
        finally:
            # If the authoritative result.json was written, record completion regardless of
            # whether QR generation succeeded.
            if (output / "result.json").exists():
                failure = store.record_completed(result, digest(result))
                if failure:
                    logger.warning("SQLite record failed (completion): %s", failure)
        print(render_text(result))
        return 0 if result.verdict == Verdict.NO_DEFECT else 1
    except UnsafeDeviceError as exc:
        logger.error("Inspection refused: %s", exc)
        return 2
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.error("Runtime error: %s", exc)
        return 3
