"""検査記録の保存先（設計書第5.7節）。

記録は検査の成否に影響させない。各メソッドは例外を外へ出さず、失敗理由の文字列を返す。
orchestrator・cliから本パッケージをimportする向きだけにし、逆向きのimportはしない。
"""

from __future__ import annotations

from typing import Protocol

from ..config import Config
from ..models import InspectionResult
from .sqlite_row import row_from_result
from .sqlite_store import SchemaTooNewError, SqliteStore

__all__ = [
    "InspectionStore",
    "NullStore",
    "SchemaTooNewError",
    "SqliteStore",
    "row_from_result",
    "store_for",
]


class InspectionStore(Protocol):
    """検査の開始と完了を記録する。戻り値は失敗理由（成功ならNone）。"""

    def record_started(self, result: InspectionResult) -> str | None:
        """開始記録を残す。output_dirが決まった直後に1回呼ばれる。"""
        ...

    def record_completed(self, result: InspectionResult, digest: str) -> str | None:
        """完了記録を残す。digestはresult.jsonのSHA-256。"""
        ...


class NullStore:
    """何も記録しない（sqlite_path = ""）。"""

    def record_started(self, result: InspectionResult) -> str | None:
        """何もせず成功（None）を返す。"""
        return None

    def record_completed(self, result: InspectionResult, digest: str) -> str | None:
        """何もせず成功（None）を返す。"""
        return None


def store_for(config: Config) -> InspectionStore:
    """設定からstoreを選ぶ。sqlite_pathがNoneならNullStore。"""
    if config.sqlite_path is None:
        return NullStore()
    return SqliteStore(config.sqlite_path)
