"""詳細QRの上限処理（150バイト、sの短縮、ページ番号）と3枚の結合のテスト。"""

import random

import pytest

from tests.test_qr_detail import ALL_KEYS, TYPICAL_DETAIL, WORST_DETAIL, merge_detail_pages
from tests.test_qr_payload import check, summary
from usb_sentinel_display.libs.qr_payload import build_detail_page, build_detail_pages


class TestDetailLimits:
    def test_page1_shortens_serial_only_when_oversize(self) -> None:
        """1枚目は150バイト超のときだけsを16文字に切る。最悪値では切られない。"""
        base = summary(serial="A" * 32, malware_count=9999, detail=WORST_DETAIL)
        assert check(build_detail_page(base, 1))["s"] == "A" * 32
        huge = summary(serial="A" * 32, malware_count=10**20, detail=WORST_DETAIL)
        data = check(build_detail_page(huge, 1))
        assert data["s"] == "A" * 16

    def test_page1_oversize_raises(self) -> None:
        """切っても150バイトを超える1枚目はValueError。"""
        with pytest.raises(ValueError, match="payload"):
            build_detail_page(summary(malware_count=10**120), 1)

    def test_other_pages_unaffected_by_page1_failure(self) -> None:
        """1枚目が生成できなくても2枚目・3枚目は作れる。"""
        s = summary(malware_count=10**120, detail=TYPICAL_DETAIL)
        check(build_detail_page(s, 2))
        check(build_detail_page(s, 3))

    @pytest.mark.parametrize("page", [0, 4, -1])
    def test_invalid_page_raises(self, page: int) -> None:
        with pytest.raises(ValueError, match="page"):
            build_detail_page(summary(), page)

    def test_merge_in_shuffled_order(self) -> None:
        """シャッフルした順でも結合でき、全キーが揃う。"""
        pages = build_detail_pages(summary(detail=TYPICAL_DETAIL))
        random.Random(0).shuffle(pages)  # noqa: S311  # 読み取り順の再現用の固定シード
        merged = merge_detail_pages(pages)
        assert set(merged) == ALL_KEYS
        assert merged["cx"] == 29500 and merged["hd"] == "abcdef0123456789"
        with_k = build_detail_pages(summary(serial="", detail=TYPICAL_DETAIL))
        assert set(merge_detail_pages(with_k)) == ALL_KEYS | {"k"}

    def test_merge_rejects_other_inspection(self) -> None:
        """dが違う枚は別の検査なので結合しない。"""
        pages = build_detail_pages(summary(detail=TYPICAL_DETAIL))
        other = build_detail_pages(summary(detail=TYPICAL_DETAIL))[2].replace(
            "0123456789ab", "ba9876543210"
        )
        with pytest.raises(ValueError, match="digest"):
            merge_detail_pages([pages[0], pages[1], other])
        with pytest.raises(ValueError, match="incomplete"):
            merge_detail_pages(pages[:2])
