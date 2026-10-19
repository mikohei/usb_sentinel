"""速度検証ステップ（fio）。

アンマウント状態のブロックデバイスに順次読み取り→順次書き込みを行う。
破壊的検査であり、orchestratorのゲートを通過したときだけ呼ばれる。
run()冒頭のis_mounted確認は自衛であり、ゲートの代替ではない。
"""

import time
from collections.abc import Callable
from typing import Any

from ..device import is_mounted
from ..models import StepResult, StepStatus
from ..runner import CommandNotFoundError
from ..speed_floor import Reference, resolve_device_class
from . import StepContext
from .common import (
    PERMISSION_ERRNOS,
    TIMEOUT_SUMMARY,
    Budget,
    DeviceQueryError,
    failure_summary,
    mark_timed_out,
    not_found_summary,
    query_block_info,
    record_raw_stdout,
    run_within_budget,
)
from .speed_fio import FioJob, fio_argv, parse_fio
from .speed_judge import (
    FAIL_PREFIX,
    Mode,
    basis_text,
    class_warnings,
    link_ceiling_warning,
    reference_for,
    severity_for,
)

# FioJob・parse_fioはspeed_fio.pyへ移した。既存の参照先として名前を残す。
__all__ = ["DEFAULT_MONOTONIC", "FioJob", "SpeedStep", "parse_fio"]

# 統合テストからmonkeypatchで差し替えるためのモジュール変数。
DEFAULT_MONOTONIC: Callable[[], float] = time.monotonic

_LABELS: dict[Mode, str] = {"read": "読み取り", "write": "書き込み"}


class SpeedStep:
    def __init__(self, monotonic: Callable[[], float] | None = None) -> None:
        """依存を注入する。Noneなら呼び出し時点のモジュール変数を使う。"""
        self.monotonic = monotonic or DEFAULT_MONOTONIC

    def run(self, ctx: StepContext) -> StepResult:
        """速度検証を実行する。読み取り（--readonly）→書き込みの順にfioを回し、参照値と比べる。

        冒頭のis_mounted確認は自衛で、マウントが残っていればfioを起動せずERRORにする。

        Args:
            ctx: ステップ文脈。

        Returns:
            PASS／FAIL／SKIPPED（参照値なし）／ERRORのいずれかを持つステップ結果。
        """
        result = StepResult("speed", StepStatus.ERROR)
        speed = ctx.config.speed
        commands: list[list[str]] = []
        try:
            # 自衛の確認: 直前の容量ステップがumountに失敗していればfioを起動しない。
            if is_mounted(query_block_info(ctx.runner, ctx.device.path)):
                result.summary = "マウントが残っているためfioを実行しない"
                return result
            if ctx.runner.which("fio") is None:
                result.summary = not_found_summary("fio")
                return result
            result.details.update(
                block_size=speed.block_size,
                queue_depth=speed.queue_depth,
                duration_s=speed.duration_s,
                pass_ratio=speed.pass_ratio,
                nominal_read_mb_s=speed.nominal_read_mb_s,
                nominal_write_mb_s=speed.nominal_write_mb_s,
                commands=commands,
            )
            link = ctx.device.link_speed_mbps
            device_class, class_basis = resolve_device_class(speed.device_class, link)
            result.details.update(
                device_class=device_class, device_class_basis=class_basis, link_speed_mbps=link
            )
            result.warnings.extend(
                class_warnings(
                    speed.device_class,
                    class_basis,
                    link,
                    has_nominal=speed.nominal_read_mb_s is not None
                    or speed.nominal_write_mb_s is not None,
                )
            )
            budget = Budget(speed.timeout_s, self.monotonic)
            read = self._measure(ctx, result, budget, "read", commands)
            if read is None:
                return result
            write = self._measure(ctx, result, budget, "write", commands)
            if write is None:
                return result
        except CommandNotFoundError as exc:
            result.summary = not_found_summary(str(exc))
            return result
        except DeviceQueryError as exc:
            result.summary = str(exc)
            return result
        return self._judge(result, ctx, device_class, class_basis, read, write)

    def _measure(
        self,
        ctx: StepContext,
        result: StepResult,
        budget: Budget,
        mode: Mode,
        commands: list[list[str]],
    ) -> FioJob | None:
        """fioを1回実行する。失敗時はresultをERRORにしてNoneを返す。"""
        run = run_within_budget(ctx, budget, fio_argv(ctx, mode), commands)
        if run is None:
            result.summary = TIMEOUT_SUMMARY
            return None
        entry: dict[str, Any] = {"returncode": run.returncode}
        result.details[mode] = entry
        if run.timed_out:
            entry["timed_out"] = True
            # 理由コード（s:timeout）はトップレベルのtimed_outを見る。
            mark_timed_out(result)
            return None
        try:
            job = parse_fio(run.stdout, mode)
        except ValueError:
            record_raw_stdout(entry, run)
            result.summary = failure_summary(run, "fioの出力を解析できない")
            return None
        entry.update(
            bw_bytes=job.bw_bytes,
            mb_s=round(job.mb_s, 1),
            iops=job.iops,
            io_bytes=job.io_bytes,
            runtime_ms=job.runtime_ms,
            error=job.error,
        )
        if job.error != 0:
            if job.error in PERMISSION_ERRNOS:
                result.summary = f"権限不足: {_LABELS[mode]}が拒否された（error={job.error}）"
            else:
                result.summary = f"fioがI/Oエラーを報告（error={job.error}）"
            return None
        return job

    def _judge(
        self,
        result: StepResult,
        ctx: StepContext,
        device_class: str | None,
        class_basis: str,
        read: FioJob,
        write: FioJob,
    ) -> StepResult:
        """参照値（公称 > 種別の最低値）と実測を比べてPASS / FAIL / SKIPPEDを決める。

        FAILには重大度（実測/基準が50%未満でsevere、それ以外minor）を付ける。
        参照値が読み・書きとも無ければSKIPPED。
        """
        speed = ctx.config.speed
        pass_ratio = speed.pass_ratio
        measured: dict[Mode, float] = {"read": read.mb_s, "write": write.mb_s}
        refs: dict[Mode, Reference] = {
            "read": reference_for("read", speed.nominal_read_mb_s, device_class),
            "write": reference_for("write", speed.nominal_write_mb_s, device_class),
        }
        thresholds: dict[Mode, float | None] = {}
        for mode, ref in refs.items():
            threshold = ref.value * pass_ratio if ref.value is not None else None
            thresholds[mode] = threshold
            result.details[f"basis_{mode}"] = ref.basis
            result.details[f"reference_{mode}_mb_s"] = ref.value
            result.details[f"threshold_{mode}_mb_s"] = threshold
            result.details[f"ratio_{mode}"] = (
                round(measured[mode] / ref.value, 3) if ref.value is not None else None
            )
        result.details["severity"] = None
        ceiling = link_ceiling_warning(refs.values(), ctx.device.link_speed_mbps)
        if ceiling is not None:
            result.warnings.append(ceiling)
        values = f"読み取り{measured['read']:.1f}MB/s、書き込み{measured['write']:.1f}MB/s"
        if thresholds["read"] is None and thresholds["write"] is None:
            result.status = StepStatus.SKIPPED
            result.summary = f"公称速度が未設定（{values}）"
            return result
        failing: list[tuple[Mode, float, float]] = [
            (mode, threshold, value)
            for mode, threshold in thresholds.items()
            if threshold is not None
            and (value := refs[mode].value) is not None
            and measured[mode] < threshold
        ]
        if failing:
            shortfalls = [
                f"{_LABELS[mode]}{measured[mode]:.1f}MB/s < 閾値{threshold:.1f}MB/s"
                + basis_text(refs[mode], pass_ratio, class_basis)
                for mode, threshold, _value in failing
            ]
            severity = severity_for(measured[mode] / value for mode, _threshold, value in failing)
            result.details["severity"] = severity
            result.status = StepStatus.FAIL
            result.summary = FAIL_PREFIX[severity] + "、".join(shortfalls)
            return result
        result.status = StepStatus.PASS
        if thresholds["read"] is not None and thresholds["write"] is not None:
            result.summary = (
                f"{values}（閾値{thresholds['read']:.1f} / {thresholds['write']:.1f}MB/s）"
            )
        else:
            parts = [
                f"{_LABELS[mode]}{measured[mode]:.1f}MB/s"
                + (f"（閾値{threshold:.1f}MB/s）" if threshold is not None else "（判定対象外）")
                for mode, threshold in thresholds.items()
            ]
            result.summary = "、".join(parts)
        return result
