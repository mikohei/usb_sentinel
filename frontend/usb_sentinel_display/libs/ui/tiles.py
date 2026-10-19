import tkinter as tk
from collections.abc import Callable
from datetime import UTC, datetime

from ..models import Slot, SlotState
from . import theme
from .i18n import language_button_label, state_label, text, verdict_label
from .theme import (
    ARROW_BTN_W,
    BLINK_BORDER,
    BORDER,
    LANG_BTN_X0,
    LANG_BTN_X1,
    TILE_COUNT,
    TILE_H,
    TILE_W,
    Fonts,
)

# シリアル表示の上限（設計書1.2）。
SERIAL_MAX = 14
SERIAL_HEAD = 13
FALLBACK_HEAD = 9
FALLBACK_HEAD_2ROWS = 5
TWO_ROW_HEAD = 10
TEXT_X = 8


def local_hhmm(ts: float) -> str:
    """エポック秒をローカル時刻のHH:MMにする。"""
    return datetime.fromtimestamp(ts, tz=UTC).astimezone().strftime("%H:%M")


def finished_hhmm(finished_at: str, fallback_ts: float) -> str:
    """finished_at（ISO 8601）をHH:MMにする。解析できなければfallback_tsを使う。"""
    try:
        parsed = datetime.fromisoformat(finished_at)
    except ValueError:
        return local_hhmm(fallback_ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone().strftime("%H:%M")


def truncate(text: str, head: int) -> str:
    """head文字を超えたらhead文字＋「…」にする。"""
    return text if len(text) <= head else text[:head] + "…"


def serial_text(slot: Slot, rows: int) -> str:
    """2行目のシリアル表示。fallbackならカーネル名を添える（設計書1.2）。"""
    serial = ""
    if slot.result is not None:
        serial = slot.result.serial
    elif slot.disk is not None:
        serial = slot.disk.serial
    kernel_name = slot.disk.kernel_name if slot.disk is not None else slot.retained_kernel
    if slot.fallback and kernel_name is not None:
        head = FALLBACK_HEAD_2ROWS if rows == 2 else FALLBACK_HEAD
        return f"{truncate(serial, head)} {kernel_name}"
    if rows == 2:
        return truncate(serial, TWO_ROW_HEAD)
    return serial if len(serial) <= SERIAL_MAX else serial[:SERIAL_HEAD] + "…"


def status_text(slot: Slot, now: float, dots: int, lang: str) -> str:
    """3行目の状態文言と時刻。検査中は経過分数と「…」×dots。"""
    state = slot.state
    if state is SlotState.EMPTY:
        return ""
    if state is SlotState.INSPECTING and slot.disk is not None:
        minutes = int(max(0.0, now - slot.disk.attached_at)) // 60
        return text(lang, "inspecting_minutes", minutes=minutes, dots="…" * dots)
    if state in (SlotState.DONE, SlotState.REMOVED) and slot.result is not None:
        key = "done_at" if state is SlotState.DONE else "removed_at"
        return text(lang, key, time=finished_hhmm(slot.result.finished_at, slot.result.mtime))
    word = state_label(lang, state)
    if slot.disk is not None:
        return text(lang, "state_at", word=word, time=local_hhmm(slot.disk.attached_at))
    return word


def title_text(slot: Slot, lang: str) -> str:
    """1行目のポート名と判定（判定が無い状態では状態の文言）。"""
    label = text(lang, "fallback_label") if slot.fallback else slot.label
    if slot.state in (SlotState.DONE, SlotState.REMOVED) and slot.result is not None:
        return f"{label} {verdict_label(lang, slot.result.verdict)}"
    return f"{label} {state_label(lang, slot.state)}"


def tile_color(slot: Slot) -> str:
    """タイルの背景色。完了は判定色、抜去は判定色を暗くしたもの。"""
    if slot.state in (SlotState.DONE, SlotState.REMOVED) and slot.result is not None:
        color = theme.verdict_color(slot.result.verdict)
        return theme.darken(color) if slot.state is SlotState.REMOVED else color
    return theme.STATE_COLORS.get(slot.state, theme.COLORS["no_result"])


def is_malware(slot: Slot) -> bool:
    """点滅対象（接続中のMalware Detected）か。"""
    return (
        slot.state is SlotState.DONE
        and slot.result is not None
        and slot.result.verdict == "Malware Detected"
    )


class TilesView:
    """左160pxのタイル4枚と◀／言語／▶ボタンを描く。"""

    def __init__(
        self,
        canvas: tk.Canvas,
        fonts: Fonts,
        on_select: Callable[[int], None],
        on_step: Callable[[int], None],
        on_language: Callable[[], None],
        rows: int,
    ) -> None:
        """描画先とコールバックを受け取り、タップ判定を束縛する。

        Args:
            canvas: 左側160pxのCanvas。
            fonts: 解決済みのフォント一式。
            on_select: タイルをタップしたときに呼ぶ関数（スロット番号）。
            on_step: ◀／▶をタップしたときに呼ぶ関数（-1／+1）。
            on_language: 言語ボタンをタップしたときに呼ぶ関数。
            rows: タイル1枚の行数（2または3。theme.row_layout()で決める）。
        """
        self.canvas = canvas
        self.fonts = fonts
        self.on_select = on_select
        self.on_step = on_step
        self.on_language = on_language
        self.rows = rows
        self._lang = "en"
        self._line_h = (fonts.verdict.metrics("linespace"), fonts.body.metrics("linespace"))
        canvas.bind("<Button-1>", self._on_click)

    def _on_click(self, event: tk.Event) -> None:
        """タップ位置からタイルかボタンを判定する。◀▶の意味（ポートかページか）はAppが決める。"""
        index = int(event.y) // TILE_H
        if index < TILE_COUNT:
            self.on_select(index)
        elif int(event.x) < ARROW_BTN_W:
            self.on_step(-1)
        elif int(event.x) < LANG_BTN_X1:
            self.on_language()
        else:
            self.on_step(1)

    def _frame(self, y0: int, outer: str, inner: str | None, background: str) -> None:
        """外周4pxと、必要なら内側2pxの枠を塗る。"""
        canvas = self.canvas
        canvas.create_rectangle(0, y0, TILE_W, y0 + TILE_H, fill=outer, width=0)
        if inner is not None:
            canvas.create_rectangle(
                BORDER, y0 + BORDER, TILE_W - BORDER, y0 + TILE_H - BORDER, fill=inner, width=0
            )
            pad = BORDER + BLINK_BORDER
        else:
            pad = BORDER
        canvas.create_rectangle(
            pad, y0 + pad, TILE_W - pad, y0 + TILE_H - pad, fill=background, width=0
        )

    def _tile(self, slot: Slot, selected: bool, blink_on: bool, now: float, dots: int) -> None:
        """タイル1枚を描く。"""
        y0 = slot.index * TILE_H
        background = tile_color(slot)
        blink = theme.COLORS["blink"] if blink_on else background
        if selected:
            inner = blink if is_malware(slot) else None
            self._frame(y0, theme.COLORS["text"], inner, background)
        else:
            outer = blink if is_malware(slot) else background
            self._frame(y0, outer, None, background)
        color = (
            theme.COLORS["empty_text"] if slot.state is SlotState.EMPTY else theme.COLORS["text"]
        )
        verdict_h, body_h = self._line_h
        y = y0 + theme.TILE_PAD
        canvas = self.canvas
        title = title_text(slot, self._lang)
        canvas.create_text(TEXT_X, y, text=title, anchor="nw", fill=color, font=self.fonts.verdict)
        y += verdict_h
        serial = serial_text(slot, self.rows)
        status = status_text(slot, now, dots, self._lang)
        if self.rows == 2:
            line = f"{serial} {status}".strip()
            canvas.create_text(TEXT_X, y, text=line, anchor="nw", fill=color, font=self.fonts.body)
            return
        canvas.create_text(TEXT_X, y, text=serial, anchor="nw", fill=color, font=self.fonts.body)
        y += body_h
        canvas.create_text(TEXT_X, y, text=status, anchor="nw", fill=color, font=self.fonts.body)

    def _buttons(self, lang: str) -> None:
        """◀／言語／▶ボタンを描く。言語ボタンは切替先の名前を14pxで出す。"""
        canvas = self.canvas
        y0 = TILE_H * TILE_COUNT
        cy = y0 + theme.BTN_H // 2
        canvas.create_rectangle(
            0, y0, TILE_W, y0 + theme.BTN_H, fill=theme.COLORS["button"], width=0
        )
        for x, arrow in ((ARROW_BTN_W // 2, "◀"), ((LANG_BTN_X1 + TILE_W) // 2, "▶")):
            canvas.create_text(x, cy, text=arrow, fill=theme.COLORS["text"], font=self.fonts.arrow)
        canvas.create_text(
            (LANG_BTN_X0 + LANG_BTN_X1) // 2,
            cy,
            text=language_button_label(lang),
            fill=theme.COLORS["text"],
            font=self.fonts.body,
        )

    def render(
        self, slots: list[Slot], selected: int, blink_on: bool, dots: int, now: float, lang: str
    ) -> None:
        """4タイルとボタン列を全部描き直す。

        Args:
            slots: スロット一覧（4件）。
            selected: 選択中のスロット番号。
            blink_on: Malwareの点滅枠を明赤にするか。
            dots: 「検査中」の末尾の「…」の個数。
            now: 現在時刻（経過分数の計算に使う）。
            lang: 'en'または'ja'。
        """
        self._lang = lang
        self.canvas.delete("all")
        for slot in slots[:TILE_COUNT]:
            self._tile(slot, slot.index == selected, blink_on, now, dots)
        self._buttons(lang)
