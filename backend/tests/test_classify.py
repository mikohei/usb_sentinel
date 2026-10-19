"""判定分類（classify）。

マルウェア > Defective > Spec Mismatch > No Defect > Inconclusive の優先順位を確認する。
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
        assert reasons == ["全ステップ Pass"]

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

        # マルウェアは他の失敗より優先される。
        assert verdict is Verdict.MALWARE_DETECTED
        assert reasons == ["マルウェアを 1 件検知"]

    def test_capacity_failure_is_spec_mismatch(self) -> None:
        result = self._result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(name="capacity", status=StepStatus.FAIL, summary="実容量が不足"),
        )

        verdict, reasons = classify(result)

        assert verdict is Verdict.SPEC_MISMATCH
        assert "capacity: 実容量が不足" in reasons

    def test_defect_failure_is_defective(self) -> None:
        result = self._result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(
                name="capacity",
                status=StepStatus.FAIL,
                summary="データ破損: 12セクタ",
                details={"defect": True},
            ),
        )

        verdict, reasons = classify(result)

        assert verdict is Verdict.DEFECTIVE
        assert reasons == ["capacity: データ破損: 12セクタ"]

    def test_defect_and_spec_failure_is_defective(self) -> None:
        result = self._result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(
                name="capacity",
                status=StepStatus.FAIL,
                summary="データ破損",
                details={"defect": True},
            ),
            StepResult(name="speed", status=StepStatus.FAIL, summary="速度不足"),
        )

        verdict, reasons = classify(result)

        assert verdict is Verdict.DEFECTIVE
        assert reasons == ["capacity: データ破損", "speed: 速度不足"]

    def test_defect_false_is_spec_mismatch(self) -> None:
        result = self._result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(
                name="capacity",
                status=StepStatus.FAIL,
                summary="容量不足",
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
            StepResult(name="malware", status=StepStatus.ERROR, summary="スキャン失敗"),
        )

        assert classify(result)[0] is Verdict.INCONCLUSIVE

    def test_not_implemented_is_inconclusive(self) -> None:
        """未実装ステップが残る限り No Defect とは判定しない。"""
        result = self._result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(name="capacity", status=StepStatus.NOT_IMPLEMENTED, summary="未実装"),
        )

        verdict, reasons = classify(result)

        assert verdict is Verdict.INCONCLUSIVE
        assert "capacity: 未実装" in reasons


def _result(*steps: StepResult, advertised_check: dict[str, Any] | None = None) -> InspectionResult:
    device = DeviceInfo(path="/dev/sda", kernel_name="sda", advertised_bytes=1)
    result = InspectionResult(
        device=device, started_at="2024-01-01T00:00:00+00:00", steps=list(steps)
    )
    if advertised_check is not None:
        result.advertised_check = advertised_check
    return result


def _counterfeit(*reasons: str, summary: str = "偽装疑い: x") -> StepResult:
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
        assert reasons[0] == "capacity: 偽装疑い: x"

    def test_malware_beats_counterfeit(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.FAIL, details={"detections": []}),
            _counterfeit("probe_bad_news"),
            advertised_check={"exceeded": True, "summary": "申告容量…"},
        )

        assert classify(result)[0] is Verdict.MALWARE_DETECTED

    def test_advertised_check_alone_is_counterfeit(self) -> None:
        """容量ステップがSKIPPEDでも非破壊チェックの超過だけで偽装疑いになる。"""
        result = _result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(name="capacity", status=StepStatus.SKIPPED, summary="allow_destructive"),
            advertised_check={"exceeded": True, "summary": "申告容量32.00GB > 上限31.03GB"},
        )

        verdict, reasons = classify(result)

        assert verdict is Verdict.COUNTERFEIT_SUSPECTED
        assert reasons[0] == "申告容量32.00GB > 上限31.03GB"
        assert len(reasons) == 1

    def test_advertised_check_not_exceeded_keeps_current_verdict(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(name="capacity", status=StepStatus.PASS),
            advertised_check={"exceeded": False, "summary": ""},
        )

        assert classify(result)[0] is Verdict.NO_DEFECT
