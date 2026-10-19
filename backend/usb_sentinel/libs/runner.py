"""外部コマンドの実行。

subprocessの呼び出しはここに集約する。argvはリストで固定し、文字列の結合でコマンドを組まない。
テストではFakeRunnerで差し替える。
"""

import shutil
import subprocess
from dataclasses import dataclass, replace
from typing import Protocol


class CommandNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        """終了コード0でタイムアウトしていなければTrue。"""
        return self.returncode == 0 and not self.timed_out

    def check(self) -> "CommandResult":
        """okでなければRuntimeError（stderr、無ければargv）を投げる。okなら自身を返す。"""
        if not self.ok:
            raise RuntimeError(self.stderr or f"command failed: {self.argv}")
        return self


class CommandRunner(Protocol):
    def which(self, command: str) -> str | None:
        """コマンドの実体パス。見つからなければNone。"""
        ...

    def run(self, argv: list[str], *, timeout_s: float | None = None) -> CommandResult:
        """argvを実行して結果を返す。timeout_sを超えたらtimed_out=Trueの結果にする。"""
        ...


class SubprocessRunner:
    def which(self, command: str) -> str | None:
        """shutil.which()でPATHから探す。"""
        return shutil.which(command)

    def run(self, argv: list[str], *, timeout_s: float | None = None) -> CommandResult:
        """subprocess.run()でargvを実行する。stdout／stderrはテキストで取り、不正バイトは置換する。

        Args:
            argv: 実行するargv（リスト固定）。
            timeout_s: 秒数。超えたら終了コード-1・timed_out=Trueの結果を返す。

        Returns:
            コマンドの実行結果。

        Raises:
            CommandNotFoundError: argv[0]が実行できないとき。
        """
        try:
            process = subprocess.run(
                argv, capture_output=True, text=True, errors="replace", timeout=timeout_s
            )
        except FileNotFoundError as exc:
            raise CommandNotFoundError(argv[0]) from exc
        except subprocess.TimeoutExpired:
            return CommandResult(tuple(argv), -1, stderr="timeout", timed_out=True)
        return CommandResult(tuple(argv), process.returncode, process.stdout, process.stderr)


class FakeRunner:
    def __init__(self, missing: set[str] | None = None) -> None:
        """登録済みの結果を返す偽の実行器。

        Args:
            missing: which()がNoneを返し、run()がCommandNotFoundErrorを投げるコマンド名。
        """
        self.missing = set(missing or ())
        self.calls: list[tuple[str, ...]] = []
        self.registered: dict[tuple[str, ...], CommandResult] = {}

    def which(self, command: str) -> str | None:
        """missingに無いコマンドは`/usr/bin/<command>`として存在するとみなす。"""
        return None if command in self.missing else f"/usr/bin/{command}"

    def register(
        self,
        argv: list[str],
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        timed_out: bool = False,
    ) -> None:
        """argvの前置一致で返す結果を登録する。同じargvを登録し直すと上書きになる。

        Args:
            argv: 一致させる前置（例: `["clamscan", "--version"]`）。
            returncode: 返す終了コード。
            stdout: 返す標準出力。
            stderr: 返す標準エラー。
            timed_out: タイムアウトした結果として返すか。
        """
        self.registered[tuple(argv)] = CommandResult(
            tuple(argv), returncode, stdout, stderr, timed_out
        )

    def run(self, argv: list[str], *, timeout_s: float | None = None) -> CommandResult:
        """呼び出しをcallsに記録し、最も長く前置一致した登録結果を返す。

        Args:
            argv: 実行するargv。
            timeout_s: 受け取るだけで使わない（CommandRunnerプロトコルの引数）。

        Returns:
            登録結果（argvは実際の呼び出しに差し替える）。

        Raises:
            CommandNotFoundError: argv[0]がmissingにあるとき。
            AssertionError: 一致する登録が無いとき（テストの設定漏れ）。
        """
        args = tuple(argv)
        self.calls.append(args)
        if argv[0] in self.missing:
            raise CommandNotFoundError(argv[0])
        matches = [key for key in self.registered if args[: len(key)] == key]
        if not matches:
            raise AssertionError(f"unregistered: {argv}")
        return replace(self.registered[max(matches, key=len)], argv=args)
