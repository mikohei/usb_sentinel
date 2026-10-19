"""結果出力（JSON / テキスト / QR）。

QR はバイトモードで最大 2953 バイトしか入らないため、ペイロードが上限内に収まる
ことを必ず検証する。ここを外すと実機で QR 生成が落ちる。
"""

from __future__ import annotations

import json

from usb_sentinel.libs import (
    DeviceInfo,
    InspectionResult,
    StepResult,
    StepStatus,
    Verdict,
    render_text,
)
from usb_sentinel.libs.report.json_report import digest, to_json
from usb_sentinel.libs.report.qr import MAX_PAYLOAD_BYTES, build_payload


def _result(*steps: StepResult, verdict: Verdict = Verdict.NO_DEFECT) -> InspectionResult:
    device = DeviceInfo(
        path="/dev/sda",
        kernel_name="sda",
        advertised_bytes=32_000_000_000,
        vendor="Generic",
        model="Flash Disk",
        serial="ABC123456789",
        removable=True,
        bus="usb",
    )
    return InspectionResult(
        device=device,
        started_at="2024-03-15T04:05:06+00:00",
        finished_at="2024-03-15T04:35:06+00:00",
        verdict=verdict,
        steps=list(steps),
        reasons=["全ステップ Pass"],
    )


def _clean_malware_step() -> StepResult:
    return StepResult(
        name="malware",
        status=StepStatus.PASS,
        summary="マルウェアなし",
        details={
            "database": {"engine_version": "1.0.3", "db_version": "27200"},
            "detections": [],
        },
    )


class TestJson:
    def test_keys_are_sorted_and_stable(self) -> None:
        """キー順を固定して result.json の差分を追えるようにする。"""
        text = to_json(_result(_clean_malware_step()))
        payload = json.loads(text)

        assert list(payload) == sorted(payload)
        assert payload["verdict"] == "No Defect"
        assert payload["device"]["serial"] == "ABC123456789"
        assert payload["steps"][0]["name"] == "malware"

    def test_japanese_is_not_escaped(self) -> None:
        text = to_json(_result(_clean_malware_step()))

        assert "マルウェアなし" in text

    def test_digest_changes_with_content(self) -> None:
        a = _result(_clean_malware_step())
        b = _result(_clean_malware_step(), verdict=Verdict.MALWARE_DETECTED)

        assert digest(a) != digest(b)

    def test_device_has_port_path_key(self) -> None:
        """表示フロントエンドが参照するport_pathキーがdeviceに出る。"""
        payload = json.loads(to_json(_result(_clean_malware_step())))

        assert "port_path" in payload["device"]
        assert payload["device"]["port_path"] is None


class TestText:
    def test_includes_verdict_and_steps(self) -> None:
        text = render_text(_result(_clean_malware_step()))

        assert "No Defect" in text
        assert "[ OK ] malware" in text
        assert "32.00 GB" in text

    def test_lists_detections_and_warnings(self) -> None:
        infected = StepResult(
            name="malware",
            status=StepStatus.FAIL,
            summary="マルウェア 1 件検知",
            details={"detections": [{"path": "/mnt/sda1/eicar.com", "signature": "Eicar"}]},
            warnings=["ClamAV 定義 DB が 100 日前のもの"],
        )

        text = render_text(_result(infected, verdict=Verdict.MALWARE_DETECTED))

        assert "[FAIL] malware" in text
        assert "Eicar: /mnt/sda1/eicar.com" in text
        assert "100 日前" in text

    def test_marks_not_implemented_steps(self) -> None:
        step = StepResult(name="capacity", status=StepStatus.NOT_IMPLEMENTED, summary="未実装")

        text = render_text(_result(step))

        assert "[ -- ] capacity" in text

    def test_unknown_capacity(self) -> None:
        device = DeviceInfo(path="/dev/sda", kernel_name="sda", advertised_bytes=0)
        result = InspectionResult(device=device, started_at="2024-01-01T00:00:00+00:00")

        assert "公称容量   : 不明" in render_text(result)


class TestQrPayload:
    def test_payload_is_within_limit(self) -> None:
        payload = build_payload(_result(_clean_malware_step()))

        assert len(payload.encode("utf-8")) <= MAX_PAYLOAD_BYTES

    def test_payload_carries_verdict_and_digest(self) -> None:
        result = _result(_clean_malware_step())
        payload = json.loads(build_payload(result))

        assert payload["v"] == 3
        assert payload["verdict"] == "No Defect"
        assert payload["serial"] == "ABC123456789"
        assert payload["steps"] == {"malware": "pass"}
        assert payload["clamav_db"] == "27200"
        # フル JSON との対応を取るためのダイジェスト。
        assert payload["digest"] == digest(result)

    def test_many_detections_are_truncated_within_limit(self) -> None:
        """検知が大量でもペイロードは上限内に収め、削ったことを明示する。"""
        detections = [
            {
                "path": f"/mnt/sda1/very/long/path/to/sample/number/{i}.bin",
                "signature": f"Win.Trojan.VeryLongSignatureName-{i}",
            }
            for i in range(200)
        ]
        infected = StepResult(
            name="malware",
            status=StepStatus.FAIL,
            details={"database": {"db_version": "27200"}, "detections": detections},
        )

        payload_text = build_payload(_result(infected, verdict=Verdict.MALWARE_DETECTED))
        payload = json.loads(payload_text)

        assert len(payload_text.encode("utf-8")) <= MAX_PAYLOAD_BYTES
        assert payload["malware_count"] == 200
        assert payload["truncated"] is True
        assert len(payload["signatures"]) <= 3


class TestDefectiveVerdict:
    def _defective(self) -> InspectionResult:
        capacity = StepResult(
            name="capacity",
            status=StepStatus.FAIL,
            summary="データ破損: 12セクタ（消失10 / 破損2 / 変化0 / 上書き0）",
            details={
                "mode": "f3write",
                "commands": [
                    ["mount", "-w", "-o", "noexec,nosuid,nodev", "/dev/sda1", "/run/x/rw/0"],
                    ["f3write", "/run/x/rw/0"],
                    ["f3read", "/run/x/rw/0"],
                    ["umount", "/run/x/rw/0"],
                ],
                "advertised_bytes": 32_000_000_000,
                "pass_ratio": 0.93,
                "threshold_bytes": 29_760_000_000,
                "actual_bytes": 31_200_000_000,
                "verified_bytes": 30_200_000_000,
                "used_bytes": 1_000_000_000,
                "ratio": 0.975,
                "verified_ratio": 0.9438,
                "bad_sectors": 12,
                "defect": True,
                "filesystems": [
                    {
                        "device": "/dev/sda1",
                        "mountpoint": "/run/x/rw/0",
                        "used_bytes": 1_000_000_000,
                        "free_bytes": 30_900_000_000,
                        "ok_sectors": 58984375,
                        "lost_sectors": 10,
                        "corrupted_sectors": 2,
                        "slightly_changed_sectors": 0,
                        "overwritten_sectors": 0,
                        "write_mb_s": 18.2,
                        "read_mb_s": 62.9,
                        "stale_h2w_removed": 0,
                        "f3write_returncode": 0,
                        "f3read_returncode": 0,
                    }
                ],
                "probe": None,
            },
        )
        speed = StepResult(
            name="speed",
            status=StepStatus.PASS,
            summary="読み取り85.3MB/s、書き込み20.1MB/s（閾値72.0 / 16.0MB/s）",
            details={"read": {"mb_s": 85.3}, "write": {"mb_s": 20.1}},
        )
        return _result(_clean_malware_step(), capacity, speed, verdict=Verdict.DEFECTIVE)

    def test_qr_payload_within_limit_and_structure_unchanged(self) -> None:
        """detailsが増えてもQRペイロードの構造と上限は変わらない。"""
        text = build_payload(self._defective())
        payload = json.loads(text)

        assert len(text.encode("utf-8")) <= MAX_PAYLOAD_BYTES
        assert payload["verdict"] == "Defective"
        assert payload["steps"] == {"malware": "pass", "capacity": "fail", "speed": "pass"}
        assert "details" not in payload

    def test_text_shows_capacity_failure(self) -> None:
        text = render_text(self._defective())

        assert "判定: Defective" in text
        assert "[FAIL] capacity: データ破損:" in text
