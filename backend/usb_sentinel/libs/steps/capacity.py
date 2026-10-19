"""容量検証ステップ（f3write+f3read、またはf3probe）。

破壊的検査であり、orchestratorのゲートを通過したときだけ呼ばれる。
外部コマンドはすべてctx.runner.run()経由で、argvはリストで固定する。
f3writeモードの実行はcapacity_write.py、f3probeモードはcapacity_probe.pyにある。
"""

import math
import os
import time
from collections.abc import Callable

from ..models import StepResult, StepStatus
from ..runner import CommandNotFoundError
from . import StepContext
from .capacity_fs import MountError, MountpointError, NotWritableError, UnmountError
from .capacity_judge import CapacityVerdict, evaluate, judge, over_threshold
from .capacity_probe import run_probe
from .capacity_write import Totals, run_f3write
from .common import (
    STDERR_HEAD_CHARS,
    Budget,
    DeviceQueryError,
    is_permission_error,
    not_found_summary,
)

# evaluate()はcapacity_judge.pyへ移した。既存の参照先として名前を残す。
__all__ = ["DEFAULT_MONOTONIC", "DEFAULT_STATVFS", "CapacityStep", "evaluate"]

# 統合テストからmonkeypatchで差し替えるためのモジュール変数。
DEFAULT_STATVFS: Callable[[str], os.statvfs_result] = os.statvfs
DEFAULT_MONOTONIC: Callable[[], float] = time.monotonic

# f3readの非OKセクタ種別（filesystems[].{key}_sectorsのキー）。
_BAD_SECTOR_KEYS = ("lost", "corrupted", "slightly_changed", "overwritten")
# rwマウントが権限不足・書き込み保護で失敗したときのsummary。
_MOUNT_NOT_WRITABLE_SUMMARY = "権限不足: 書き込み可能でマウントできない"


class CapacityStep:
    def __init__(
        self,
        statvfs: Callable[[str], os.statvfs_result] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        """依存を注入する。Noneなら呼び出し時点のモジュール変数を使う。"""
        self.statvfs = statvfs or DEFAULT_STATVFS
        self.monotonic = monotonic or DEFAULT_MONOTONIC

    def run(self, ctx: StepContext) -> StepResult:
        """容量検証を実行する。use_probeならf3probe、そうでなければf3write+f3read。

        例外（コマンド不在、lsblk失敗、マウント失敗、パーサ失敗、umount失敗）はここで
        ERRORのsummaryに変換し、外へは出さない。

        Args:
            ctx: ステップ文脈。

        Returns:
            PASS／FAIL／ERRORのいずれかを持つステップ結果。
        """
        result = StepResult("capacity", StepStatus.ERROR)
        config = ctx.config.capacity
        advertised = ctx.device.advertised_bytes
        base_bytes = config.nominal_bytes or advertised
        commands: list[list[str]] = []
        result.details.update(
            mode="f3probe" if config.use_probe else "f3write",
            advertised_bytes=advertised,
            nominal_bytes=config.nominal_bytes,
            base_bytes=base_bytes,
            base="nominal" if config.nominal_bytes else "advertised",
            pass_ratio=config.pass_ratio,
            over_ratio=config.over_ratio,
            threshold_bytes=math.ceil(base_bytes * config.pass_ratio),
            over_threshold_bytes=over_threshold(config.nominal_bytes, config.over_ratio),
            commands=commands,
            filesystems=[],
            probe=None,
        )
        budget = Budget(config.timeout_s, self.monotonic)
        try:
            if config.use_probe:
                actual = run_probe(ctx, result, budget, commands)
                if actual is None:
                    return result
                return self._judge_probe(result, actual)
            totals = run_f3write(ctx, result, budget, commands, self.statvfs)
            if totals is None:
                return result
            return self._judge_f3write(result, totals)
        except CommandNotFoundError as exc:
            result.summary = not_found_summary(str(exc))
        except DeviceQueryError as exc:
            result.summary = str(exc)
        except MountpointError as exc:
            result.summary = str(exc)
        except MountError as exc:
            if is_permission_error(str(exc)):
                result.summary = _MOUNT_NOT_WRITABLE_SUMMARY
            else:
                result.summary = f"mount失敗: {str(exc)[:STDERR_HEAD_CHARS]}"
        except NotWritableError:
            result.summary = _MOUNT_NOT_WRITABLE_SUMMARY
        except ValueError as exc:
            # parse_f3write()・parse_f3read()の変換済みメッセージ
            # （run_probe()は自前でsummaryを確定する）。
            result.summary = str(exc)
        except UnmountError as exc:
            cause = exc.__cause__
            suppressed = str(cause) if cause is not None else result.summary
            if suppressed:
                result.details["suppressed_error"] = suppressed
            result.status = StepStatus.ERROR
            result.summary = f"umount失敗: {str(exc)[:STDERR_HEAD_CHARS]}"
        return result

    def _judge_f3write(self, result: StepResult, totals: Totals) -> StepResult:
        """f3writeモードの判定（umount完了後に行う）。判定本体はcapacity_judge.judge()。"""
        entries = result.details["filesystems"]
        bad = {key: sum(entry[f"{key}_sectors"] for entry in entries) for key in _BAD_SECTOR_KEYS}
        sectors = sum(entry["ok_sectors"] for entry in entries) + sum(bad.values())
        advertised: int = result.details["advertised_bytes"]
        verdict = self._judge(
            result,
            actual=totals.used_bytes + totals.verified_bytes,
            bad_sectors=totals.bad_sectors,
            lost_ratio=bad["lost"] / sectors if sectors else None,
            probe_verdict_line=None,
            verified_bytes=totals.verified_bytes,
        )
        result.details.update(
            used_bytes=totals.used_bytes,
            verified_ratio=round(totals.verified_bytes / advertised, 4) if advertised else 0.0,
        )
        if verdict.kind == "defect":
            result.summary += (
                f"（消失{bad['lost']} / 破損{bad['corrupted']}"
                f" / 変化{bad['slightly_changed']} / 上書き{bad['overwritten']}）"
            )
        return result

    def _judge_probe(self, result: StepResult, actual: int) -> StepResult:
        """f3probeモードの判定。bad_sectorsの情報が無いためdefectは常にFalse。"""
        self._judge(
            result,
            actual=actual,
            bad_sectors=None,
            lost_ratio=None,
            probe_verdict_line=result.details["probe"]["verdict_line"],
            verified_bytes=None,
        )
        result.details.update(used_bytes=None, verified_ratio=None)
        return result

    def _judge(
        self,
        result: StepResult,
        *,
        actual: int,
        bad_sectors: int | None,
        lost_ratio: float | None,
        probe_verdict_line: str | None,
        verified_bytes: int | None,
    ) -> CapacityVerdict:
        """judge()を呼び、status・summaryと両モード共通のdetailsをresultに書く。"""
        details = result.details
        advertised: int = details["advertised_bytes"]
        nominal: int | None = details["nominal_bytes"]
        verdict = judge(
            actual=actual,
            advertised=advertised,
            nominal=nominal,
            pass_ratio=details["pass_ratio"],
            over_ratio=details["over_ratio"],
            bad_sectors=bad_sectors,
            lost_ratio=lost_ratio,
            probe_verdict_line=probe_verdict_line,
            verified_bytes=verified_bytes,
            threshold=details["threshold_bytes"],
        )
        details.update(
            actual_bytes=actual,
            verified_bytes=verified_bytes,
            ratio=round(actual / advertised, 4) if advertised else 0.0,
            bad_sectors=bad_sectors,
            defect=verdict.kind == "defect",
            kind=verdict.kind,
            counterfeit=verdict.counterfeit,
            counterfeit_reasons=list(verdict.counterfeit_reasons),
            lost_ratio=round(lost_ratio, 4) if lost_ratio is not None else None,
            advertised_ratio=round(advertised / nominal, 4) if nominal else None,
            probe_verdict_line=probe_verdict_line,
        )
        result.status = verdict.status
        result.summary = verdict.summary
        return verdict
