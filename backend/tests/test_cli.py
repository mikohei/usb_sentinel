"""Wiring between cli.main() inspect and the SQLite completion record.

Verify that even if QR generation fails (ValueError), a completion row is kept as long as
result.json was written, and the exit code stays 3. External commands are replaced by
FakeRunner.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from tests import test_orchestrator
from usb_sentinel.libs import FakeRunner, InspectionResult
from usb_sentinel.libs import cli as cli_mod
from usb_sentinel.libs import report as report_mod
from usb_sentinel.libs.report.json_report import digest

# Reuse the FakeRunner that runs the full inspection sequence (including the SYS_BLOCK and
# SYS_USB_DEVICES monkeypatches).
full_runner = test_orchestrator.full_runner


def _write_config(tmp_path: Path, sqlite_path: str) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(
        "\n".join(
            [
                f'results_dir = "{tmp_path / "results"}"',
                f'mount_root = "{tmp_path / "mnt"}"',
                f'sqlite_path = "{sqlite_path}"',
                "allow_destructive = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config


def _run_main(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, config: Path) -> int:
    monkeypatch.setattr(
        sys, "argv", ["usb-sentinel", "--config", str(config), "inspect", "/dev/sda"]
    )
    monkeypatch.setattr(cli_mod, "SubprocessRunner", lambda: runner)
    return cli_mod.main()


def _rows(db: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db))
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM inspections")]
    finally:
        conn.close()


def _result_dirs(tmp_path: Path) -> list[Path]:
    return [p for p in (tmp_path / "results").iterdir() if p.is_dir()]


class TestMain:
    def test_inspect_records_completed_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, full_runner: FakeRunner
    ) -> None:
        """A normal inspect leaves one completed row, and the exit code follows the verdict
        (1 for Inconclusive)."""
        db = tmp_path / "db" / "inspections.sqlite"
        db.parent.mkdir()

        code = _run_main(monkeypatch, full_runner, _write_config(tmp_path, str(db)))

        assert code == 1
        rows = _rows(db)
        assert len(rows) == 1
        row = rows[0]
        assert row["state"] == "completed"
        assert row["verdict"] == "Inconclusive"
        assert row["step_malware"] == "pass"
        assert row["step_capacity"] == "skipped"
        (result_dir,) = _result_dirs(tmp_path)
        assert row["inspection_id"] == result_dir.name
        assert (result_dir / "result.json").is_file()
        assert (result_dir / "result-qr.png").is_file()

    def test_qr_failure_still_records_completed_and_exits_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, full_runner: FakeRunner
    ) -> None:
        """Even if QR generation raises ValueError, result.json exists, the completed row
        remains, and the exit code is 3."""
        db = tmp_path / "db" / "inspections.sqlite"
        db.parent.mkdir()
        digests: list[str] = []

        def failing_payload(result: InspectionResult) -> str:
            digests.append(digest(result))
            raise ValueError("QR payload exceeds the limit")

        monkeypatch.setattr(report_mod, "build_payload", failing_payload)

        code = _run_main(monkeypatch, full_runner, _write_config(tmp_path, str(db)))

        assert code == 3
        (result_dir,) = _result_dirs(tmp_path)
        assert (result_dir / "result.json").is_file()
        assert not (result_dir / "result-qr.png").exists()
        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0]["state"] == "completed"
        assert rows[0]["result_digest"] == digests[0]

    def test_empty_sqlite_path_creates_no_db(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, full_runner: FakeRunner
    ) -> None:
        code = _run_main(monkeypatch, full_runner, _write_config(tmp_path, ""))

        assert code == 1
        assert list(tmp_path.rglob("*.sqlite*")) == []
        assert len(_result_dirs(tmp_path)) == 1
