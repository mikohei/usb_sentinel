"""Lines added to result.txt in phase 2. Helpers are borrowed from test_report.py and
test_report_v3.py."""

from __future__ import annotations

from tests.test_report import _clean_malware_step, _result
from tests.test_report_v3 import _error_capacity_step, _full_result, _skipped_speed_step
from usb_sentinel.libs import StepStatus, Verdict, render_text


class TestTextV3:
    def test_new_lines_are_shown(self) -> None:
        """Lines for elapsed time, link speed, advertised-capacity overrun, and severity
        appear."""
        result = _full_result()
        result.verdict = Verdict.COUNTERFEIT_SUSPECTED
        result.advertised_check = {
            "exceeded": True,
            "summary": "申告容量36.00GB > 上限34.24GB（公称32.00GBの112.5%）",
        }
        speed = result.step("speed")
        assert speed is not None
        speed.status = StepStatus.FAIL
        speed.details["severity"] = "minor"

        text = render_text(result)

        assert "所要時間: 1800.25秒" in text
        assert "リンク速度: 5000Mbps" in text
        assert "申告容量超過: 申告容量36.00GB > 上限34.24GB（公称32.00GBの112.5%）" in text
        assert "  公称（商品）: 32.00 GB" in text
        assert "  区別: ok" in text
        assert "  重大度: minor" in text

    def test_missing_new_keys_omit_lines_without_keyerror(self) -> None:
        """An ERROR capacity step or a speed step with empty details raises no KeyError;
        the affected lines are omitted."""
        result = _result(_clean_malware_step(), _error_capacity_step(), _skipped_speed_step())

        text = render_text(result)

        assert "所要時間" not in text
        assert "リンク速度" not in text
        assert "申告容量超過" not in text
        assert "公称（商品）" not in text
        assert "重大度" not in text
        assert "[ERR ] capacity: f3writeが見つからない" in text
        assert "[ -- ] speed: 破壊的検査は無効" in text

    def test_result_warnings_are_listed(self) -> None:
        result = _full_result()
        result.warnings = ["SQLite記録失敗（開始）: ファイル操作エラー: x", "種別未設定のため推定"]

        text = render_text(result)

        assert "警告: SQLite記録失敗（開始）: ファイル操作エラー: x\n" in text
        assert "警告: 種別未設定のため推定\n" in text

    def test_first_three_lines_keep_legacy_format(self) -> None:
        """The verdict, device, and nominal-capacity lines come first, in an unchanged
        format."""
        lines = render_text(_full_result()).splitlines()

        assert lines[:3] == [
            "判定: No Defect",
            "デバイス: /dev/sda (ABC123456789)",
            "公称容量   : 32.00 GB",
        ]
