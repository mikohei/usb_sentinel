"""結果ファイルの書き出し（write_all）。

表示フロントエンドが書き込み途中のresult.jsonを読まないよう、
一時ファイル＋os.replaceで置き換えることを検証する。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.test_report import _clean_malware_step, _result
from usb_sentinel.libs import report as report_mod
from usb_sentinel.libs import write_all
from usb_sentinel.libs.report.json_report import digest


class TestWriteAll:
    def test_writes_all_artifacts(self, tmp_path: Path) -> None:
        written = write_all(_result(_clean_malware_step()), tmp_path, emit_qr=True)

        names = {p.name for p in written}
        assert names == {"result.json", "result.txt", "result-qr.png"}
        for path in written:
            assert path.is_file()
            assert path.stat().st_size > 0

    def test_qr_can_be_disabled(self, tmp_path: Path) -> None:
        written = write_all(_result(_clean_malware_step()), tmp_path, emit_qr=False)

        assert {p.name for p in written} == {"result.json", "result.txt"}

    def test_creates_output_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "run-1"

        write_all(_result(_clean_malware_step()), target, emit_qr=False)

        assert (target / "result.json").is_file()

    def test_replaces_via_tmp_in_same_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """3ファイルとも同ディレクトリの.tmpからos.replaceで置き換える。"""
        calls: list[tuple[Path, Path]] = []
        original = report_mod.os.replace

        def recorder(src: str | Path, dst: str | Path) -> None:
            calls.append((Path(src), Path(dst)))
            original(src, dst)

        monkeypatch.setattr(report_mod.os, "replace", recorder)

        write_all(_result(_clean_malware_step()), tmp_path, emit_qr=True)

        assert [dst.name for _, dst in calls] == ["result.json", "result.txt", "result-qr.png"]
        for src, dst in calls:
            assert src.name == dst.name + ".tmp"
            assert src.parent == dst.parent == tmp_path

    def test_leaves_no_tmp_files(self, tmp_path: Path) -> None:
        write_all(_result(_clean_malware_step()), tmp_path, emit_qr=True)

        assert list(tmp_path.glob("*.tmp")) == []

    def test_keeps_old_content_when_replace_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """置き換えに失敗しても旧result.jsonは壊れない。"""
        target = tmp_path / "result.json"
        target.write_text("old", encoding="utf-8")

        def fail(src: str | Path, dst: str | Path) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(report_mod.os, "replace", fail)

        with pytest.raises(OSError, match="replace failed"):
            write_all(_result(_clean_malware_step()), tmp_path, emit_qr=False)

        assert target.read_text(encoding="utf-8") == "old"

    def test_json_bytes_match_digest(self, tmp_path: Path) -> None:
        """result.jsonをそのままハッシュした値がQRのdigestと一致する。"""
        result = _result(_clean_malware_step())

        write_all(result, tmp_path, emit_qr=False)

        actual = hashlib.sha256((tmp_path / "result.json").read_bytes()).hexdigest()
        assert actual == digest(result)
