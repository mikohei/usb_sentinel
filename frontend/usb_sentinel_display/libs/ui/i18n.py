"""画面文言のja／en 2組と言語の記録。GUIライブラリを読まないのでcli.pyからもimportできる。"""

import os
from pathlib import Path

from ..models import SlotState

LANGUAGES: tuple[str, ...] = ("en", "ja")
DEFAULT_LANGUAGE = "en"
# 言語の記録先（$XDG_STATE_HOME、無ければ~/.local/state）。結果ディレクトリには書かない。
STATE_DIR_NAME = "usb-sentinel-display"
STATE_FILE_NAME = "language"

# 文言。enとjaは同じキー集合を持つ（tests/test_i18n.pyで確認する）。
MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        # タイトル行は"P4 "付きで144pxに収める必要があるため短い語を選ぶ（18px太字で実測）。
        "state.empty": "No disk",
        "state.waiting": "Starting",
        "state.inspecting": "Scanning",
        "state.done": "Done",
        "state.no_result": "No result",
        "state.broken": "Broken",
        "state.removed": "Removed",
        "qr_failed": "QR generation failed",
        "fallback_label": "P?",
        "inspecting_minutes": "Scanning ({minutes} min){dots}",
        "done_at": "Done {time}",
        "removed_at": "Removed {time}",
        "state_at": "{word} {time}",
        "overflow": "+{count} more",
        "check_journal": "Check journalctl -u {unit}",
        "resolution": "Resolution {width}x{height} (expected 480x320)",
        "internal_error": "Internal error: {name}",
        "internal_error_repeated": "Internal error persists; check stderr",
        "notice.results_missing": "Results directory not found",
        "notice.results_permission": (
            "No permission to read results dir (README step 7: usb-sentinel group, re-login)"
        ),
        "notice.sysfs_missing": "sysfs not found (check sys_block setting)",
        "notice.sysfs_permission": "No permission to read sysfs",
        "notice.db_missing": "No SQLite index; scanning results dir",
        "notice.db_open_failed": "SQLite index unreadable; scanning results dir",
        "notice.db_schema_new": "SQLite index schema is newer; scanning results dir",
        "qr_mode_detail": "Detail {page}/{total}",
        "qr_detail_hint": "◀▶ page",
        "language_button": "日本語",
        "language_save_failed": "Failed to save language: {path}",
    },
    "ja": {
        "state.empty": "未接続",
        "state.waiting": "起動待ち",
        "state.inspecting": "検査中",
        "state.done": "完了",
        "state.no_result": "結果なし",
        "state.broken": "結果破損",
        "state.removed": "抜去",
        "qr_failed": "QR生成失敗",
        "fallback_label": "P?",
        "inspecting_minutes": "検査中（{minutes}分）{dots}",
        "done_at": "完了{time}",
        "removed_at": "抜去{time}",
        "state_at": "{word}{time}",
        "overflow": "他{count}台",
        "check_journal": "journalctl -u {unit}を確認",
        "resolution": "画面解像度{width}x{height}（480x320を想定）",
        "internal_error": "内部エラー: {name}",
        "internal_error_repeated": "内部エラーが継続中。標準エラー出力を確認",
        "notice.results_missing": "結果ディレクトリが見つからない",
        "notice.results_permission": (
            "結果ディレクトリを読む権限がない（README手順7。usb-sentinelグループと再ログインを確認）"
        ),
        "notice.sysfs_missing": "sysfsが見つからない（sys_blockの設定を確認）",
        "notice.sysfs_permission": "sysfsを読む権限がない",
        "notice.db_missing": "SQLite索引が無いため結果ディレクトリを走査",
        "notice.db_open_failed": "SQLite索引を開けないため結果ディレクトリを走査",
        "notice.db_schema_new": "SQLite索引が新しい形式のため結果ディレクトリを走査",
        "qr_mode_detail": "詳細{page}/{total}",
        "qr_detail_hint": "◀▶でページ",
        "language_button": "EN",
        "language_save_failed": "言語の記録に失敗: {path}",
    },
}

# 判定ラベル（設計書8節）。英語はタイル1行目（幅160px、18px太字）に収まる短縮形。
VERDICT_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "No Defect": "No Defect",
        "Malware Detected": "Malware",
        "Counterfeit Suspected": "Counterfeit",
        # "Spec Mismatch"は"P1 "付きで157px（18px太字、macOS実測）となり144pxを超えるので短縮。
        "Spec Mismatch": "Mismatch",
        "Defective": "Defective",
        "Inconclusive": "Inconclusive",
    },
    "ja": {
        "No Defect": "異常なし",
        "Malware Detected": "マルウェア検知",
        "Counterfeit Suspected": "偽装疑い",
        "Spec Mismatch": "仕様不一致",
        "Defective": "不良",
        "Inconclusive": "判定不能",
    },
}
UNKNOWN_VERDICT_LABEL: dict[str, str] = {"en": "Unknown", "ja": "判定不明"}


def text(lang: str, key: str, **kwargs: object) -> str:
    """文言を取り出し、プレースホルダをstr.formatで埋める。

    Args:
        lang: 'en'または'ja'。
        key: MESSAGESのキー。
        **kwargs: プレースホルダの値。

    Returns:
        言語に応じた文言。
    """
    template = MESSAGES[lang][key]
    return template.format(**kwargs) if kwargs else template


def verdict_label(lang: str, verdict: str) -> str:
    """判定文字列に対応するラベル。未知の判定は「判定不明」／「Unknown」。"""
    return VERDICT_LABELS[lang].get(verdict, UNKNOWN_VERDICT_LABEL[lang])


def state_label(lang: str, state: SlotState) -> str:
    """スロット状態の文言。"""
    return text(lang, "state." + state.value)


def other_language(lang: str) -> str:
    """切替先の言語。"""
    return "ja" if lang == "en" else "en"


def language_button_label(lang: str) -> str:
    """言語ボタンに描く切替先の名前（英語表示中は「日本語」、日本語表示中は「EN」）。"""
    return text(lang, "language_button")


def state_file() -> Path:
    """言語の記録ファイルのパス。$XDG_STATE_HOMEが空でなければそれ、無ければ~/.local/state。"""
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / STATE_DIR_NAME / STATE_FILE_NAME


def load_saved_language(path: Path) -> str | None:
    """記録された言語を読む。読めない・不正な値ならNone。"""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value if value in LANGUAGES else None


def save_language(path: Path, lang: str) -> str | None:
    """言語を記録する。

    Returns:
        成功ならNone、失敗なら理由の文字列（例外は出さない）。
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(lang + "\n", encoding="utf-8")
    except OSError as exc:
        # 切り替え先の言語で理由を返す（journalを読む運用者の言語に揃える）。
        return f"{text(lang, 'language_save_failed', path=path)} ({exc})"
    return None


def initial_language(configured: str | None, saved: str | None) -> str:
    """起動時の言語。優先順は記録→設定→既定（LANGUAGES外の値は無視する）。"""
    if saved is not None and saved in LANGUAGES:
        return saved
    if configured is not None and configured in LANGUAGES:
        return configured
    return DEFAULT_LANGUAGE
