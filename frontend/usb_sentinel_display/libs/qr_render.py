import qrcode
from qrcode.constants import ERROR_CORRECT_M

# 画面に収めるQRの最大バージョン（設計書2.1の二重防御）。
MAX_VERSION = 8
# QRキャンバスの辺（px）。
CANVAS_PX = 296
BORDER = 4
BLACK = b"\x00\x00\x00"
WHITE = b"\xff\xff\xff"


def _make_qr(payload: str) -> qrcode.QRCode:  # ty: ignore[missing-type-argument] -- qrcodeのジェネリクス引数は画像生成用で、行列だけを使うため省く
    """ペイロードから最小バージョンのQRCodeを作る。"""
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, border=BORDER)
    qr.add_data(payload)
    qr.make(fit=True)
    if qr.version is None or qr.version > MAX_VERSION:
        raise ValueError(f"QR version {qr.version} exceeds {MAX_VERSION}")
    return qr


def qr_version(payload: str) -> int:
    """ペイロードが収まる最小バージョンを返す。8を超えればValueError。"""
    version = _make_qr(payload).version
    return int(version) if version is not None else 0


def make_matrix(payload: str) -> list[list[bool]]:
    """クワイエットゾーンを含むモジュール行列を返す。

    Raises:
        ValueError: バージョンが8を超えるとき。
    """
    return [[bool(cell) for cell in row] for row in _make_qr(payload).get_matrix()]


def box_size_for(matrix_size: int, canvas_px: int = CANVAS_PX) -> int:
    """1モジュールのpx数。matrix_sizeはクワイエットゾーン込みの辺（D10）。"""
    if matrix_size <= 0:
        raise ValueError("matrix size must be positive")
    return canvas_px // matrix_size


def matrix_to_ppm(matrix: list[list[bool]], box: int) -> bytes:
    """行列をbox倍に拡大したP6形式のPPMにする（tk.PhotoImage用）。"""
    side = len(matrix) * box
    rows: list[bytes] = []
    for row in matrix:
        line = b"".join((BLACK if cell else WHITE) * box for cell in row)
        rows.append(line * box)
    return b"P6 %d %d 255\n" % (side, side) + b"".join(rows)


def render(payload: str) -> tuple[bytes, int]:
    """ペイロードからPPMバイト列と辺のpxを返す。"""
    matrix = make_matrix(payload)
    box = box_size_for(len(matrix))
    return matrix_to_ppm(matrix, box), len(matrix) * box
