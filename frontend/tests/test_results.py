import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.conftest import make_result, phase2_result, sample_result
from usb_sentinel_display.libs import results as results_mod
from usb_sentinel_display.libs.models import ResultDetail, ResultSummary
from usb_sentinel_display.libs.results import (
    ResultsMissingError,
    ResultsPermissionError,
    ResultStore,
    file_digest,
    parse_dir_name,
    parse_summary,
    serial_for_dir,
)

NAME = "20250601T120000Z-ABC123"


class TestDirName:
    def test_parse_plain(self) -> None:
        """<timestamp>-<serial> can be split."""
        assert parse_dir_name(NAME) == ("20250601T120000Z", "ABC123")

    def test_parse_with_suffix(self) -> None:
        """With a -%f name, the 6 digits do not leak into the serial part."""
        assert parse_dir_name("20250601T120000Z-ABC123-000123") == ("20250601T120000Z", "ABC123")

    def test_serial_containing_dash_digits(self) -> None:
        """Even if the serial itself contains -6 digits, only the trailing suffix is removed."""
        assert parse_dir_name("20250601T120000Z-AB-123456-000001") == (
            "20250601T120000Z",
            "AB-123456",
        )

    @pytest.mark.parametrize("name", ["result.json", "20250601-ABC", "notes", ""])
    def test_parse_invalid(self, name: str) -> None:
        """Malformed names give None."""
        assert parse_dir_name(name) is None

    def test_serial_for_dir(self) -> None:
        """Same sanitizing rules as result_dir_for()."""
        assert serial_for_dir("ABC 123/x", "sda") == "ABC_123_x"
        assert serial_for_dir("", "sda") == "sda-unknown-serial"
        assert len(serial_for_dir("a" * 200, "sda")) == 128


class TestFileDigest:
    def test_matches_hashlib(self, tmp_path: Path) -> None:
        """Matches the SHA-256 of the file bytes."""
        path = tmp_path / "result.json"
        path.write_bytes(b'{"a": 1}\n')
        assert file_digest(path) == hashlib.sha256(path.read_bytes()).hexdigest()


class TestParseSummary:
    def parse(self, data: object) -> ResultSummary | None:
        return parse_summary(data, dir_name=NAME, path=Path("/x"), mtime=1.0, digest="0" * 64)

    def parse_ok(self, data: object) -> ResultSummary:
        summary = self.parse(data)
        assert summary is not None
        return summary

    def test_full(self) -> None:
        """Extract the needed fields from a valid result.json."""
        data = sample_result(port_path="1-1.3")
        summary = parse_summary(
            data, dir_name=NAME, path=Path("/x/result.json"), mtime=5.0, digest="f" * 64
        )
        assert summary is not None
        assert summary.verdict == "No Defect"
        assert summary.serial == "ABC123"
        assert summary.kernel_name == "sda"
        assert summary.port_path == "1-1.3"
        assert summary.steps == (
            ("malware", "pass"),
            ("hash_inventory", "pass"),
            ("capacity", "skipped"),
            ("speed", "skipped"),
        )
        assert summary.malware_count == 0
        assert summary.digest == "f" * 64
        assert summary.mtime == 5.0
        assert summary.dir_name == NAME

    @pytest.mark.parametrize("verdict", ["No Defect (Scan Only)", "Inconclusive", "No Defect"])
    def test_new_and_old_verdicts_load(self, verdict: str) -> None:
        """新しい検査のみの判定も、以前からの判定もそのまま読み込める。"""
        assert self.parse_ok(sample_result(verdict=verdict)).verdict == verdict

    def test_port_path_missing_or_null(self) -> None:
        """None if port_path is missing or null."""
        assert self.parse_ok(sample_result()).port_path is None
        data = sample_result()
        device = data["device"]
        assert isinstance(device, dict)
        device["port_path"] = None
        assert self.parse_ok(data).port_path is None

    def test_malware_count(self) -> None:
        """Count the detections in the malware step."""
        steps: list[dict[str, object]] = [
            {"name": "malware", "status": "fail", "details": {"detections": [{"a": 1}, {"b": 2}]}}
        ]
        assert self.parse_ok(sample_result(steps=steps)).malware_count == 2

    def test_sanitizes_text_fields(self) -> None:
        """Control chars in serial/vendor/model become ?."""
        data = sample_result(serial="AB\nC")
        device = data["device"]
        assert isinstance(device, dict)
        device["vendor"] = "V\tx"
        summary = self.parse_ok(data)
        assert summary.serial == "AB?C"
        assert summary.vendor == "V?x"

    @pytest.mark.parametrize("missing", ["device", "verdict", "steps", "started_at"])
    def test_missing_required_key(self, missing: str) -> None:
        """None if a required key is missing."""
        data = sample_result()
        del data[missing]
        assert self.parse(data) is None

    @pytest.mark.parametrize("missing", ["kernel_name", "serial"])
    def test_missing_device_key(self, missing: str) -> None:
        """None if a required key under device is missing."""
        data = sample_result()
        device = data["device"]
        assert isinstance(device, dict)
        del device[missing]
        assert self.parse(data) is None

    @pytest.mark.parametrize("data", [[], "str", None, {"device": 1, "verdict": "x", "steps": []}])
    def test_wrong_types(self, data: object) -> None:
        """None if it is not a dict, or device is not a dict."""
        assert self.parse(data) is None

    def test_steps_entries_are_filtered(self) -> None:
        """Non-dict steps elements are ignored; names and statuses are stringified."""
        steps: list[dict[str, object]] = [{"name": "malware", "status": "pass"}]
        data = sample_result(steps=steps)
        data["steps"] = [*steps, "junk", {"name": 1}]
        assert self.parse_ok(data).steps == (("malware", "pass"),)

    def test_detail_attached(self) -> None:
        """parse_summary() puts the parse_detail() result into detail."""
        summary = self.parse_ok(phase2_result())
        assert summary.detail.elapsed_s == 312.5
        assert summary.detail.capacity_kind == "short"
        assert self.parse_ok(sample_result()).detail == ResultDetail()

    @pytest.mark.parametrize("mode", ["scan_only", "full"])
    def test_mode_parsed(self, mode: str) -> None:
        """mode is kept only when it is scan_only or full."""
        data = sample_result()
        data["mode"] = mode
        assert self.parse_ok(data).mode == mode

    @pytest.mark.parametrize("mode", ["erase", "FULL", "", 1, None, True])
    def test_mode_unknown_is_none(self, mode: object) -> None:
        data = sample_result()
        data["mode"] = mode
        assert self.parse_ok(data).mode is None

    def test_mode_missing_is_none(self) -> None:
        assert self.parse_ok(sample_result()).mode is None


class TestResultStore:
    def test_missing_root(self, tmp_path: Path) -> None:
        """ResultsMissingError if the results root does not exist."""
        with pytest.raises(ResultsMissingError):
            ResultStore(tmp_path / "none").scan(0.0)

    def test_root_is_file(self, tmp_path: Path) -> None:
        """ResultsMissingError even if the results root is a file."""
        path = tmp_path / "file"
        path.write_text("x")
        with pytest.raises(ResultsMissingError):
            ResultStore(path).scan(0.0)

    def test_permission_denied(self, tmp_path: Path) -> None:
        """An unreadable results root gives ResultsPermissionError."""
        root = tmp_path / "locked"
        root.mkdir()
        root.chmod(0)
        try:
            try:
                os.scandir(root).close()
            except PermissionError:
                pass
            else:
                pytest.skip("chmod 0 does not deny access here")
            with pytest.raises(ResultsPermissionError):
                ResultStore(root).scan(0.0)
        finally:
            root.chmod(0o700)

    def test_none_min_mtime_reads_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If min_mtime is None, only scandir and do not read JSON."""
        make_result(tmp_path, NAME, sample_result(), 100.0)
        calls = count_loads(monkeypatch)
        assert ResultStore(tmp_path).scan(None) == []
        assert calls == [0]

    def test_scan_reads_valid(self, tmp_path: Path) -> None:
        """A readable result.json becomes a record with a summary."""
        make_result(tmp_path, NAME, sample_result(), 100.0)
        (tmp_path / "notes.txt").write_text("x")
        (tmp_path / "junk").mkdir()
        (tmp_path / "20250601T130000Z-NOJSON").mkdir()
        records = ResultStore(tmp_path).scan(50.0)
        assert len(records) == 1
        record = records[0]
        assert record.name == NAME
        assert record.serial_part == "ABC123"
        assert record.mtime == 100.0
        assert record.broken is False
        assert record.summary is not None
        assert record.summary.digest == file_digest(tmp_path / NAME / "result.json")

    def test_skips_older_than_min_mtime(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Directories older than min_mtime are not read or offered as candidates."""
        make_result(tmp_path, NAME, sample_result(), 100.0)
        calls = count_loads(monkeypatch)
        assert ResultStore(tmp_path).scan(100.5) == []
        assert calls == [0]

    def test_truncated_json_not_broken_until_threshold(self, tmp_path: Path) -> None:
        """Truncated JSON: summary=None, broken=True after 10 in a row, recovers once readable."""
        make_result(tmp_path, NAME, '{"device": {"kern', 100.0)
        store = ResultStore(tmp_path)
        for _ in range(9):
            (record,) = store.scan(0.0)
            assert record.summary is None
            assert record.broken is False
        (record,) = store.scan(0.0)
        assert record.broken is True
        make_result(tmp_path, NAME, sample_result(), 101.0)
        (record,) = store.scan(0.0)
        assert record.summary is not None
        assert record.broken is False

    def test_missing_key_counts_as_failure(self, tmp_path: Path) -> None:
        """A missing required key is also treated as a write in progress."""
        make_result(tmp_path, NAME, {"device": {"kernel_name": "sda"}}, 100.0)
        (record,) = ResultStore(tmp_path).scan(0.0)
        assert record.summary is None
        assert record.broken is False

    def test_invalid_utf8(self, tmp_path: Path) -> None:
        """Invalid UTF-8 does not raise and counts as a failure."""
        directory = tmp_path / NAME
        directory.mkdir()
        (directory / "result.json").write_bytes(b"\xff\xfe{")
        (record,) = ResultStore(tmp_path).scan(0.0)
        assert record.summary is None

    def test_mtime_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """No reload if mtime is unchanged; reload if it changes."""
        make_result(tmp_path, NAME, sample_result(), 100.0)
        calls = count_loads(monkeypatch)
        store = ResultStore(tmp_path)
        store.scan(0.0)
        store.scan(0.0)
        assert calls == [1]
        os.utime(tmp_path / NAME / "result.json", (200.0, 200.0))
        store.scan(0.0)
        assert calls == [2]
        store.reset()
        store.scan(0.0)
        assert calls == [3]

    def test_reset_clears_failures(self, tmp_path: Path) -> None:
        """reset() also drops the failure counts."""
        make_result(tmp_path, NAME, "{", 100.0)
        store = ResultStore(tmp_path, max_failures=2)
        store.scan(0.0)
        store.reset()
        (record,) = store.scan(0.0)
        assert record.broken is False


def count_loads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Install a counter for the number of results.json.loads calls."""
    calls = [0]
    original = json.loads

    def counting(data: bytes | str) -> object:
        calls[0] += 1
        return original(data)

    monkeypatch.setattr(results_mod.json, "loads", counting)
    return calls
