import hashlib
import json
from dataclasses import asdict

from ..models import InspectionResult


def to_json(result: InspectionResult) -> str:
    """result.jsonの本文を作る。キーはソートし、日本語はエスケープせず、末尾に改行を1つ付ける。

    Args:
        result: 検査結果。

    Returns:
        インデント2のJSON文字列。
    """
    return json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def digest(result: InspectionResult) -> str:
    """to_json()のUTF-8バイト列のSHA-256（小文字hex）。QRとSQLiteがresult.jsonと対応を取るのに使う。

    Args:
        result: 検査結果。

    Returns:
        64文字の16進ダイジェスト。
    """
    return hashlib.sha256(to_json(result).encode("utf-8")).hexdigest()
