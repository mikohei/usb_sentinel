import logging
import signal
import time
import tkinter as tk
import traceback
from collections.abc import Callable
from types import FrameType

from .. import trust
from ..config import DisplayConfig
from ..geometry import Geometry
from ..mode_challenge import Challenge, random_code
from ..mode_state import ModeController
from ..models import SlotState
from ..monitor import Snapshot
from ..units import Runner
from . import theme
from .app_wiring import AppOptions, MonitorLike, build_monitor, choose_scale
from .i18n import (
    initial_language,
    load_saved_language,
    other_language,
    save_language,
    state_file,
    text,
)
from .keys import bind_keys
from .mode_ui import ModeUi
from .qr_view import QrView
from .strip import resolution_warning, strip_text
from .theme import QR_W, SCREEN_H, TILE_COUNT, TILE_W, Fonts
from .tiles import TilesView

__all__ = ["App", "AppOptions", "MonitorLike", "build_monitor", "run_app"]

logger = logging.getLogger(__name__)

WINDOW_TITLE = "USB Sentinel Display"
FOCUS_RETRY_MS = 200
CONFIGURE_SETTLE_MS = 100
# Number of detail QR pages (qr_mode 1-3).
DETAIL_PAGES = 3
# Slot states that mean a drive inserted in erase mode may still be erased (design 3.8).
IN_PROGRESS_STATES = (SlotState.WAITING, SlotState.INSPECTING)


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
        make_code: Callable[[], str] = random_code,
    ) -> None:
        """Build the tk root, Canvases, Views, and key bindings, and wire them to the Monitor.

        Args:
            config: Display config.
            options: Startup options (windowed/kiosk/scale).
            runner: systemctl runner. If None, unit states are not queried.
            now: Current time (swapped in tests).
            monitor: Monitor override. If None, build it with build_monitor().
            make_code: Source of the erase-mode confirmation code (fixed in tests; D2).
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
        # Scale of the 480x320 design: --scale, else 1.0 windowed, else fit the screen.
        self.geometry = Geometry(
            choose_scale(options, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        )
        px = self.geometry.px
        family, font_found = theme.resolve_font(self.root, config.font_family)
        # Shown in the info strip when no Japanese font was found.
        self.font_missing = not font_found
        self.fonts = Fonts.create(self.root, family, self.geometry)
        self.rows = theme.row_layout(self.root, self.fonts, self.geometry)

        # The root shows through as black bars where the screen is not 3:2 (letterbox).
        self.root.configure(background=theme.COLORS["letterbox"])
        frame_w, frame_h = self.geometry.frame_size
        self.frame = tk.Frame(
            self.root, width=frame_w, height=frame_h, background=theme.COLORS["empty"]
        )
        self.frame.place(relx=0.5, rely=0.5, anchor="center")
        self.left_canvas = tk.Canvas(
            self.frame, width=px(TILE_W), height=px(SCREEN_H), highlightthickness=0, bd=0
        )
        self.left_canvas.place(x=0, y=0)
        self.right_canvas = tk.Canvas(
            self.frame, width=px(QR_W), height=px(SCREEN_H), highlightthickness=0, bd=0
        )
        self.right_canvas.place(x=px(TILE_W), y=0)
        self.tiles = TilesView(
            self.left_canvas,
            self.fonts,
            self.select,
            self.step,
            self.toggle_language,
            self.toggle_mode,
            self.rows,
            geometry=self.geometry,
        )
        self.qr_view = QrView(
            self.right_canvas, self.fonts, self.toggle_qr_mode, geometry=self.geometry
        )
        controller = ModeController(
            config.control_dir,
            config.host_config_dir,
            config.sys_class_input,
            trust.strike_file(),
            challenge=Challenge(make_code=make_code),
        )
        self.mode_ui = ModeUi(
            self.root,
            self.frame,
            self.fonts,
            controller,
            self._redraw,
            lambda: self.language,
            self._drives_in_progress,
            geometry=self.geometry,
        )

        if options.windowed:
            self.root.geometry(f"{frame_w}x{frame_h}")
            self.root.bind("<Configure>", self._on_configure)
        else:
            self.root.attributes("-fullscreen", True)
            self.root.configure(cursor="none")
            self.root.focus_force()
            self.root.bind("<FocusOut>", self._on_focus_out)
            self._check_resolution(self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        if options.kiosk:
            # A window-manager close request must not end the app and expose the desktop (3.11).
            self.root.protocol("WM_DELETE_WINDOW", lambda: None)
        bind_keys(self.root, self, kiosk=options.kiosk)
        signal.signal(signal.SIGTERM, self._on_sigterm)
        # Every start resets the host to scan_only (R3), before the first render.
        self.mode_ui.start()

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
        """Warn once when the screen is smaller than the 480x320 design; keep it for the strip."""
        resolution = resolution_warning(width, height)
        if resolution is not None and self._resolution is None:
            logger.warning(text(self.language, "resolution", width=width, height=height))
        self._resolution = resolution

    def _redraw(self) -> None:
        """Redraw if there is a latest snapshot."""
        if self._last_snapshot is not None:
            self._render(self._last_snapshot)

    def _drives_in_progress(self) -> bool:
        """Whether any slot is WAITING or INSPECTING in the latest snapshot (False before one)."""
        snapshot = self._last_snapshot
        if snapshot is None:
            return False
        return any(slot.state in IN_PROGRESS_STATES for slot in snapshot.slots)

    def select(self, index: int) -> None:
        """Keys 1-4 / tile tap: a code digit while the dialog is open, else select a slot."""
        if self.mode_ui.dialog_open:
            self.mode_ui.digit(index)
            return
        self.selected = index % TILE_COUNT
        self._redraw()

    def step(self, delta: int) -> None:
        """Prev/next and arrow keys. Normal QR: adjacent slot. Detail QR: next page (1->2->3->1)."""
        if self.mode_ui.dialog_open:
            return
        if self.qr_mode == 0:
            self.select(self.selected + delta)
            return
        self.qr_mode = (self.qr_mode - 1 + delta) % DETAIL_PAGES + 1
        self._redraw()

    def toggle_qr_mode(self) -> None:
        """Right canvas tap and key Q. Normal QR -> detail page 1; in detail -> normal QR."""
        if self.mode_ui.dialog_open:
            return
        self.qr_mode = 1 if self.qr_mode == 0 else 0
        self._redraw()

    def toggle_language(self) -> None:
        """Language button and key L. Switch and save; log a save failure only once."""
        if self.mode_ui.dialog_open:
            return
        self.language = other_language(self.language)
        reason = save_language(state_file(), self.language)
        if reason is not None and not self._save_warned:
            logger.warning(reason)
            self._save_warned = True
        self._redraw()

    def force_reload(self) -> None:
        """F5: drop the result cache."""
        if self.mode_ui.dialog_open:
            return
        self.monitor.force_reload()

    def toggle_mode(self) -> None:
        """Mode button and key M."""
        self.mode_ui.toggle()

    def cancel_dialog(self) -> None:
        """Escape: close the code dialog without a wrong answer."""
        self.mode_ui.cancel()

    def quit(self) -> None:
        """Ctrl+Q (bound only when not in kiosk mode)."""
        self.root.destroy()

    def _render(self, snapshot: Snapshot) -> None:
        """Draw the snapshot on both Canvases."""
        self._last_snapshot = snapshot
        blink_on = snapshot.tick_count % 2 == 0
        dots = snapshot.tick_count % 3 + 1
        mode = self.mode_ui.view()
        self.tiles.render(
            snapshot.slots, self.selected, blink_on, dots, self.now(), self.language, mode
        )
        slot = snapshot.slots[self.selected] if self.selected < len(snapshot.slots) else None
        strip = strip_text(
            snapshot,
            self.selected,
            self.config.unit_template,
            self._resolution,
            self.language,
            self.qr_mode,
            mode_notice=mode.notice,
            font_missing=self.font_missing,
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
        """Run the first tick and the main loop; reset the mode to scan_only when it ends."""
        try:
            self.tick()
            self.root.mainloop()
        finally:
            self.mode_ui.shutdown()


def run_app(config: DisplayConfig, options: AppOptions, runner: Runner | None) -> int:
    """Build and run the App.

    Returns:
        Exit code (always 0).
    """
    App(config, options, runner=runner).run()
    return 0
