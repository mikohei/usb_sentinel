import contextlib
import tkinter as tk
from collections.abc import Iterator
from pathlib import Path

import pytest

from usb_sentinel_display.libs.config import DisplayConfig, PortMapping
from usb_sentinel_display.libs.mode_view import ModeState, ModeView
from usb_sentinel_display.libs.models import (
    VERDICT_CODES,
    AttachedDisk,
    ResultDetail,
    ResultSummary,
    Slot,
    SlotState,
)
from usb_sentinel_display.libs.monitor import Snapshot
from usb_sentinel_display.libs.ui import i18n, theme
from usb_sentinel_display.libs.ui.app import App, AppOptions
from usb_sentinel_display.libs.ui.keys import bind_keys
from usb_sentinel_display.libs.ui.qr_view import QrView
from usb_sentinel_display.libs.ui.theme import Fonts
from usb_sentinel_display.libs.ui.tiles import TEXT_X, TilesView, serial_text, title_text

pytestmark = pytest.mark.gui

NOW = 1_750_000_000.0
# Mode button in its enabled scan-only state (what TilesView.render needs at minimum).
SCAN_ONLY_VIEW = ModeView(ModeState.SCAN_ONLY, None, None, None, 0)
# Input that makes detail QR page 2 127 bytes (v8) (same as TYPICAL_DETAIL in test_qr_payload).
DETAIL = ResultDetail(
    elapsed_s=312.5,
    clamav_db_version="27650",
    clamav_db_date="2025-06-01T08:00:00+00:00",
    nominal_bytes=32_000_000_000,
    advertised_bytes=32_000_000_000,
    actual_bytes=29_500_000_000,
    advertised_over=False,
    capacity_kind="short",
    lost_ratio=0.0123,
    counterfeit_reasons=(),
    link_speed_mbps=5000,
    device_class="usb3_flash",
    read_mb_s=45.67,
    write_mb_s=12.34,
    threshold_read_mb_s=50.0,
    threshold_write_mb_s=15.0,
    speed_basis_read="class",
    speed_basis_write="class",
    speed_severity="minor",
    hash_digest="abcdef0123456789" * 4,
    hash_lines=1234,
    reason_codes=("c:short", "s:minor"),
)
STEPS = (
    ("malware", "pass"),
    ("hash_inventory", "pass"),
    ("unmount", "pass"),
    ("capacity", "pass"),
    ("speed", "pass"),
)


def summary(
    verdict: str = "No Defect",
    serial: str = "ABC123456789XYZ",
    detail: ResultDetail | None = None,
) -> ResultSummary:
    """Result summary that yields the typical payload (109 bytes, v7)."""
    return ResultSummary(
        dir_name=f"20250601T120000Z-{serial}",
        path=Path("/tmp/result.json"),  # noqa: S108 -- dummy, never read
        mtime=NOW,
        verdict=verdict,
        serial=serial,
        vendor="Vendor",
        model="Model",
        kernel_name="sda",
        port_path="1-1.1",
        started_at="2025-06-01T12:00:00.123456+00:00",
        finished_at="2025-06-01T12:05:00+00:00",
        steps=STEPS,
        malware_count=0,
        digest="0123456789abcdef" * 4,
        detail=detail if detail is not None else ResultDetail(),
    )


def disk(kernel: str, port: str, serial: str) -> AttachedDisk:
    return AttachedDisk(kernel, port, serial, NOW - 600)


def scene_a_slots() -> list[Slot]:
    """Equivalent of scene A (clean, malware, inspecting, corrupt result)."""
    return [
        Slot(
            0, "P1", SlotState.DONE, disk("sda", "1-1.1", "ABC123456789XYZ"), summary(), None, False
        ),
        Slot(
            1,
            "P2",
            SlotState.DONE,
            disk("sdb", "1-1.2", "MAL0001"),
            summary("Malware Detected", "MAL0001"),
            "inactive",
            False,
        ),
        Slot(
            2,
            "P3",
            SlotState.INSPECTING,
            disk("sdc", "1-1.3", "INSPECT01"),
            None,
            "activating",
            False,
        ),
        Slot(3, "P4", SlotState.BROKEN, disk("sdd", "1-1.4", "BROKEN12"), None, None, False),
    ]


class FakeMonitor:
    """Fake Monitor that returns a fixed Snapshot."""

    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        self.reloads = 0

    def tick(self) -> Snapshot:
        return self.snapshot

    def force_reload(self) -> None:
        self.reloads += 1


@pytest.fixture(autouse=True)
def state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the language store at tmp_path so the dev machine's real file is untouched."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


def make_app(monitor: FakeMonitor, language: str = "en") -> App:
    """Build App windowed. Skip if tk.Tk() cannot be created."""
    try:
        app = App(
            DisplayConfig(
                ports=tuple(PortMapping(f"P{i}", (f"1-1.{i}",)) for i in range(1, 5)),
                language=language,
            ),
            AppOptions(windowed=True, kiosk=False),
            runner=None,
            now=lambda: NOW,
            monitor=monitor,
        )
    except tk.TclError as exc:
        pytest.skip(f"cannot create tk.Tk(): {exc}")
    app.root.update()  # see the root fixture: never destroy a root that handled no events
    return app


@pytest.fixture
def root() -> Iterator[tk.Tk]:
    """Create tk.Tk(). Skip where it cannot be created."""
    try:
        window = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"cannot create tk.Tk(): {exc}")
    # macOS Tk: a root destroyed before it ever processed events makes a later event_generate()
    # in the same process segfault, so let every root handle its first events.
    window.update()
    yield window
    with contextlib.suppress(tk.TclError):
        window.destroy()


@pytest.fixture
def fonts(root: tk.Tk) -> Fonts:
    return Fonts.create(root, theme.resolve_font_family(root, "Noto Sans CJK JP"))


class TestQrView:
    def test_v7_image_width_is_265(self, root: tk.Tk, fonts: Fonts) -> None:
        """The PhotoImage width for the typical payload (v7) is 265 px per the design table."""
        canvas = tk.Canvas(root, width=320, height=320)
        view = QrView(canvas, fonts, lambda: None)
        view.render(scene_a_slots()[0], "P1", 0, "ja")
        image = view.image
        assert image is not None
        assert image.width() == 265
        assert image.height() == 265

    def test_v8_detail_page_width_is_285(self, root: tk.Tk, fonts: Fonts) -> None:
        """The PhotoImage width for detail QR page 2 (v8) is 285 px per the design table."""
        canvas = tk.Canvas(root, width=320, height=320)
        view = QrView(canvas, fonts, lambda: None)
        slot = Slot(0, "P1", SlotState.DONE, None, summary(detail=DETAIL), None, False)
        view.render(slot, "P1", 2, "en")
        image = view.image
        assert image is not None
        assert image.width() == 285

    def test_no_result_leaves_blank(self, root: tk.Tk, fonts: Fonts) -> None:
        canvas = tk.Canvas(root, width=320, height=320)
        view = QrView(canvas, fonts, lambda: None)
        view.render(None, "未接続", 0, "ja")
        assert view.image is None


class TestTilesView:
    @pytest.mark.parametrize("lang", ["ja", "en"])
    @pytest.mark.parametrize("rows", [2, 3])
    @pytest.mark.parametrize("selected", [0, 1])
    def test_render_all_states(
        self, root: tk.Tk, fonts: Fonts, rows: int, selected: int, lang: str
    ) -> None:
        """No errors: Malware (un)selected, fallback, removed, empty, 2 lines, both languages."""
        canvas = tk.Canvas(root, width=160, height=320)
        view = TilesView(
            canvas, fonts, lambda _i: None, lambda _d: None, lambda: None, lambda: None, rows
        )
        slots = scene_a_slots()
        slots[2] = Slot(
            2, "P3", SlotState.WAITING, disk("sdc", "9-9", "FALLBACK12345"), None, None, True
        )
        slots[3] = Slot(3, "P4", SlotState.REMOVED, None, summary("Defective"), None, False)
        for blink in (True, False):
            view.render(slots, selected, blink, 2, NOW, lang, SCAN_ONLY_VIEW)
        empty = [Slot(i, f"P{i + 1}", SlotState.EMPTY, None, None, None, False) for i in range(4)]
        view.render(empty, 0, False, 1, NOW, lang, SCAN_ONLY_VIEW)

    def test_verdict_labels_fit_tile(self, fonts: Fonts) -> None:
        """All verdict labels (incl. unknown), both langs, fit tile width (160-8x2) with "P1 ".

        Only 異常なし（検査のみ） may need the compact font; it must fit at that size."""
        limit = theme.TILE_W - TEXT_X * 2
        for lang in i18n.LANGUAGES:
            labels = [i18n.verdict_label(lang, v) for v in VERDICT_CODES]
            labels.append(i18n.verdict_label(lang, "Bogus"))
            for label in labels:
                width = fonts.tile_title.measure("P1 " + label)
                if label == "異常なし（検査のみ）":
                    width = min(width, fonts.verdict_compact.measure("P1 " + label))
                assert width <= limit, f"{lang} {label!r} {width}px > {limit}px"

    def test_long_title_uses_compact_font(self, root: tk.Tk, fonts: Fonts) -> None:
        """幅に収まらない 1 行目は小さい太字に切り替え、通常の判定名は元の太字のまま描く。"""
        canvas = tk.Canvas(root, width=160, height=320)
        view = TilesView(
            canvas, fonts, lambda _i: None, lambda _d: None, lambda: None, lambda: None, 3
        )
        slots = [
            Slot(0, "P1", SlotState.DONE, disk("sda", "1-1.1", "A1"), summary(), None, False),
            Slot(
                1,
                "P2",
                SlotState.DONE,
                disk("sdb", "1-1.2", "B2"),
                summary("No Defect (Scan Only)", "B2"),
                None,
                False,
            ),
        ]
        view.render(slots, 0, False, 1, NOW, "ja", SCAN_ONLY_VIEW)
        used: dict[str, str] = {
            canvas.itemcget(item, "text"): canvas.itemcget(item, "font")
            for item in canvas.find_all()
            if canvas.type(item) == "text"
        }
        assert used["P1 異常なし"] == str(fonts.tile_title)
        assert used["P2 異常なし（検査のみ）"] == str(fonts.verdict_compact)

    def test_state_labels_fit_tile(self, fonts: Fonts) -> None:
        """Title-row state labels (empty, inspecting, etc.) also fit the tile width with "P4 "."""
        limit = theme.TILE_W - TEXT_X * 2
        state_keys = [k for k in i18n.MESSAGES["en"] if k.startswith("state.")]
        for lang in i18n.LANGUAGES:
            for key in state_keys:
                label = i18n.text(lang, key)
                width = fonts.tile_title.measure("P4 " + label)
                assert width <= limit, f"{lang} {key} {label!r} {width}px > {limit}px"

    def test_serial_text_rules(self) -> None:
        """Serial display truncation rules (design doc 1.2)."""
        long = Slot(0, "P1", SlotState.DONE, disk("sda", "1-1.1", "x"), summary(), None, False)
        assert serial_text(long, 3) == "ABC123456789X…"
        assert serial_text(long, 2) == "ABC1234567…"
        fb = Slot(2, "P3", SlotState.WAITING, disk("sdc", "9-9", "ABC123456789"), None, None, True)
        assert serial_text(fb, 3) == "ABC123456… sdc"
        assert serial_text(fb, 2) == "ABC12… sdc"
        short = Slot(2, "P3", SlotState.WAITING, disk("sdc", "9-9", "ABC12"), None, None, True)
        assert serial_text(short, 3) == "ABC12 sdc"
        removed = Slot(2, "P3", SlotState.REMOVED, None, summary(), None, True, "sdc")
        assert serial_text(removed, 3) == "ABC123456… sdc"
        assert title_text(removed, "ja") == "P? 異常なし"
        assert title_text(removed, "en") == "P? No Defect"


class TestKeys:
    def test_keypad_keys_are_bound(self, root: tk.Tk) -> None:
        """USB keypad keysyms (KP_1-KP_4, KP_Left, etc.) and L and Q are also bound."""

        class Target:
            def select(self, index: int) -> None: ...

            def step(self, delta: int) -> None: ...

            def force_reload(self) -> None: ...

            def quit(self) -> None: ...

            def toggle_language(self) -> None: ...

            def toggle_qr_mode(self) -> None: ...

            def toggle_mode(self) -> None: ...

            def cancel_dialog(self) -> None: ...

        bind_keys(root, Target(), kiosk=True)
        bound = set(root.bind())
        for name in ("KP_1", "KP_2", "KP_3", "KP_4", "KP_Left", "KP_Right", "KP_Up", "KP_Down"):
            assert f"<Key-{name}>" in bound
        # Tk lists one-char keysyms as "l", not "<Key-l>".
        for name in ("l", "L", "q", "Q", "m", "M"):
            assert name in bound
        assert "<Key-Escape>" in bound
        assert "<Control-Key-q>" not in bound
