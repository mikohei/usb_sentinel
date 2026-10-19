import os
from pathlib import Path

import qrcode

from ..models import InspectionResult
from .json_report import to_json
from .qr import build_payload
from .text import render_text

__all__ = ["render_text", "write_all"]


def _write_atomic(path: Path, data: bytes) -> None:
    """同ディレクトリの`<name>.tmp`に書いてからos.replace()で置き換える。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)  # noqa: PTH105 -- テストがos.replaceを差し替えて記録・失敗させるため


def write_all(result: InspectionResult, target: Path, *, emit_qr: bool = True) -> list[Path]:
    """result.json・result.txt・result-qr.pngをtargetに書き出す。

    表示フロントエンドが書き込み途中のファイルを読まないよう、各ファイルは一時ファイル経由で
    アトミックに置き換える。
    """
    target.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in (("result.json", to_json(result)), ("result.txt", render_text(result))):
        path = target / name
        _write_atomic(path, text.encode("utf-8"))
        paths.append(path)
    if emit_qr:
        path = target / "result-qr.png"
        tmp = path.with_name(path.name + ".tmp")
        # PilImage.save()はformat="PNG"を既定で渡すので拡張子が.tmpでもPNGになる。
        qrcode.make(build_payload(result), error_correction=qrcode.constants.ERROR_CORRECT_L).save(
            str(tmp)
        )
        os.replace(tmp, path)  # noqa: PTH105 -- 同上
        paths.append(path)
    return paths
