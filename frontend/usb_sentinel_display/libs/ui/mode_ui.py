"""Glue between ModeController, ConfirmView and the heartbeat timer (design doc 5.6).

Kept out of app.py so that file stays near 300 lines. Every public call ends by syncing the
dialog with the controller and asking the App to redraw.
"""

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable

from ..mode_state import HEARTBEAT_S, ModeController, ModeView
from .confirm import ConfirmView
from .theme import Fonts

logger = logging.getLogger(__name__)

HEARTBEAT_MS = int(HEARTBEAT_S * 1000)
# Dialog buttons and keys 1-4 deliver these 0-based indices; anything else is ignored.
DIGIT_INDICES = range(4)


class ModeUi:
    """Drive the inspection-mode controller from the GUI and keep the dialog in step."""

    def __init__(
        self,
        root: tk.Misc,
        frame: tk.Frame,
        fonts: Fonts,
        controller: ModeController,
        on_change: Callable[[], None],
        lang: Callable[[], str],
        drives_in_progress: Callable[[], bool],
    ) -> None:
        """Build the hidden ConfirmView; nothing is written until start().

        Args:
            root: Widget that owns the after() timer.
            frame: The 480x320 App frame the dialog covers.
            fonts: The resolved font set.
            controller: The mode state machine.
            on_change: App redraw, called after every controller call.
            lang: Returns the current display language.
            drives_in_progress: Whether any slot is WAITING or INSPECTING (erase_off_busy).
        """
        self.root = root
        self.controller = controller
        self.confirm = ConfirmView(frame, fonts, self.digit, self.cancel)
        self._on_change = on_change
        self._lang = lang
        self._drives_in_progress = drives_in_progress
        self._job: str | None = None

    def _sync(self) -> None:
        """Show, refresh or hide the dialog from the controller state, then redraw."""
        view = self.controller.view()
        if self.controller.dialog_open and view.code is not None:
            self.confirm.show(self._lang(), view.code, view.entered)
        else:
            self.confirm.hide()
        self._on_change()

    def _schedule(self) -> None:
        self._job = self.root.after(HEARTBEAT_MS, self._heartbeat)

    def _heartbeat(self) -> None:
        """Every HEARTBEAT_S: refresh the full stamp or re-check locks; always re-schedule."""
        self._job = None
        try:
            self.controller.heartbeat()
            self._sync()
        except Exception:
            logger.exception("Inspection-mode heartbeat failed")
        finally:
            self._schedule()

    def start(self) -> None:
        """Reset the host to scan_only (R3) and start the heartbeat."""
        self.controller.start()
        self._schedule()
        self._sync()

    def toggle(self) -> None:
        """Mode button and key M."""
        self.controller.toggle(drives_in_progress=self._drives_in_progress())
        self._sync()

    def digit(self, index: int) -> None:
        """One code digit as a 0-based index from keys 1-4, KP_1-4 or the dialog buttons."""
        if index not in DIGIT_INDICES:
            return
        self.controller.digit(str(index + 1))
        self._sync()

    def cancel(self) -> None:
        """Escape and the Cancel button; only acts while the dialog is open."""
        if not self.controller.dialog_open:
            return
        self.controller.cancel()
        self._sync()

    def shutdown(self) -> None:
        """Stop the heartbeat and reset the host to scan_only; safe after root.destroy()."""
        if self._job is not None:
            with contextlib.suppress(tk.TclError):
                self.root.after_cancel(self._job)
            self._job = None
        self.controller.shutdown()

    @property
    def dialog_open(self) -> bool:
        """Whether the code dialog is open (keys 1-4 then feed the code)."""
        return self.controller.dialog_open

    def view(self) -> ModeView:
        """The controller's current view for drawing."""
        return self.controller.view()
