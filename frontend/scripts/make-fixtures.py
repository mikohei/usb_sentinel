"""偽sysfsツリーとサンプル結果を作る開発用スクリプト（plan.md D15）。

使い方: uv run python scripts/make-fixtures.py [OUT] --scene a|b
OUT（既定/tmp/usb-sentinel-fixtures）にsys_block/、devices/...、results/、display.tomlを作る。
既存のOUTがあれば同名ファイルを上書きし、削除はしない。
"""

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_OUT = Path("/tmp/usb-sentinel-fixtures")  # noqa: S108 -- 開発機専用の既定出力先
PCIE = "devices/platform/scb/fd500000.pcie/pci0000:00/0000:00:00.0/0000:01:00.0"
# ポートディレクトリをどれだけ過去にするか（起動待ちの20秒を確実に過ぎた状態にする）。
PORT_AGE_S = 600
ALL_PASS = ["malware", "hash_inventory", "unmount", "capacity", "speed"]


@dataclass(frozen=True)
class Device:
    """フィクスチャ1台分の定義。"""

    kernel: str
    port_path: str
    serial: str
    verdict: str | None
    # "ok"（正常なresult.json）、"broken"（途中で切れたJSON）、None（結果なし）。
    result_kind: str | None


SCENES: dict[str, list[Device]] = {
    "a": [
        Device("sda", "1-1.1", "ABC123456789XYZ", "No Defect", "ok"),
        Device("sdb", "1-1.2", "MAL0001", "Malware Detected", "ok"),
        Device("sdc", "1-1.3", "INSPECT01", None, None),
        Device("sdd", "1-1.4", "BROKEN12", "No Defect", "broken"),
    ],
    "b": [
        Device("sda", "1-1.1", "SPEC9999", "Spec Mismatch", "ok"),
        Device("sdb", "2-2", "DEFECT77", "Defective", "ok"),
    ],
    # 第2期: 偽装疑いと、詳細QRに数値が入る正常品。
    "c": [
        Device("sda", "1-1.1", "FAKE32GB", "Counterfeit Suspected", "ok"),
        Device("sdb", "1-1.2", "GOOD64GB", "No Defect", "ok"),
        Device("sdc", "1-1.3", "INSPECT01", None, None),
    ],
}

# 詳細QR用の第2期の数値（容量・速度・ClamAV・台帳）。シーンcの検体にだけ付ける。
PHASE2_DETAILS: dict[str, dict[str, object]] = {
    "FAKE32GB": {
        "nominal_bytes": 32_000_000_000,
        "advertised_bytes": 34_500_000_000,
        "actual_bytes": 7_900_000_000,
        "kind": "short",
        "lost_ratio": 0.76,
        "counterfeit_reasons": ["advertised_over", "lost_ratio"],
        "read_mb_s": 18.4,
        "write_mb_s": 6.1,
        "reason_codes": ["c:over", "c:short", "s:severe"],
    },
    "GOOD64GB": {
        "nominal_bytes": 64_000_000_000,
        "advertised_bytes": 62_000_000_000,
        "actual_bytes": 61_800_000_000,
        "kind": "ok",
        "lost_ratio": 0.0,
        "counterfeit_reasons": [],
        "read_mb_s": 92.3,
        "write_mb_s": 31.7,
        "reason_codes": [],
    },
}

DISPLAY_TOML = """\
results_dir = "{results}"
sys_block = "{sys_block}"
poll_interval_s = 1.0
# 開発機にはSQLite索引が無いので無効にし、情報ストリップにdb_missingを出さない。
sqlite_path = ""

[[ports]]
label = "P1"
paths = ["1-1.1", "2-1"]

[[ports]]
label = "P2"
paths = ["1-1.2", "2-2"]

[[ports]]
label = "P3"
paths = ["1-1.3"]

[[ports]]
label = "P4"
paths = ["1-1.4"]
"""


def device_dir(out: Path, device: Device) -> tuple[Path, Path]:
    """ブロックデバイスの実体ディレクトリとポートディレクトリのパスを返す。"""
    bus = device.port_path.split("-")[0]
    hub = device.port_path.split(".")[0]
    pcie = out / PCIE / f"usb{bus}"
    port_dir = pcie / hub if hub == device.port_path else pcie / hub / device.port_path
    block = (
        port_dir
        / f"{device.port_path}:1.0"
        / f"host{bus}"
        / f"target{bus}:0:0"
        / f"{bus}:0:0:0"
        / "block"
        / device.kernel
    )
    return block, port_dir


def steps_for(device: Device) -> list[dict[str, object]]:
    """判定に応じたステップ一覧。"""
    if device.verdict == "Malware Detected":
        detections = [
            {"path": "/mnt/usb/autorun.inf", "signature": "Win.Trojan.Agent-1"},
            {"path": "/mnt/usb/setup.exe", "signature": "Win.Malware.Generic-2"},
        ]
        steps: list[dict[str, object]] = [
            {"name": "malware", "status": "fail", "details": {"detections": detections}}
        ]
        steps.extend({"name": n, "status": "skipped", "details": {}} for n in ALL_PASS[1:])
        return steps
    steps_out: list[dict[str, object]] = [
        {"name": "malware", "status": "pass", "details": {"detections": []}}
    ]
    for name in ALL_PASS[1:]:
        status = "pass"
        if device.verdict == "Spec Mismatch" and name == "capacity":
            status = "fail"
        if device.verdict == "Defective" and name == "speed":
            status = "fail"
        if device.verdict == "Counterfeit Suspected" and name in ("capacity", "speed"):
            status = "fail"
        steps_out.append({"name": name, "status": status, "details": {}})
    return _with_phase2_details(device, steps_out)


def _with_phase2_details(device: Device, steps: list[dict[str, object]]) -> list[dict[str, object]]:
    """シーンcの検体に、backend第2期のdetails（数値）を埋める。"""
    extra = PHASE2_DETAILS.get(device.serial)
    if extra is None:
        return steps
    for step in steps:
        details = step["details"]
        if not isinstance(details, dict):
            continue
        if step["name"] == "malware":
            details["database"] = {"db_version": "27450", "db_date": "2026-10-14"}
        elif step["name"] == "hash_inventory":
            details["digest"] = "7f3a9c1e" * 8
            details["lines"] = 1842
        elif step["name"] == "capacity":
            details.update(
                actual_bytes=extra["actual_bytes"],
                kind=extra["kind"],
                lost_ratio=extra["lost_ratio"],
                counterfeit_reasons=extra["counterfeit_reasons"],
            )
        elif step["name"] == "speed":
            details.update(
                read={"mb_s": extra["read_mb_s"]},
                write={"mb_s": extra["write_mb_s"]},
                threshold_read_mb_s=50.0,
                threshold_write_mb_s=15.0,
                basis_read="class_floor",
                basis_write="class_floor",
                device_class="usb3_flash",
                severity="severe" if step["status"] == "fail" else None,
            )
    return steps


def result_json(device: Device, started: datetime) -> str:
    """backendのto_json()と同じ形のresult.json文字列。"""
    extra = PHASE2_DETAILS.get(device.serial)
    device_info: dict[str, object] = {
        "kernel_name": device.kernel,
        "model": "Flash Disk",
        "path": f"/dev/{device.kernel}",
        "port_path": device.port_path,
        "serial": device.serial,
        "vendor": "Vendor",
    }
    result: dict[str, object] = {
        "device": device_info,
        "finished_at": (started + timedelta(minutes=5)).isoformat(),
        "started_at": started.isoformat(),
        "steps": steps_for(device),
        "verdict": device.verdict,
    }
    if extra is not None:
        # backend第2期が足すトップレベルのキー。
        device_info["advertised_bytes"] = extra["advertised_bytes"]
        device_info["link_speed_mbps"] = 5000
        nominal = extra["nominal_bytes"]
        advertised = extra["advertised_bytes"]
        exceeded = (
            isinstance(nominal, int) and isinstance(advertised, int) and advertised > nominal * 1.07
        )
        result["advertised_check"] = {"nominal_bytes": nominal, "exceeded": exceeded}
        result["reason_codes"] = extra["reason_codes"]
        result["elapsed_s"] = 300.0
        result["warnings"] = []
    return json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_result(results: Path, device: Device, now: float) -> None:
    """結果ディレクトリとresult.jsonを書き、mtimeを現在に揃える。"""
    started = datetime.fromtimestamp(now - 300, tz=UTC)
    name = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{device.serial}"
    directory = results / name
    directory.mkdir(parents=True, exist_ok=True)
    text = result_json(device, started)
    if device.result_kind == "broken":
        text = text[: len(text) // 2]
    path = directory / "result.json"
    path.write_text(text, encoding="utf-8")
    os.utime(path, (now, now))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"{device.kernel} {device.port_path} {name} d:{digest[:16]}")


def build(out: Path, scene: str) -> None:
    """OUT配下にシーンのフィクスチャ一式を作る。"""
    now = time.time()
    sys_block = out / "sys_block"
    results = out / "results"
    sys_block.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    for device in SCENES[scene]:
        block, port_dir = device_dir(out, device)
        block.mkdir(parents=True, exist_ok=True)
        (port_dir / "serial").write_bytes(device.serial.encode("ascii") + b"\n")
        link = sys_block / device.kernel
        if link.is_symlink():
            link.unlink()
        link.symlink_to(block)
        past = now - PORT_AGE_S
        os.utime(port_dir, (past, past))
        if device.result_kind is not None:
            write_result(results, device, now)
    # 他シーンのリンクが残っていれば、このシーンに無いカーネル名のリンクだけ外す。
    wanted = {d.kernel for d in SCENES[scene]}
    for entry in sys_block.iterdir():
        if entry.is_symlink() and entry.name not in wanted:
            entry.unlink()
    (out / "display.toml").write_text(
        DISPLAY_TOML.format(results=results, sys_block=sys_block), encoding="utf-8"
    )
    print(f"fixtures: {out}")


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", nargs="?", type=Path, default=DEFAULT_OUT, help="出力先")
    parser.add_argument("--scene", choices=sorted(SCENES), default="a", help="シーン")
    args = parser.parse_args(argv)
    build(args.out, args.scene)
    return 0


if __name__ == "__main__":
    sys.exit(main())
