"""App（tkルート、tick結線、キー、QRモード、言語切替）の煙テスト。@pytest.mark.gui。

FakeMonitor／make_app()／scene_a_slots()はtest_gui_smokeから借りる。
"""

from pathlib import Path

import pytest

from tests.test_gui_smoke import DETAIL, FakeMonitor, disk, make_app, scene_a_slots, summary
from usb_sentinel_display.libs.models import Slot, SlotState
from usb_sentinel_display.libs.monitor import Snapshot
from usb_sentinel_display.libs.ui.strip import strip_text

pytestmark = pytest.mark.gui


@pytest.fixture(autouse=True)
def state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """言語の記録先をtmp_pathに向け、開発機の実ファイルを読み書きしない。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


class TestApp:
    def test_tick_render_and_destroy(self) -> None:
        """windowedでAppを組み、tick→update→destroyが例外なく終わる。"""
        monitor = FakeMonitor(Snapshot(scene_a_slots(), 0, None, None, 4))
        app = make_app(monitor)
        try:
            app.tick()
            app.root.update()
            app.select(1)
            app.step(1)
            assert app.selected == 2
            app.force_reload()
            assert monitor.reloads == 1
            assert app.qr_view.image is None
            app.select(0)
            assert app.qr_view.image is not None
        finally:
            app.root.destroy()

    def test_step_switches_port_or_page(self) -> None:
        """通常QRでは◀▶がポート、詳細QRではページ送り（1→2→3→1）でポートは変わらない。"""
        app = make_app(FakeMonitor(Snapshot(scene_a_slots(), 0, None, None, 4)))
        try:
            app.tick()
            assert app.qr_mode == 0
            app.step(1)
            assert app.selected == 1
            app.toggle_qr_mode()
            assert app.qr_mode == 1
            for expected in (2, 3, 1):
                app.step(1)
                assert app.qr_mode == expected
                assert app.selected == 1
            app.step(-1)
            assert app.qr_mode == 3
            app.select(0)
            assert app.qr_mode == 3
            app.toggle_qr_mode()
            assert app.qr_mode == 0
        finally:
            app.root.destroy()

    def test_right_canvas_tap_toggles_qr_mode(self) -> None:
        """右キャンバスのタップで通常↔詳細が切り替わり、詳細1〜3の各モードで画像がある。"""
        slots = scene_a_slots()
        slots[0] = Slot(0, "P1", SlotState.DONE, slots[0].disk, summary(detail=DETAIL), None, False)
        app = make_app(FakeMonitor(Snapshot(slots, 0, None, None, 4)))
        try:
            app.tick()
            app.root.update()
            app.right_canvas.event_generate("<Button-1>", x=10, y=10)
            assert app.qr_mode == 1
            for _ in range(3):
                assert app.qr_view.image is not None
                app.step(1)
            app.right_canvas.event_generate("<Button-1>", x=10, y=10)
            assert app.qr_mode == 0
        finally:
            app.root.destroy()

    def test_language_button_saves_state(self, state_home: Path) -> None:
        """左キャンバス(80, 290)のクリックで言語がjaになり、XDG_STATE_HOME配下に記録される。"""
        app = make_app(FakeMonitor(Snapshot(scene_a_slots(), 0, None, None, 4)))
        try:
            assert app.language == "en"
            app.tick()
            app.root.update()
            app.left_canvas.event_generate("<Button-1>", x=80, y=290)
            assert app.language == "ja"
            saved = state_home / "usb-sentinel-display" / "language"
            assert saved.read_text(encoding="utf-8") == "ja\n"
            app.left_canvas.event_generate("<Button-1>", x=20, y=290)
            assert app.language == "ja"
            assert app.selected == 3
            app.left_canvas.event_generate("<Button-1>", x=140, y=290)
            assert app.selected == 0
        finally:
            app.root.destroy()

    def test_saved_language_beats_config(self, state_home: Path) -> None:
        """記録された言語が設定より優先される。"""
        saved = state_home / "usb-sentinel-display" / "language"
        saved.parent.mkdir(parents=True)
        saved.write_text("en\n", encoding="utf-8")
        app = make_app(FakeMonitor(Snapshot(scene_a_slots())), language="ja")
        try:
            assert app.language == "en"
        finally:
            app.root.destroy()


class TestStrip:
    def test_strip_text_priority(self) -> None:
        """情報ストリップの優先順位（ja）と詳細QR中の文言。"""
        slots = scene_a_slots()
        slots[3] = Slot(3, "P4", SlotState.NO_RESULT, disk("sdd", "1-1.4", "X"), None, None, False)
        template = "usb-sentinel-docker@{kernel}.service"

        def ja(snapshot: Snapshot, selected: int, resolution: tuple[int, int] | None) -> str:
            return strip_text(snapshot, selected, template, resolution, "ja", 0)

        assert ja(Snapshot(slots), 0, (640, 480)) == "画面解像度640x480（480x320を想定）"
        assert ja(Snapshot(slots, error="E"), 0, None) == "内部エラー: E"
        assert (
            ja(Snapshot(slots, error="E", error_repeated=True), 0, None)
            == "内部エラーが継続中。標準エラー出力を確認"
        )
        assert ja(Snapshot(slots, notice="sysfs_missing"), 0, None).startswith("sysfs")
        assert ja(Snapshot(slots, overflow=2), 0, None) == "他2台"
        assert ja(Snapshot(slots), 3, None) == "journalctl -u usb-sentinel-docker@sdd.serviceを確認"
        assert ja(Snapshot(slots), 0, None) == "P1 ABC12345… d:0123456789abcdef"
        assert (
            strip_text(Snapshot(slots), 0, template, None, "ja", 2)
            == "P1 詳細2/3 ◀▶でページ d:0123456789ab"
        )
        assert (
            strip_text(Snapshot(slots), 0, template, None, "en", 1)
            == "P1 Detail 1/3 ◀▶ page d:0123456789ab"
        )
        assert strip_text(Snapshot(slots, overflow=1), 0, template, None, "en", 0) == "+1 more"
