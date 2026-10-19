"""Build the SHA-256 hash inventory (hash-inventory.tsv).

Called only inside the `ro,noexec,nosuid,nodev` mount, and only after clamscan has run a scan
(an orchestrator branch). Files are read only as bytes; their contents are never interpreted.
The time limit is enforced with `Budget` and must not be removed.
"""

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from .models import StepResult, StepStatus
from .steps import StepContext
from .steps.common import Budget

# Bytes read per call. The time budget is checked after each chunk.
CHUNK_SIZE = 1024 * 1024
# Maximum entries kept in details.read_errors (the total goes into read_error_count).
MAX_READ_ERRORS = 50


def _hash_file(path: Path, budget: Budget) -> tuple[str, int] | None:
    """Read the file in chunks and compute its SHA-256.

    Args:
        path: Target file.
        budget: Time budget. The remainder is checked after each chunk.

    Returns:
        (hex digest, bytes read). None if the budget is found to be exceeded.

    Raises:
        OSError: If the file cannot be opened or read.
    """
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            hasher.update(chunk)
            size += len(chunk)
            if budget.remaining() <= 0:
                return None
    return hasher.hexdigest(), size


def _write_ledger(
    ctx: StepContext, stream: TextIO, budget: Budget, details: dict[str, Any]
) -> None:
    """Write every file under the mount points to the hash inventory. Progress goes into details."""
    for index, mountpoint in enumerate(ctx.mountpoints):
        for path in sorted(mountpoint.rglob("*")):
            # Apply the budget to many zero-byte files and directory walks too (separate from
            # the per-chunk check).
            if budget.remaining() <= 0:
                details["timed_out"] = True
                return
            if path.is_symlink() or not path.is_file():
                continue
            name = f"{index}/{path.relative_to(mountpoint)}"
            try:
                hashed = _hash_file(path, budget)
            except OSError as exc:
                # Record per-file failures and move on (do not lose the whole inventory).
                details["read_error_count"] += 1
                if len(details["read_errors"]) < MAX_READ_ERRORS:
                    details["read_errors"].append({"path": name, "errno": exc.errno})
                continue
            if hashed is None:
                # Do not write the file that was in progress when the overrun was detected.
                details["timed_out"] = True
                return
            digest, size = hashed
            details["bytes_hashed"] += size
            # JSON quoting keeps tabs/newlines in USB filenames within one TSV row.
            stream.write(f"{digest}\t{json.dumps(name, ensure_ascii=False)}\n")
            details["lines"] += 1


def inventory(ctx: StepContext, monotonic: Callable[[], float] = time.monotonic) -> StepResult:
    """Build the hash inventory and put the SHA-256 of the inventory file itself in details.digest.

    Args:
        ctx: Step context. `ctx.config.hash_inventory_timeout_s` is the time limit.
        monotonic: Monotonic clock (replaced in tests).

    Returns:
        PASS even with zero files. ERROR if the time limit is exceeded or at least one file is
        unreadable. Also ERROR if the inventory file itself cannot be written.
    """
    timeout_s = ctx.config.hash_inventory_timeout_s
    budget = Budget(timeout_s, monotonic)
    ledger = ctx.output_dir / "hash-inventory.tsv"
    details: dict[str, Any] = {
        "lines": 0,
        "bytes_hashed": 0,
        "timed_out": False,
        "read_error_count": 0,
        "read_errors": [],
        "timeout_s": timeout_s,
    }
    try:
        with ledger.open("w", encoding="utf-8") as stream:
            _write_ledger(ctx, stream, budget, details)
        with ledger.open("rb") as source:
            details["digest"] = hashlib.file_digest(source, "sha256").hexdigest()
    except OSError as exc:
        return StepResult("hash_inventory", StepStatus.ERROR, str(exc))
    lines = details["lines"]
    if details["timed_out"]:
        return StepResult(
            "hash_inventory", StepStatus.ERROR, f"Timed out: {lines} files written", details
        )
    if details["read_error_count"]:
        return StepResult(
            "hash_inventory",
            StepStatus.ERROR,
            f"{details['read_error_count']} unreadable files",
            details,
        )
    return StepResult("hash_inventory", StepStatus.PASS, f"{lines} files", details)
