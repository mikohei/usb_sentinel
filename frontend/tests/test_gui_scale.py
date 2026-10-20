"""GUI tests for drawing the 480x320 design at scale 2.0 (@pytest.mark.gui).

Fixtures and scene material are borrowed from test_gui_smoke.
"""

import tkinter as tk

import pytest

from tests.test_gui_smoke import NOW, SCAN_ONLY_VIEW, FakeMonitor, fonts, root, scene_a_slots
from usb_sentinel_display.libs.config import DisplayConfig, PortMapping
from usb_sentinel_display.libs.geometry import Geometry
from usb_sentinel_display.libs.monitor import Snapshot
from usb_sentinel_display.libs.ui import confirm as confirm_mod
from usb_sentinel_display.libs.ui import theme
from usb_sentinel_display.libs.ui.app import App, AppOptions
from usb_sentinel_display.libs.ui.confirm import ConfirmView
from usb_sentinel_display.libs.ui.theme import Fonts
from usb_sentinel_display.libs.ui.tiles import TilesView

__all__ = ["fonts", "root"]

pytestmark = pytest.mark.gui

SCALE = 2.0


@pytest.fixture(autouse=True)
def state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """Point the language store at tmp_path so the dev machine's real file is untouched."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))


def make_scaled_app(monitor: FakeMonitor) -> App:
    """Build App windowed at --scale 2.0. Skip if tk.Tk() cannot be created."""
    try:
        app = App(
            DisplayConfig(ports=tuple(PortMapping(f"P{i}", (f"1-1.{i}",)) for i in range(1, 5))),
            AppOptions(windowed=True, kiosk=False, scale=SCALE),
            runner=None,
            now=lambda: NOW,
            monitor=monitor,
        )
    except tk.TclError as exc:
        pytest.skip(f"cannot create tk.Tk(): {exc}")
    app.root.update()
    return app


class TestAppScaled:
    def test_frame_canvases_qr_and_fonts(self) -> None:
        """Frame 960x640, left canvas 320 wide, v7 QR 583 px, fonts about twice as tall."""
        app = make_scaled_app(FakeMonitor(Snapshot(scene_a_slots(), 0, None, None, 4)))
        try:
            assert app.geometry.scale == SCALE
            assert app.frame.winfo_reqwidth() == 960
            assert app.frame.winfo_reqheight() == 640
            assert app.left_canvas.winfo_reqwidth() == 320
            assert app.right_canvas.winfo_reqwidth() == 640
            app.tick()
            app.root.update()
            image = app.qr_view.image
            assert image is not None
            assert image.width() == 583
            family = str(app.fonts.body.actual("family"))
            base = Fonts.create(app.root, family)
            ratio = app.fonts.body.metrics("linespace") / base.body.metrics("linespace")
            assert ratio == pytest.approx(SCALE, abs=0.15)
        finally:
            app.root.destroy()

    def test_windowed_default_is_identity(self) -> None:
        """Without --scale a windowed App keeps the 480x320 frame."""
        try:
            app = App(
                DisplayConfig(),
                AppOptions(windowed=True, kiosk=False),
                runner=None,
                now=lambda: NOW,
                monitor=FakeMonitor(Snapshot(scene_a_slots())),
            )
        except tk.TclError as exc:
            pytest.skip(f"cannot create tk.Tk(): {exc}")
        try:
            app.root.update()
            assert app.geometry.is_identity
            assert app.frame.winfo_reqwidth() == 480
            assert app.frame.winfo_reqheight() == 320
        finally:
            app.root.destroy()


class TestTilesViewScaled:
    def test_taps_are_mapped_to_design_units(self, root: tk.Tk, fonts: Fonts) -> None:
        """On a 320x640 canvas (100,540) is prev and (300,100) is tile 0."""
        canvas = tk.Canvas(root, width=320, height=640)
        canvas.pack()
        calls: list[object] = []
        view = TilesView(
            canvas,
            fonts,
            lambda i: calls.append(("select", i)),
            lambda d: calls.append(("step", d)),
            lambda: calls.append("language"),
            lambda: calls.append("mode"),
            3,
            geometry=Geometry(SCALE),
        )
        view.render(scene_a_slots(), 0, True, 1, NOW, "en", SCAN_ONLY_VIEW)
        root.update()
        canvas.event_generate("<Button-1>", x=100, y=540)
        canvas.event_generate("<Button-1>", x=300, y=100)
        assert calls == [("step", -1), ("select", 0)]
        # The drawing was scaled: the mode label sits at design (80, 304) -> device (160, 608).
        x, y = canvas.coords("mode_label")
        assert (x, y) == (160.0, 608.0)
        assert canvas.bbox("all")[2] >= theme.TILE_W * SCALE - 1


class TestConfirmViewScaled:
    def test_body_width_and_tap(self, root: tk.Tk, fonts: Fonts) -> None:
        """Body wrap width is 880 device px and a tap at (30,394) is digit 0."""
        frame = tk.Frame(root, width=960, height=640)
        frame.pack()
        calls: list[object] = []
        view = ConfirmView(
            frame,
            fonts,
            calls.append,
            lambda: calls.append("cancel"),
            geometry=Geometry(SCALE),
        )
        root.update()
        view.show("en", "3142", 0)
        root.update()
        canvas = view.canvas
        assert canvas.winfo_reqwidth() == 960
        assert canvas.winfo_reqheight() == 640
        (body,) = canvas.find_withtag(confirm_mod.TAG_BODY)
        assert canvas.itemcget(body, "width") == "880"
        canvas.event_generate("<Button-1>", x=30, y=394)
        assert calls == [0]
        cx0, cy0, cx1, cy1 = theme.DIALOG_CANCEL
        canvas.event_generate("<Button-1>", x=int((cx0 + cx1) * SCALE // 2), y=int(cy1 * SCALE) - 2)
        assert calls == [0, "cancel"]
