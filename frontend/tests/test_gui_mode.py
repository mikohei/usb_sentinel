"""GUI tests for the mode button, tile tags and strip notices (@pytest.mark.gui).

Shared helpers for the App-level cases in test_gui_mode_app.py: mode_dirs() builds the control,
host and input directories under tmp_path and make_mode_app() builds an App on them with the
fixed code CODE.
"""

import dataclasses
import tkinter as tk
from pathlib import Path

import pytest

from tests.test_gui_smoke import NOW, FakeMonitor, disk, fonts, root, scene_a_slots, summary
from usb_sentinel_display.libs.config import DisplayConfig, PortMapping
from usb_sentinel_display.libs.mode_view import ModeState, ModeView
from usb_sentinel_display.libs.models import Slot, SlotState
from usb_sentinel_display.libs.monitor import Snapshot
from usb_sentinel_display.libs.ui import i18n, theme
from usb_sentinel_display.libs.ui.app import App, AppOptions
from usb_sentinel_display.libs.ui.qr_view import STRIP_TEXT_X
from usb_sentinel_display.libs.ui.theme import Fonts
from usb_sentinel_display.libs.ui.tiles import TAG_GAP, TEXT_X, TilesView

__all__ = ["fonts", "root"]

pytestmark = pytest.mark.gui

CODE = "3142"
# Indices (0-based) that enter CODE through select() / the dialog buttons.
CODE_INDICES = (2, 0, 3, 1)
STRIP_MAX_PX = theme.QR_W - STRIP_TEXT_X
LINE2_MAX_PX = theme.TILE_W - TEXT_X * 2
VIEWS = {
    "scan_only": ModeView(ModeState.SCAN_ONLY, None, None, None, 0),
    "confirming": ModeView(ModeState.CONFIRMING, None, None, CODE, 1),
    "full": ModeView(ModeState.FULL, None, None, None, 0),
    "stopping": ModeView(ModeState.STOPPING, None, "reset_failed", None, 0),
    "locked": ModeView(ModeState.SCAN_ONLY, "locked_ceiling", None, None, 0),
}


@pytest.fixture(autouse=True)
def state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Keep the language and strike files under tmp_path/state."""
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


def mode_dirs(tmp_path: Path, *, enabled: bool = True) -> tuple[Path, Path, Path]:
    """Create control/, host/ (config.toml + docker.env) and input/input0 under tmp_path."""
    control = tmp_path / "control"
    host = tmp_path / "host"
    inputs = tmp_path / "input"
    control.mkdir(exist_ok=True)
    host.mkdir(exist_ok=True)
    (inputs / "input0").mkdir(parents=True, exist_ok=True)
    (host / "config.toml").write_text("allow_destructive = true\n", encoding="utf-8")
    access = "rw" if enabled else "ro"
    (host / "docker.env").write_text(f"USB_SENTINEL_DEVICE_ACCESS={access}\n", encoding="utf-8")
    return control, host, inputs


def make_mode_app(
    tmp_path: Path,
    monitor: FakeMonitor | None = None,
    *,
    enabled: bool = True,
    kiosk: bool = False,
) -> App:
    """Build a windowed App on mode dirs under tmp_path, ticked once. Skip if tk.Tk() fails."""
    control, host, inputs = mode_dirs(tmp_path, enabled=enabled)
    config = DisplayConfig(
        ports=tuple(PortMapping(f"P{i}", (f"1-1.{i}",)) for i in range(1, 5)),
        results_dir=tmp_path / "results",
        sqlite_path=None,
        control_dir=control,
        host_config_dir=host,
        sys_class_input=inputs,
    )
    try:
        app = App(
            config,
            AppOptions(windowed=True, kiosk=kiosk),
            runner=None,
            now=lambda: NOW,
            monitor=monitor if monitor is not None else FakeMonitor(scene_snapshot()),
            make_code=lambda: CODE,
        )
    except tk.TclError as exc:
        pytest.skip(f"cannot create tk.Tk(): {exc}")
    app.tick()
    app.root.update()
    app.root.focus_force()
    app.root.update()
    return app


def scene_snapshot() -> Snapshot:
    return Snapshot(scene_a_slots(), 0, None, None, 4)


def mode_file(tmp_path: Path) -> str | None:
    """Content of control/mode, or None when the file does not exist."""
    path = tmp_path / "control" / "mode"
    return path.read_text(encoding="utf-8") if path.exists() else None


def strike_text(state_home: Path) -> str | None:
    path = state_home / "usb-sentinel-display" / "erase-strikes"
    return path.read_text(encoding="utf-8") if path.exists() else None


def canvas_texts(canvas: tk.Canvas) -> list[str]:
    """All text items currently drawn on a canvas."""
    return [
        str(canvas.itemcget(item, "text"))
        for item in canvas.find_all()
        if canvas.type(item) == "text"
    ]


def tagged_slot(index: int, tag: str, serial: str = "ABC123456789XYZ") -> Slot:
    """DONE slot whose result gives drive_tag() == tag ("erased" or "scan")."""
    if tag == "erased":
        result = summary(serial=serial)
    else:
        result = dataclasses.replace(
            summary(serial=serial),
            steps=(("malware", "pass"), ("capacity", "skipped"), ("speed", "skipped")),
            mode="scan_only",
        )
    return Slot(
        index, f"P{index + 1}", SlotState.DONE, disk("sda", "1-1.1", serial), result, None, False
    )


class TestModeButton:
    @pytest.mark.parametrize("lang", ["en", "ja"])
    @pytest.mark.parametrize("name", sorted(VIEWS))
    def test_label_fits_152_px(self, root: tk.Tk, fonts: Fonts, name: str, lang: str) -> None:
        """Every rendered mode label measures at most MODE_LABEL_MAX_PX in fonts.body."""
        canvas = tk.Canvas(root, width=160, height=320)
        view = TilesView(
            canvas, fonts, lambda _i: None, lambda _d: None, lambda: None, lambda: None, 3
        )
        view.render(scene_a_slots(), 0, False, 1, NOW, lang, VIEWS[name])
        (label,) = [str(canvas.itemcget(i, "text")) for i in canvas.find_withtag("mode_label")]
        width = fonts.body.measure(label)
        assert width <= theme.MODE_LABEL_MAX_PX, f"{lang} {label!r} {width}px"
        x, y = canvas.coords("mode_label")
        assert (x, y) == (80, 304)

    def test_tap_on_mode_row_calls_on_mode(self, root: tk.Tk, fonts: Fonts) -> None:
        """Taps at y >= 288 call on_mode; the navigation row above still works."""
        calls: list[str] = []
        canvas = tk.Canvas(root, width=160, height=320)
        canvas.pack()
        TilesView(
            canvas,
            fonts,
            lambda i: calls.append(f"tile{i}"),
            lambda d: calls.append(f"step{d}"),
            lambda: calls.append("lang"),
            lambda: calls.append("mode"),
            3,
        )
        root.update()
        for x, y in ((0, 288), (80, 304), (159, 319)):
            canvas.event_generate("<Button-1>", x=x, y=y)
        canvas.event_generate("<Button-1>", x=80, y=287)
        canvas.event_generate("<Button-1>", x=20, y=256)
        canvas.event_generate("<Button-1>", x=80, y=255)
        assert calls == ["mode", "mode", "mode", "lang", "step-1", "tile3"]


def tile_texts(canvas: tk.Canvas, index: int) -> list[int]:
    """Text items drawn inside tile `index` (by their y coordinate)."""
    y0, y1 = index * theme.TILE_H, (index + 1) * theme.TILE_H
    return [
        item
        for item in canvas.find_all()
        if canvas.type(item) == "text" and y0 <= canvas.coords(item)[1] < y1
    ]


def tag_items(canvas: tk.Canvas, index: int) -> list[int]:
    """Right-aligned (anchor ne) text items inside tile `index`: the drive tag."""
    return [i for i in tile_texts(canvas, index) if canvas.itemcget(i, "anchor") == "ne"]


def tiles_view(root: tk.Tk, fonts: Fonts, rows: int) -> tuple[tk.Canvas, TilesView]:
    canvas = tk.Canvas(root, width=160, height=320)
    view = TilesView(
        canvas, fonts, lambda _i: None, lambda _d: None, lambda: None, lambda: None, rows
    )
    return canvas, view


class TestTileTag:
    @pytest.mark.parametrize("lang", ["en", "ja"])
    @pytest.mark.parametrize("tag", ["erased", "scan"])
    def test_tag_and_serial_fit_line_2(
        self, root: tk.Tk, fonts: Fonts, tag: str, lang: str
    ) -> None:
        """Tag right-aligned at x=152 plus the shortened serial fit 144 px with a 6 px gap."""
        canvas, view = tiles_view(root, fonts, 3)
        slots = scene_a_slots()
        slots[0] = tagged_slot(0, tag)
        view.render(slots, 0, False, 1, NOW, lang, VIEWS["scan_only"])
        (tag_item,) = tag_items(canvas, 0)
        tag_text = str(canvas.itemcget(tag_item, "text"))
        assert tag_text == i18n.text(lang, f"mode.tag_{tag}")
        x, y = canvas.coords(tag_item)
        assert x == theme.TAG_RIGHT_X
        serials = [i for i in tile_texts(canvas, 0) if canvas.coords(i)[1] == y and i != tag_item]
        assert len(serials) == 1
        serial = str(canvas.itemcget(serials[0], "text"))
        assert serial.endswith("…")
        total = fonts.tile_body.measure(serial) + TAG_GAP + fonts.tile_body.measure(tag_text)
        assert total <= LINE2_MAX_PX, f"{lang} {tag} {serial!r} {total}px"

    def test_removed_slot_also_tagged(self, root: tk.Tk, fonts: Fonts) -> None:
        canvas, view = tiles_view(root, fonts, 3)
        slots = scene_a_slots()
        slots[0] = dataclasses.replace(tagged_slot(0, "erased"), state=SlotState.REMOVED, disk=None)
        view.render(slots, 1, False, 1, NOW, "ja", VIEWS["scan_only"])
        (tag_item,) = tag_items(canvas, 0)
        assert canvas.itemcget(tag_item, "text") == "消去済"

    def test_no_tag_without_mode_or_in_two_rows(self, root: tk.Tk, fonts: Fonts) -> None:
        """A result without mode and non-destructive steps, or a 2-line layout, draws no tag."""
        slots = scene_a_slots()
        untagged = tagged_slot(0, "scan")
        assert untagged.result is not None
        slots[0] = dataclasses.replace(
            untagged, result=dataclasses.replace(untagged.result, mode=None)
        )
        canvas, view = tiles_view(root, fonts, 3)
        view.render(slots, 0, False, 1, NOW, "en", VIEWS["scan_only"])
        assert tag_items(canvas, 0) == []
        assert len(tag_items(canvas, 1)) == 1  # the malware slot ran capacity/speed: Erased
        canvas, view = tiles_view(root, fonts, 2)
        slots[0] = tagged_slot(0, "erased")
        view.render(slots, 0, False, 1, NOW, "en", VIEWS["scan_only"])
        assert tag_items(canvas, 0) == []
        assert "Erased" not in canvas_texts(canvas)


class TestStripNotices:
    @pytest.mark.parametrize("lang", ["en", "ja"])
    def test_all_mode_notices_fit_strip(self, fonts: Fonts, lang: str) -> None:
        """Every notice.mode.* text measures at most 316 px in the strip font (fonts.body)."""
        keys = [k for k in i18n.MESSAGES["en"] if k.startswith("notice.mode.")]
        assert "notice.mode.erase_off_busy" in keys
        assert "notice.mode.start_unknown" in keys
        for key in keys:
            label = i18n.text(lang, key)
            width = fonts.body.measure(label)
            assert width <= STRIP_MAX_PX, f"{lang} {key} {label!r} {width}px > {STRIP_MAX_PX}px"
