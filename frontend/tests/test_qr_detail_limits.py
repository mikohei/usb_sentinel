"""Tests for detail-QR limits (150 bytes, shortening s, page numbers) and joining 3 pages."""

import random
from dataclasses import replace

import pytest

from tests.test_qr_detail import ALL_KEYS, TYPICAL_DETAIL, WORST_DETAIL, merge_detail_pages
from tests.test_qr_payload import check, summary
from usb_sentinel_display.libs.qr_payload import build_detail_page, build_detail_pages


class TestDetailLimits:
    def test_page1_shortens_serial_only_when_oversize(self) -> None:
        """Page 1 cuts s to 16 chars only above 150 bytes. Worst-case values are not cut."""
        base = summary(serial="A" * 32, malware_count=9999, detail=WORST_DETAIL)
        assert check(build_detail_page(base, 1))["s"] == "A" * 32
        huge = summary(serial="A" * 32, malware_count=10**20, detail=WORST_DETAIL)
        data = check(build_detail_page(huge, 1))
        assert data["s"] == "A" * 16

    @pytest.mark.parametrize(("mode", "md"), [(None, None), ("scan_only", "s"), ("full", "f")])
    def test_page1_worst_case_with_md(self, mode: str | None, md: str | None) -> None:
        """Page 1 worst case stays <= 150 bytes with md, both with a 32-char serial and kernel."""
        with_serial = summary(serial="A" * 32, malware_count=9999, detail=WORST_DETAIL)
        with_serial = replace(with_serial, mode=mode)
        data = check(build_detail_page(with_serial, 1))
        assert data["s"] == "A" * 32
        assert data["md"] == md
        assert list(data)[-2:] == ["dv", "md"]
        with_kernel = summary(serial="", kernel="sdabc", malware_count=9999, detail=WORST_DETAIL)
        data = check(build_detail_page(replace(with_kernel, mode=mode), 1))
        assert data["k"] == 1
        assert data["md"] == md

    def test_page1_md_mapping(self) -> None:
        """md is "s" for scan_only, "f" for full and null when unknown."""
        for mode, md in ((None, None), ("scan_only", "s"), ("full", "f")):
            data = check(build_detail_page(replace(summary(), mode=mode), 1))
            assert data["md"] == md

    def test_page1_oversize_raises(self) -> None:
        """Page 1 that still exceeds 150 bytes after truncation raises ValueError."""
        with pytest.raises(ValueError, match="payload"):
            build_detail_page(summary(malware_count=10**120), 1)

    def test_other_pages_unaffected_by_page1_failure(self) -> None:
        """Pages 2 and 3 can be built even if page 1 cannot."""
        s = summary(malware_count=10**120, detail=TYPICAL_DETAIL)
        check(build_detail_page(s, 2))
        check(build_detail_page(s, 3))

    @pytest.mark.parametrize("page", [0, 4, -1])
    def test_invalid_page_raises(self, page: int) -> None:
        with pytest.raises(ValueError, match="page"):
            build_detail_page(summary(), page)

    def test_merge_in_shuffled_order(self) -> None:
        """Pages join in shuffled order and all keys are present."""
        pages = build_detail_pages(summary(detail=TYPICAL_DETAIL))
        random.Random(0).shuffle(pages)  # noqa: S311  # fixed seed to reproduce the read order
        merged = merge_detail_pages(pages)
        assert set(merged) == ALL_KEYS
        assert merged["cx"] == 29500 and merged["hd"] == "abcdef0123456789"
        with_k = build_detail_pages(summary(serial="", detail=TYPICAL_DETAIL))
        assert set(merge_detail_pages(with_k)) == ALL_KEYS | {"k"}

    def test_merge_rejects_other_inspection(self) -> None:
        """Pages with a different d belong to another inspection and are not joined."""
        pages = build_detail_pages(summary(detail=TYPICAL_DETAIL))
        other = build_detail_pages(summary(detail=TYPICAL_DETAIL))[2].replace(
            "0123456789ab", "ba9876543210"
        )
        with pytest.raises(ValueError, match="digest"):
            merge_detail_pages([pages[0], pages[1], other])
        with pytest.raises(ValueError, match="incomplete"):
            merge_detail_pages(pages[:2])
