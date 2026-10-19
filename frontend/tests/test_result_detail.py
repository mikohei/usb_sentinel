"""result.jsonのsteps[*].detailsからResultDetailを組むparse_detail()のテスト。"""

import pytest

from tests.conftest import phase2_result, sample_result
from usb_sentinel_display.libs.models import ResultDetail
from usb_sentinel_display.libs.result_detail import parse_detail


def set_step(data: dict[str, object], name: str, **changes: object) -> None:
    """stepsの該当ステップのキーを書き換える（status／detailsの差し替え用）。"""
    steps = data["steps"]
    assert isinstance(steps, list)
    for step in steps:
        if isinstance(step, dict) and step.get("name") == name:
            step.update(changes)
            return
    raise AssertionError(name)


class TestParseDetail:
    def test_full_phase2(self) -> None:
        """第2期形式のresult.jsonから全フィールドが入る（バイト単位のまま）。"""
        detail = parse_detail(phase2_result())
        assert detail == ResultDetail(
            elapsed_s=312.5,
            clamav_db_version="27650",
            clamav_db_date="2025-06-01T08:00:00+00:00",
            nominal_bytes=32_000_000_000,
            advertised_bytes=32_000_000_000,
            actual_bytes=29_500_000_000,
            advertised_over=False,
            capacity_kind="short",
            lost_ratio=0.0123,
            counterfeit_reasons=(),
            link_speed_mbps=5000,
            device_class="usb3_flash",
            read_mb_s=45.67,
            write_mb_s=12.34,
            threshold_read_mb_s=50.0,
            threshold_write_mb_s=15.0,
            speed_basis_read="class",
            speed_basis_write="class",
            speed_severity="minor",
            hash_digest="abcdef0123456789" * 4,
            hash_lines=1234,
            reason_codes=("c:short", "s:minor"),
        )

    def test_phase1_all_none(self) -> None:
        """第1期形式（新キーなし）は全None、reason_codesは空タプル。"""
        detail = parse_detail(sample_result())
        assert detail == ResultDetail()
        assert detail.reason_codes == ()
        assert detail.advertised_over is None

    def test_capacity_skipped_ignores_kind(self) -> None:
        """容量ステップがskippedならdetailsにkindがあってもcapacity_kindはNone。"""
        data = phase2_result()
        set_step(data, "capacity", status="skipped")
        detail = parse_detail(data)
        assert detail.capacity_kind is None
        assert detail.lost_ratio is None
        assert detail.counterfeit_reasons is None
        # actual_bytesはstatusを問わず読む。
        assert detail.actual_bytes == 29_500_000_000

    def test_capacity_error_reads_actual_bytes(self) -> None:
        """errorでもactual_bytesだけは拾う（無ければNone）。"""
        data = phase2_result()
        set_step(data, "capacity", status="error", details={"actual_bytes": 1_000})
        detail = parse_detail(data)
        assert detail.actual_bytes == 1_000
        assert detail.capacity_kind is None

    def test_nominal_null_makes_advertised_over_none(self) -> None:
        """advertised_check.nominal_bytesがnullならexceededがFalseでもadvertised_overはNone。"""
        data = phase2_result()
        data["advertised_check"] = {"nominal_bytes": None, "exceeded": False}
        detail = parse_detail(data)
        assert detail.nominal_bytes is None
        assert detail.advertised_over is None

    def test_exceeded_true(self) -> None:
        data = phase2_result()
        data["advertised_check"] = {"nominal_bytes": 16_000_000_000, "exceeded": True}
        assert parse_detail(data).advertised_over is True

    def test_counterfeit_reasons_tuple(self) -> None:
        """counterfeit_reasonsはstr要素だけのタプルになる。"""
        data = phase2_result()
        set_step(
            data,
            "capacity",
            details={
                "kind": "counterfeit",
                "counterfeit_reasons": ["over_actual", 1, "lost_ratio"],
            },
        )
        detail = parse_detail(data)
        assert detail.capacity_kind == "counterfeit"
        assert detail.counterfeit_reasons == ("over_actual", "lost_ratio")

    def test_wrong_types_become_none(self) -> None:
        """型違いの値はNone（elapsed_sが文字列、linesが文字列、boolの整数）。"""
        data = phase2_result()
        data["elapsed_s"] = "312"
        data["reason_codes"] = ["c:short", 3, None]
        set_step(data, "hash_inventory", details={"digest": 123, "lines": "1234"})
        set_step(data, "speed", details={"read": "fast", "write": {"mb_s": "12"}, "severity": 1})
        device = data["device"]
        assert isinstance(device, dict)
        device["link_speed_mbps"] = True
        data["advertised_check"] = {"nominal_bytes": True, "exceeded": True}
        detail = parse_detail(data)
        assert detail.elapsed_s is None
        assert detail.reason_codes == ("c:short",)
        assert detail.hash_digest is None
        assert detail.hash_lines is None
        assert detail.read_mb_s is None
        assert detail.write_mb_s is None
        assert detail.speed_severity is None
        assert detail.link_speed_mbps is None
        assert detail.nominal_bytes is None
        assert detail.advertised_over is None

    def test_elapsed_int_and_db_version_int(self) -> None:
        """elapsed_sの整数はfloatに、db_versionの整数は文字列に直す。"""
        data = phase2_result()
        data["elapsed_s"] = 300
        set_step(data, "malware", details={"database": {"db_version": 27650, "db_date": 20250601}})
        detail = parse_detail(data)
        assert detail.elapsed_s == 300.0
        assert isinstance(detail.elapsed_s, float)
        assert detail.clamav_db_version == "27650"
        assert detail.clamav_db_date is None

    @pytest.mark.parametrize("broken", [None, "text", 1, []])
    def test_non_dict_containers(self, broken: object) -> None:
        """device／advertised_check／details／databaseが辞書でなければその部分はNone。"""
        data = phase2_result()
        data["advertised_check"] = broken
        set_step(data, "malware", details={"database": broken})
        set_step(data, "speed", details=broken)
        detail = parse_detail(data)
        assert detail.nominal_bytes is None
        assert detail.clamav_db_version is None
        assert detail.device_class is None
        # 他のステップは影響を受けない。
        assert detail.hash_lines == 1234

    def test_steps_not_list(self) -> None:
        data = phase2_result()
        data["steps"] = "junk"
        assert parse_detail(data).capacity_kind is None
