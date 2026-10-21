import logging
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont

from ..geometry import DESIGN_H, DESIGN_W, IDENTITY, Geometry
from ..models import SlotState

logger = logging.getLogger(__name__)

# Dimensions of the design (design units; see geometry.py) and each area (design doc 1.1).
SCREEN_W = DESIGN_W
SCREEN_H = DESIGN_H
TILE_W = 160
TILE_H = 64
# Bottom-left area (y256-320) is two 32 px rows (design doc inspection-mode 3.5): the navigation
# row prev (x0-53), language (x53-107), next (x107-160), then the full-width mode button.
ARROW_BTN_W = 53
LANG_BTN_X0 = 53
LANG_BTN_X1 = 107
NAV_ROW_Y0 = 256
NAV_ROW_H = 32
MODE_ROW_Y0 = 288
MODE_ROW_H = 32
# Mode button labels must measure at most 160 - 2 x 4 px (GUI test).
MODE_LABEL_MAX_PX = 152
# Right edge of the drive tag ("Erased" / "Scan") on tile line 2.
TAG_RIGHT_X = 152
# Confirmation dialog (design doc 3.6): digit buttons x ranges, their y range, Cancel rectangle.
DIALOG_KEYS: tuple[tuple[int, int], ...] = ((14, 118), (130, 234), (246, 350), (362, 466))
DIALOG_KEY_Y = (196, 304)
DIALOG_CANCEL = (392, 4, 476, 40)
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
    # Scan only (capacity and speed not tested). Teal, distinct from full No Defect green.
    "No Defect (Scan Only)": "#00695C",
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
    # Erase mode on / stopping: an orange-red no tile state uses.
    "erase_on": "#BF360C",
    "dialog": "#202020",
    "dialog_key": "#424242",
    "separator": "#000000",
    # Root window background seen around the frame when the screen is not 3:2.
    "letterbox": "#000000",
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


# Japanese-capable families tried, in order, after config.font_family. fonts-noto-cjk
# provides the first one on Ubuntu.
FONT_FALLBACKS = (
    "Noto Sans CJK JP",
    "Noto Sans JP",
    "Noto Sans CJK SC",
    "IPAGothic",
    "IPAexGothic",
    "TakaoGothic",
    "VL Gothic",
)


def resolve_font(root: tk.Misc, preferred: str) -> tuple[str, bool]:
    """Pick config.font_family, else the first installed FONT_FALLBACKS entry (D17).

    Without Xft, Tk lists no fontconfig family, so a missing Japanese font shows up here.

    Args:
        root: Widget used to list fonts.
        preferred: font_family from the config file.

    Returns:
        (family actually used, found). found is False when neither preferred nor any
        FONT_FALLBACKS entry is installed; TkDefaultFont's family is used then.
    """
    installed = set(tkfont.families(root))
    for family in dict.fromkeys((preferred, *FONT_FALLBACKS)):
        if family in installed:
            if family != preferred:
                logger.warning("Font %s not found; using %s", preferred, family)
            logger.info("Font family: %s", family)
            return family, True
    fallback = str(tkfont.nametofont("TkDefaultFont", root).actual("family"))
    logger.warning("Font %s not found; using %s", preferred, fallback)
    return fallback, False


def resolve_font_family(root: tk.Misc, preferred: str) -> str:
    """Return only the family chosen by resolve_font.

    Args:
        root: Widget used to list fonts.
        preferred: font_family from the config file.

    Returns:
        The font family actually used.
    """
    return resolve_font(root, preferred)[0]


@dataclass(frozen=True)
class Fonts:
    """Font set used on screen (design doc 1.3)."""

    verdict: tkfont.Font
    body: tkfont.Font
    arrow: tkfont.Font
    # Smaller bold font for a tile line 1 that does not fit at the tile_title size.
    verdict_compact: tkfont.Font
    # Tile line 1 (port and verdict) and lines 2-3 (serial, tag, status, file count). Sized so
    # Noto Sans CJK JP fits 3 lines: 24 + 17 x 2 = 58 px (measured on the Pi).
    tile_title: tkfont.Font
    tile_body: tkfont.Font

    @classmethod
    def create(cls, root: tk.Misc, family: str, geometry: Geometry = IDENTITY) -> "Fonts":
        """Build the font set from a family name at device size. A negative size means pixels."""
        return cls(
            verdict=tkfont.Font(root, family=family, size=-geometry.font_px(18), weight="bold"),
            body=tkfont.Font(root, family=family, size=-geometry.font_px(14)),
            arrow=tkfont.Font(root, family=family, size=-geometry.font_px(24)),
            verdict_compact=tkfont.Font(
                root, family=family, size=-geometry.font_px(12), weight="bold"
            ),
            tile_title=tkfont.Font(root, family=family, size=-geometry.font_px(16), weight="bold"),
            tile_body=tkfont.Font(root, family=family, size=-geometry.font_px(11)),
        )


def row_layout(root: tk.Misc, fonts: Fonts, geometry: Geometry = IDENTITY) -> int:
    """Choose the tile line count. 2 lines if the 3-line total pitch of the tile fonts exceeds
    58 px (design doc 1.2).

    Args:
        root: Unused, but tells the caller that font measuring happens after Tk init.
        fonts: Fonts to measure (device pixels).
        geometry: Scale that turns the 58 design px limit into device px.

    Returns:
        2 or 3.
    """
    total = fonts.tile_title.metrics("linespace") + fonts.tile_body.metrics("linespace") * 2
    return 2 if total > geometry.px(ROW_LIMIT_PX) else 3
