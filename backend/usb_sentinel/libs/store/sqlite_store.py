"""SQLiteへの検査記録（設計書第5節）。

result.jsonが正本で、DBは「どの検査がいつ・どのポートで・どう終わったか」を1行で引く索引である。
記録の失敗は検査の成否に影響させない。公開メソッドは例外を外へ出さず、失敗理由の文字列を返す。
接続は操作ごとに開いて閉じる（検査中の数時間、接続を保持しない）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from ..models import InspectionResult
from .schema import MIGRATIONS, SCHEMA_VERSION
from .sqlite_row import STEP_NAMES, VERDICT_CODES, row_from_result

# VERDICT_CODES・STEP_NAMES・row_from_resultはsqlite_row.pyへ移した。既存の参照先として名前を残す。
__all__ = [
    "DB_MODE",
    "STARTED_COLUMNS",
    "STEP_NAMES",
    "VERDICT_CODES",
    "SchemaTooNewError",
    "SqliteStore",
    "row_from_result",
]

# 本体DBファイルのモード。ディレクトリが2770 setgidなのでグループはusb-sentinelになる。
DB_MODE = 0o660

# 開始時（state='started'）に入れる列。判定・ステップの列は完了時に埋める。
STARTED_COLUMNS = (
    "inspection_id",
    "state",
    "serial",
    "kernel_name",
    "port_path",
    "vendor",
    "model",
    "started_at",
    "started_compact",
    "output_dir",
    "created_at",
    "updated_at",
)


class SchemaTooNewError(RuntimeError):
    """DBのuser_versionが本バージョンのSCHEMA_VERSIONより新しい（書き込まない）。"""


def _insert_sql(columns: tuple[str, ...]) -> str:
    """列名は本モジュールの定数由来で、外部入力を含まない。値は名前付きプレースホルダ。"""
    names = ", ".join(columns)
    params = ", ".join(f":{column}" for column in columns)
    return f"INSERT INTO inspections ({names}) VALUES ({params})"  # noqa: S608


def _update_sql(columns: tuple[str, ...]) -> str:
    """inspection_idとcreated_at以外の全列を更新する。"""
    assignments = ", ".join(
        f"{column} = :{column}"
        for column in columns
        if column not in ("inspection_id", "created_at")
    )
    return f"UPDATE inspections SET {assignments} WHERE inspection_id = :inspection_id"  # noqa: S608


class SqliteStore:
    """検査の開始・完了をSQLiteに記録する。接続は保持しない。"""

    def __init__(
        self, path: Path, *, connect: Callable[[str], sqlite3.Connection] = sqlite3.connect
    ) -> None:
        """DBファイルのパスと接続関数を受け取る。接続はここでは開かない。

        Args:
            path: inspections.sqliteのパス。
            connect: 接続を開く関数（テストで差し替える）。
        """
        self.path = path
        self._connect = connect

    def _chmod_db(self) -> str | None:
        """本体inspections.sqliteだけに0o660を掛ける。-wal・-shmはSQLiteが本体のモードを写す。"""
        try:
            self.path.chmod(DB_MODE)
        except OSError as exc:
            return f"権限設定エラー: {exc}"
        return None

    def _open(self) -> sqlite3.Connection:
        """接続を開き、PRAGMAとマイグレーションを済ませて返す。失敗時は閉じてから例外を伝える。"""
        conn = self._connect(str(self.path))
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA temp_store = MEMORY")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise SchemaTooNewError(
                    f"DBのスキーマ版{version}が本バージョン{SCHEMA_VERSION}より新しい"
                )
            if version < SCHEMA_VERSION:
                with conn:
                    conn.execute("BEGIN")
                    for target, statements in MIGRATIONS:
                        if target <= version:
                            continue
                        for sql in statements:
                            conn.execute(sql)
                        conn.execute(f"PRAGMA user_version = {int(target)}")
                if version == 0:
                    # 初回作成。失敗理由は各操作の後の_chmod_db()が改めて返す。
                    self._chmod_db()
        except BaseException:
            conn.close()
            raise
        return conn

    def _write(self, operate: Callable[[sqlite3.Connection], None]) -> str | None:
        """接続を開いて1トランザクションで書き、chmodして閉じる。失敗理由を返す。"""
        try:
            conn = self._open()
            try:
                with conn:
                    operate(conn)
            finally:
                conn.close()
        except SchemaTooNewError as exc:
            return str(exc)
        except sqlite3.Error as exc:
            return f"SQLiteエラー: {exc}"
        except OSError as exc:
            return f"ファイル操作エラー: {exc}"
        except ValueError as exc:
            return f"値の変換エラー: {exc}"
        chmod_error = self._chmod_db()
        if chmod_error is not None:
            # 行の書き込みは済んでいるので、記録失敗と区別できる文言で返す。
            return f"権限設定に失敗（記録は完了）: {chmod_error}"
        return None

    def record_started(self, result: InspectionResult) -> str | None:
        """state='started'の行を入れる。

        Returns:
            失敗理由。成功ならNone。
        """

        def operate(conn: sqlite3.Connection) -> None:
            """開始時の列だけをINSERTする。"""
            row = row_from_result(result, None)
            row["state"] = "started"
            conn.execute(_insert_sql(STARTED_COLUMNS), {c: row[c] for c in STARTED_COLUMNS})

        return self._write(operate)

    def record_completed(self, result: InspectionResult, digest: str) -> str | None:
        """state='completed'で全列を更新する。開始行が無ければINSERTに切り替える。

        Returns:
            失敗理由。成功ならNone。
        """

        def operate(conn: sqlite3.Connection) -> None:
            """全列をUPDATEし、影響行が0ならINSERTする。"""
            row = row_from_result(result, digest)
            columns = tuple(row)
            cursor = conn.execute(_update_sql(columns), row)
            if cursor.rowcount == 0:
                conn.execute(_insert_sql(columns), row)

        return self._write(operate)
