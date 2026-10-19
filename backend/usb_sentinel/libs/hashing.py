"""SHA-256ハッシュ台帳（hash-inventory.tsv）の作成。

`ro,noexec,nosuid,nodev`マウントの内側で、clamscanがスキャンを実行した後にだけ呼ばれる
（orchestratorの分岐）。ファイルはバイト列として読むだけで内容を解釈しない。
時間上限は`Budget`で付け、外さない。
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

# 1回に読むバイト数。チャンクごとに時間予算を確認する。
CHUNK_SIZE = 1024 * 1024
# details.read_errorsに残す件数の上限（件数自体はread_error_countに入る）。
MAX_READ_ERRORS = 50


def _hash_file(path: Path, budget: Budget) -> tuple[str, int] | None:
    """ファイルをチャンクで読みSHA-256を求める。

    Args:
        path: 対象ファイル。
        budget: 時間予算。チャンクごとに残りを確認する。

    Returns:
        (16進ダイジェスト, 読んだバイト数)。予算超過を検知したらNone。

    Raises:
        OSError: ファイルを開けない、または読めない。
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
    """マウントポイント内の全ファイルを台帳へ書く。進捗はdetailsに入れる。"""
    for index, mountpoint in enumerate(ctx.mountpoints):
        for path in sorted(mountpoint.rglob("*")):
            # 0バイトファイル多数やディレクトリ走査にも予算を効かせる（チャンク後の確認とは別）。
            if budget.remaining() <= 0:
                details["timed_out"] = True
                return
            if path.is_symlink() or not path.is_file():
                continue
            name = f"{index}/{path.relative_to(mountpoint)}"
            try:
                hashed = _hash_file(path, budget)
            except OSError as exc:
                # ファイル単位の失敗は記録して次へ進む（台帳全体を失わない）。
                details["read_error_count"] += 1
                if len(details["read_errors"]) < MAX_READ_ERRORS:
                    details["read_errors"].append({"path": name, "errno": exc.errno})
                continue
            if hashed is None:
                # 超過を検知した途中のファイルは書かない。
                details["timed_out"] = True
                return
            digest, size = hashed
            details["bytes_hashed"] += size
            # JSON quoting keeps tabs/newlines in USB filenames within one TSV row.
            stream.write(f"{digest}\t{json.dumps(name, ensure_ascii=False)}\n")
            details["lines"] += 1


def inventory(ctx: StepContext, monotonic: Callable[[], float] = time.monotonic) -> StepResult:
    """ハッシュ台帳を作り、台帳自体のSHA-256をdetails.digestに入れる。

    Args:
        ctx: ステップ文脈。`ctx.config.hash_inventory_timeout_s`を時間上限にする。
        monotonic: 単調増加時刻（テストで差し替える）。

    Returns:
        ファイル0件でもPASS。時間超過または読み取り不能ファイルが1件以上あればERROR。
        台帳ファイル自体を書けないときもERROR。
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
            "hash_inventory", StepStatus.ERROR, f"タイムアウト: {lines}件まで作成", details
        )
    if details["read_error_count"]:
        return StepResult(
            "hash_inventory",
            StepStatus.ERROR,
            f"読み取り不能ファイル{details['read_error_count']}件",
            details,
        )
    return StepResult("hash_inventory", StepStatus.PASS, f"{lines}件", details)
