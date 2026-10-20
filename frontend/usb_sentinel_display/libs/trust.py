"""Trust state that survives a display restart (design doc 3.7).

Two files live outside the process so that a USB keyboard that restarts the session or the
host cannot get a fresh budget:

1. The per-boot input baseline `<control_dir>/input-baseline` (tmpfs, cleared by a reboot). The
   first display start of a boot creates it; later starts read it. A device name not in it locks
   erase mode until the next reboot.
2. The persistent strike count `$XDG_STATE_HOME/usb-sentinel-display/erase-strikes`. Opening the
   dialog charges one strike before the code is shown; the strike is refunded only when the
   attempt ends in the same process without a wrong digit.

The display never repairs or overwrites an invalid file: an unusable file locks erase mode.
Together with libs/control.py this is the only writer outside the XDG state directory.
No GUI import, no i18n, no logging (the controller logs).
"""

import contextlib
import os
import re
import stat
import tempfile
from pathlib import Path

BASELINE_FILE_NAME = "input-baseline"
STRIKE_DIR_NAME = "usb-sentinel-display"  # same directory as the saved language
STRIKE_FILE_NAME = "erase-strikes"
MAX_STRIKES = 3
MAX_BASELINE_BYTES = 8192
MAX_STRIKE_BYTES = 16
BASELINE_FILE_MODE = 0o640
STRIKE_FILE_MODE = 0o600
_INPUT_NAME_RE = re.compile(r"input([0-9]{1,6})")
_STRIKES_RE = re.compile(r"([0-9]{1,3})\n")


def strike_file() -> Path:
    """Path of the strike file: $XDG_STATE_HOME if set and not empty, else ~/.local/state."""
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / STRIKE_DIR_NAME / STRIKE_FILE_NAME


def _input_number(name: str) -> int | None:
    """The N of inputN, or None when the name is outside the pattern."""
    match = _INPUT_NAME_RE.fullmatch(name)
    return None if match is None else int(match.group(1))


def _parse_baseline(raw: bytes) -> frozenset[str] | None:
    """Validate the baseline file content: ASCII inputN lines, sorted by number, no duplicates."""
    if len(raw) > MAX_BASELINE_BYTES:
        return None
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    if text == "":
        return frozenset[str]()
    if not text.endswith("\n"):
        return None
    names = text[:-1].split("\n")
    numbers: list[int] = []
    for name in names:
        number = _input_number(name)
        if number is None:
            return None
        numbers.append(number)
    if len(set(names)) != len(names) or numbers != sorted(numbers):
        return None
    return frozenset(names)


def _read_baseline(path: Path) -> frozenset[str] | None:
    """Read and validate an existing baseline file. None when it is unusable."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        with path.open("rb") as stream:
            raw = stream.read(MAX_BASELINE_BYTES + 1)
    except OSError:
        return None
    return _parse_baseline(raw)


def _create_baseline(path: Path, control_dir: Path, current: frozenset[str]) -> bool:
    """Create the baseline file from current without ever overwriting an existing one.

    Returns:
        True when this process created it; False on FileExistsError (another instance won).

    Raises:
        OSError: Any other file system error.
    """
    ordered = sorted(current, key=lambda name: _input_number(name) or 0)
    content = "".join(f"{name}\n" for name in ordered).encode("ascii")
    fd, tmp = tempfile.mkstemp(dir=control_dir, prefix=".baseline-")
    try:
        try:
            os.write(fd, content)
            os.fchmod(fd, BASELINE_FILE_MODE)
        finally:
            os.close(fd)
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
    finally:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
    return True


def load_or_create_baseline(control_dir: Path, current: frozenset[str]) -> frozenset[str] | None:
    """Return the per-boot input baseline, creating it from current if this is the first start.

    Args:
        control_dir: The control directory (tmpfs; a reboot clears it).
        current: Input device names present now (from sysfs.list_input_devices).

    Returns:
        The baseline set, or None when the file is unusable or cannot be created. Never raises.
    """
    path = control_dir / BASELINE_FILE_NAME
    if os.path.lexists(path):
        return _read_baseline(path)
    if any(_input_number(name) is None for name in current):
        return None
    try:
        created = _create_baseline(path, control_dir, current)
    except OSError:
        return None
    return current if created else _read_baseline(path)


def read_strikes(path: Path) -> int | None:
    """Return the strike count; 0 if the file is missing; None if it is unusable."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        with path.open("rb") as stream:
            raw = stream.read(MAX_STRIKE_BYTES + 1)
    except FileNotFoundError:
        return 0
    except OSError:
        return None
    if len(raw) > MAX_STRIKE_BYTES:
        return None
    try:
        match = _STRIKES_RE.fullmatch(raw.decode("ascii"))
    except UnicodeDecodeError:
        return None
    return None if match is None else int(match.group(1))


def write_strikes(path: Path, count: int) -> str | None:
    """Write the strike count atomically (mkstemp, fchmod 0600, fsync, replace).

    Args:
        path: The strike file; its parent is created when missing.
        count: 0..999.

    Returns:
        None on success, else `<ExceptionName>: <message>`.

    Raises:
        ValueError: count outside 0..999 (programming error).
    """
    if not 0 <= count <= 999:
        raise ValueError(f"strike count out of range: {count}")
    tmp: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".erase-strikes-")
        try:
            os.write(fd, f"{count}\n".encode("ascii"))
            os.fchmod(fd, STRIKE_FILE_MODE)
            os.fsync(fd)
        finally:
            os.close(fd)
        Path(tmp).replace(path)
    except OSError as exc:
        if tmp is not None:
            with contextlib.suppress(OSError):
                Path(tmp).unlink()
        return f"{type(exc).__name__}: {exc}"
    return None
