import json
from pathlib import Path

import pytest

from usb_sentinel_display.libs.models import ResultDetail, ResultSummary
from usb_sentinel_display.libs.qr_payload import (
    MAX_PAYLOAD_BYTES,
    build_payload,
)
from usb_sentinel_display.libs.qr_render import MAX_VERSION, qr_version

FIVE_STEPS = (
    ("malware", "pass"),
    ("hash_inventory", "pass"),
    ("capacity", "pass"),
    ("speed", "pass"),
    ("unmount", "pass"),
)


def summary(
    *,
    serial: str = "ABC123456789",
    kernel: str = "sda",
    verdict: str = "Inconclusive",
    steps: tuple[tuple[str, str], ...] = (
        ("malware", "pass"),
        ("hash_inventory", "pass"),
        ("capacity", "skipped"),
        ("speed", "skipped"),
    ),
    malware_count: int = 0,
    started_at: str = "2025-06-01T12:00:00.123456+00:00",
    detail: ResultDetail | None = None,
) -> ResultSummary:
    return ResultSummary(
        dir_name="20250601T120000Z-ABC123456789",
        path=Path("/x/result.json"),
        mtime=0.0,
        verdict=verdict,
        serial=serial,
        vendor="V",
        model="M",
        kernel_name=kernel,
        port_path=None,
        started_at=started_at,
        finished_at="",
        steps=steps,
        malware_count=malware_count,
        digest="0123456789abcdef" + "0" * 48,
        detail=detail if detail is not None else ResultDetail(),
    )


def check(payload: str) -> dict[str, object]:
    """Check the limits and version, then decode back into a dict."""
    assert len(payload.encode("ascii")) <= MAX_PAYLOAD_BYTES
    assert qr_version(payload) <= MAX_VERSION
    data = json.loads(payload)
    assert isinstance(data, dict)
    return {str(key): value for key, value in data.items()}


class TestBuildPayload:
    def test_typical(self) -> None:
        """The same content as the design doc example is 109 bytes."""
        payload = build_payload(summary())
        assert payload == (
            '{"v":2,"r":"I","s":"ABC123456789","t":"20250601T120000Z",'
            '"d":"0123456789abcdef","st":"m:P,h:P,c:S,s:S","n":0}'
        )
        assert len(payload) == 109
        check(payload)

    def test_worst_case_serial(self) -> None:
        """A 32-byte serial + 5 steps + n=9999 is 136 bytes."""
        payload = build_payload(summary(serial="A" * 32, steps=FIVE_STEPS, malware_count=9999))
        assert len(payload) == 136
        data = check(payload)
        assert "k" not in data

    def test_worst_case_with_k(self) -> None:
        """Even the worst case (empty serial, kernel name used instead) is 150 or less."""
        payload = build_payload(
            summary(serial="", kernel="sdabc", steps=FIVE_STEPS, malware_count=9999)
        )
        data = check(payload)
        assert data["k"] == 1
        assert data["s"] == "sdabc"
        assert len(payload) <= 142

    def test_long_serial_truncated(self) -> None:
        """33 or more chars are truncated to 32."""
        data = check(build_payload(summary(serial="X" * 40)))
        assert data["s"] == "X" * 32

    @pytest.mark.parametrize("length", [32, 40])
    def test_fullwidth_serial_becomes_question_marks(self, length: int) -> None:
        """Full-width serial chars become ?; the result is <=150 bytes and version <=8."""
        payload = build_payload(summary(serial="あ" * length, steps=FIVE_STEPS, malware_count=9999))
        data = check(payload)
        assert data["s"] == "?" * 32
        assert len(payload) <= 142

    def test_empty_serial_uses_kernel(self) -> None:
        data = check(build_payload(summary(serial="")))
        assert data == {
            "v": 2,
            "r": "I",
            "s": "sda",
            "k": 1,
            "t": "20250601T120000Z",
            "d": "0123456789abcdef",
            "st": "m:P,h:P,c:S,s:S",
            "n": 0,
        }

    def test_unknown_codes(self) -> None:
        """Unknown Verdict/StepStatus becomes ?; an unknown step name becomes its first 3 chars."""
        data = check(build_payload(summary(verdict="Weird", steps=(("firmware", "odd"),))))
        assert data["r"] == "?"
        assert data["st"] == "fir:?"

    def test_step_order_preserved(self) -> None:
        data = check(build_payload(summary(steps=(("speed", "fail"), ("malware", "pass")))))
        assert data["st"] == "s:F,m:P"

    def test_started_at_fallback(self) -> None:
        """If started_at cannot be read, use the first 16 chars of the directory name."""
        data = check(build_payload(summary(started_at="garbage")))
        assert data["t"] == "20250601T120000Z"

    def test_verdict_codes(self) -> None:
        for verdict, code in (
            ("No Defect", "N"),
            ("Malware Detected", "M"),
            ("Spec Mismatch", "S"),
            ("Defective", "D"),
        ):
            assert check(build_payload(summary(verdict=verdict)))["r"] == code

    def test_oversize_raises(self) -> None:
        """ValueError when it still exceeds 150 bytes after shortening."""
        with pytest.raises(ValueError, match="payload"):
            build_payload(summary(malware_count=10**120))

    def test_shortens_serial_then_drops_steps(self) -> None:
        """Above 150 bytes, first cut s to 16 chars; if still over, drop st."""
        steps = tuple((f"step{i:02d}", "pass") for i in range(6))
        data = check(build_payload(summary(serial="A" * 32, steps=steps, malware_count=9999)))
        assert data["s"] == "A" * 16
        assert "st" in data
        steps = tuple((f"step{i:02d}", "pass") for i in range(10))
        data = check(build_payload(summary(serial="A" * 32, steps=steps, malware_count=9999)))
        assert "st" not in data
