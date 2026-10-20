"""Write the inspection-mode file read by scripts/run-docker.sh (design doc 3.1, 3.10, 5.4).

The mode file `<control_dir>/mode` is the only channel from the display to the host. Together
with libs/trust.py this module is the only writer outside the XDG state directory. It never
touches the results directory or the SQLite file.

The file content is exactly `<mode> <epoch>\\n`; the host honours `full` only while the stamp is
at most HOST_MODE_TTL_S seconds old, so a crashed display falls back to scan_only by itself.

No GUI import, no i18n, no logging (the controller logs).
"""

import contextlib
import os
import tempfile
from pathlib import Path

SCAN_ONLY = "scan_only"
FULL = "full"
MODES = (SCAN_ONLY, FULL)
MODE_FILE_NAME = "mode"
FILE_MODE = 0o640
# Must equal MODE_TTL_S in scripts/run-docker.sh (tests/test_control.py checks it).
HOST_MODE_TTL_S = 20
REASON_DISABLED = "locked_disabled"
REASON_NO_CONTROL = "locked_no_control"
_TEMP_PREFIX = ".mode-"


def control_problem(control_dir: Path | None) -> str | None:
    """Return why this process cannot write the mode file, or None when it can.

    Args:
        control_dir: Configured control directory; None means switching is disabled.

    Returns:
        REASON_DISABLED for None; REASON_NO_CONTROL when the directory is missing, a symlink, not
        a directory, or not writable and searchable (for example before the display user's new
        group membership is active). Never raises.
    """
    if control_dir is None:
        return REASON_DISABLED
    try:
        if control_dir.is_symlink() or not control_dir.is_dir():
            return REASON_NO_CONTROL
    except OSError:
        return REASON_NO_CONTROL
    if not os.access(control_dir, os.W_OK | os.X_OK):
        return REASON_NO_CONTROL
    return None


def mode_file_present(control_dir: Path) -> bool:
    """Return whether a mode file (of any kind) exists; assume True when that cannot be told.

    False on FileNotFoundError or NotADirectoryError (the host would find no file either). True
    when lstat succeeds and on any other OSError (no search permission: the file may exist).
    """
    try:
        os.lstat(control_dir / MODE_FILE_NAME)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


def write_mode(control_dir: Path, mode: str, now: float) -> str | None:
    """Write `<mode> <int(now)>\\n` atomically to <control_dir>/mode with mode 0640.

    Args:
        control_dir: The control directory (never None; the controller checks first).
        mode: SCAN_ONLY or FULL.
        now: Current epoch seconds; the stamp is int(now).

    Returns:
        None on success, else `<ExceptionName>: <message>`. No fsync (tmpfs).

    Raises:
        ValueError: mode is not in MODES (programming error).
    """
    if mode not in MODES:
        raise ValueError(f"invalid inspection mode: {mode!r}")
    content = f"{mode} {int(now)}\n".encode("ascii")
    tmp: str | None = None
    try:
        fd, tmp = tempfile.mkstemp(dir=control_dir, prefix=_TEMP_PREFIX)
        try:
            os.write(fd, content)
            os.fchmod(fd, FILE_MODE)
        finally:
            os.close(fd)
        Path(tmp).replace(control_dir / MODE_FILE_NAME)
    except OSError as exc:
        if tmp is not None:
            with contextlib.suppress(OSError):
                Path(tmp).unlink()
        return f"{type(exc).__name__}: {exc}"
    return None


def reset_mode(control_dir: Path, now: float) -> str | None:
    """Put the host back to scan_only: rewrite the file, else remove it (missing = scan_only).

    Returns:
        None when either step worked, else both reasons joined with "; ".
    """
    first = write_mode(control_dir, SCAN_ONLY, now)
    if first is None:
        return None
    try:
        (control_dir / MODE_FILE_NAME).unlink(missing_ok=True)
    except OSError as exc:
        return f"{first}; {type(exc).__name__}: {exc}"
    return None
