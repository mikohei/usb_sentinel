"""Pure tap-mapping and text-fitting helpers of the GUI layer (no tk.Tk() needed)."""

import pytest

from tests.test_gui_smoke import summary as sample_summary
from usb_sentinel_display.libs.mode_view import ModeState, ModeView
from usb_sentinel_display.libs.models import AttachedDisk, ResultDetail, Slot, SlotState
from usb_sentinel_display.libs.ui import theme
from usb_sentinel_display.libs.ui.confirm import confirm_hit
from usb_sentinel_display.libs.ui.tiles_text import (
    files_text,
    fit_text,
    hit_target,
    mode_button,
    tag_key,
)


def view(state: ModeState, locked: str | None = None) -> ModeView:
    return ModeView(state=state, locked_reason=locked, notice=None, code=None, entered=0)


class TestHitTarget:
    @pytest.mark.parametrize(("y", "index"), [(0, 0), (63, 0), (64, 1), (191, 2), (255, 3)])
    def test_tiles(self, y: int, index: int) -> None:
        """y below 256 maps to the tile y // 64 regardless of x."""
        assert hit_target(0, y) == ("tile", index)
        assert hit_target(159, y) == ("tile", index)

    @pytest.mark.parametrize("y", [256, 272, 287])
    def test_navigation_row_by_x(self, y: int) -> None:
        """256 <= y < 288: prev for x < 53, language for 53 <= x < 107, next from 107."""
        assert hit_target(0, y) == ("prev", 0)
        assert hit_target(52, y) == ("prev", 0)
        assert hit_target(53, y) == ("language", 0)
        assert hit_target(106, y) == ("language", 0)
        assert hit_target(107, y) == ("next", 0)
        assert hit_target(159, y) == ("next", 0)

    @pytest.mark.parametrize("y", [288, 304, 319])
    @pytest.mark.parametrize("x", [0, 80, 159])
    def test_mode_row(self, x: int, y: int) -> None:
        """y >= 288 is the mode button at every x."""
        assert hit_target(x, y) == ("mode", 0)

    def test_constants_match_layout(self) -> None:
        """The rows are 32 px each and follow the four 64 px tiles."""
        assert theme.NAV_ROW_Y0 == theme.TILE_H * theme.TILE_COUNT == 256
        assert theme.NAV_ROW_Y0 + theme.NAV_ROW_H == theme.MODE_ROW_Y0 == 288
        assert theme.MODE_ROW_Y0 + theme.MODE_ROW_H == theme.SCREEN_H
        assert theme.MODE_LABEL_MAX_PX == theme.TILE_W - 2 * 4
        assert theme.TAG_RIGHT_X == 152
        assert not hasattr(theme, "BTN_H")


class TestFitText:
    @staticmethod
    def measure(text: str) -> int:
        """Fake font: every character is 10 px wide."""
        return 10 * len(text)

    def test_fits_unchanged(self) -> None:
        assert fit_text("ABCDE", 50, self.measure) == "ABCDE"
        assert fit_text("", 0, self.measure) == ""

    def test_cut_with_ellipsis(self) -> None:
        """Characters are dropped from the end and an ellipsis is added until it fits."""
        assert fit_text("ABCDEFGH", 50, self.measure) == "ABCD…"
        assert fit_text("ABCDEFGH", 49, self.measure) == "ABC…"
        assert fit_text("ABCDEFGH", 10, self.measure) == "…"

    def test_nothing_fits(self) -> None:
        assert fit_text("ABC", 9, self.measure) == ""
        assert fit_text("ABC", 0, self.measure) == ""


class TestTagKey:
    def test_mapping(self) -> None:
        assert tag_key("scan") == "mode.tag_scan"
        assert tag_key("erased") == "mode.tag_erased"
        assert tag_key(None) is None
        assert tag_key("bogus") is None


class TestModeButton:
    def test_scan_only_enabled(self) -> None:
        assert mode_button(view(ModeState.SCAN_ONLY)) == ("mode.button_scan_only", "button", "text")

    def test_confirming_looks_like_scan_only(self) -> None:
        assert mode_button(view(ModeState.CONFIRMING)) == (
            "mode.button_scan_only",
            "button",
            "text",
        )

    def test_full(self) -> None:
        assert mode_button(view(ModeState.FULL)) == ("mode.button_full", "erase_on", "text")

    def test_stopping(self) -> None:
        assert mode_button(view(ModeState.STOPPING)) == (
            "mode.button_stopping",
            "erase_on",
            "text",
        )

    def test_locked(self) -> None:
        assert mode_button(view(ModeState.SCAN_ONLY, "locked_ceiling")) == (
            "mode.button_locked",
            "button",
            "empty_text",
        )

    def test_colour_keys_exist(self) -> None:
        for state in ModeState:
            _key, background, foreground = mode_button(view(state))
            assert background in theme.COLORS
            assert foreground in theme.COLORS
        assert theme.COLORS["erase_on"] == "#BF360C"
        assert theme.COLORS["dialog"] == "#202020"
        assert theme.COLORS["dialog_key"] == "#424242"
        assert theme.COLORS["separator"] == "#000000"


class TestConfirmHit:
    @pytest.mark.parametrize("y", [196, 250, 303])
    def test_digit_buttons(self, y: int) -> None:
        """The four buttons map left to right to digit indices 0-3."""
        for index, (x0, x1) in enumerate(theme.DIALOG_KEYS):
            assert confirm_hit(x0, y) == ("digit", index)
            assert confirm_hit((x0 + x1) // 2, y) == ("digit", index)
            assert confirm_hit(x1 - 1, y) == ("digit", index)

    @pytest.mark.parametrize("x", [120, 236, 0, 13, 466, 479])
    def test_gaps_between_buttons(self, x: int) -> None:
        assert confirm_hit(x, 250) is None

    def test_vertical_boundaries(self) -> None:
        assert confirm_hit(60, 195) is None
        assert confirm_hit(60, 196) == ("digit", 0)
        assert confirm_hit(60, 304) is None

    def test_cancel_button(self) -> None:
        assert confirm_hit(392, 4) == ("cancel", 0)
        assert confirm_hit(434, 22) == ("cancel", 0)
        assert confirm_hit(475, 39) == ("cancel", 0)
        assert confirm_hit(391, 22) is None
        assert confirm_hit(476, 22) is None
        assert confirm_hit(434, 3) is None
        assert confirm_hit(434, 40) is None

    def test_text_area_is_inert(self) -> None:
        """Taps on the title, body, code and hint do nothing."""
        for y in (22, 48, 140, 172):
            assert confirm_hit(240, y) is None

    def test_constants(self) -> None:
        assert theme.DIALOG_KEYS == ((14, 118), (130, 234), (246, 350), (362, 466))
        assert theme.DIALOG_KEY_Y == (196, 304)
        assert theme.DIALOG_CANCEL == (392, 4, 476, 40)


class TestFilesText:
    @staticmethod
    def slot(state: SlotState, lines: int | None) -> Slot:
        result = sample_summary(detail=ResultDetail(hash_lines=lines))
        disk = AttachedDisk("sda", "1-1.1", "ABC", 0.0) if state is SlotState.DONE else None
        return Slot(0, "P1", state, disk, result, None, False)

    @pytest.mark.parametrize("state", [SlotState.DONE, SlotState.REMOVED])
    def test_count_when_the_inventory_has_rows(self, state: SlotState) -> None:
        """A finished result with hash inventory rows names the file count."""
        assert files_text(self.slot(state, 96), "ja") == ("ファイル96件", "96件")
        assert files_text(self.slot(state, 1), "en") == ("Files: 1", "F:1")

    @pytest.mark.parametrize("lines", [0, None])
    def test_nothing_for_an_empty_or_missing_inventory(self, lines: int | None) -> None:
        assert files_text(self.slot(SlotState.DONE, lines), "ja") is None

    def test_nothing_without_a_finished_result(self) -> None:
        inspecting = Slot(0, "P1", SlotState.INSPECTING, None, None, None, False)
        assert files_text(inspecting, "ja") is None
