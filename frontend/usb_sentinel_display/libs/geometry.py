"""Map the 480x320 design to the actual screen: scale factor, letterbox offsets, pixel helpers.

The views draw in design units and scale the Canvas once per redraw; fonts and the QR image are
produced at device size. This module imports no GUI library so ui/strip.py and cli.py can use it.
"""

from dataclasses import dataclass
from typing import Final

# Size of the design (the 3.5-inch display the layout was drawn for).
DESIGN_W: Final = 480
DESIGN_H: Final = 320
# Accepted range of --scale.
MIN_SCALE: Final = 0.25
MAX_SCALE: Final = 8.0


def compute_scale(width: int, height: int) -> float:
    """Largest factor at which the 480x320 design fits a width x height screen.

    Args:
        width: Screen width in px.
        height: Screen height in px.

    Returns:
        min(width / 480, height / 320).

    Raises:
        ValueError: When either side is not positive.
    """
    if width <= 0 or height <= 0:
        raise ValueError("screen size must be positive")
    return min(width / DESIGN_W, height / DESIGN_H)


def letterbox(width: int, height: int, scale: float) -> tuple[int, int, int, int]:
    """Frame size at scale and its centred offset in a width x height screen.

    App centres the frame with place(relx=0.5, rely=0.5, anchor="center"), so it re-centres
    itself on resize; this helper states the expected offsets and is checked by
    tests/test_geometry.py.

    Returns:
        (x0, y0, frame_w, frame_h).
    """
    frame_w = round(DESIGN_W * scale)
    frame_h = round(DESIGN_H * scale)
    return (width - frame_w) // 2, (height - frame_h) // 2, frame_w, frame_h


@dataclass(frozen=True)
class Geometry:
    """Conversion between design units (480x320) and device pixels at one scale factor."""

    scale: float

    def px(self, design: float) -> int:
        """Device pixels for a design length (rounded)."""
        return round(design * self.scale)

    def design(self, px: float) -> float:
        """Design units for a device length (e.g. a tap position)."""
        return px / self.scale

    @property
    def frame_size(self) -> tuple[int, int]:
        """Device size of the whole 480x320 frame."""
        return self.px(DESIGN_W), self.px(DESIGN_H)

    def font_px(self, design_px: int) -> int:
        """Font pixel size for a design pixel size; never below 1."""
        return max(1, round(design_px * self.scale))

    @property
    def is_identity(self) -> bool:
        """True at scale 1.0, where Canvas.scale() can be skipped."""
        return self.scale == 1.0


IDENTITY: Final = Geometry(1.0)
