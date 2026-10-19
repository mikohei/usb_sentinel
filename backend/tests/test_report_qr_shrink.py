"""Shrink order of QR v:3 (design doc section 6.3). Helpers are borrowed from test_report_v3.py."""

from __future__ import annotations

import json

import pytest

from tests.test_report_v3 import _full_result
from usb_sentinel.libs import InspectionResult, StepResult, StepStatus, Verdict
from usb_sentinel.libs.report.qr import MAX_PAYLOAD_BYTES, build_payload


class TestQrTruncationOrder:
    """Shrink order from design doc section 6.3. Recompute after each stage and stop at 2953
    bytes or less."""

    def _infected(self, count: int) -> InspectionResult:
        detections = [
            {
                "path": f"/mnt/sda1/very/long/path/to/sample/number/{i}.bin",
                "signature": f"Win.Trojan.VeryLongSignatureName-{i:04d}",
            }
            for i in range(count)
        ]
        result = _full_result()
        result.verdict = Verdict.MALWARE_DETECTED
        result.steps[0] = StepResult(
            name="malware",
            status=StepStatus.FAIL,
            details={"database": {"db_version": "27200"}, "detections": detections},
        )
        return result

    def test_signatures_cut_to_three(self) -> None:
        """Stage 1: 60 signatures cut to 3 fit, and truncated is set."""
        text = build_payload(self._infected(60))
        payload = json.loads(text)

        assert len(text.encode("utf-8")) <= MAX_PAYLOAD_BYTES
        assert payload["truncated"] is True
        assert payload["malware_count"] == 60
        assert len(payload["signatures"]) == 3
        # It did not proceed to stage 2.
        assert payload["reasons"] == ["全ステップ Pass"]

    def test_reasons_clipped_to_120_chars(self) -> None:
        """Stage 2: 3 reasons x 600 chars cut to 120 chars each plus "…"."""
        result = _full_result()
        result.reasons = ["あ" * 600, "い" * 600, "う" * 600]

        text = build_payload(result)
        payload = json.loads(text)

        assert len(text.encode("utf-8")) <= MAX_PAYLOAD_BYTES
        assert payload["truncated"] is True
        assert payload["reasons"] == ["あ" * 120 + "…", "い" * 120 + "…", "う" * 120 + "…"]

    def test_short_reasons_are_not_clipped(self) -> None:
        """Reasons of 120 chars or less get no "…"."""
        result = self._infected(60)
        result.reasons = ["短い理由", "え" * 600]

        payload = json.loads(build_payload(result))

        assert payload["reasons"][0] == "短い理由"
        assert payload["reasons"][1] == "え" * 120 + "…"

    def test_serial_cut_to_128_chars(self) -> None:
        """Stage 5: serial cut from 200 chars to 128 chars.

        Leave signatures and reasons empty so stages 1-4 pass through, and pad port_path
        (not subject to shrinking) so that a 128-char serial lands exactly at the limit.
        """
        base = _full_result(serial="S" * 128)
        base.reasons = []
        base.device.port_path = ""
        room = MAX_PAYLOAD_BYTES - len(build_payload(base).encode("utf-8"))
        assert room > 0

        result = _full_result(serial="S" * 200)
        result.reasons = []
        result.device.port_path = "p" * room

        text = build_payload(result)
        payload = json.loads(text)

        assert len(text.encode("utf-8")) <= MAX_PAYLOAD_BYTES
        assert payload["truncated"] is True
        assert payload["serial"] == "S" * 128

    def test_probe_verdict_line_dropped_last(self) -> None:
        """Stage 6: if still over after trimming the rest, drop capacity.probe_verdict_line."""
        result = _full_result()
        result.reasons = []
        capacity = result.step("capacity")
        assert capacity is not None
        capacity.details["probe_verdict_line"] = "Bad news: " + "x" * 3000

        text = build_payload(result)
        payload = json.loads(text)

        assert len(text.encode("utf-8")) <= MAX_PAYLOAD_BYTES
        assert payload["truncated"] is True
        assert payload["capacity"]["probe_verdict_line"] is None

    def test_still_too_large_raises(self) -> None:
        """Stage 7: ValueError when the non-shrinkable keys alone exceed the limit."""
        result = _full_result()
        result.device.port_path = "p" * 3000

        with pytest.raises(ValueError, match="QR payload too large"):
            build_payload(result)
