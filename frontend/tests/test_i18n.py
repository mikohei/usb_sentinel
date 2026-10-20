from pathlib import Path

import pytest

from usb_sentinel_display.libs import ceiling, config, control, mode_view
from usb_sentinel_display.libs.models import VERDICT_CODES, SlotState
from usb_sentinel_display.libs.ui import i18n
from usb_sentinel_display.libs.ui.i18n import (
    MESSAGES,
    VERDICT_LABELS,
    initial_language,
    language_button_label,
    load_saved_language,
    other_language,
    save_language,
    state_file,
    state_label,
    text,
    verdict_label,
)

# Every lock reason and notice the mode controller can put into ModeView (design doc 5.4/5.6).
MODE_NOTICE_KEYS: tuple[str, ...] = (
    ceiling.REASON_CEILING_OFF,
    ceiling.REASON_UNREADABLE,
    control.REASON_NO_CONTROL,
    control.REASON_DISABLED,
    mode_view.LOCKED_NEW_INPUT,
    mode_view.LOCKED_STRIKE_FILE,
    mode_view.LOCKED_NO_INPUT_WATCH,
    mode_view.LOCKED_COOLDOWN,
    mode_view.LOCKED_TOO_MANY,
    mode_view.NOTICE_WRONG_CODE,
    mode_view.NOTICE_WRITE_FAILED,
    mode_view.NOTICE_RESET_FAILED,
    mode_view.NOTICE_START_UNKNOWN,
    mode_view.NOTICE_ERASE_OFF_BUSY,
    mode_view.NOTICE_CEILING_LOST,
    mode_view.NOTICE_NEW_INPUT_RESET,
)
MODE_LABEL_KEYS: tuple[str, ...] = (
    "mode.button_scan_only",
    "mode.button_full",
    "mode.button_stopping",
    "mode.button_locked",
    "mode.tag_scan",
    "mode.tag_erased",
    "mode.confirm_title",
    "mode.confirm_body",
    "mode.confirm_code",
    "mode.confirm_hint",
    "mode.confirm_cancel",
)


class TestModeMessages:
    def test_every_controller_reason_has_a_text(self) -> None:
        """Each lock reason / notice the controller emits has a notice.mode.* text in en and ja."""
        assert len(set(MODE_NOTICE_KEYS)) == 16
        for lang in i18n.LANGUAGES:
            for reason in MODE_NOTICE_KEYS:
                assert text(lang, "notice.mode." + reason)

    def test_no_orphan_mode_notice(self) -> None:
        """No notice.mode.* text exists without a controller reason that uses it."""
        expected = {"notice.mode." + reason for reason in MODE_NOTICE_KEYS}
        present = {key for key in MESSAGES["en"] if key.startswith("notice.mode.")}
        assert present == expected

    def test_mode_labels_present(self) -> None:
        """The button, tag and dialog texts exist in both languages (27 keys with the notices)."""
        for lang in i18n.LANGUAGES:
            for key in MODE_LABEL_KEYS:
                assert text(lang, key)
        mode_keys = {key for key in MESSAGES["en"] if key.startswith("mode.")}
        assert mode_keys == set(MODE_LABEL_KEYS)
        assert len(MODE_LABEL_KEYS) + len(MODE_NOTICE_KEYS) == 27

    def test_placeholders(self) -> None:
        assert text("en", "mode.confirm_code", code="3 1 4 2") == "Code: 3 1 4 2"
        assert text("ja", "mode.confirm_code", code="3 1 4 2") == "コード: 3 1 4 2"
        assert text("en", "mode.confirm_hint", n=2).endswith("(2/4)")
        assert text("ja", "mode.confirm_hint", n=2).endswith("（2/4）")


class TestMessages:
    def test_same_keys_and_no_empty(self) -> None:
        """The ja and en key sets match and contain no empty strings."""
        assert set(MESSAGES["ja"]) == set(MESSAGES["en"])
        for lang in i18n.LANGUAGES:
            assert all(value for value in MESSAGES[lang].values())

    def test_all_states_and_verdicts_have_labels(self) -> None:
        """Every SlotState and every VERDICT_CODES key has a label in both languages."""
        for lang in i18n.LANGUAGES:
            for state in SlotState:
                assert state_label(lang, state)
            assert set(VERDICT_LABELS[lang]) == set(VERDICT_CODES)

    @pytest.mark.parametrize(
        ("verdict", "ja", "en"),
        [
            ("No Defect", "異常なし", "No Defect"),
            ("Malware Detected", "マルウェア検知", "Malware"),
            ("Counterfeit Suspected", "偽装疑い", "Counterfeit"),
            ("Spec Mismatch", "仕様不一致", "Mismatch"),
            ("Defective", "不良", "Defective"),
            ("Inconclusive", "判定不能", "Inconclusive"),
            ("Bogus", "判定不明", "Unknown"),
        ],
    )
    def test_verdict_label(self, verdict: str, ja: str, en: str) -> None:
        """Labels for the six verdicts and an unknown value."""
        assert verdict_label("ja", verdict) == ja
        assert verdict_label("en", verdict) == en

    def test_language_button_label(self) -> None:
        """The language button shows the name of the language it switches to."""
        assert language_button_label("en") == "日本語"
        assert language_button_label("ja") == "EN"
        assert other_language("en") == "ja"
        assert other_language("ja") == "en"

    def test_text_formats_placeholders(self) -> None:
        """Placeholders are filled by str.format."""
        assert text("ja", "overflow", count=2) == "他2台"
        assert text("en", "overflow", count=2) == "+2 more"
        assert text("en", "notice.sysfs_missing").startswith("sysfs")


class TestStateFile:
    def test_xdg_state_home_preferred(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Use XDG_STATE_HOME if it is not empty."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert state_file() == tmp_path / "usb-sentinel-display" / "language"

    def test_fallback_to_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Without XDG_STATE_HOME, use HOME/.local/state. An empty string counts as unset."""
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        expected = tmp_path / ".local" / "state" / "usb-sentinel-display" / "language"
        assert state_file() == expected
        monkeypatch.setenv("XDG_STATE_HOME", "")
        assert state_file() == expected


class TestSaveLoad:
    def test_round_trip(self, tmp_path: Path) -> None:
        """save -> load returns the same value (and creates the parent directory)."""
        path = tmp_path / "state" / "usb-sentinel-display" / "language"
        assert save_language(path, "ja") is None
        assert path.read_text(encoding="utf-8") == "ja\n"
        assert load_saved_language(path) == "ja"

    @pytest.mark.parametrize("content", ["fr", "", "  \n"])
    def test_invalid_content_is_none(self, tmp_path: Path, content: str) -> None:
        """Invalid values and an empty file give None."""
        path = tmp_path / "language"
        path.write_text(content, encoding="utf-8")
        assert load_saved_language(path) is None

    def test_missing_file_is_none(self, tmp_path: Path) -> None:
        assert load_saved_language(tmp_path / "none") is None

    def test_binary_garbage_is_none(self, tmp_path: Path) -> None:
        """Content that is not valid UTF-8 gives None."""
        path = tmp_path / "language"
        path.write_bytes(b"\xff\xfe\x00ja")
        assert load_saved_language(path) is None

    def test_save_failure_returns_reason(self, tmp_path: Path) -> None:
        """If the parent is a file, return a reason string without raising."""
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        reason = save_language(blocker / "language", "ja")
        assert reason is not None
        assert str(blocker / "language") in reason

    def test_save_failure_reason_in_target_language(self, tmp_path: Path) -> None:
        """The failure reason is in the target language (Japanese for ja, English for en)."""
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        target = blocker / "language"
        ja_reason = save_language(target, "ja")
        en_reason = save_language(target, "en")
        assert ja_reason is not None and en_reason is not None
        assert ja_reason.startswith(f"言語の記録に失敗: {target}")
        assert en_reason.startswith(f"Failed to save language: {target}")


class TestInitialLanguage:
    def test_priority(self) -> None:
        """Priority: saved -> config -> default. Values outside LANGUAGES are ignored."""
        assert initial_language(None, None) == "en"
        assert initial_language("ja", None) == "ja"
        assert initial_language("en", "ja") == "ja"
        assert initial_language("ja", "en") == "en"
        assert initial_language("fr", None) == "en"
        assert initial_language("fr", "fr") == "en"
        assert initial_language("ja", "fr") == "ja"

    def test_config_languages_match(self) -> None:
        """The set that config.py keeps on its own matches i18n."""
        assert config.LANGUAGES == i18n.LANGUAGES
        assert i18n.DEFAULT_LANGUAGE in i18n.LANGUAGES
