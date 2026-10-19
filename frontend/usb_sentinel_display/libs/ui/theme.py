import sys
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont

from ..models import SlotState

# Dimensions of the screen and each area (design doc 1.1).
SCREEN_W = 480
SCREEN_H = 320
TILE_W = 160
TILE_H = 64
# Bottom-left button row (y256-320) is split into prev (x0-53), language (x53-107), next (x107-160).
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
# Upper limit of the total line pitch that allows 3 lines (design doc 1.2).
ROW_LIMIT_PX = 58
TILE_PAD = 3

# Background color per verdict (design doc section 8).
VERDICT_COLORS: dict[str, str] = {
    "No Defect": "#2E7D32",
    "Malware Detected": "#B71C1C",
    # Suspected counterfeit (phase 2). Deep magenta, distinct from malware red and bad purple
    # (doc/qr-payload.md 6.1).
    "Counterfeit Suspected": "#AD1457",
    "Spec Mismatch": "#E65100",
    "Defective": "#6A1B9A",
    "Inconclusive": "#5D4037",
}
UNKNOWN_VERDICT_COLOR = "#424242"

# Colors other than verdicts.
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

# Background color per state (done and removed use the verdict color, so they are excluded).
STATE_COLORS: dict[SlotState, str] = {
    SlotState.EMPTY: COLORS["empty"],
    SlotState.WAITING: COLORS["waiting"],
    SlotState.INSPECTING: COLORS["inspecting"],
    SlotState.NO_RESULT: COLORS["no_result"],
    SlotState.BROKEN: COLORS["broken"],
}


def verdict_color(verdict: str) -> str:
    """Background color for a verdict string. Unknown verdicts are dark gray."""
    return VERDICT_COLORS.get(verdict, UNKNOWN_VERDICT_COLOR)


def darken(color: str) -> str:
    """Return the color with each #RRGGBB component at 50% (removed display).

    Args:
        color: Color in #RRGGBB form.

    Returns:
        #RRGGBB with each component halved.
    """
    value = color.lstrip("#")
    red, green, blue = (int(value[i : i + 2], 16) // 2 for i in (0, 2, 4))
    return f"#{red:02X}{green:02X}{blue:02X}"


def resolve_font_family(root: tk.Misc, preferred: str) -> str:
    """Return the preferred font if present, else warn and return TkDefaultFont's family (D17).

    Args:
        root: Widget used to list fonts.
        preferred: font_family from the config file.

    Returns:
        The font family actually used.
    """
    if preferred in tkfont.families(root):
        return preferred
    fallback = str(tkfont.nametofont("TkDefaultFont", root).actual("family"))
    print(f"フォント{preferred}が無いため{fallback}を使う", file=sys.stderr)
    return fallback


@dataclass(frozen=True)
class Fonts:
    """Font set used on screen (design doc 1.3)."""

    verdict: tkfont.Font
    body: tkfont.Font
    arrow: tkfont.Font

    @classmethod
    def create(cls, root: tk.Misc, family: str) -> "Fonts":
        """Build the font set from a family name. A negative size means pixels."""
        return cls(
            verdict=tkfont.Font(root, family=family, size=-18, weight="bold"),
            body=tkfont.Font(root, family=family, size=-14),
            arrow=tkfont.Font(root, family=family, size=-24),
        )


def row_layout(root: tk.Misc, fonts: Fonts) -> int:
    """Choose the tile line count. 2 lines if the 3-line total pitch exceeds 58 px (design doc 1.2).

    Args:
        root: Unused, but tells the caller that font measuring happens after Tk init.
        fonts: Fonts to measure.

    Returns:
        2 or 3.
    """
    total = fonts.verdict.metrics("linespace") + fonts.body.metrics("linespace") * 2
    return 2 if total > ROW_LIMIT_PX else 3
