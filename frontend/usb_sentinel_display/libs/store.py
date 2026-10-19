"""backendのSQLite索引（inspections.sqlite）の読み取り専用利用（doc/qr-payload.md第8節）。

result.jsonが正本で、DBは候補を素早く引くための索引に過ぎない。ここではSELECTしか実行せず、
mode=roのURIとPRAGMA query_only=ONの二重の守りで書き込みを封じる。DBが無い・開けない・
スキーマ版が新しいときは理由キーを返し、呼び出し側（results.ResultStore）が結果ディレクトリの
走査にフォールバックする。
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# 読める最大のスキーマ版。backendのschema.SCHEMA_VERSIONと同じ値を手で合わせる。
EXPECTED_SCHEMA_VERSION = 1

# 情報ストリップの通知キー（文章はui/i18n.pyの"notice." + キー）。
NOTICE_DB_MISSING = "db_missing"
NOTICE_DB_OPEN_FAILED = "db_open_failed"
NOTICE_DB_SCHEMA_NEW = "db_schema_new"

# SELECTで読む列（DbRowのフィールド順）。
QUERY_COLUMNS: tuple[str, ...] = (
    "inspection_id",
    "state",
    "serial",
    "kernel_name",
    "port_path",
    "started_at",
    "result_digest",
)
# 列名は上の定数と同じ固定文字列で、外部入力を含まない。値はプレースホルダだけで渡す。
_QUERY_SQL = (
    "SELECT inspection_id, state, serial, kernel_name, port_path, started_at, result_digest"
    " FROM inspections WHERE started_at >= ? ORDER BY started_at"
)
# 接続の待ち時間（秒）とbusy_timeout（ミリ秒）。backendの書き込みと重なっても1秒で諦める。
_CONNECT_TIMEOUT_S = 1.0
_BUSY_TIMEOUT_MS = 1000


@dataclass(frozen=True)
class DbRow:
    """inspectionsテーブル1行のうち、結び付けに使う列だけを持つ。"""

    inspection_id: str
    state: str
    serial: str
    kernel_name: str
    port_path: str | None
    started_at: str
    result_digest: str | None


def _row_from_tuple(values: tuple[object, ...]) -> DbRow | None:
    """SELECTの1行をDbRowにする。列の型が想定と違う行はNone（捨てる）。"""
    if len(values) != len(QUERY_COLUMNS):
        return None
    inspection_id, state, serial, kernel_name, port_path, started_at, result_digest = values
    if not all(isinstance(v, str) for v in (inspection_id, state, serial, kernel_name, started_at)):
        return None
    if port_path is not None and not isinstance(port_path, str):
        return None
    if result_digest is not None and not isinstance(result_digest, str):
        return None
    return DbRow(
        inspection_id=str(inspection_id),
        state=str(state),
        serial=str(serial),
        kernel_name=str(kernel_name),
        port_path=port_path if isinstance(port_path, str) else None,
        started_at=str(started_at),
        result_digest=result_digest if isinstance(result_digest, str) else None,
    )


class SqliteIndex:
    """inspections.sqliteを読み取り専用で開き、開始時刻の下限で候補行を引く。"""

    def __init__(
        self,
        path: Path,
        *,
        connect: Callable[..., sqlite3.Connection] = sqlite3.connect,
    ) -> None:
        """
        Args:
            path: inspections.sqliteのパス。
            connect: 接続を作る関数（テストで差し替える）。sqlite3.connectと同じ引数を受ける。
        """
        self.path = path
        self._connect = connect

    def query(self, min_started_at: str) -> tuple[list[DbRow], str | None]:
        """started_atがmin_started_at以上の行をstarted_at順で返す。

        Args:
            min_started_at: ISO 8601（UTC）の下限。文字列比較なのでbackendと同じ形式で渡す。

        Returns:
            (行の一覧、通知キー)。通知キーがNoneでなければ行は空で、呼び出し側は走査に戻る。
        """
        try:
            # Path.exists()はENOENT等以外のOSError（親ディレクトリに実行権限が無いEACCESなど）を
            # 再送出するので、tryの中で呼び「開けない」に落とす。
            if not self.path.exists():
                return [], NOTICE_DB_MISSING
            uri = f"{self.path.resolve().as_uri()}?mode=ro"
            conn = self._connect(uri, uri=True, timeout=_CONNECT_TIMEOUT_S)
        except (sqlite3.Error, OSError, ValueError):
            return [], NOTICE_DB_OPEN_FAILED
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > EXPECTED_SCHEMA_VERSION:
                return [], NOTICE_DB_SCHEMA_NEW
            if version == 0:
                # テーブルを作る前のファイル（backendが一度も書いていない）。
                return [], NOTICE_DB_MISSING
            fetched = conn.execute(_QUERY_SQL, (min_started_at,)).fetchall()
        except (sqlite3.Error, OSError, ValueError):
            return [], NOTICE_DB_OPEN_FAILED
        finally:
            conn.close()
        rows: list[DbRow] = []
        for values in fetched:
            row = _row_from_tuple(tuple(values))
            if row is not None:
                rows.append(row)
        return rows, None
