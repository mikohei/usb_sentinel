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
    """上限とバージョンを検証して辞書に戻す。"""
    assert len(payload.encode("ascii")) <= MAX_PAYLOAD_BYTES
    assert qr_version(payload) <= MAX_VERSION
    data = json.loads(payload)
    assert isinstance(data, dict)
    return {str(key): value for key, value in data.items()}


class TestBuildPayload:
    def test_typical(self) -> None:
        """設計書の例と同じ内容で109バイト。"""
        payload = build_payload(summary())
        assert payload == (
            '{"v":2,"r":"I","s":"ABC123456789","t":"20250601T120000Z",'
            '"d":"0123456789abcdef","st":"m:P,h:P,c:S,s:S","n":0}'
        )
        assert len(payload) == 109
        check(payload)

    def test_worst_case_serial(self) -> None:
        """32バイトのシリアル＋5ステップ＋n=9999で136バイト。"""
        payload = build_payload(summary(serial="A" * 32, steps=FIVE_STEPS, malware_count=9999))
        assert len(payload) == 136
        data = check(payload)
        assert "k" not in data

    def test_worst_case_with_k(self) -> None:
        """空シリアルでカーネル名を使う最悪条件でも150以下。"""
        payload = build_payload(
            summary(serial="", kernel="sdabc", steps=FIVE_STEPS, malware_count=9999)
        )
        data = check(payload)
        assert data["k"] == 1
        assert data["s"] == "sdabc"
        assert len(payload) <= 142

    def test_long_serial_truncated(self) -> None:
        """33文字以上は32文字に切る。"""
        data = check(build_payload(summary(serial="X" * 40)))
        assert data["s"] == "X" * 32

    @pytest.mark.parametrize("length", [32, 40])
    def test_fullwidth_serial_becomes_question_marks(self, length: int) -> None:
        """全角のシリアルは?に置き換わり150バイト以下・バージョン8以下。"""
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
        """未知のVerdict／StepStatusは?、未知のステップ名は先頭3文字。"""
        data = check(build_payload(summary(verdict="Weird", steps=(("firmware", "odd"),))))
        assert data["r"] == "?"
        assert data["st"] == "fir:?"

    def test_step_order_preserved(self) -> None:
        data = check(build_payload(summary(steps=(("speed", "fail"), ("malware", "pass")))))
        assert data["st"] == "s:F,m:P"

    def test_started_at_fallback(self) -> None:
        """started_atが読めなければディレクトリ名の先頭16文字。"""
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
        """短縮しても150バイトを超えるときはValueError。"""
        with pytest.raises(ValueError, match="payload"):
            build_payload(summary(malware_count=10**120))

    def test_shortens_serial_then_drops_steps(self) -> None:
        """150バイト超なら先にsを16文字に、まだ超えればstを落とす。"""
        steps = tuple((f"step{i:02d}", "pass") for i in range(6))
        data = check(build_payload(summary(serial="A" * 32, steps=steps, malware_count=9999)))
        assert data["s"] == "A" * 16
        assert "st" in data
        steps = tuple((f"step{i:02d}", "pass") for i in range(10))
        data = check(build_payload(summary(serial="A" * 32, steps=steps, malware_count=9999)))
        assert "st" not in data
