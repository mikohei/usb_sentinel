"""Mode scenes for dev-screenshot.py (plan.md Step 10).

A scene is what the operator would have done on the display before the screenshot: nothing,
open the code dialog, enter the code, or press the button while erase mode is locked.
"""

from typing import Protocol

SCENES = ("none", "scan_only", "confirm", "full", "locked", "lockout")
DEFAULT_CODE = "3142"
CODE_LEN = 4
CODE_DIGITS = "1234"


class SceneApp(Protocol):
    """The two App methods a scene uses."""

    def toggle_mode(self) -> None:
        """Mode button / M key."""

    def select(self, index: int) -> None:
        """Tile tap / keys 1-4 (a code digit while the dialog is open)."""


def code_indices(code: str) -> tuple[int, ...]:
    """Convert a 4-digit code of 1-4 into the 0-based indices select() expects.

    Raises:
        ValueError: The code is not exactly four characters from 1-4.
    """
    if len(code) != CODE_LEN or any(ch not in CODE_DIGITS for ch in code):
        raise ValueError(f"code must be {CODE_LEN} digits from {CODE_DIGITS}, got {code!r}")
    return tuple(int(ch) - 1 for ch in code)


def run_scene(app: SceneApp, scene: str, code: str) -> None:
    """Drive the App into the scene (called once after the first tick).

    confirm opens the dialog; full opens it and enters the code; locked and lockout press the
    button so the lock notice appears in the info strip (the fixtures decide which lock).

    Raises:
        ValueError: Unknown scene or bad code.
    """
    if scene not in SCENES:
        raise ValueError(f"unknown scene {scene!r}; choose from {', '.join(SCENES)}")
    indices = code_indices(code)
    if scene in ("none", "scan_only"):
        return
    app.toggle_mode()
    if scene == "full":
        for index in indices:
            app.select(index)
