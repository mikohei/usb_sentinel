import tkinter as tk
from collections.abc import Callable

from .. import qr_payload, qr_render
from ..models import Slot
from . import theme
from .i18n import text
from .theme import QR_H, QR_W, STRIP_H, Fonts

STRIP_TEXT_X = 4


class QrView:
    """右320pxのQRキャンバス（白地）と情報ストリップを描く。タップで通常／詳細を切り替える。"""

    def __init__(self, canvas: tk.Canvas, fonts: Fonts, on_toggle: Callable[[], None]) -> None:
        """描画先のCanvasとフォントを受け取り、タップで通常／詳細の切替を呼ぶよう束縛する。

        Args:
            canvas: 右側320pxのCanvas。
            fonts: 解決済みのフォント一式。
            on_toggle: タップ時に呼ぶ関数（通常QR／詳細QRの切替）。
        """
        self.canvas = canvas
        self.fonts = fonts
        # PhotoImageはCanvasが参照を持たないので、GCされないよう自身で保持する。
        self._image: tk.PhotoImage | None = None
        self._image_key: tuple[str, str, int] | None = None
        canvas.bind("<Button-1>", lambda _e: on_toggle())

    def _photo(self, slot: Slot, qr_mode: int) -> tk.PhotoImage | None:
        """スロットの結果とモードからPhotoImageを作る。同じ結果・同じモードなら前回のものを使う。

        Raises:
            ValueError: ペイロードがその枚の上限を超えたとき（他の枚には影響しない）。
        """
        if slot.result is None:
            return None
        key = (slot.result.dir_name, slot.result.digest, qr_mode)
        if self._image is not None and self._image_key == key:
            return self._image
        if qr_mode == 0:
            payload = qr_payload.build_payload(slot.result)
        else:
            payload = qr_payload.build_detail_page(slot.result, qr_mode)
        ppm, _side = qr_render.render(payload)
        self._image = tk.PhotoImage(master=self.canvas, data=ppm)
        self._image_key = key
        return self._image

    def render(self, slot: Slot | None, strip_text: str, qr_mode: int, lang: str) -> None:
        """白地にQRを中央に描き、情報ストリップに文言を出す。

        Args:
            slot: 選択中のスロット。Noneか結果が無ければ白地のまま。
            strip_text: 情報ストリップの文言。
            qr_mode: 0が通常QR、1〜3が詳細QRのページ。
            lang: 'en'または'ja'（QR生成失敗の文言に使う）。
        """
        canvas = self.canvas
        canvas.delete("all")
        canvas.create_rectangle(0, 0, QR_W, QR_H, fill=theme.COLORS["qr_background"], width=0)
        if slot is not None and slot.result is not None:
            try:
                image = self._photo(slot, qr_mode)
            except ValueError:
                self._image = None
                self._image_key = None
                canvas.create_text(
                    QR_W // 2,
                    QR_H // 2,
                    text=text(lang, "qr_failed"),
                    fill=theme.COLORS["qr_module"],
                    font=self.fonts.verdict,
                )
            else:
                canvas.create_image(QR_W // 2, QR_H // 2, image=image)
        else:
            self._image = None
            self._image_key = None
        canvas.create_rectangle(0, QR_H, QR_W, QR_H + STRIP_H, fill=theme.COLORS["strip"], width=0)
        canvas.create_text(
            STRIP_TEXT_X,
            QR_H + STRIP_H // 2,
            text=strip_text,
            anchor="w",
            fill=theme.COLORS["text"],
            font=self.fonts.body,
        )

    @property
    def image(self) -> tk.PhotoImage | None:
        """現在表示しているQRのPhotoImage（テスト用）。"""
        return self._image
