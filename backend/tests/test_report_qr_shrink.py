"""QR v:3の省略順序（設計書第6.3節）。材料はtest_report_v3.pyから借りる。"""

from __future__ import annotations

import json

import pytest

from tests.test_report_v3 import _full_result
from usb_sentinel.libs import InspectionResult, StepResult, StepStatus, Verdict
from usb_sentinel.libs.report.qr import MAX_PAYLOAD_BYTES, build_payload


class TestQrTruncationOrder:
    """設計書第6.3節の省略順序。各段階の後に再計算し、2953バイト以下で止める。"""

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
        """段階1: シグネチャ60件→3件で収まり、truncatedが立つ。"""
        text = build_payload(self._infected(60))
        payload = json.loads(text)

        assert len(text.encode("utf-8")) <= MAX_PAYLOAD_BYTES
        assert payload["truncated"] is True
        assert payload["malware_count"] == 60
        assert len(payload["signatures"]) == 3
        # 段階2には進んでいない。
        assert payload["reasons"] == ["全ステップ Pass"]

    def test_reasons_clipped_to_120_chars(self) -> None:
        """段階2: 理由3件×600文字→各120文字＋「…」。"""
        result = _full_result()
        result.reasons = ["あ" * 600, "い" * 600, "う" * 600]

        text = build_payload(result)
        payload = json.loads(text)

        assert len(text.encode("utf-8")) <= MAX_PAYLOAD_BYTES
        assert payload["truncated"] is True
        assert payload["reasons"] == ["あ" * 120 + "…", "い" * 120 + "…", "う" * 120 + "…"]

    def test_short_reasons_are_not_clipped(self) -> None:
        """120文字以下の理由には「…」を付けない。"""
        result = self._infected(60)
        result.reasons = ["短い理由", "え" * 600]

        payload = json.loads(build_payload(result))

        assert payload["reasons"][0] == "短い理由"
        assert payload["reasons"][1] == "え" * 120 + "…"

    def test_serial_cut_to_128_chars(self) -> None:
        """段階5: シリアル200文字→128文字。

        シグネチャと理由は空にして段階1〜4を素通りさせ、port_path（省略対象外）で
        「シリアル128文字ならちょうど上限」になるよう埋める。
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
        """段階6: 他を削っても超えるときはcapacity.probe_verdict_lineを落とす。"""
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
        """段階7: 省略対象外のキーだけで超えるときはValueError。"""
        result = _full_result()
        result.device.port_path = "p" * 3000

        with pytest.raises(ValueError, match="QR payload too large"):
            build_payload(result)
