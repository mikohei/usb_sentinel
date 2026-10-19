import os
from pathlib import Path

import qrcode

from ..models import InspectionResult
from .json_report import to_json
from .qr import build_payload
from .text import render_text

__all__ = ["render_text", "write_all"]


def _write_atomic(path: Path, data: bytes) -> None:
    """Write to `<name>.tmp` in the same directory, then replace with os.replace()."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)  # noqa: PTH105 -- tests patch os.replace to record calls and fail


def write_all(result: InspectionResult, target: Path, *, emit_qr: bool = True) -> list[Path]:
    """Write result.json, result.txt, and result-qr.png to target.

    Each file is replaced atomically via a temporary file so that the display frontend never
    reads a partially written file.
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
        # PilImage.save() passes format="PNG" by default, so the output is PNG even with .tmp.
        qrcode.make(build_payload(result), error_correction=qrcode.constants.ERROR_CORRECT_L).save(
            str(tmp)
        )
        os.replace(tmp, path)  # noqa: PTH105 -- same as above
        paths.append(path)
    return paths
