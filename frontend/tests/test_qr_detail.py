"""Tests for building/joining detail QR (v:3, 3 pages). summary()/check() from test_qr_payload."""

import json
import re

from tests.test_qr_payload import check, summary
from usb_sentinel_display.libs.models import ResultDetail
from usb_sentinel_display.libs.qr_payload import (
    DETAIL_DIGEST_LEN,
    DETAIL_PAGES,
    build_detail_page,
    build_detail_pages,
    build_payload,
)

TYPICAL_DETAIL = ResultDetail(
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

# Worst-case values from design doc 7.3 (pages 2 and 3).
WORST_DETAIL = ResultDetail(
    elapsed_s=86399.0,
    clamav_db_version="999999",
    clamav_db_date="2025-06-01T00:00:00+00:00",
    nominal_bytes=5_000_000_000_000,
    advertised_bytes=5_000_000_000_000,
    actual_bytes=5_000_000_000_000,
    advertised_over=True,
    capacity_kind="counterfeit",
    lost_ratio=0.9999,
    counterfeit_reasons=("over_advertised", "over_actual"),
    link_speed_mbps=20000,
    device_class="nvme_ssd",
    read_mb_s=999.9,
    write_mb_s=999.9,
    threshold_read_mb_s=999.9,
    threshold_write_mb_s=999.9,
    speed_basis_read="class",
    speed_basis_write="nominal",
    speed_severity="severe",
    hash_digest="f" * 64,
    hash_lines=999999,
)

PAGE_KEYS = {
    1: ["t", "s", "r", "e", "m", "dv"],
    2: ["cn", "ca", "cx", "ck", "co", "cl", "ls", "dc", "hn"],
    3: ["sr", "sw", "tr", "tw", "sb", "sv", "db", "hd"],
}
ALL_KEYS = {key for keys in PAGE_KEYS.values() for key in keys}
PAGE_RE = re.compile(r"^([1-3])/3$")


def merge_detail_pages(pages: list[str]) -> dict[str, object]:
    """Join the 3 detail QR pages in any read order (reader steps in doc/qr-payload.md 5.6)."""
    merged: dict[str, object] = {}
    seen: set[int] = set()
    digest: str | None = None
    for text in pages:
        data = json.loads(text)
        assert isinstance(data, dict)
        if data.get("v") != 3:
            raise ValueError("not a detail page")
        match = PAGE_RE.match(str(data.get("p", "")))
        if match is None:
            raise ValueError("bad page marker")
        if digest is None:
            digest = str(data["d"])
        elif data["d"] != digest:
            raise ValueError("digest mismatch")
        seen.add(int(match.group(1)))
        merged.update({k: v for k, v in data.items() if k not in ("v", "p", "d")})
    if seen != {1, 2, 3}:
        raise ValueError("pages incomplete")
    return merged


class TestDetailPages:
    def test_constants(self) -> None:
        assert DETAIL_PAGES == 3
        assert DETAIL_DIGEST_LEN == 12

    def test_typical_three_pages(self) -> None:
        """Typical 3 pages are each <=150 bytes and <=v8, with key order per the spec table."""
        pages = build_detail_pages(summary(detail=TYPICAL_DETAIL))
        assert len(pages) == 3
        for page, text in enumerate(pages, start=1):
            data = check(text)
            assert list(data)[:3] == ["v", "p", "d"]
            assert data["v"] == 3
            assert data["p"] == f"{page}/3"
            assert data["d"] == "0123456789ab"
            assert list(data)[3:] == PAGE_KEYS[page]

    def test_typical_values(self) -> None:
        """MB conversion, rounding, and coding follow the spec."""
        pages = build_detail_pages(summary(verdict="Spec Mismatch", detail=TYPICAL_DETAIL))
        one, two, three = (check(text) for text in pages)
        assert one["t"] == "20250601T120000Z"
        assert one["s"] == "ABC123456789"
        assert one["r"] == "S"
        assert one["e"] == 312
        assert one["m"] == 0
        assert one["dv"] == 27650
        assert two == {
            "v": 3,
            "p": "2/3",
            "d": "0123456789ab",
            "cn": 32000,
            "ca": 32000,
            "cx": 29500,
            "ck": "S",
            "co": 0,
            "cl": 0.0123,
            "ls": 5000,
            "dc": "u3",
            "hn": 1234,
        }
        assert three == {
            "v": 3,
            "p": "3/3",
            "d": "0123456789ab",
            "sr": 45.7,
            "sw": 12.3,
            "tr": 50.0,
            "tw": 15.0,
            "sb": "c",
            "sv": "m",
            "db": "20250601",
            "hd": "abcdef0123456789",
        }

    def test_worst_case_page1_with_serial(self) -> None:
        """Page 1 worst case (s 32 chars, e=86399, m=9999, dv=999999) is <=150 and not truncated."""
        text = build_detail_page(
            summary(serial="A" * 32, malware_count=9999, detail=WORST_DETAIL), 1
        )
        data = check(text)
        assert data["s"] == "A" * 32
        assert "k" not in data
        assert data["e"] == 86399
        assert data["dv"] == 999999

    def test_worst_case_page1_with_kernel(self) -> None:
        """Worst case with empty serial (kernel name used) still includes k==1 and is <=150."""
        text = build_detail_page(
            summary(serial="", kernel="sdabc", malware_count=9999, detail=WORST_DETAIL), 1
        )
        data = check(text)
        assert data["k"] == 1
        assert data["s"] == "sdabc"
        assert list(data) == ["v", "p", "d", "t", "s", "k", "r", "e", "m", "dv"]

    def test_worst_case_page2(self) -> None:
        """Page 2 worst case (three 5 TB values, ck=C, co=1, cl=0.9999, ls=20000, hn=999999)."""
        data = check(build_detail_page(summary(detail=WORST_DETAIL), 2))
        assert data["cn"] == data["ca"] == data["cx"] == 5_000_000
        assert data["ck"] == "C"
        assert data["co"] == 1
        assert data["cl"] == 0.9999
        assert data["ls"] == 20000
        assert data["dc"] == "ns"
        assert data["hn"] == 999999

    def test_worst_case_page3(self) -> None:
        """Page 3 worst case (all 4 values 999.9, 8-digit db, 16-digit hd)."""
        data = check(build_detail_page(summary(detail=WORST_DETAIL), 3))
        assert data["sr"] == data["sw"] == data["tr"] == data["tw"] == 999.9
        assert data["sb"] == "c"
        assert data["sv"] == "s"
        assert data["db"] == "20250601"
        assert data["hd"] == "f" * 16

    def test_old_format_all_null(self) -> None:
        """Old format (detail all None) still builds 3 pages; values are null and keys are kept."""
        pages = build_detail_pages(summary())
        one, two, three = (check(text) for text in pages)
        assert "k" not in one
        assert one["e"] is None and one["dv"] is None
        assert one["m"] == 0
        assert two["cn"] is None and two["ca"] is None and two["cx"] is None
        assert two["ck"] == "?"
        assert two["co"] is None and two["cl"] is None
        assert two["ls"] is None and two["dc"] is None and two["hn"] is None
        assert three["sr"] is None and three["sw"] is None
        assert three["tr"] is None and three["tw"] is None
        assert three["sb"] == "-" and three["sv"] == "-"
        assert three["db"] is None and three["hd"] is None
        assert set(one) | set(two) | set(three) == ALL_KEYS | {"v", "p", "d"}

    def test_empty_serial_sets_k(self) -> None:
        data = check(build_detail_page(summary(serial=""), 1))
        assert data["s"] == "sda"
        assert data["k"] == 1

    def test_digest_matches_normal_qr(self) -> None:
        """Each page's d matches the first 12 chars of the normal QR's d."""
        s = summary(detail=TYPICAL_DETAIL)
        normal = json.loads(build_payload(s))
        for text in build_detail_pages(s):
            assert json.loads(text)["d"] == normal["d"][:12]

    def test_unknown_codes(self) -> None:
        """kind/device_class/basis/severity not in the table become ?/null/-."""
        detail = ResultDetail(
            capacity_kind="weird",
            device_class="floppy",
            speed_basis_read="guess",
            speed_severity="bad",
            clamav_db_version="27650-rc1",
            clamav_db_date="yesterday",
        )
        one = check(build_detail_page(summary(detail=detail), 1))
        two = check(build_detail_page(summary(detail=detail), 2))
        three = check(build_detail_page(summary(detail=detail), 3))
        assert one["dv"] is None
        assert two["ck"] == "?"
        assert two["dc"] is None
        assert three["sb"] == "-" and three["sv"] == "-"
        assert three["db"] is None

    def test_db_date_without_time(self) -> None:
        """A date-only db_date also becomes 8 digits."""
        data = check(
            build_detail_page(summary(detail=ResultDetail(clamav_db_date="2025-06-01")), 3)
        )
        assert data["db"] == "20250601"

    def test_capacity_kind_codes(self) -> None:
        for kind, code in (
            ("ok", "-"),
            ("short", "S"),
            ("defect", "D"),
            ("counterfeit", "C"),
            ("unverified", "U"),
        ):
            data = check(build_detail_page(summary(detail=ResultDetail(capacity_kind=kind)), 2))
            assert data["ck"] == code

    def test_mb_floor(self) -> None:
        """MB conversion truncates."""
        detail = ResultDetail(nominal_bytes=1_999_999, advertised_bytes=0, actual_bytes=10**6)
        data = check(build_detail_page(summary(detail=detail), 2))
        assert data["cn"] == 1
        assert data["ca"] == 0
        assert data["cx"] == 1
