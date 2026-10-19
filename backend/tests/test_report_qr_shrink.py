"""Shrink order of QR v:3 (design doc section 6.3). Helpers are borrowed from test_report_v3.py."""

from __future__ import annotations

import json

import pytest

from tests.test_report_v3 import _full_result
from usb_sentinel.libs import InspectionResult, StepResult, StepStatus, Verdict, classify
from usb_sentinel.libs.orchestrator import reason_codes_for
from usb_sentinel.libs.report.qr import MAX_PAYLOAD_BYTES, build_payload
from usb_sentinel.libs.steps.capacity_judge import judge, precheck_advertised
from usb_sentinel.libs.steps.speed_judge import FAIL_PREFIX, Reference, basis_text


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
        assert payload["reasons"] == ["All steps passed"]

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
        result.reasons = ["short reason", "え" * 600]

        payload = json.loads(build_payload(result))

        assert payload["reasons"][0] == "short reason"
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


class TestEnglishReasonsFit:
    """English reasons for the longest realistic case fit the file QR without truncation."""

    def test_english_worst_case_fits_without_truncation(self) -> None:
        # Counterfeit (all four conditions) and severe speed shortfall (read and write, class
        # inferred) together give the longest reasons. Malware FAIL would stop at one reason.
        nominal = 2_000_000_000_000
        advertised = 2_200_000_000_000
        actual = 2_150_000_000_000
        threshold = 1_860_000_000_000
        result = _full_result()
        result.advertised_check = precheck_advertised(advertised, nominal, 1.07)
        verdict = judge(
            actual=actual,
            advertised=advertised,
            nominal=nominal,
            pass_ratio=0.93,
            over_ratio=1.07,
            bad_sectors=2_100_000_000,
            lost_ratio=0.52,
            probe_verdict_line="Bad news: The device `/dev/sda' is a counterfeit of type limbo",
            verified_bytes=actual,
            threshold=threshold,
        )
        capacity = result.step("capacity")
        assert capacity is not None
        capacity.status = verdict.status
        capacity.summary = verdict.summary
        capacity.details["kind"] = verdict.kind
        capacity.details["counterfeit"] = verdict.counterfeit
        capacity.details["counterfeit_reasons"] = list(verdict.counterfeit_reasons)
        speed = result.step("speed")
        assert speed is not None
        speed.status = StepStatus.FAIL
        speed.details["severity"] = "severe"
        speed.summary = FAIL_PREFIX["severe"] + "; ".join(
            [
                "read 12.3MB/s < threshold 40.0MB/s"
                + basis_text(Reference(50.0, "class", "usb3_flash"), 0.80, "link_speed"),
                "write 1.2MB/s < threshold 12.0MB/s"
                + basis_text(Reference(15.0, "class", "usb3_flash"), 0.80, "link_speed"),
            ]
        )
        result.verdict, result.reasons = classify(result)
        result.reason_codes = reason_codes_for(result)
        assert result.verdict is Verdict.COUNTERFEIT_SUSPECTED

        text = build_payload(result)
        payload = json.loads(text)

        assert len(text.encode("utf-8")) <= MAX_PAYLOAD_BYTES
        assert payload["truncated"] is False
        assert payload["reasons"] == list(result.reasons)
        assert all(reason.isascii() for reason in result.reasons)
        # Long enough to be a clipping candidate, yet it fits, so nothing is cut.
        assert max(len(reason) for reason in result.reasons) > 120
