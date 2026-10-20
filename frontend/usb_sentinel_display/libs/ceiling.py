"""Read the host's erase ceiling from /etc/usb-sentinel (design doc 3.4).

The display only decides whether the mode button is enabled. Enforcement stays with
run-docker.sh and the backend orchestrator, so this module is deliberately at least as strict
as the host: it allows erasing only when BOTH rules below hold.

- config.toml parses with tomllib, its top-level `allow_destructive` is the boolean true, AND at
  least one line matches the `grep -E` rule used by run-docker.sh.
- In docker.env the last assignment to USB_SENTINEL_DEVICE_ACCESS is `rw` (one pair of matching
  quotes removed; comments are not stripped from values, as in systemd EnvironmentFile).

No GUI import, no i18n, no logging. Never raises.
"""

import re
import tomllib
from pathlib import Path

REASON_CEILING_OFF = "locked_ceiling"
REASON_UNREADABLE = "locked_unreadable"
MAX_FILE_BYTES = 1_048_576
CONFIG_FILE_NAME = "config.toml"
ENV_FILE_NAME = "docker.env"
ENV_KEY = "USB_SENTINEL_DEVICE_ACCESS"
ENV_RW = "rw"
# Same rule as `grep -E '^[[:space:]]*allow_destructive[[:space:]]*=[[:space:]]*true'`.
_ALLOW_LINE_RE = re.compile(r"^[ \t]*allow_destructive[ \t]*=[ \t]*true", re.MULTILINE)


def _read_text(path: Path) -> tuple[str | None, str | None]:
    """Read a host file as UTF-8 text.

    Returns:
        (text, None) on success; (None, locked_ceiling) when the file is missing (the host
        defaults are off); (None, locked_unreadable) on any other error or a file larger than
        MAX_FILE_BYTES.
    """
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return None, REASON_CEILING_OFF
    except OSError:
        return None, REASON_UNREADABLE
    if len(raw) > MAX_FILE_BYTES:
        return None, REASON_UNREADABLE
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, REASON_UNREADABLE


def _config_problem(text: str) -> str | None:
    """Apply the config.toml rules. None when allow_destructive is on by both rules."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return REASON_UNREADABLE
    if data.get("allow_destructive") is not True:
        return REASON_CEILING_OFF
    if _ALLOW_LINE_RE.search(text) is None:
        return REASON_CEILING_OFF
    return None


def _unquote(value: str) -> str:
    """Remove one pair of matching double or single quotes around the value."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _env_problem(text: str) -> str | None:
    """Apply the docker.env rule (last assignment wins). None when the value is rw."""
    last: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == ENV_KEY:
            last = _unquote(value.strip())
    return None if last == ENV_RW else REASON_CEILING_OFF


def read_ceiling(host_config_dir: Path) -> str | None:
    """Return None when the host allows erasing, else a reason key.

    Args:
        host_config_dir: Directory with config.toml and docker.env (normally /etc/usb-sentinel).

    Returns:
        None, REASON_CEILING_OFF (a file is missing or a rule is not met) or REASON_UNREADABLE
        (a file cannot be read or parsed, or is larger than MAX_FILE_BYTES). Never raises.
    """
    config_text, reason = _read_text(host_config_dir / CONFIG_FILE_NAME)
    if config_text is None:
        return reason
    problem = _config_problem(config_text)
    if problem is not None:
        return problem
    env_text, reason = _read_text(host_config_dir / ENV_FILE_NAME)
    if env_text is None:
        return reason
    return _env_problem(env_text)
