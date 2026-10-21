"""GUI tests for the tile fonts and the file count on line 3 (@pytest.mark.gui)."""

import dataclasses
import tkinter as tk

import pytest

from tests.test_gui_mode import VIEWS, tagged_slot, tile_texts, tiles_view
from tests.test_gui_smoke import NOW, fonts, root, scene_a_slots
from usb_sentinel_display.libs.models import ResultDetail, Slot, SlotState
from usb_sentinel_display.libs.ui import i18n, theme
from usb_sentinel_display.libs.ui.theme import Fonts
from usb_sentinel_display.libs.ui.tiles import TAG_GAP, TEXT_X

__all__ = ["fonts", "root"]

pytestmark = pytest.mark.gui

LINE_MAX_PX = theme.TILE_W - TEXT_X * 2


def files_slot(index: int, lines: int | None, state: SlotState = SlotState.DONE) -> Slot:
    """A scan-only slot whose hash inventory has `lines` rows."""
    slot = tagged_slot(index, "scan")
    assert slot.result is not None
    result = dataclasses.replace(slot.result, detail=ResultDetail(hash_lines=lines))
    disk = slot.disk if state is SlotState.DONE else None
    return dataclasses.replace(slot, state=state, disk=disk, result=result)


def line_items(canvas: tk.Canvas, index: int) -> list[list[int]]:
    """Text items of tile `index` grouped by line (y), top line first."""
    rows: dict[float, list[int]] = {}
    for item in tile_texts(canvas, index):
        rows.setdefault(canvas.coords(item)[1], []).append(item)
    return [rows[y] for y in sorted(rows)]


class TestFileCount:
    @pytest.mark.parametrize("lang", ["en", "ja"])
    @pytest.mark.parametrize("state", [SlotState.DONE, SlotState.REMOVED])
    def test_count_right_aligned_on_line_3(
        self, root: tk.Tk, fonts: Fonts, lang: str, state: SlotState
    ) -> None:
        """Line 3 ends with the count at x=152; status, gap and count fit 144 px. A font too wide
        for the full text (not Noto Sans CJK JP on the Pi) gets the short form with every digit."""
        canvas, view = tiles_view(root, fonts, 3)
        slots = scene_a_slots()
        slots[0] = files_slot(0, 9999, state)
        view.render(slots, 1, False, 1, NOW, lang, VIEWS["scan_only"])
        line3 = line_items(canvas, 0)[2]
        (count,) = [i for i in line3 if canvas.itemcget(i, "anchor") == "ne"]
        (status,) = [i for i in line3 if i != count]
        assert canvas.itemcget(count, "text") in (
            i18n.text(lang, "tile.files", count=9999),
            i18n.text(lang, "tile.files_short", count=9999),
        )
        assert canvas.coords(count)[0] == theme.TAG_RIGHT_X
        width = fonts.tile_body.measure
        total = (
            width(canvas.itemcget(status, "text")) + TAG_GAP + width(canvas.itemcget(count, "text"))
        )
        assert total <= LINE_MAX_PX, f"{lang} {state} {total}px"

    def test_no_count_for_an_empty_inventory_or_two_rows(self, root: tk.Tk, fonts: Fonts) -> None:
        canvas, view = tiles_view(root, fonts, 3)
        slots = scene_a_slots()
        slots[0] = files_slot(0, 0)
        view.render(slots, 0, False, 1, NOW, "ja", VIEWS["scan_only"])
        assert len(line_items(canvas, 0)[2]) == 1
        canvas, view = tiles_view(root, fonts, 2)
        slots[0] = files_slot(0, 96)
        view.render(slots, 0, False, 1, NOW, "ja", VIEWS["scan_only"])
        texts = [canvas.itemcget(i, "text") for i in tile_texts(canvas, 0)]
        assert "ファイル96件" not in texts


class TestTileFonts:
    def test_lines_2_and_3_use_the_tile_body_font(self, root: tk.Tk, fonts: Fonts) -> None:
        """Line 1 uses the tile title font; the serial, tag, status and count use tile_body."""
        canvas, view = tiles_view(root, fonts, 3)
        slots = scene_a_slots()
        slots[0] = files_slot(0, 3)
        view.render(slots, 0, False, 1, NOW, "en", VIEWS["scan_only"])
        line1, line2, line3 = line_items(canvas, 0)
        assert [canvas.itemcget(i, "font") for i in line1] == [str(fonts.tile_title)]
        assert {canvas.itemcget(i, "font") for i in line2 + line3} == {str(fonts.tile_body)}
