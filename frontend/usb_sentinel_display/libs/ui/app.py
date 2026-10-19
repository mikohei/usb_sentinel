import signal
import sys
import time
import tkinter as tk
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from types import FrameType
from typing import Protocol

from ..config import DisplayConfig
from ..mapper import Mapper
from ..monitor import Monitor, Snapshot
from ..results import ResultStore
from ..store import SqliteIndex
from ..units import Runner
from . import theme
from .i18n import (
    initial_language,
    load_saved_language,
    other_language,
    save_language,
    state_file,
    text,
)
from .keys import bind_keys
from .qr_view import QrView
from .strip import resolution_warning, strip_text
from .theme import QR_W, SCREEN_H, SCREEN_W, TILE_COUNT, TILE_W, Fonts
from .tiles import TilesView

WINDOW_TITLE = "USB Sentinel Display"
FOCUS_RETRY_MS = 200
CONFIGURE_SETTLE_MS = 100
# Number of detail QR pages (qr_mode 1-3).
DETAIL_PAGES = 3


class MonitorLike(Protocol):
    """Monitor operations used by App (replaced by a fake in tests)."""

    def tick(self) -> Snapshot:
        """Return the display contents for one tick. Never raises (returns the previous slots)."""
        ...

    def force_reload(self) -> None:
        """Drop the result cache and reload every result.json on the next tick (F5)."""
        ...


@dataclass(frozen=True)
class AppOptions:
    """Startup options. windowed and kiosk are never given together (D13)."""

    windowed: bool
    kiosk: bool


def build_monitor(
    config: DisplayConfig, runner: Runner | None, now: Callable[[], float]
) -> Monitor:
    """Build the Monitor from the config (FEAT-002 injection point). Empty sqlite_path: no index."""
    index = SqliteIndex(config.sqlite_path) if config.sqlite_path is not None else None
    return Monitor(
        config,
        store=ResultStore(config.results_dir, index=index),
        runner=runner,
        mapper=Mapper(config.ports, retain_s=config.retain_minutes * 60),
        now=now,
    )


class App:
    """Wire the tk window to the Monitor. Holds no logic other than drawing."""

    def __init__(
        self,
        config: DisplayConfig,
        options: AppOptions,
        *,
        runner: Runner | None,
        now: Callable[[], float] = time.time,
        monitor: MonitorLike | None = None,
    ) -> None:
        """Build the tk root, Canvases, Views, and key bindings, and wire them to the Monitor.

        Args:
            config: Display config.
            options: Startup options (windowed/kiosk).
            runner: systemctl runner. If None, unit states are not queried.
            now: Current time (swapped in tests).
            monitor: Monitor override. If None, build it with build_monitor().
        """
        self.config = config
        self.options = options
        self.now = now
        self.monitor: MonitorLike = (
            monitor if monitor is not None else build_monitor(config, runner, now)
        )
        self.selected = 0
        # Language priority: saved -> config -> default (en).
        self.language = initial_language(config.language, load_saved_language(state_file()))
        # 0 is normal QR, 1-3 are detail QR pages. Kept when switching ports.
        self.qr_mode = 0
        self.stop_requested = False
        self._save_warned = False
        self._resolution: tuple[int, int] | None = None
        self._last_snapshot: Snapshot | None = None
        self._configure_job: str | None = None

        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        family = theme.resolve_font_family(self.root, config.font_family)
        self.fonts = Fonts.create(self.root, family)
        self.rows = theme.row_layout(self.root, self.fonts)

        self.frame = tk.Frame(
            self.root, width=SCREEN_W, height=SCREEN_H, background=theme.COLORS["empty"]
        )
        self.frame.place(relx=0.5, rely=0.5, anchor="center")
        self.left_canvas = tk.Canvas(
            self.frame, width=TILE_W, height=SCREEN_H, highlightthickness=0, bd=0
        )
        self.left_canvas.place(x=0, y=0)
        self.right_canvas = tk.Canvas(
            self.frame, width=QR_W, height=SCREEN_H, highlightthickness=0, bd=0
        )
        self.right_canvas.place(x=TILE_W, y=0)
        self.tiles = TilesView(
            self.left_canvas, self.fonts, self.select, self.step, self.toggle_language, self.rows
        )
        self.qr_view = QrView(self.right_canvas, self.fonts, self.toggle_qr_mode)

        if options.windowed:
            self.root.geometry(f"{SCREEN_W}x{SCREEN_H}")
            self.root.bind("<Configure>", self._on_configure)
        else:
            self.root.attributes("-fullscreen", True)
            self.root.configure(cursor="none")
            self.root.focus_force()
            self.root.bind("<FocusOut>", self._on_focus_out)
            self._check_resolution(self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        bind_keys(self.root, self, kiosk=options.kiosk)
        signal.signal(signal.SIGTERM, self._on_sigterm)

    def _on_sigterm(self, signum: int, frame: FrameType | None) -> None:
        """On SIGTERM only set a flag; destroy happens on the next tick (design doc 1.4)."""
        self.stop_requested = True

    def _on_focus_out(self, event: object) -> None:
        """In full screen, take focus back (the keyboard is the only input)."""
        self.root.after(FOCUS_RETRY_MS, self.root.focus_force)

    def _on_configure(self, event: object) -> None:
        """With --windowed, judge the resolution from the top-level's real size (D7).

        Right after geometry(), <Configure> also arrives with transient sizes like 1x1 or 200x200,
        so wait a little and read the settled size.
        """
        if self._configure_job is not None:
            self.root.after_cancel(self._configure_job)
        self._configure_job = self.root.after(CONFIGURE_SETTLE_MS, self._check_window_size)

    def _check_window_size(self) -> None:
        """Judge the resolution from the settled window size."""
        self._configure_job = None
        self._check_resolution(self.root.winfo_width(), self.root.winfo_height())

    def _check_resolution(self, width: int, height: int) -> None:
        """If not 480x320, print once to stderr and keep it for the info strip."""
        resolution = resolution_warning(width, height)
        if resolution is not None and self._resolution is None:
            print(text(self.language, "resolution", width=width, height=height), file=sys.stderr)
        self._resolution = resolution

    def _redraw(self) -> None:
        """Redraw if there is a latest snapshot."""
        if self._last_snapshot is not None:
            self._render(self._last_snapshot)

    def select(self, index: int) -> None:
        """Select a slot and redraw with the latest snapshot."""
        self.selected = index % TILE_COUNT
        self._redraw()

    def step(self, delta: int) -> None:
        """Prev/next and arrow keys. Normal QR: adjacent slot. Detail QR: next page (1->2->3->1)."""
        if self.qr_mode == 0:
            self.select(self.selected + delta)
            return
        self.qr_mode = (self.qr_mode - 1 + delta) % DETAIL_PAGES + 1
        self._redraw()

    def toggle_qr_mode(self) -> None:
        """Right canvas tap and key Q. Normal QR -> detail page 1; in detail -> normal QR."""
        self.qr_mode = 1 if self.qr_mode == 0 else 0
        self._redraw()

    def toggle_language(self) -> None:
        """Language button and key L. Switch and save; print a save failure to stderr only once."""
        self.language = other_language(self.language)
        reason = save_language(state_file(), self.language)
        if reason is not None and not self._save_warned:
            print(reason, file=sys.stderr)
            self._save_warned = True
        self._redraw()

    def force_reload(self) -> None:
        """F5: drop the result cache."""
        self.monitor.force_reload()

    def quit(self) -> None:
        """Ctrl+Q (bound only when not in kiosk mode)."""
        self.root.destroy()

    def _render(self, snapshot: Snapshot) -> None:
        """Draw the snapshot on both Canvases."""
        self._last_snapshot = snapshot
        blink_on = snapshot.tick_count % 2 == 0
        dots = snapshot.tick_count % 3 + 1
        self.tiles.render(snapshot.slots, self.selected, blink_on, dots, self.now(), self.language)
        slot = snapshot.slots[self.selected] if self.selected < len(snapshot.slots) else None
        strip = strip_text(
            snapshot,
            self.selected,
            self.config.unit_template,
            self._resolution,
            self.language,
            self.qr_mode,
        )
        self.qr_view.render(slot, strip, self.qr_mode, self.language)

    def tick(self) -> None:
        """One tick. Re-register after even on draw errors; destroy on stop request (doc 4.1)."""
        try:
            snapshot = self.monitor.tick()
            self._render(snapshot)
        except Exception:
            traceback.print_exc()
        finally:
            if self.stop_requested:
                self.root.destroy()
            else:
                self.root.after(int(self.config.poll_interval_s * 1000), self.tick)

    def run(self) -> None:
        """Run the first tick and enter the main loop."""
        self.tick()
        self.root.mainloop()


def run_app(config: DisplayConfig, options: AppOptions, runner: Runner | None) -> int:
    """Build and run the App.

    Returns:
        Exit code (always 0).
    """
    App(config, options, runner=runner).run()
    return 0
