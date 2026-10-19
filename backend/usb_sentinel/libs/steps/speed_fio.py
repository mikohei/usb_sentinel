"""fio argv construction and JSON output parser.

Holds pure functions and data types used by the speed step (speed.py). No external commands
are run.
"""

import json
from dataclasses import dataclass

from . import StepContext
from .common import bytes_to_mb_s
from .speed_judge import Mode


@dataclass
class FioJob:
    """Measurements extracted from jobs[0] of fio JSON output."""

    error: int
    bw_bytes: int
    iops: float
    io_bytes: int
    runtime_ms: int

    @property
    def mb_s(self) -> float:
        """Bandwidth converted to MB/s."""
        return bytes_to_mb_s(self.bw_bytes)


def parse_fio(text: str, mode: Mode) -> FioJob:
    """Parse stdout of fio --output-format=json.

    fio may print warning lines at the start of stdout, so parsing starts at the first `{`.

    Raises:
        ValueError: Invalid JSON, empty jobs, a missing read/write key, and so on.
    """
    start = text.find("{")
    if start < 0:
        raise ValueError("Could not parse fio output")
    try:
        job = json.loads(text[start:])["jobs"][0]
        io = job[mode]
        bw_bytes = io["bw_bytes"] if "bw_bytes" in io else io["bw"] * 1024
        return FioJob(
            error=int(job.get("error", 0)),
            bw_bytes=int(bw_bytes),
            iops=float(io.get("iops", 0.0)),
            io_bytes=int(io.get("io_bytes", 0)),
            runtime_ms=int(io.get("runtime", 0)),
        )
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("Could not parse fio output") from exc


def fio_argv(ctx: StepContext, mode: Mode) -> list[str]:
    """Build fio argv. Values come only from config.speed. --readonly is added for reads.

    Args:
        ctx: Step context (config.speed and device.path are used).
        mode: "read" or "write".

    Returns:
        fio argv (fixed list; never built by string concatenation).
    """
    speed = ctx.config.speed
    argv = [
        "fio",
        f"--name=seq_{mode}",
        f"--filename={ctx.device.path}",
        f"--rw={mode}",
    ]
    if mode == "read":
        argv.append("--readonly")
    argv.extend(
        [
            "--direct=1",
            "--ioengine=libaio",
            f"--bs={speed.block_size}",
            f"--iodepth={speed.queue_depth}",
            f"--runtime={speed.duration_s}",
            "--time_based",
            "--output-format=json",
        ]
    )
    return argv
