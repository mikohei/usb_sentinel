"""Tests for result_file.drive_tag(): what the inspection did to the drive (design doc 3.9)."""

from pathlib import Path

import pytest

from tests.conftest import phase2_result, sample_result
from tests.test_results import NAME, TestParseSummary
from usb_sentinel_display.libs.models import ResultSummary
from usb_sentinel_display.libs.results import drive_tag


def tagged(mode: str | None, steps: tuple[tuple[str, str], ...]) -> ResultSummary:
    """Summary with only the fields drive_tag() reads."""
    return ResultSummary(
        dir_name=NAME,
        path=Path("/x"),
        mtime=0.0,
        verdict="No Defect",
        serial="S",
        vendor="",
        model="",
        kernel_name="sda",
        port_path=None,
        started_at="",
        finished_at="",
        steps=steps,
        malware_count=0,
        digest="0" * 64,
        mode=mode,
    )


class TestDriveTag:
    """Rows of design doc 3.9; step statuses are checked before the mode."""

    SKIPPED = (("malware", "pass"), ("capacity", "skipped"), ("speed", "skipped"))

    @pytest.mark.parametrize("mode", ["scan_only", "full", None])
    @pytest.mark.parametrize(
        "steps",
        [
            (("capacity", "pass"), ("speed", "pass")),
            (("capacity", "fail"), ("speed", "skipped")),
            (("capacity", "error"),),
            (("capacity", "skipped"), ("speed", "pass")),
            (("capacity", "skipped"), ("speed", "fail")),
            (("speed", "weird"),),
            (("capacity", ""),),
        ],
    )
    def test_destructive_step_ran_is_erased(
        self, mode: str | None, steps: tuple[tuple[str, str], ...]
    ) -> None:
        assert drive_tag(tagged(mode, steps)) == "erased"

    @pytest.mark.parametrize("mode", ["scan_only", "full"])
    @pytest.mark.parametrize(
        "steps",
        [
            (("capacity", "skipped"), ("speed", "skipped")),
            (("capacity", "not_implemented"), ("speed", "skipped")),
            (("malware", "pass"), ("hash_inventory", "pass")),
            (),
            (("malware", "fail"), ("unmount", "error")),
        ],
    )
    def test_no_destructive_step_with_mode_is_scan(
        self, mode: str, steps: tuple[tuple[str, str], ...]
    ) -> None:
        assert drive_tag(tagged(mode, steps)) == "scan"

    @pytest.mark.parametrize(
        "steps",
        [(("capacity", "skipped"), ("speed", "skipped")), (), (("malware", "pass"),)],
    )
    def test_no_destructive_step_without_mode_is_none(
        self, steps: tuple[tuple[str, str], ...]
    ) -> None:
        assert drive_tag(tagged(None, steps)) is None

    def test_parsed_results(self) -> None:
        """End to end: sample_result (skipped steps) and phase2_result (capacity fail)."""
        parse = TestParseSummary().parse_ok
        assert drive_tag(parse(sample_result())) is None
        data = sample_result()
        data["mode"] = "full"
        assert drive_tag(parse(data)) == "scan"
        assert drive_tag(parse(phase2_result())) == "erased"
