"""Font resolution in ui/theme.py. tkfont is replaced, so no Tk root is needed."""

import logging
import tkinter as tk
from tkinter import font as tkfont
from typing import cast

import pytest

from usb_sentinel_display.libs.ui import theme


class _FakeFont:
    def actual(self, option: str) -> str:
        return "DejaVu Sans" if option == "family" else ""


@pytest.fixture
def fake_tkfont(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(theme.tkfont, "families", lambda root: ("DejaVu Sans", "Noto Sans"))
    monkeypatch.setattr(theme.tkfont, "nametofont", lambda name, root: _FakeFont())


ROOT = cast(tk.Misc, object())


@pytest.mark.usefixtures("fake_tkfont")
class TestResolveFontFamily:
    def test_present_font_is_used_without_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        assert theme.resolve_font_family(ROOT, "Noto Sans") == "Noto Sans"
        assert caplog.records == []

    def test_missing_font_falls_back_and_logs_warning(
        self, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A missing font logs one WARNING and returns TkDefaultFont's family."""
        assert theme.resolve_font_family(ROOT, "Noto Sans CJK JP") == "DejaVu Sans"
        assert [(r.name, r.levelno, r.getMessage()) for r in caplog.records] == [
            (
                "usb_sentinel_display.libs.ui.theme",
                logging.WARNING,
                "Font Noto Sans CJK JP not found; using DejaVu Sans",
            )
        ]
        assert capsys.readouterr().out == ""


class TestFontFallbacks:
    def test_fallback_list_picks_first_installed_cjk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """設定のフォントが無ければ FONT_FALLBACKS の先頭から入っているものを選ぶ。"""
        monkeypatch.setattr(
            theme.tkfont, "families", lambda root: ("DejaVu Sans", "IPAGothic", "VL Gothic")
        )
        monkeypatch.setattr(theme.tkfont, "nametofont", lambda name, root: _FakeFont())
        assert theme.resolve_font(ROOT, "Noto Sans CJK JP") == ("IPAGothic", True)

    def test_chosen_family_is_logged_info(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """選んだフォント名を INFO で記録する。"""
        monkeypatch.setattr(theme.tkfont, "families", lambda root: ("Noto Sans CJK JP",))
        caplog.set_level(logging.INFO, logger="usb_sentinel_display.libs.ui.theme")
        assert theme.resolve_font(ROOT, "Noto Sans CJK JP") == ("Noto Sans CJK JP", True)
        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.INFO, "Font family: Noto Sans CJK JP")
        ]

    def test_no_cjk_font_reports_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """日本語フォントが1つも無ければ found=False を返す。"""
        monkeypatch.setattr(theme.tkfont, "families", lambda root: ("DejaVu Sans",))
        monkeypatch.setattr(theme.tkfont, "nametofont", lambda name, root: _FakeFont())
        assert theme.resolve_font(ROOT, "Noto Sans CJK JP") == ("DejaVu Sans", False)


class TestVerdictColors:
    def test_scan_only_color_is_distinct(self) -> None:
        """検査のみの色は、異常なしとも判定不能とも違う。"""
        scan_only = theme.verdict_color("No Defect (Scan Only)")
        assert scan_only not in (
            theme.verdict_color("No Defect"),
            theme.verdict_color("Inconclusive"),
            theme.UNKNOWN_VERDICT_COLOR,
        )


class _LineFont:
    """Stands in for tkfont.Font in row_layout: only metrics("linespace")."""

    def __init__(self, linespace: int) -> None:
        self.linespace = linespace

    def metrics(self, option: str) -> int:
        assert option == "linespace"
        return self.linespace


def _tile_fonts(title: int, body: int) -> theme.Fonts:
    """A font set whose tile fonts have the given line pitches; the others are huge."""
    huge = cast(tkfont.Font, _LineFont(99))
    return theme.Fonts(
        verdict=huge,
        body=huge,
        arrow=huge,
        verdict_compact=huge,
        tile_title=cast(tkfont.Font, _LineFont(title)),
        tile_body=cast(tkfont.Font, _LineFont(body)),
    )


class TestTileFonts:
    def test_row_layout_counts_only_the_tile_fonts(self) -> None:
        """Noto Sans CJK JP on the Pi: 16 px bold (24) + 11 px (17) x 2 = 58 gives 3 lines."""
        assert theme.row_layout(ROOT, _tile_fonts(24, 17)) == 3
        assert theme.row_layout(ROOT, _tile_fonts(24, 18)) == 2

    def test_sizes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tile line 1 16 px bold (long labels 12 px bold), lines 2-3 11 px; the QR strip, the
        buttons and the dialog keep 18 px bold / 14 px."""
        monkeypatch.setattr(theme.tkfont, "Font", lambda root, **options: options)
        fonts = theme.Fonts.create(ROOT, "F")
        sizes = {name: getattr(fonts, name) for name in theme.Fonts.__dataclass_fields__}
        assert sizes == {
            "verdict": {"family": "F", "size": -18, "weight": "bold"},
            "body": {"family": "F", "size": -14},
            "arrow": {"family": "F", "size": -24},
            "verdict_compact": {"family": "F", "size": -12, "weight": "bold"},
            "tile_title": {"family": "F", "size": -16, "weight": "bold"},
            "tile_body": {"family": "F", "size": -11},
        }
