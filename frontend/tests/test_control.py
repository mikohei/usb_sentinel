"""Tests for control.py: the mode file the display writes for run-docker.sh (design doc 5.4)."""

import os
import re
import stat
from pathlib import Path

import pytest

from usb_sentinel_display.libs import control
from usb_sentinel_display.libs.control import (
    FILE_MODE,
    FULL,
    HOST_MODE_TTL_S,
    MODE_FILE_NAME,
    MODES,
    REASON_DISABLED,
    REASON_NO_CONTROL,
    SCAN_ONLY,
    control_problem,
    mode_file_present,
    reset_mode,
    write_mode,
)
from usb_sentinel_display.libs.mode_state import HEARTBEAT_S

NOW = 1760850000.4
RUN_DOCKER = Path(__file__).resolve().parents[2] / "scripts" / "run-docker.sh"


def is_root() -> bool:
    return os.geteuid() == 0


def leftovers(directory: Path, prefix: str) -> list[str]:
    return [p.name for p in directory.iterdir() if p.name.startswith(prefix)]


class TestConstants:
    def test_values(self) -> None:
        assert SCAN_ONLY == "scan_only"
        assert FULL == "full"
        assert MODES == ("scan_only", "full")
        assert MODE_FILE_NAME == "mode"
        assert FILE_MODE == 0o640
        assert REASON_DISABLED == "locked_disabled"
        assert REASON_NO_CONTROL == "locked_no_control"

    def test_ttl_matches_run_docker(self) -> None:
        """HOST_MODE_TTL_S equals MODE_TTL_S in scripts/run-docker.sh (the host's freshness)."""
        if not RUN_DOCKER.is_file():
            pytest.skip("scripts/run-docker.sh not found")
        match = re.search(r"^readonly MODE_TTL_S=([0-9]+)", RUN_DOCKER.read_text(), re.MULTILINE)
        assert match is not None, "MODE_TTL_S not found in run-docker.sh"
        assert int(match.group(1)) == HOST_MODE_TTL_S

    def test_heartbeat_well_inside_ttl(self) -> None:
        """Three missed heartbeats still fit in the host TTL."""
        assert HEARTBEAT_S * 3 < HOST_MODE_TTL_S


class TestWriteMode:
    def test_exact_bytes_and_mode(self, tmp_path: Path) -> None:
        """The file is `<mode> <int(now)>\\n` in ASCII with mode 0640 and no temp leftovers."""
        assert write_mode(tmp_path, FULL, NOW) is None
        path = tmp_path / MODE_FILE_NAME
        assert path.read_bytes() == b"full 1760850000\n"
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
        assert leftovers(tmp_path, ".mode-") == []

    def test_scan_only(self, tmp_path: Path) -> None:
        assert write_mode(tmp_path, SCAN_ONLY, 5.0) is None
        assert (tmp_path / MODE_FILE_NAME).read_bytes() == b"scan_only 5\n"

    def test_replaces_existing(self, tmp_path: Path) -> None:
        (tmp_path / MODE_FILE_NAME).write_bytes(b"full 1\n")
        assert write_mode(tmp_path, SCAN_ONLY, NOW) is None
        assert (tmp_path / MODE_FILE_NAME).read_bytes() == b"scan_only 1760850000\n"
        assert leftovers(tmp_path, ".mode-") == []

    def test_missing_dir_returns_reason(self, tmp_path: Path) -> None:
        reason = write_mode(tmp_path / "none", FULL, NOW)
        assert reason is not None
        assert "FileNotFoundError" in reason

    def test_read_only_dir_returns_reason(self, tmp_path: Path) -> None:
        if is_root():
            pytest.skip("root ignores directory permissions")
        tmp_path.chmod(0o500)
        try:
            reason = write_mode(tmp_path, FULL, NOW)
        finally:
            tmp_path.chmod(0o700)
        assert reason is not None
        assert "PermissionError" in reason
        assert leftovers(tmp_path, ".mode-") == []

    def test_replace_failure_removes_temp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def failing_replace(src: object, dst: object) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(control.os, "replace", failing_replace)
        reason = write_mode(tmp_path, FULL, NOW)
        assert reason == "OSError: replace failed"
        assert leftovers(tmp_path, ".mode-") == []
        assert not (tmp_path / MODE_FILE_NAME).exists()

    @pytest.mark.parametrize("mode", ["erase", "FULL", "", "full "])
    def test_invalid_mode_raises(self, tmp_path: Path, mode: str) -> None:
        with pytest.raises(ValueError, match="mode"):
            write_mode(tmp_path, mode, NOW)
        assert not (tmp_path / MODE_FILE_NAME).exists()


class TestResetMode:
    def test_writes_scan_only(self, tmp_path: Path) -> None:
        (tmp_path / MODE_FILE_NAME).write_bytes(b"full 1\n")
        assert reset_mode(tmp_path, NOW) is None
        assert (tmp_path / MODE_FILE_NAME).read_bytes() == b"scan_only 1760850000\n"

    def test_unlink_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the rewrite fails, the file is removed instead (a missing file is scan_only)."""
        (tmp_path / MODE_FILE_NAME).write_bytes(b"full 1\n")

        def failing_replace(src: object, dst: object) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(control.os, "replace", failing_replace)
        assert reset_mode(tmp_path, NOW) is None
        assert not (tmp_path / MODE_FILE_NAME).exists()
        assert leftovers(tmp_path, ".mode-") == []

    def test_missing_file_after_failed_write_is_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def failing_replace(src: object, dst: object) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(control.os, "replace", failing_replace)
        assert reset_mode(tmp_path, NOW) is None

    def test_both_fail_returns_joined_reasons(self, tmp_path: Path) -> None:
        if is_root():
            pytest.skip("root ignores directory permissions")
        (tmp_path / MODE_FILE_NAME).write_bytes(b"full 1\n")
        tmp_path.chmod(0o500)
        try:
            reason = reset_mode(tmp_path, NOW)
        finally:
            tmp_path.chmod(0o700)
        assert reason is not None
        assert "; " in reason
        assert reason.count("PermissionError") == 2
        assert (tmp_path / MODE_FILE_NAME).read_bytes() == b"full 1\n"

    def test_missing_dir_is_success(self, tmp_path: Path) -> None:
        """No directory means no file, which the host reads as scan_only."""
        assert reset_mode(tmp_path / "none", NOW) is None


class TestControlProblem:
    def test_none_is_disabled(self) -> None:
        assert control_problem(None) == REASON_DISABLED

    def test_writable_dir_ok(self, tmp_path: Path) -> None:
        assert control_problem(tmp_path) is None

    def test_missing_dir(self, tmp_path: Path) -> None:
        assert control_problem(tmp_path / "none") == REASON_NO_CONTROL

    def test_file_in_place_of_dir(self, tmp_path: Path) -> None:
        path = tmp_path / "file"
        path.write_text("x")
        assert control_problem(path) == REASON_NO_CONTROL

    def test_symlink_to_dir_rejected(self, tmp_path: Path) -> None:
        """A symlinked control directory is rejected, as run-docker.sh rejects it too."""
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        assert control_problem(link) == REASON_NO_CONTROL

    def test_not_writable(self, tmp_path: Path) -> None:
        if is_root():
            pytest.skip("root ignores directory permissions")
        tmp_path.chmod(0o500)
        try:
            assert control_problem(tmp_path) == REASON_NO_CONTROL
        finally:
            tmp_path.chmod(0o700)

    def test_not_searchable(self, tmp_path: Path) -> None:
        if is_root():
            pytest.skip("root ignores directory permissions")
        tmp_path.chmod(0o600)
        try:
            assert control_problem(tmp_path) == REASON_NO_CONTROL
        finally:
            tmp_path.chmod(0o700)


class TestModeFilePresent:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert mode_file_present(tmp_path) is False

    def test_missing_dir(self, tmp_path: Path) -> None:
        assert mode_file_present(tmp_path / "none") is False

    def test_file_in_place_of_dir(self, tmp_path: Path) -> None:
        path = tmp_path / "file"
        path.write_text("x")
        assert mode_file_present(path) is False

    def test_file_present(self, tmp_path: Path) -> None:
        (tmp_path / MODE_FILE_NAME).write_bytes(b"full 1\n")
        assert mode_file_present(tmp_path) is True

    def test_dangling_symlink_present(self, tmp_path: Path) -> None:
        (tmp_path / MODE_FILE_NAME).symlink_to(tmp_path / "gone")
        assert mode_file_present(tmp_path) is True

    def test_unsearchable_dir_assumes_present(self, tmp_path: Path) -> None:
        """Without search permission the file may exist, so the caller assumes the worst."""
        if is_root():
            pytest.skip("root ignores directory permissions")
        tmp_path.chmod(0o600)
        try:
            assert mode_file_present(tmp_path) is True
        finally:
            tmp_path.chmod(0o700)
