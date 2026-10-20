"""Font resolution in ui/theme.py. tkfont is replaced, so no Tk root is needed."""

import logging
import tkinter as tk
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
