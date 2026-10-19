import contextlib
import tkinter as tk
from typing import Protocol

# テンキー（NumLockオン）のキーシンボル。X11ではKey-1等とは別物として届く。
KEYPAD_DIGITS = ("<KP_1>", "<KP_2>", "<KP_3>", "<KP_4>")
PREV_KEYS = ("<Left>", "<Up>", "<KP_Left>", "<KP_Up>")
NEXT_KEYS = ("<Right>", "<Down>", "<KP_Right>", "<KP_Down>")
# 第2期の追加キー。Lは言語切替、Qは通常QR／詳細QRの切替。
LANGUAGE_KEYS = ("<Key-l>", "<Key-L>")
QR_MODE_KEYS = ("<Key-q>", "<Key-Q>")


class KeyTarget(Protocol):
    """キー操作の受け手（App）が持つ操作。"""

    def select(self, index: int) -> None:
        """スロットindex（0〜3）を選択する。"""
        ...

    def step(self, delta: int) -> None:
        """選択スロット（通常QR）または詳細QRのページ（詳細QR）をdelta分進める。"""
        ...

    def force_reload(self) -> None:
        """結果キャッシュを捨てて次のtickで読み直す（F5）。"""
        ...

    def quit(self) -> None:
        """アプリを終了する（kioskでないときのCtrl+Q）。"""
        ...

    def toggle_language(self) -> None:
        """表示言語をja／enで切り替える（L）。"""
        ...

    def toggle_qr_mode(self) -> None:
        """通常QRと詳細QRを切り替える（Q）。"""
        ...


def bind_keys(root: tk.Tk, target: KeyTarget, *, kiosk: bool) -> None:
    """設計書1.4＋第2期（L、Q）のキーバインドを登録する。

    Args:
        root: 束縛先のトップレベル。
        target: 操作の受け手。
        kiosk: Trueなら終了（Ctrl+Q）を登録しない。
    """
    for index in range(4):
        root.bind(f"<Key-{index + 1}>", lambda _e, i=index: target.select(i))
        # KP_*のキーシンボルを持たないTk（macOS等）ではTclErrorになるので無視する。
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
        # Tkはより特定的な<Control-q>を<Key-q>より優先するので、両方束縛してよい。
        root.bind("<Control-q>", lambda _e: target.quit())
