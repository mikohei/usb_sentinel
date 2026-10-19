"""速度ステップの参照値（公称＞種別＞なし）、リンク上限の警告、重大度の境界。

材料（fio_runner、_step、_with_speed、_with_link）はtest_speed.pyから借りる。
"""

from __future__ import annotations

import pytest

from tests import test_speed
from tests.conftest import fio_json
from tests.test_speed import READ_ARGV, WRITE_ARGV, _step, _with_link, _with_speed
from usb_sentinel.libs import FakeRunner, StepContext, StepStatus

fio_runner = test_speed.fio_runner


class TestReference:
    """参照値の決定（公称 > 種別 > なし）と種別の根拠。"""

    def test_nominal_takes_priority_over_class(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        result = _step().run(
            _with_speed(
                ctx, nominal_read_mb_s=90.0, nominal_write_mb_s=20.0, device_class="usb3_flash"
            )
        )

        assert result.status is StepStatus.PASS
        assert result.details["basis_read"] == "nominal"
        assert result.details["basis_write"] == "nominal"
        assert result.details["reference_read_mb_s"] == 90.0
        assert result.details["threshold_read_mb_s"] == 72.0
        assert result.details["device_class"] == "usb3_flash"
        assert result.details["device_class_basis"] == "config"
        assert result.summary == "読み取り85.3MB/s、書き込み20.1MB/s（閾値72.0 / 16.0MB/s）"

    def test_configured_class_sets_thresholds(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        result = _step().run(_with_speed(ctx, device_class="usb3_flash"))

        assert result.status is StepStatus.PASS
        assert result.details["threshold_read_mb_s"] == 40.0
        assert result.details["threshold_write_mb_s"] == 12.0
        assert result.details["basis_read"] == "class"
        assert result.details["reference_read_mb_s"] == 50.0
        assert result.details["reference_write_mb_s"] == 15.0
        assert result.warnings == []
        assert result.summary == "読み取り85.3MB/s、書き込み20.1MB/s（閾値40.0 / 12.0MB/s）"

    def test_configured_class_fail_names_floor(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        fio_runner.register(WRITE_ARGV, stdout=fio_json("write", bw_bytes=3_000_000))

        result = _step().run(_with_speed(ctx, device_class="usb3_flash"))

        assert result.status is StepStatus.FAIL
        assert result.summary == (
            "速度不足（著しく下回る）: 書き込み3.0MB/s < 閾値12.0MB/s"
            "（USB3.0フラッシュ既定15.0×0.80）"
        )
        assert result.warnings == []

    def test_link_speed_5000_infers_usb3_flash(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        fio_runner.register(WRITE_ARGV, stdout=fio_json("write", bw_bytes=3_000_000))

        result = _step().run(_with_link(ctx, 5000))

        assert result.status is StepStatus.FAIL
        assert result.details["device_class"] == "usb3_flash"
        assert result.details["device_class_basis"] == "link_speed"
        assert result.summary == (
            "速度不足（著しく下回る）: 書き込み3.0MB/s < 閾値12.0MB/s"
            "（USB3.0フラッシュ既定15.0×0.80・種別推定）"
        )
        assert any("種別未設定" in w for w in result.warnings)
        assert len(result.warnings) == 1

    def test_link_speed_480_infers_usb2_flash(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        result = _step().run(_with_link(ctx, 480))

        assert result.status is StepStatus.PASS
        assert result.details["device_class"] == "usb2_flash"
        assert result.details["device_class_basis"] == "link_speed"
        assert result.details["threshold_read_mb_s"] == 12.0
        assert result.details["threshold_write_mb_s"] == 4.0
        assert any("種別未設定" in w for w in result.warnings)

    def test_usb11_link_is_skipped_with_warning(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        result = _step().run(_with_link(ctx, 12))

        assert result.status is StepStatus.SKIPPED
        assert result.summary == "公称速度が未設定（読み取り85.3MB/s、書き込み20.1MB/s）"
        assert result.details["device_class"] is None
        assert result.details["device_class_basis"] == "unknown"
        assert result.details["link_speed_mbps"] == 12
        assert len(result.warnings) == 1
        assert "判定基準なし" in result.warnings[0]
        assert "12Mbps" in result.warnings[0]

    def test_usb11_link_with_nominal_has_no_basis_warning(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        """リンク12Mbpsでも公称値が設定済みなら判定でき、「判定基準なし」の警告は出さない。"""
        result = _step().run(_with_link(_with_speed(ctx, nominal_read_mb_s=90.0), 12))

        assert result.status is StepStatus.PASS
        assert result.details["basis_read"] == "nominal"
        assert result.details["link_speed_mbps"] == 12
        assert not any("判定基準なし" in w for w in result.warnings)

    def test_nonstandard_link_speed_is_skipped(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        result = _step().run(_with_link(ctx, 4999))

        assert result.status is StepStatus.SKIPPED
        assert result.details["device_class_basis"] == "unknown"
        assert result.warnings == []


class TestLinkCeiling:
    """基準値がリンク速度の実効上限を超えるときの警告。判定自体は変えない。"""

    def test_nvme_on_gen1_warns(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        without_link = _step().run(_with_speed(ctx, device_class="nvme_ssd"))
        with_link = _step().run(_with_link(_with_speed(ctx, device_class="nvme_ssd"), 5000))

        assert with_link.status is StepStatus.FAIL
        assert with_link.status is without_link.status
        assert with_link.summary == without_link.summary
        assert without_link.warnings == []
        assert len(with_link.warnings) == 1
        warning = with_link.warnings[0]
        assert "実効上限" in warning
        assert "500.0MB/s" in warning and "5000Mbps" in warning

    def test_usb3_flash_on_gen1_does_not_warn(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        result = _step().run(_with_link(_with_speed(ctx, device_class="usb3_flash"), 5000))

        assert result.warnings == []

    def test_nvme_on_gen2_does_not_warn(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        result = _step().run(_with_link(_with_speed(ctx, device_class="nvme_ssd"), 10000))

        assert result.status is StepStatus.FAIL
        assert result.warnings == []

    def test_nominal_reference_does_not_warn(
        self, ctx: StepContext, fio_runner: FakeRunner
    ) -> None:
        """公称値が基準のときは実効上限を超えていてもリンク速度と比べない。"""
        result = _step().run(
            _with_link(
                _with_speed(
                    ctx,
                    nominal_read_mb_s=600.0,
                    nominal_write_mb_s=600.0,
                    device_class="nvme_ssd",
                ),
                5000,
            )
        )

        assert result.status is StepStatus.FAIL
        assert result.details["basis_read"] == "nominal"
        assert result.warnings == []


class TestSeverity:
    @pytest.mark.parametrize(
        ("bw_bytes", "status", "severity"),
        [
            (49_900_000, StepStatus.FAIL, "severe"),
            (50_000_000, StepStatus.FAIL, "minor"),
            (79_900_000, StepStatus.FAIL, "minor"),
            (80_000_000, StepStatus.PASS, None),
        ],
    )
    def test_boundaries(
        self,
        ctx: StepContext,
        fio_runner: FakeRunner,
        bw_bytes: int,
        status: StepStatus,
        severity: str | None,
    ) -> None:
        """公称100.0に対し50%未満がsevere、50%以上80%未満がminor、80%以上はPASS。"""
        fio_runner.register(READ_ARGV, stdout=fio_json("read", bw_bytes=bw_bytes))

        result = _step().run(_with_speed(ctx, nominal_read_mb_s=100.0))

        assert result.status is status
        assert result.details["severity"] == severity

    def test_severe_wins_over_minor(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        fio_runner.register(READ_ARGV, stdout=fio_json("read", bw_bytes=40_000_000))
        fio_runner.register(WRITE_ARGV, stdout=fio_json("write", bw_bytes=15_000_000))

        result = _step().run(_with_speed(ctx, nominal_read_mb_s=100.0, nominal_write_mb_s=20.0))

        assert result.status is StepStatus.FAIL
        assert result.details["severity"] == "severe"
        assert result.details["ratio_read"] == 0.4
        assert result.details["ratio_write"] == 0.75
        assert result.summary == (
            "速度不足（著しく下回る）: 読み取り40.0MB/s < 閾値80.0MB/s（公称100.0×0.80）、"
            "書き込み15.0MB/s < 閾値16.0MB/s（公称20.0×0.80）"
        )

    def test_skipped_has_no_severity(self, ctx: StepContext, fio_runner: FakeRunner) -> None:
        result = _step().run(ctx)

        assert result.status is StepStatus.SKIPPED
        assert result.details["severity"] is None
