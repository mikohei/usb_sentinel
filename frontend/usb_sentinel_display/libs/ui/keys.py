import contextlib
import tkinter as tk
from typing import Protocol

# Keypad (NumLock on) keysyms. On X11 they arrive separately from Key-1 and so on.
KEYPAD_DIGITS = ("<KP_1>", "<KP_2>", "<KP_3>", "<KP_4>")
PREV_KEYS = ("<Left>", "<Up>", "<KP_Left>", "<KP_Up>")
NEXT_KEYS = ("<Right>", "<Down>", "<KP_Right>", "<KP_Down>")
# Phase-2 keys. L switches language; Q toggles normal QR/detail QR.
LANGUAGE_KEYS = ("<Key-l>", "<Key-L>")
QR_MODE_KEYS = ("<Key-q>", "<Key-Q>")


class KeyTarget(Protocol):
    """Operations provided by the key receiver (App)."""

    def select(self, index: int) -> None:
        """Select slot index (0-3)."""
        ...

    def step(self, delta: int) -> None:
        """Advance the selected slot (normal QR) or the detail QR page (detail QR) by delta."""
        ...

    def force_reload(self) -> None:
        """Drop the result cache and reload on the next tick (F5)."""
        ...

    def quit(self) -> None:
        """Quit the app (Ctrl+Q when not in kiosk mode)."""
        ...

    def toggle_language(self) -> None:
        """Switch the display language between ja and en (L)."""
        ...

    def toggle_qr_mode(self) -> None:
        """Toggle between normal QR and detail QR (Q)."""
        ...


def bind_keys(root: tk.Tk, target: KeyTarget, *, kiosk: bool) -> None:
    """Register key bindings from design doc 1.4 plus phase 2 (L, Q).

    Args:
        root: Top-level widget to bind to.
        target: Receiver of the operations.
        kiosk: If True, do not register quit (Ctrl+Q).
    """
    for index in range(4):
        root.bind(f"<Key-{index + 1}>", lambda _e, i=index: target.select(i))
        # Tk without KP_* keysyms (macOS etc.) raises TclError, so ignore it.
        with contextlib.suppress(tk.TclError):
            root.bind(KEYPAD_DIGITS[index], lambda _e, i=index: target.select(i))
    for key in PREV_KEYS:
        with contextlib.suppress(tk.TclError):
            root.bind(key, lambda _e: target.step(-1))
    for key in NEXT_KEYS:
        with contextlib.suppress(tk.TclError):
            root.bind(key, lambda _e: target.step(1))
    root.bind("<F5>", lambda _e: target.force_reload())
    for key in LANGUAGE_KEYS:
        root.bind(key, lambda _e: target.toggle_language())
    for key in QR_MODE_KEYS:
        root.bind(key, lambda _e: target.toggle_qr_mode())
    if not kiosk:
        # Tk prefers the more specific <Control-q> over <Key-q>, so binding both is fine.
        root.bind("<Control-q>", lambda _e: target.quit())
