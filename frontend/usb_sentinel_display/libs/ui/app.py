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
# 詳細QRのページ数（qr_mode 1〜3）。
DETAIL_PAGES = 3


class MonitorLike(Protocol):
    """Appが使うMonitorの操作（テストでは偽物に差し替える）。"""

    def tick(self) -> Snapshot:
        """1tick分の表示内容を返す。例外は投げない（前回のスロットを保持して返す）。"""
        ...

    def force_reload(self) -> None:
        """結果キャッシュを捨て、次のtickで全result.jsonを読み直す（F5）。"""
        ...


@dataclass(frozen=True)
class AppOptions:
    """起動オプション。windowedとkioskは同時に指定しない（D13）。"""

    windowed: bool
    kiosk: bool


def build_monitor(
    config: DisplayConfig, runner: Runner | None, now: Callable[[], float]
) -> Monitor:
    """設定からMonitorを組む（FEAT-002の注入点）。sqlite_pathが空ならSQLite索引を使わない。"""
    index = SqliteIndex(config.sqlite_path) if config.sqlite_path is not None else None
    return Monitor(
        config,
        store=ResultStore(config.results_dir, index=index),
        runner=runner,
        mapper=Mapper(config.ports, retain_s=config.retain_minutes * 60),
        now=now,
    )


class App:
    """tkのウィンドウとMonitorを結線する。描画以外のロジックは持たない。"""

    def __init__(
        self,
        config: DisplayConfig,
        options: AppOptions,
        *,
        runner: Runner | None,
        now: Callable[[], float] = time.time,
        monitor: MonitorLike | None = None,
    ) -> None:
        """tkルートとCanvas・View・キーバインドを組み、Monitorと結線する。

        Args:
            config: 表示設定。
            options: 起動オプション（windowed／kiosk）。
            runner: systemctlの実行器。Noneならユニット状態を問い合わせない。
            now: 現在時刻（テストで差し替える）。
            monitor: Monitorの差し替え。Noneならbuild_monitor()で組む。
        """
        self.config = config
        self.options = options
        self.now = now
        self.monitor: MonitorLike = (
            monitor if monitor is not None else build_monitor(config, runner, now)
        )
        self.selected = 0
        # 言語の優先順は記録→設定→既定（en）。
        self.language = initial_language(config.language, load_saved_language(state_file()))
        # 0が通常QR、1〜3が詳細QRのページ。ポートを切り替えても保つ。
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
        """SIGTERMではフラグだけ立て、destroyは次のtickで行う（設計書1.4）。"""
        self.stop_requested = True

    def _on_focus_out(self, event: object) -> None:
        """全画面ではフォーカスを取り戻す（キーボードが唯一の操作手段のため）。"""
        self.root.after(FOCUS_RETRY_MS, self.root.focus_force)

    def _on_configure(self, event: object) -> None:
        """--windowedではトップレベルの実サイズで解像度を判定する（D7）。

        geometry()直後は1x1や200x200の過渡的なサイズでも<Configure>が届くので、
        少し待ってから確定したサイズを読む。
        """
        if self._configure_job is not None:
            self.root.after_cancel(self._configure_job)
        self._configure_job = self.root.after(CONFIGURE_SETTLE_MS, self._check_window_size)

    def _check_window_size(self) -> None:
        """確定したウィンドウサイズで解像度を判定する。"""
        self._configure_job = None
        self._check_resolution(self.root.winfo_width(), self.root.winfo_height())

    def _check_resolution(self, width: int, height: int) -> None:
        """480x320以外なら標準エラーに1回出し、情報ストリップ用に保持する。"""
        resolution = resolution_warning(width, height)
        if resolution is not None and self._resolution is None:
            print(text(self.language, "resolution", width=width, height=height), file=sys.stderr)
        self._resolution = resolution

    def _redraw(self) -> None:
        """直近のスナップショットがあれば描き直す。"""
        if self._last_snapshot is not None:
            self._render(self._last_snapshot)

    def select(self, index: int) -> None:
        """スロットを選択し、直近のスナップショットで描き直す。"""
        self.selected = index % TILE_COUNT
        self._redraw()

    def step(self, delta: int) -> None:
        """◀▶と矢印キー。通常QRでは前後のスロット、詳細QRではページ送り（1→2→3→1）。"""
        if self.qr_mode == 0:
            self.select(self.selected + delta)
            return
        self.qr_mode = (self.qr_mode - 1 + delta) % DETAIL_PAGES + 1
        self._redraw()

    def toggle_qr_mode(self) -> None:
        """右キャンバスのタップとキーQ。通常QR→詳細1枚目、詳細中→通常QR。"""
        self.qr_mode = 1 if self.qr_mode == 0 else 0
        self._redraw()

    def toggle_language(self) -> None:
        """言語ボタンとキーL。切り替えて記録し、記録の失敗は標準エラーに1回だけ出す。"""
        self.language = other_language(self.language)
        reason = save_language(state_file(), self.language)
        if reason is not None and not self._save_warned:
            print(reason, file=sys.stderr)
            self._save_warned = True
        self._redraw()

    def force_reload(self) -> None:
        """F5: 結果キャッシュを捨てる。"""
        self.monitor.force_reload()

    def quit(self) -> None:
        """Ctrl+Q（kioskでない場合だけ束縛される）。"""
        self.root.destroy()

    def _render(self, snapshot: Snapshot) -> None:
        """スナップショットを両Canvasに描く。"""
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
        """1tick。描画例外でもafterを再登録し、停止要求があればdestroyする（設計書4.1）。"""
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
        """最初のtickを呼んでメインループに入る。"""
        self.tick()
        self.root.mainloop()


def run_app(config: DisplayConfig, options: AppOptions, runner: Runner | None) -> int:
    """Appを組んで実行する。

    Returns:
        終了コード（常に0）。
    """
    App(config, options, runner=runner).run()
    return 0
