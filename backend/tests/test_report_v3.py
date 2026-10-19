"""ファイル用QR v:3（設計書第6節）のペイロード。材料はtest_report.pyから借りる。"""

from __future__ import annotations

import json

from tests.test_report import _clean_malware_step, _result
from usb_sentinel.libs import InspectionResult, StepResult, StepStatus, Verdict
from usb_sentinel.libs.report.qr import MAX_PAYLOAD_BYTES, build_payload

V3_KEYS = {
    "v",
    "verdict",
    "serial",
    "kernel_name",
    "port_path",
    "started_at",
    "finished_at",
    "elapsed_s",
    "steps",
    "clamav_db",
    "clamav_db_date",
    "digest",
    "malware_count",
    "signatures",
    "advertised_over",
    "capacity",
    "link_speed_mbps",
    "device_class",
    "speed",
    "reason_codes",
    "reasons",
    "hash_inventory_digest",
    "hash_inventory_lines",
    "truncated",
}

HASH_DIGEST = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _malware_with_date() -> StepResult:
    step = _clean_malware_step()
    step.details["database"]["db_date"] = "2024-02-12T09:23:12"
    return step


def _hash_step() -> StepResult:
    return StepResult(
        name="hash_inventory",
        status=StepStatus.PASS,
        summary="3件",
        details={"lines": 3, "bytes_hashed": 1024, "digest": HASH_DIGEST},
    )


def _capacity_step() -> StepResult:
    return StepResult(
        name="capacity",
        status=StepStatus.PASS,
        summary="実容量31.20GB / 公称32.00GB（97.5%）、閾値29.76GB",
        details={
            "mode": "f3write",
            "nominal_bytes": 32_000_000_000,
            "advertised_bytes": 32_000_000_000,
            "actual_bytes": 31_200_000_000,
            "kind": "ok",
            "counterfeit": False,
            "counterfeit_reasons": [],
            "lost_ratio": 0.0,
            "probe_verdict_line": None,
        },
    )


def _speed_step() -> StepResult:
    return StepResult(
        name="speed",
        status=StepStatus.PASS,
        summary="読み取り85.3MB/s、書き込み20.1MB/s（閾値72.0 / 16.0MB/s）",
        details={
            "device_class": "usb3_flash",
            "device_class_basis": "config",
            "link_speed_mbps": 5000,
            "read": {"mb_s": 85.3},
            "write": {"mb_s": 20.1},
            "threshold_read_mb_s": 72.0,
            "threshold_write_mb_s": 16.0,
            "basis_read": "nominal",
            "basis_write": "nominal",
            "severity": None,
        },
    )


def _unmount_step() -> StepResult:
    return StepResult(name="unmount", status=StepStatus.PASS, summary="アンマウント済み")


def _full_result(*, serial: str = "ABC123456789") -> InspectionResult:
    """5ステップ・理由1件の典型ケース（設計書第6.2節）。"""
    result = _result(
        _malware_with_date(), _hash_step(), _capacity_step(), _speed_step(), _unmount_step()
    )
    result.device.serial = serial
    result.device.port_path = "1-1.3"
    result.device.link_speed_mbps = 5000
    result.elapsed_s = 1800.25
    result.reason_codes = []
    result.warnings = []
    result.advertised_check = {
        "nominal_bytes": 32_000_000_000,
        "advertised_bytes": 32_000_000_000,
        "over_ratio": 1.07,
        "over_threshold_bytes": 34_240_000_000,
        "advertised_ratio": 1.0,
        "exceeded": False,
        "summary": "",
    }
    return result


def _error_capacity_step() -> StepResult:
    """ERROR経路の容量ステップ。run()冒頭のキーしか持たない（設計書第6.1節）。"""
    return StepResult(
        name="capacity",
        status=StepStatus.ERROR,
        summary="f3writeが見つからない",
        details={
            "mode": "f3write",
            "nominal_bytes": None,
            "advertised_bytes": 32_000_000_000,
            "base_bytes": 32_000_000_000,
            "base": "advertised",
            "over_ratio": 1.07,
            "over_threshold_bytes": None,
        },
    )


def _skipped_speed_step() -> StepResult:
    return StepResult(name="speed", status=StepStatus.SKIPPED, summary="破壊的検査は無効")


class TestQrPayloadV3:
    def test_all_v3_keys_present(self) -> None:
        """設計書第6.1節の表の全キーが出る。"""
        payload = json.loads(build_payload(_full_result()))

        assert set(payload) == V3_KEYS
        assert payload["v"] == 3

    def test_device_and_timing_fields(self) -> None:
        result = _full_result()
        payload = json.loads(build_payload(result))

        assert payload["kernel_name"] == "sda"
        assert payload["port_path"] == "1-1.3"
        assert payload["started_at"] == result.started_at
        assert payload["finished_at"] == result.finished_at
        assert payload["elapsed_s"] == 1800.25
        assert payload["link_speed_mbps"] == 5000
        assert payload["reason_codes"] == []
        assert payload["reasons"] == ["全ステップ Pass"]

    def test_clamav_db_date_is_first_ten_chars(self) -> None:
        """db_dateのISO文字列からYYYY-MM-DDだけを切り出す。"""
        payload = json.loads(build_payload(_full_result()))

        assert payload["clamav_db_date"] == "2024-02-12"

    def test_clamav_db_date_missing_is_none(self) -> None:
        payload = json.loads(build_payload(_result(_clean_malware_step())))

        assert payload["clamav_db_date"] is None

    def test_advertised_over_true(self) -> None:
        result = _full_result()
        result.advertised_check = {"exceeded": True}

        payload = json.loads(build_payload(result))

        assert payload["advertised_over"] is True

    def test_advertised_over_defaults_to_false(self) -> None:
        """advertised_checkが既定の{}でもFalseになる（nominal_bytes未設定の運用）。"""
        payload = json.loads(build_payload(_result(_clean_malware_step())))

        assert payload["advertised_over"] is False

    def test_typical_case_is_within_limit_and_not_truncated(self) -> None:
        text = build_payload(_full_result())
        payload = json.loads(text)

        assert len(text.encode("utf-8")) <= MAX_PAYLOAD_BYTES
        assert payload["truncated"] is False

    def test_capacity_dict_from_details(self) -> None:
        payload = json.loads(build_payload(_full_result()))

        assert payload["capacity"] == {
            "nominal_bytes": 32_000_000_000,
            "advertised_bytes": 32_000_000_000,
            "actual_bytes": 31_200_000_000,
            "kind": "ok",
            "lost_ratio": 0.0,
            "counterfeit_reasons": [],
            "probe_verdict_line": None,
        }

    def test_error_capacity_step_gives_null_without_keyerror(self) -> None:
        """ERROR状態の容量ステップは新キーを持たない。KeyErrorを出さずnullにする。"""
        result = _result(_clean_malware_step(), _error_capacity_step(), _skipped_speed_step())

        payload = json.loads(build_payload(result))

        assert payload["capacity"] is None
        assert payload["speed"] is None
        assert payload["device_class"] is None
        assert payload["steps"] == {"malware": "pass", "capacity": "error", "speed": "skipped"}

    def test_fail_capacity_without_new_keys_uses_null_values(self) -> None:
        """FAILでもdetailsに新キーが無ければ値はnullで、辞書自体は組む。"""
        capacity = StepResult(
            name="capacity",
            status=StepStatus.FAIL,
            details={"advertised_bytes": 32_000_000_000, "actual_bytes": 8_000_000_000},
        )

        payload = json.loads(build_payload(_result(_clean_malware_step(), capacity)))

        assert payload["capacity"]["actual_bytes"] == 8_000_000_000
        assert payload["capacity"]["kind"] is None
        assert payload["capacity"]["counterfeit_reasons"] is None

    def test_speed_dict_from_details(self) -> None:
        payload = json.loads(build_payload(_full_result()))

        assert payload["device_class"] == "usb3_flash"
        assert payload["speed"] == {
            "read_mb_s": 85.3,
            "write_mb_s": 20.1,
            "threshold_read_mb_s": 72.0,
            "threshold_write_mb_s": 16.0,
            "basis_read": "nominal",
            "basis_write": "nominal",
            "severity": None,
        }

    def test_speed_error_without_read_is_null(self) -> None:
        """fioがERRORで終わりreadが無いときはspeedをnullにする（device_classは残る）。"""
        speed = StepResult(
            name="speed",
            status=StepStatus.ERROR,
            summary="fioが見つからない",
            details={"device_class": "usb2_flash", "device_class_basis": "link_speed"},
        )

        payload = json.loads(build_payload(_result(_clean_malware_step(), speed)))

        assert payload["speed"] is None
        assert payload["device_class"] == "usb2_flash"

    def test_hash_inventory_digest_and_lines(self) -> None:
        payload = json.loads(build_payload(_full_result()))

        assert payload["hash_inventory_digest"] == HASH_DIGEST
        assert payload["hash_inventory_lines"] == 3

    def test_hash_inventory_missing_is_none(self) -> None:
        """台帳がSKIPPEDや台帳ファイルを開けなかったERRORではdetailsにdigestが無い。"""
        skipped = StepResult(name="hash_inventory", status=StepStatus.SKIPPED, summary="無効")

        payload = json.loads(build_payload(_result(_clean_malware_step(), skipped)))

        assert payload["hash_inventory_digest"] is None
        assert payload["hash_inventory_lines"] is None

    def test_counterfeit_suspected_verdict(self) -> None:
        result = _full_result()
        result.verdict = Verdict.COUNTERFEIT_SUSPECTED
        result.reason_codes = ["c:over"]

        payload = json.loads(build_payload(result))

        assert payload["verdict"] == "Counterfeit Suspected"
        assert payload["reason_codes"] == ["c:over"]
