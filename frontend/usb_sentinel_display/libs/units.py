import subprocess
from dataclasses import dataclass
from typing import Protocol

from .sysfs import KERNEL_NAME_RE

# 「検査中」とみなすActiveState。
ACTIVE_STATES = frozenset({"active", "activating"})
# systemctl showの固定引数。ユニット名はこの後ろに並べる。
SHOW_ARGV = ("systemctl", "show", "-p", "Id", "-p", "ActiveState")


@dataclass(frozen=True)
class CommandOutput:
    """外部コマンドの実行結果。"""

    returncode: int
    stdout: str
    timed_out: bool


class Runner(Protocol):
    """外部コマンドの実行を差し替えるためのプロトコル。"""

    def run(self, argv: list[str], *, timeout_s: float) -> CommandOutput:
        """argvを実行して結果を返す。例外を外へ出さず、失敗は戻り値で表す。"""
        ...


class SubprocessRunner:
    """subprocessで実際にコマンドを実行する。"""

    def run(self, argv: list[str], *, timeout_s: float) -> CommandOutput:
        """argvを実行する。コマンド不在は127、タイムアウトはtimed_out=Trueで返す。

        Args:
            argv: 実行するargv（リスト固定。文字列の結合でコマンドを組まない）。
            timeout_s: タイムアウト秒。

        Returns:
            終了コードとstdout。不在は`CommandOutput(127, "", False)`、タイムアウトは
            `CommandOutput(-1, "", True)`。
        """
        try:
            process = subprocess.run(
                argv, capture_output=True, text=True, errors="replace", timeout=timeout_s
            )
        except FileNotFoundError:
            return CommandOutput(127, "", False)
        except subprocess.TimeoutExpired:
            return CommandOutput(-1, "", True)
        return CommandOutput(process.returncode, process.stdout, False)


def unit_name(template: str, kernel: str) -> str:
    """unit_templateにカーネル名を埋めてユニット名を作る。

    Raises:
        ValueError: カーネル名がsd[a-z]{1,3}の形でないとき。
    """
    if not KERNEL_NAME_RE.fullmatch(kernel):
        raise ValueError(f"invalid kernel name: {kernel!r}")
    # str.formatは属性参照や他のプレースホルダを解釈するので、単純置換にする。
    return template.replace("{kernel}", kernel)


def parse_show_output(text: str) -> dict[str, str]:
    """systemctl show -p Id -p ActiveStateの出力をユニット名→ActiveStateに変換する。"""
    states: dict[str, str] = {}
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                fields[key] = value
        if "Id" in fields and "ActiveState" in fields:
            states[fields["Id"]] = fields["ActiveState"]
    return states


def query_unit_states(
    runner: Runner, units: list[str], *, timeout_s: float = 2.0
) -> dict[str, str]:
    """複数ユニットのActiveStateを1回のsystemctl showで問い合わせる。

    Args:
        runner: コマンド実行器。
        units: ユニット名の一覧。空なら呼ばずに空辞書を返す。
        timeout_s: タイムアウト秒。

    Returns:
        ユニット名→ActiveState。失敗（非0、タイムアウト、不在）は空辞書。
    """
    if not units:
        return {}
    output = runner.run([*SHOW_ARGV, *units], timeout_s=timeout_s)
    if output.timed_out or output.returncode != 0:
        return {}
    return parse_show_output(output.stdout)
