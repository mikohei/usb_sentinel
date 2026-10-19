import sys
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont

from ..models import SlotState

# 画面と各領域の寸法（設計書1.1）。
SCREEN_W = 480
SCREEN_H = 320
TILE_W = 160
TILE_H = 64
# 左下のボタン列（y256〜320）は◀（x0〜53）、言語（x53〜107）、▶（x107〜160）の3分割。
ARROW_BTN_W = 53
LANG_BTN_X0 = 53
LANG_BTN_X1 = 107
BTN_H = 64
QR_W = 320
QR_H = 296
STRIP_H = 24
BORDER = 4
BLINK_BORDER = 2
TILE_COUNT = 4
# 3行構成を許す行送り合計の上限（設計書1.2）。
ROW_LIMIT_PX = 58
TILE_PAD = 3

# 判定ごとの背景色（設計書8節）。
VERDICT_COLORS: dict[str, str] = {
    "No Defect": "#2E7D32",
    "Malware Detected": "#B71C1C",
    # 偽装疑い（第2期）。マルウェアの赤・不良の紫と区別できる濃いマゼンタ（doc/qr-payload.md 6.1）。
    "Counterfeit Suspected": "#AD1457",
    "Spec Mismatch": "#E65100",
    "Defective": "#6A1B9A",
    "Inconclusive": "#5D4037",
}
UNKNOWN_VERDICT_COLOR = "#424242"

# 判定以外の色。
COLORS: dict[str, str] = {
    "text": "#FFFFFF",
    "empty_text": "#9E9E9E",
    "blink": "#FF5252",
    "empty": "#303030",
    "waiting": "#1565C0",
    "inspecting": "#1565C0",
    "no_result": "#424242",
    "broken": "#424242",
    "strip": "#303030",
    "button": "#303030",
    "qr_background": "#FFFFFF",
    "qr_module": "#000000",
}

# 状態ごとの背景色（完了・抜去は判定色を使うので含めない）。
STATE_COLORS: dict[SlotState, str] = {
    SlotState.EMPTY: COLORS["empty"],
    SlotState.WAITING: COLORS["waiting"],
    SlotState.INSPECTING: COLORS["inspecting"],
    SlotState.NO_RESULT: COLORS["no_result"],
    SlotState.BROKEN: COLORS["broken"],
}


def verdict_color(verdict: str) -> str:
    """判定文字列に対応する背景色。未知の判定は濃灰。"""
    return VERDICT_COLORS.get(verdict, UNKNOWN_VERDICT_COLOR)


def darken(color: str) -> str:
    """#RRGGBBの各成分を50%にした色を返す（抜去の表示）。

    Args:
        color: #RRGGBB形式の色。

    Returns:
        各成分を半分にした#RRGGBB。
    """
    value = color.lstrip("#")
    red, green, blue = (int(value[i : i + 2], 16) // 2 for i in (0, 2, 4))
    return f"#{red:02X}{green:02X}{blue:02X}"


def resolve_font_family(root: tk.Misc, preferred: str) -> str:
    """希望のフォントがあればそれを、無ければ警告を出してTkDefaultFontの族名を返す（D17）。

    Args:
        root: フォント一覧の取得に使うウィジェット。
        preferred: 設定ファイルのfont_family。

    Returns:
        実際に使うフォント族名。
    """
    if preferred in tkfont.families(root):
        return preferred
    fallback = str(tkfont.nametofont("TkDefaultFont", root).actual("family"))
    print(f"フォント{preferred}が無いため{fallback}を使う", file=sys.stderr)
    return fallback


@dataclass(frozen=True)
class Fonts:
    """画面で使うフォント一式（設計書1.3）。"""

    verdict: tkfont.Font
    body: tkfont.Font
    arrow: tkfont.Font

    @classmethod
    def create(cls, root: tk.Misc, family: str) -> "Fonts":
        """族名からフォント一式を作る。sizeは負値でピクセル指定。"""
        return cls(
            verdict=tkfont.Font(root, family=family, size=-18, weight="bold"),
            body=tkfont.Font(root, family=family, size=-14),
            arrow=tkfont.Font(root, family=family, size=-24),
        )


def row_layout(root: tk.Misc, fonts: Fonts) -> int:
    """タイルの行数を決める。3行の行送り合計が58pxを超えれば2行構成（設計書1.2）。

    Args:
        root: 未使用だが、フォント計測がTkの初期化後であることを呼び出し側に示す。
        fonts: 計測対象のフォント。

    Returns:
        2または3。
    """
    total = fonts.verdict.metrics("linespace") + fonts.body.metrics("linespace") * 2
    return 2 if total > ROW_LIMIT_PX else 3
