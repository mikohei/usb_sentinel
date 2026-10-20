"""ja/en screen text sets and the saved language. No GUI library, so cli.py can import it."""

import os
from pathlib import Path

from ..models import SlotState

LANGUAGES: tuple[str, ...] = ("en", "ja")
DEFAULT_LANGUAGE = "en"
# Where the language is saved ($XDG_STATE_HOME, else ~/.local/state). Never the results directory.
STATE_DIR_NAME = "usb-sentinel-display"
STATE_FILE_NAME = "language"

# Screen text. en and ja have the same key set (checked by tests/test_i18n.py).
MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        # The title row must fit 144 px with "P4 ", so use short words (measured in 18 px bold).
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
        "resolution": "Resolution {width}x{height} is below 480x320",
        "font_missing": "No Japanese font (install fonts-noto-cjk)",
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
        # Inspection-mode button (<= 152 px in 14 px), tile tags and the confirmation dialog.
        "mode.button_scan_only": "Mode: Scan only",
        "mode.button_full": "Mode: Scan + erase",
        "mode.button_stopping": "Stopping erase…",
        "mode.button_locked": "Scan only (locked)",
        "mode.tag_scan": "Scan",
        "mode.tag_erased": "Erased",
        "mode.confirm_title": "Turn on erase mode?",
        "mode.confirm_body": (
            "Drives inserted from now on will be erased after a clean malware scan. Continue?"
        ),
        "mode.confirm_code": "Code: {code}",
        "mode.confirm_hint": "Enter the code with keys 1-4 or the buttons. Esc: cancel ({n}/4)",
        "mode.confirm_cancel": "Cancel",
        # Info strip notices from the mode controller (<= 316 px in 14 px).
        "notice.mode.locked_ceiling": "Erasing is off on this host (admin settings)",
        "notice.mode.locked_unreadable": "Cannot read /etc/usb-sentinel settings",
        "notice.mode.locked_no_control": "Mode control dir unusable (group, re-login)",
        "notice.mode.locked_disabled": "Mode switching off (control_dir is empty)",
        "notice.mode.locked_new_input": "New input device seen; reboot to allow erase",
        "notice.mode.locked_strike_file": "Cannot use the wrong-code count; erase locked",
        "notice.mode.locked_no_input_watch": "Cannot watch input devices; erase locked",
        "notice.mode.locked_cooldown": "Wrong code; try again in 60 s",
        "notice.mode.locked_too_many": "3 wrong codes; admin must reset to allow erase",
        "notice.mode.wrong_code": "Wrong code; erase mode stays off",
        "notice.mode.write_failed": "Could not turn on erase mode",
        "notice.mode.reset_failed": "Could not turn erase off; it stops within 20 s",
        # "erase may run up to 20 s" measures 317 px on macOS; "for" keeps the meaning.
        "notice.mode.start_unknown": "Mode control unusable; erase may run for 20 s",
        "notice.mode.erase_off_busy": "Inserted drives may still be erased; remove them",
        "notice.mode.ceiling_lost": "Erasing was turned off on the host",
        "notice.mode.new_input_reset": "New input device; erase mode turned off",
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
        "resolution": "画面解像度{width}x{height}（480x320未満）",
        # ASCII on purpose: without a Japanese font, Japanese text would render as boxes.
        "font_missing": "No Japanese font (install fonts-noto-cjk)",
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
        "mode.button_scan_only": "モード: 検査のみ",
        "mode.button_full": "モード: 検査＋消去",
        "mode.button_stopping": "消去を停止中…",
        "mode.button_locked": "検査のみ（消去不可）",
        "mode.tag_scan": "検査のみ",
        "mode.tag_erased": "消去済",
        "mode.confirm_title": "消去モードにしますか？",
        "mode.confirm_body": (
            "これから挿すドライブは、マルウェア検査で問題がなければ消去して容量と速度を検査します。"
            "続けますか？"
        ),
        "mode.confirm_code": "コード: {code}",
        "mode.confirm_hint": "1〜4のキーかボタンで入力。Escでやめる（{n}/4）",
        "mode.confirm_cancel": "やめる",
        "notice.mode.locked_ceiling": "この機器では消去が無効（管理者設定）",
        "notice.mode.locked_unreadable": "/etc/usb-sentinelの設定を読めない",
        "notice.mode.locked_no_control": "モード制御を使えない（グループ、再ログイン）",
        "notice.mode.locked_disabled": "モード切替は無効（control_dir）",
        "notice.mode.locked_new_input": "新しい入力機器を検出。再起動まで消去不可",
        "notice.mode.locked_strike_file": "誤入力回数を記録できないため消去不可",
        "notice.mode.locked_no_input_watch": "入力機器を監視できないため消去不可",
        "notice.mode.locked_cooldown": "コード誤り。60秒後に再試行",
        "notice.mode.locked_too_many": "コード誤り3回。管理者の解除まで消去不可",
        "notice.mode.wrong_code": "コードが違うため消去モードにしない",
        "notice.mode.write_failed": "消去モードにできなかった",
        "notice.mode.reset_failed": "消去を解除できない。20秒以内に自動解除",
        "notice.mode.start_unknown": "モード制御不可。最大20秒は消去の可能性",
        "notice.mode.erase_off_busy": "挿入済みのドライブは消去されうる。抜いてください",
        "notice.mode.ceiling_lost": "管理者設定で消去が無効になった",
        "notice.mode.new_input_reset": "新しい入力機器のため消去モードを解除",
    },
}

# Verdict labels (design doc section 8). English uses short forms that fit tile line 1
# (160 px wide, 18 px bold).
VERDICT_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "No Defect": "No Defect",
        "No Defect (Scan Only)": "Scan OK",
        "Malware Detected": "Malware",
        "Counterfeit Suspected": "Counterfeit",
        # "Spec Mismatch" with "P1 " is 157 px (18 px bold, measured on macOS), over 144 px,
        # so it is shortened.
        "Spec Mismatch": "Mismatch",
        "Defective": "Defective",
        "Inconclusive": "Inconclusive",
    },
    "ja": {
        "No Defect": "異常なし",
        "No Defect (Scan Only)": "異常なし（検査のみ）",
        "Malware Detected": "マルウェア検知",
        "Counterfeit Suspected": "偽装疑い",
        "Spec Mismatch": "仕様不一致",
        "Defective": "不良",
        "Inconclusive": "判定不能",
    },
}
UNKNOWN_VERDICT_LABEL: dict[str, str] = {"en": "Unknown", "ja": "判定不明"}


def text(lang: str, key: str, **kwargs: object) -> str:
    """Look up a text and fill its placeholders with str.format.

    Args:
        lang: 'en' or 'ja'.
        key: A MESSAGES key.
        **kwargs: Placeholder values.

    Returns:
        The text for the language.
    """
    template = MESSAGES[lang][key]
    return template.format(**kwargs) if kwargs else template


def verdict_label(lang: str, verdict: str) -> str:
    """Label for a verdict string. Unknown verdicts get the "unknown verdict" label."""
    return VERDICT_LABELS[lang].get(verdict, UNKNOWN_VERDICT_LABEL[lang])


def state_label(lang: str, state: SlotState) -> str:
    """Text for a slot state."""
    return text(lang, "state." + state.value)


def other_language(lang: str) -> str:
    """The language to switch to."""
    return "ja" if lang == "en" else "en"


def language_button_label(lang: str) -> str:
    """Target language name on the language button (Japanese name while in English, "EN" in ja)."""
    return text(lang, "language_button")


def state_file() -> Path:
    """Path of the saved-language file: $XDG_STATE_HOME if not empty, else ~/.local/state."""
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / STATE_DIR_NAME / STATE_FILE_NAME


def load_saved_language(path: Path) -> str | None:
    """Read the saved language. None if unreadable or invalid."""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value if value in LANGUAGES else None


def save_language(path: Path, lang: str) -> str | None:
    """Save the language.

    Returns:
        None on success, or a reason string on failure (never raises).
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(lang + "\n", encoding="utf-8")
    except OSError as exc:
        # Return the reason in the target language (matching the operator reading the journal).
        return f"{text(lang, 'language_save_failed', path=path)} ({exc})"
    return None


def initial_language(configured: str | None, saved: str | None) -> str:
    """Startup language. Priority: saved -> config -> default (values outside LANGUAGES ignored)."""
    if saved is not None and saved in LANGUAGES:
        return saved
    if configured is not None and configured in LANGUAGES:
        return configured
    return DEFAULT_LANGUAGE
