"""fioのargv組み立てとJSON出力のパーサ。

速度ステップ（speed.py）が使う純関数とデータ型を置く。外部コマンドは呼ばない。
"""

import json
from dataclasses import dataclass

from . import StepContext
from .common import bytes_to_mb_s
from .speed_judge import Mode


@dataclass
class FioJob:
    """fioのJSON出力jobs[0]から取り出した測定値。"""

    error: int
    bw_bytes: int
    iops: float
    io_bytes: int
    runtime_ms: int

    @property
    def mb_s(self) -> float:
        """帯域をMB/sに換算した値。"""
        return bytes_to_mb_s(self.bw_bytes)


def parse_fio(text: str, mode: Mode) -> FioJob:
    """fio --output-format=jsonのstdoutを解析する。

    stdoutの先頭にfioが警告行を出すことがあるため、最初の`{`以降を解析する。

    Raises:
        ValueError: JSON不正、jobsが空、read/writeキーが無いなど。
    """
    start = text.find("{")
    if start < 0:
        raise ValueError("fioの出力を解析できない")
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
        raise ValueError("fioの出力を解析できない") from exc


def fio_argv(ctx: StepContext, mode: Mode) -> list[str]:
    """fioのargvを組む。値はconfig.speedからだけ埋める。読み取りには--readonlyを付ける。

    Args:
        ctx: ステップ文脈（config.speedとdevice.pathを使う）。
        mode: "read"または"write"。

    Returns:
        fioのargv（リスト固定。文字列の結合でコマンドを組まない）。
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
