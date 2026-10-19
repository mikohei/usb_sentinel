"""容量・速度ステップで共用する部品（時間予算、権限判定、lsblk問い合わせ、コマンド実行と失敗文言）。

summaryの文言はresult.jsonに出るため、ここに置く定数と関数の文字列を変えると外部の互換性に関わる。
"""

from collections.abc import Callable, Iterable
from typing import Any

from ..device import block_info
from ..models import StepResult
from ..runner import CommandResult, CommandRunner
from . import StepContext

# fioのjobs[0].errorで権限不足とみなすerrno（EPERM、EACCES、EROFS）。
PERMISSION_ERRNOS: frozenset[int] = frozenset({1, 13, 30})

_PERMISSION_PHRASES = ("Permission denied", "Read-only file system", "Operation not permitted")

# 外部コマンドがタイムアウトした、または時間予算を使い切ったときのsummary。
TIMEOUT_SUMMARY = "タイムアウト"
# 書き込みが権限不足・書き込み保護で拒否されたときのsummary。
WRITE_DENIED_SUMMARY = "権限不足: 書き込みが拒否された"
# summaryに載せるstderrの先頭文字数。
STDERR_HEAD_CHARS = 200
# details["raw_stdout_head"]に残すstdoutの先頭文字数。
RAW_STDOUT_HEAD_CHARS = 500


class Budget:
    """ステップ全体の時間予算。各外部コマンドに残り秒数をtimeout_sとして渡す。"""

    def __init__(self, timeout_s: float, monotonic: Callable[[], float]) -> None:
        """開始時刻を記録する。

        Args:
            timeout_s: ステップ全体に許す秒数。
            monotonic: 単調増加時刻を返す関数（テストでは差し替える）。
        """
        self.timeout_s = timeout_s
        self.monotonic = monotonic
        self.started = monotonic()

    def remaining(self) -> float:
        """残り秒数を返す。超過後は0.0に張り付く。"""
        return max(0.0, self.timeout_s - (self.monotonic() - self.started))


def bytes_to_mb_s(bytes_per_s: float) -> float:
    """バイト/秒をMB/s（10^6バイト/秒）に換算する。"""
    return bytes_per_s / 1e6


def is_permission_error(text: str) -> bool:
    """stdout/stderrの文言が権限不足・書き込み保護を示すか。

    util-linuxのmount -wが書き込み保護デバイスで出す
    「cannot mount /dev/sdX read-only」も拾うため、大文字小文字を無視したread-onlyを含める。
    """
    return any(phrase in text for phrase in _PERMISSION_PHRASES) or "read-only" in text.lower()


class DeviceQueryError(RuntimeError):
    """lsblkによるデバイス情報の取得に失敗した（環境異常）。"""


def query_block_info(runner: CommandRunner, path: str) -> dict[str, Any]:
    """device.block_info()を呼び、失敗をDeviceQueryErrorに包む。

    Args:
        runner: コマンド実行器。
        path: 対象デバイスのパス。

    Returns:
        lsblkの先頭ブロックデバイスの辞書。

    Raises:
        DeviceQueryError: lsblkの失敗、JSON不正、blockdevicesキー欠落のいずれか。
    """
    try:
        return block_info(runner, path)
    except (RuntimeError, ValueError, KeyError) as exc:
        raise DeviceQueryError(f"デバイス情報を取得できない: {exc}") from exc


def not_found_summary(command: str) -> str:
    """「<コマンド>が見つからない」。which()の不在とCommandNotFoundErrorの両方で使う。

    Args:
        command: コマンド名（CommandNotFoundErrorならstr(exc)）。

    Returns:
        summaryに入れる文言。
    """
    return f"{command}が見つからない"


def missing_command(runner: CommandRunner, commands: Iterable[str]) -> str | None:
    """which()で見つからない最初のコマンド名を返す。全部あればNone。

    Args:
        runner: コマンド実行器。
        commands: 確認するコマンド名の並び（この順に確認し、最初の不在で止める）。

    Returns:
        見つからなかったコマンド名。全部あればNone。
    """
    for command in commands:
        if runner.which(command) is None:
            return command
    return None


def run_within_budget(
    ctx: StepContext, budget: Budget, argv: list[str], commands: list[list[str]]
) -> CommandResult | None:
    """残り予算があればargvをcommandsに記録してから実行する。予算が無ければ実行せずNone。

    budget.remaining()は予算の確認で1回、timeout_sの決定で1回の計2回呼ぶ。固定クロックの
    テストがこの呼び出し回数に依存するため、回数を変えない。

    Args:
        ctx: ステップ文脈（runnerを使う）。
        budget: 時間予算。
        argv: 実行するargv（リスト固定）。
        commands: 実行したargvの記録先（実行直前に追記する）。

    Returns:
        コマンドの実行結果。予算切れで実行しなかったときはNone。
    """
    if budget.remaining() <= 0:
        return None
    commands.append(argv)
    return ctx.runner.run(argv, timeout_s=budget.remaining())


def mark_timed_out(result: StepResult) -> None:
    """details["timed_out"] = Trueとsummary「タイムアウト」を書く。

    Args:
        result: 書き込み先のステップ結果。
    """
    result.details["timed_out"] = True
    result.summary = TIMEOUT_SUMMARY


def failure_summary(run: CommandResult, what: str) -> str:
    """コマンド失敗のsummary。権限不足の文言ならWRITE_DENIED_SUMMARY、それ以外はstderrの先頭を添える。

    Args:
        run: 失敗したコマンドの実行結果。
        what: 失敗の見出し（例: "f3write失敗"、"fioの出力を解析できない"）。

    Returns:
        summaryに入れる文言。
    """
    if is_permission_error(run.stdout + run.stderr):
        return WRITE_DENIED_SUMMARY
    return f"{what}: {run.stderr[:STDERR_HEAD_CHARS]}"


def record_raw_stdout(target: dict[str, Any], run: CommandResult) -> None:
    """解析できなかったstdoutの先頭をtarget["raw_stdout_head"]に残す。

    Args:
        target: 書き込み先（result.detailsや読み書きごとのentry）。
        run: 解析に失敗したコマンドの実行結果。
    """
    target["raw_stdout_head"] = run.stdout[:RAW_STDOUT_HEAD_CHARS]
