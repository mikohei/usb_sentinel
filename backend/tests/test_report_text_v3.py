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
            "summary": "advertised capacity 36.00GB > limit 34.24GB (112.5% of nominal 32.00GB)",
        }
        speed = result.step("speed")
        assert speed is not None
        speed.status = StepStatus.FAIL
        speed.details["severity"] = "minor"

        text = render_text(result)

        assert "Elapsed: 1800.25s" in text
        assert "Link speed: 5000Mbps" in text
        assert (
            "Advertised capacity exceeded: advertised capacity 36.00GB > limit 34.24GB"
            " (112.5% of nominal 32.00GB)" in text
        )
        assert "  Nominal (product): 32.00 GB" in text
        assert "  Kind: ok" in text
        assert "  Severity: minor" in text

    def test_missing_new_keys_omit_lines_without_keyerror(self) -> None:
        """An ERROR capacity step or a speed step with empty details raises no KeyError;
        the affected lines are omitted."""
        result = _result(_clean_malware_step(), _error_capacity_step(), _skipped_speed_step())

        text = render_text(result)

        assert "Elapsed" not in text
        assert "Link speed" not in text
        assert "Advertised capacity exceeded" not in text
        assert "Nominal (product)" not in text
        assert "Severity" not in text
        assert "[ERR ] capacity: f3write not found" in text
        assert "[ -- ] speed: Destructive checks disabled" in text

    def test_result_warnings_are_listed(self) -> None:
        result = _full_result()
        result.warnings = [
            "SQLite record failed (start): File operation error: x",
            "Device class not set; inferred",
        ]

        text = render_text(result)

        assert "Warning: SQLite record failed (start): File operation error: x\n" in text
        assert "Warning: Device class not set; inferred\n" in text

    def test_first_three_lines_keep_legacy_format(self) -> None:
        """The verdict, device, and advertised-capacity lines come first, in the same order
        and number."""
        lines = render_text(_full_result()).splitlines()

        assert lines[:3] == [
            "Verdict: No Defect",
            "Device: /dev/sda (ABC123456789)",
            "Advertised capacity: 32.00 GB",
        ]
