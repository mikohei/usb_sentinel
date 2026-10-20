"""Verdict classification (classify).

Verify the priority order: malware > Defective > Spec Mismatch > No Defect > Inconclusive.
"""

from __future__ import annotations

from typing import Any

from usb_sentinel.libs import (
    DeviceInfo,
    InspectionResult,
    StepResult,
    StepStatus,
    Verdict,
    classify,
)


class TestClassify:
    def _result(self, *steps: StepResult) -> InspectionResult:
        device = DeviceInfo(path="/dev/sda", kernel_name="sda", advertised_bytes=1)
        return InspectionResult(
            device=device, started_at="2024-01-01T00:00:00+00:00", steps=list(steps)
        )

    def test_all_pass_is_no_defect(self) -> None:
        result = self._result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(name="capacity", status=StepStatus.PASS),
        )

        verdict, reasons = classify(result)

        assert verdict is Verdict.NO_DEFECT
        assert reasons == ["All steps passed"]

    def test_malware_detection_is_usn(self) -> None:
        result = self._result(
            StepResult(
                name="malware",
                status=StepStatus.FAIL,
                details={"detections": [{"path": "a", "signature": "X"}]},
            ),
            StepResult(name="capacity", status=StepStatus.FAIL),
        )

        verdict, reasons = classify(result)

        # Malware takes priority over other failures.
        assert verdict is Verdict.MALWARE_DETECTED
        assert reasons == ["Malware detections: 1"]

    def test_capacity_failure_is_spec_mismatch(self) -> None:
        result = self._result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(name="capacity", status=StepStatus.FAIL, summary="Capacity shortfall"),
        )

        verdict, reasons = classify(result)

        assert verdict is Verdict.SPEC_MISMATCH
        assert "capacity: Capacity shortfall" in reasons

    def test_defect_failure_is_defective(self) -> None:
        result = self._result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(
                name="capacity",
                status=StepStatus.FAIL,
                summary="Data corruption: 12 sectors",
                details={"defect": True},
            ),
        )

        verdict, reasons = classify(result)

        assert verdict is Verdict.DEFECTIVE
        assert reasons == ["capacity: Data corruption: 12 sectors"]

    def test_defect_and_spec_failure_is_defective(self) -> None:
        result = self._result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(
                name="capacity",
                status=StepStatus.FAIL,
                summary="Data corruption",
                details={"defect": True},
            ),
            StepResult(name="speed", status=StepStatus.FAIL, summary="Speed shortfall"),
        )

        verdict, reasons = classify(result)

        assert verdict is Verdict.DEFECTIVE
        assert reasons == ["capacity: Data corruption", "speed: Speed shortfall"]

    def test_defect_false_is_spec_mismatch(self) -> None:
        result = self._result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(
                name="capacity",
                status=StepStatus.FAIL,
                summary="Capacity shortfall",
                details={"defect": False},
            ),
        )

        assert classify(result)[0] is Verdict.SPEC_MISMATCH

    def test_malware_beats_defect(self) -> None:
        result = self._result(
            StepResult(name="malware", status=StepStatus.FAIL, details={"detections": []}),
            StepResult(name="capacity", status=StepStatus.FAIL, details={"defect": True}),
        )

        assert classify(result)[0] is Verdict.MALWARE_DETECTED

    def test_error_is_inconclusive(self) -> None:
        result = self._result(
            StepResult(name="malware", status=StepStatus.ERROR, summary="Scan failed"),
        )

        assert classify(result)[0] is Verdict.INCONCLUSIVE

    def test_not_implemented_is_inconclusive(self) -> None:
        """Never judge No Defect while unimplemented steps remain."""
        result = self._result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(
                name="capacity", status=StepStatus.NOT_IMPLEMENTED, summary="Not implemented"
            ),
        )

        verdict, reasons = classify(result)

        assert verdict is Verdict.INCONCLUSIVE
        assert "capacity: Not implemented" in reasons


def _result(*steps: StepResult, advertised_check: dict[str, Any] | None = None) -> InspectionResult:
    device = DeviceInfo(path="/dev/sda", kernel_name="sda", advertised_bytes=1)
    result = InspectionResult(
        device=device, started_at="2024-01-01T00:00:00+00:00", steps=list(steps)
    )
    if advertised_check is not None:
        result.advertised_check = advertised_check
    return result


def _counterfeit(*reasons: str, summary: str = "Suspected counterfeit: x") -> StepResult:
    return StepResult(
        name="capacity",
        status=StepStatus.FAIL,
        summary=summary,
        details={
            "defect": False,
            "kind": "counterfeit",
            "counterfeit": True,
            "counterfeit_reasons": list(reasons),
        },
    )


class TestClassifyCounterfeit:
    def test_counterfeit_beats_defect(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.PASS),
            _counterfeit("lost_ratio"),
            StepResult(name="speed", status=StepStatus.FAIL, details={"defect": True}),
        )

        verdict, reasons = classify(result)

        assert verdict is Verdict.COUNTERFEIT_SUSPECTED
        assert reasons[0] == "capacity: Suspected counterfeit: x"

    def test_malware_beats_counterfeit(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.FAIL, details={"detections": []}),
            _counterfeit("probe_bad_news"),
            advertised_check={"exceeded": True, "summary": "advertised capacity…"},
        )

        assert classify(result)[0] is Verdict.MALWARE_DETECTED

    def test_advertised_check_alone_is_counterfeit(self) -> None:
        """Even with the capacity step SKIPPED, an overrun in the non-destructive check alone
        means suspected counterfeit."""
        result = _result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(name="capacity", status=StepStatus.SKIPPED, summary="allow_destructive"),
            advertised_check={
                "exceeded": True,
                "summary": "advertised capacity 32.00GB > limit 31.03GB",
            },
        )

        verdict, reasons = classify(result)

        assert verdict is Verdict.COUNTERFEIT_SUSPECTED
        assert reasons[0] == "advertised capacity 32.00GB > limit 31.03GB"
        assert len(reasons) == 1

    def test_advertised_check_not_exceeded_keeps_current_verdict(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(name="capacity", status=StepStatus.PASS),
            advertised_check={"exceeded": False, "summary": ""},
        )

        assert classify(result)[0] is Verdict.NO_DEFECT


def test_missing_db_reason_reaches_reasons() -> None:
    """定義DB欠落の理由文が Inconclusive の reasons にそのまま載る。"""
    from usb_sentinel.libs.steps.malware import DB_MISSING_SUMMARY

    device = DeviceInfo(path="/dev/sda", kernel_name="sda", advertised_bytes=1)
    result = InspectionResult(
        device=device,
        started_at="2024-01-01T00:00:00+00:00",
        steps=[StepResult(name="malware", status=StepStatus.ERROR, summary=DB_MISSING_SUMMARY)],
    )
    verdict, reasons = classify(result)
    assert verdict is Verdict.INCONCLUSIVE
    assert f"malware: {DB_MISSING_SUMMARY}" in reasons
