"""Reason codes (reason_codes_for). Helpers are borrowed from test_classify.py."""

from __future__ import annotations

from tests.test_classify import _counterfeit, _result
from usb_sentinel.libs import StepResult, StepStatus, Verdict
from usb_sentinel.libs.orchestrator import classify, reason_codes_for


class TestReasonCodes:
    def test_malware_found(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.FAIL, details={"detections": []}),
            StepResult(name="hash_inventory", status=StepStatus.PASS),
            StepResult(name="capacity", status=StepStatus.SKIPPED),
            StepResult(name="speed", status=StepStatus.SKIPPED),
        )

        assert reason_codes_for(result) == ["m:found", "c:skipped", "s:skipped"]

    def test_over_fires_once_from_both_sources(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.PASS),
            _counterfeit("over_advertised", "over_actual"),
            advertised_check={"exceeded": True, "summary": "x"},
        )

        assert reason_codes_for(result) == ["c:over"]

    def test_over_from_step_only(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.PASS),
            _counterfeit("over_actual"),
        )

        assert reason_codes_for(result) == ["c:over"]

    def test_over_from_check_only_precedes_step_codes(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(name="capacity", status=StepStatus.SKIPPED),
            StepResult(name="speed", status=StepStatus.SKIPPED),
            advertised_check={"exceeded": True, "summary": "x"},
        )

        assert reason_codes_for(result) == ["c:over", "c:skipped", "s:skipped"]

    def test_lost_and_probe(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.PASS),
            _counterfeit("probe_bad_news", "lost_ratio"),
        )

        assert reason_codes_for(result) == ["c:probe", "c:lost"]

    def test_short(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(name="capacity", status=StepStatus.FAIL, details={"kind": "short"}),
        )

        assert reason_codes_for(result) == ["c:short"]

    def test_defect(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(
                name="capacity", status=StepStatus.FAIL, details={"kind": "defect", "defect": True}
            ),
        )

        assert reason_codes_for(result) == ["c:defect"]

    def test_slow_is_spec_mismatch(self) -> None:
        # A capacity step stopped for a slow or stalled drive (neither defect nor counterfeit).
        result = _result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(
                name="capacity",
                status=StepStatus.FAIL,
                summary="Write stalled: files 1-8 not finished within 8590s (below 1MB/s)",
                details={"kind": "slow", "defect": False, "counterfeit": False},
            ),
        )

        assert reason_codes_for(result) == ["c:slow"]
        assert classify(result)[0] is Verdict.SPEC_MISMATCH

    def test_speed_severity(self) -> None:
        severe = _result(
            StepResult(name="speed", status=StepStatus.FAIL, details={"severity": "severe"}),
        )
        minor = _result(
            StepResult(name="speed", status=StepStatus.FAIL, details={"severity": "minor"}),
        )
        unknown = _result(StepResult(name="speed", status=StepStatus.FAIL))

        assert reason_codes_for(severe) == ["s:severe"]
        assert reason_codes_for(minor) == ["s:minor"]
        # severity is added in FEAT-003. Without it, no code is emitted.
        assert reason_codes_for(unknown) == []

    def test_timeout_takes_precedence_over_error(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.ERROR, details={"timed_out": True}),
            StepResult(name="hash_inventory", status=StepStatus.PASS),
        )

        assert reason_codes_for(result) == ["m:timeout"]

    def test_error_and_skipped(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(name="hash_inventory", status=StepStatus.ERROR),
            StepResult(name="capacity", status=StepStatus.ERROR, details={"timed_out": False}),
            StepResult(name="speed", status=StepStatus.SKIPPED),
            StepResult(name="unmount", status=StepStatus.ERROR),
        )

        assert reason_codes_for(result) == ["h:error", "c:error", "s:skipped", "u:error"]

    def test_all_pass_has_no_codes(self) -> None:
        result = _result(
            StepResult(name="malware", status=StepStatus.PASS),
            StepResult(name="capacity", status=StepStatus.PASS),
        )

        assert reason_codes_for(result) == []
