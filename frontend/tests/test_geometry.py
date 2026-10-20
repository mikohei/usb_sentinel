"""Pure tests for libs/geometry.py (design units -> device pixels) and resolution_warning."""

import pytest

from usb_sentinel_display.libs.geometry import (
    DESIGN_H,
    DESIGN_W,
    IDENTITY,
    MAX_SCALE,
    MIN_SCALE,
    Geometry,
    compute_scale,
    letterbox,
)
from usb_sentinel_display.libs.ui.strip import resolution_warning


class TestComputeScale:
    @pytest.mark.parametrize(
        ("width", "height", "expected"),
        [
            (800, 600, 1.6667),
            (800, 480, 1.5),
            (960, 640, 2.0),
            (480, 320, 1.0),
            (400, 300, 0.8333),
            (1920, 1080, 3.375),
        ],
    )
    def test_fit_shorter_side(self, width: int, height: int, expected: float) -> None:
        """The scale is the smaller of width/480 and height/320."""
        assert compute_scale(width, height) == pytest.approx(expected, abs=1e-3)

    @pytest.mark.parametrize(("width", "height"), [(0, 320), (480, -1)])
    def test_non_positive_raises(self, width: int, height: int) -> None:
        with pytest.raises(ValueError, match="positive"):
            compute_scale(width, height)

    def test_constants(self) -> None:
        assert (DESIGN_W, DESIGN_H) == (480, 320)
        assert (MIN_SCALE, MAX_SCALE) == (0.25, 8.0)


class TestLetterbox:
    @pytest.mark.parametrize(
        ("width", "height", "scale", "expected"),
        [
            (800, 600, 1.6667, (0, 33, 800, 533)),
            (800, 480, 1.5, (40, 0, 720, 480)),
            (960, 640, 2.0, (0, 0, 960, 640)),
            (480, 320, 1.0, (0, 0, 480, 320)),
        ],
    )
    def test_offsets_and_frame(
        self, width: int, height: int, scale: float, expected: tuple[int, int, int, int]
    ) -> None:
        """Frame size is the rounded design size; offsets centre it in the screen."""
        result = letterbox(width, height, scale)
        assert result == expected
        x0, y0, frame_w, frame_h = result
        assert frame_w <= width
        assert frame_h <= height
        assert x0 >= 0
        assert y0 >= 0


class TestGeometry:
    def test_px_design_and_fonts_at_800x600(self) -> None:
        """At k=1.6667: 160 -> 267 px, fonts 18/14/24 -> 30/23/40, frame 800x533."""
        geometry = Geometry(compute_scale(800, 600))
        assert geometry.px(160) == 267
        assert geometry.design(267) == pytest.approx(160.2, abs=0.05)
        assert geometry.font_px(18) == 30
        assert geometry.font_px(14) == 23
        assert geometry.font_px(24) == 40
        assert geometry.frame_size == (800, 533)

    def test_font_px_floors_at_one(self) -> None:
        assert Geometry(0.01).font_px(14) == 1

    def test_is_identity(self) -> None:
        assert IDENTITY.is_identity is True
        assert IDENTITY.scale == 1.0
        assert Geometry(1.5).is_identity is False
        assert IDENTITY.frame_size == (480, 320)


class TestResolutionWarning:
    @pytest.mark.parametrize(("width", "height"), [(480, 320), (800, 600), (960, 640), (0, 0)])
    def test_none_when_design_fits(self, width: int, height: int) -> None:
        """No warning at or above 480x320, nor for transient unmapped sizes."""
        assert resolution_warning(width, height) is None

    @pytest.mark.parametrize(("width", "height"), [(479, 320), (400, 300)])
    def test_tuple_when_smaller(self, width: int, height: int) -> None:
        assert resolution_warning(width, height) == (width, height)
