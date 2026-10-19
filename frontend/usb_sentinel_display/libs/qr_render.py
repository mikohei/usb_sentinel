import qrcode
from qrcode.constants import ERROR_CORRECT_M

# Maximum QR version that fits on screen (double guard from design doc 2.1).
MAX_VERSION = 8
# Side of the QR canvas (px).
CANVAS_PX = 296
BORDER = 4
BLACK = b"\x00\x00\x00"
WHITE = b"\xff\xff\xff"


def _make_qr(payload: str) -> qrcode.QRCode:  # ty: ignore[missing-type-argument] -- generic arg is for images; only the matrix is used
    """Build a QRCode of the smallest version from the payload."""
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, border=BORDER)
    qr.add_data(payload)
    qr.make(fit=True)
    if qr.version is None or qr.version > MAX_VERSION:
        raise ValueError(f"QR version {qr.version} exceeds {MAX_VERSION}")
    return qr


def qr_version(payload: str) -> int:
    """Return the smallest version that fits the payload. ValueError if it exceeds 8."""
    version = _make_qr(payload).version
    return int(version) if version is not None else 0


def make_matrix(payload: str) -> list[list[bool]]:
    """Return the module matrix including the quiet zone.

    Raises:
        ValueError: When the version exceeds 8.
    """
    return [[bool(cell) for cell in row] for row in _make_qr(payload).get_matrix()]


def box_size_for(matrix_size: int, canvas_px: int = CANVAS_PX) -> int:
    """Pixels per module. matrix_size is the side including the quiet zone (D10)."""
    if matrix_size <= 0:
        raise ValueError("matrix size must be positive")
    return canvas_px // matrix_size


def matrix_to_ppm(matrix: list[list[bool]], box: int) -> bytes:
    """Scale the matrix by box into a P6 PPM (for tk.PhotoImage)."""
    side = len(matrix) * box
    rows: list[bytes] = []
    for row in matrix:
        line = b"".join((BLACK if cell else WHITE) * box for cell in row)
        rows.append(line * box)
    return b"P6 %d %d 255\n" % (side, side) + b"".join(rows)


def render(payload: str) -> tuple[bytes, int]:
    """Return the PPM bytes and the side in px for the payload."""
    matrix = make_matrix(payload)
    box = box_size_for(len(matrix))
    return matrix_to_ppm(matrix, box), len(matrix) * box
