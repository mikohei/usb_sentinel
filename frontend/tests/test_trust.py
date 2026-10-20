"""Tests for trust.py: the per-boot input baseline and the persistent strike count (design 3.7)."""

import os
import stat
from pathlib import Path

import pytest

from usb_sentinel_display.libs import trust
from usb_sentinel_display.libs.trust import (
    BASELINE_FILE_MODE,
    BASELINE_FILE_NAME,
    MAX_BASELINE_BYTES,
    MAX_STRIKE_BYTES,
    MAX_STRIKES,
    STRIKE_DIR_NAME,
    STRIKE_FILE_MODE,
    STRIKE_FILE_NAME,
    load_or_create_baseline,
    read_strikes,
    strike_file,
    write_strikes,
)

CURRENT = frozenset({"input10", "input2", "input0"})


def is_root() -> bool:
    return os.geteuid() == 0


def leftovers(directory: Path, prefix: str) -> list[str]:
    return [p.name for p in directory.iterdir() if p.name.startswith(prefix)]


class TestConstants:
    def test_values(self) -> None:
        assert BASELINE_FILE_NAME == "input-baseline"
        assert STRIKE_DIR_NAME == "usb-sentinel-display"
        assert STRIKE_FILE_NAME == "erase-strikes"
        assert MAX_STRIKES == 3
        assert MAX_BASELINE_BYTES == 8192
        assert MAX_STRIKE_BYTES == 16
        assert BASELINE_FILE_MODE == 0o640
        assert STRIKE_FILE_MODE == 0o600


class TestStrikeFile:
    def test_honours_xdg_state_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert strike_file() == tmp_path / STRIKE_DIR_NAME / STRIKE_FILE_NAME

    def test_unset_falls_back_to_local_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        expected = Path.home() / ".local" / "state" / STRIKE_DIR_NAME / STRIKE_FILE_NAME
        assert strike_file() == expected

    def test_empty_falls_back_to_local_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", "")
        expected = Path.home() / ".local" / "state" / STRIKE_DIR_NAME / STRIKE_FILE_NAME
        assert strike_file() == expected


class TestBaseline:
    def test_first_call_creates_sorted_file(self, tmp_path: Path) -> None:
        """Names are sorted by number (input2 before input10), mode 0640, no temp leftovers."""
        assert load_or_create_baseline(tmp_path, CURRENT) == CURRENT
        path = tmp_path / BASELINE_FILE_NAME
        assert path.read_bytes() == b"input0\ninput2\ninput10\n"
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
        assert leftovers(tmp_path, ".baseline-") == []

    def test_second_call_returns_first_set(self, tmp_path: Path) -> None:
        """A later start with more devices reads the baseline and does not overwrite it."""
        load_or_create_baseline(tmp_path, CURRENT)
        before = (tmp_path / BASELINE_FILE_NAME).read_bytes()
        assert load_or_create_baseline(tmp_path, CURRENT | {"input5"}) == CURRENT
        assert (tmp_path / BASELINE_FILE_NAME).read_bytes() == before

    def test_empty_set(self, tmp_path: Path) -> None:
        assert load_or_create_baseline(tmp_path, frozenset()) == frozenset()
        assert (tmp_path / BASELINE_FILE_NAME).read_bytes() == b""
        assert load_or_create_baseline(tmp_path, frozenset({"input3"})) == frozenset()

    def test_race_returns_existing_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Another instance wins the os.link race: its content is returned, ours is dropped."""
        path = tmp_path / BASELINE_FILE_NAME

        def racing_link(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
            path.write_bytes(b"input7\n")
            raise FileExistsError(str(dst))

        monkeypatch.setattr(trust.os, "link", racing_link)
        assert load_or_create_baseline(tmp_path, CURRENT) == frozenset({"input7"})
        assert path.read_bytes() == b"input7\n"
        assert leftovers(tmp_path, ".baseline-") == []

    @pytest.mark.parametrize(
        "content",
        [
            b"event3\n",
            b"input\n",
            b"input1\ninput1\n",
            b"input1",
            b"input1\r\ninput2\n",
            b"input1\n\xff\n",
            b"input2\ninput1\n",
            b"input1\n\n",
            b"input1234567\n",
            b"input1 \n",
        ],
    )
    def test_invalid_content_gives_none_and_keeps_file(
        self, tmp_path: Path, content: bytes
    ) -> None:
        path = tmp_path / BASELINE_FILE_NAME
        path.write_bytes(content)
        assert load_or_create_baseline(tmp_path, CURRENT) is None
        assert path.read_bytes() == content

    def test_too_large_gives_none(self, tmp_path: Path) -> None:
        path = tmp_path / BASELINE_FILE_NAME
        names = "".join(f"input{i}\n" for i in range(100000, 100000 + 1200))
        content = names.encode("ascii")
        assert len(content) > MAX_BASELINE_BYTES
        path.write_bytes(content)
        assert load_or_create_baseline(tmp_path, CURRENT) is None
        assert path.read_bytes() == content

    def test_exactly_max_bytes_is_valid(self, tmp_path: Path) -> None:
        path = tmp_path / BASELINE_FILE_NAME
        # "input12\n" (8 bytes) + 682 six-digit names (12 bytes each) = 8192 bytes.
        names = ["input12", *(f"input{i}" for i in range(100000, 100000 + 682))]
        content = "".join(f"{n}\n" for n in names).encode("ascii")
        assert len(content) == MAX_BASELINE_BYTES
        path.write_bytes(content)
        assert load_or_create_baseline(tmp_path, CURRENT) == frozenset(names)

    def test_symlink_gives_none(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.write_bytes(b"input1\n")
        (tmp_path / BASELINE_FILE_NAME).symlink_to(target)
        assert load_or_create_baseline(tmp_path, CURRENT) is None
        assert target.read_bytes() == b"input1\n"

    def test_directory_gives_none(self, tmp_path: Path) -> None:
        (tmp_path / BASELINE_FILE_NAME).mkdir()
        assert load_or_create_baseline(tmp_path, CURRENT) is None

    def test_unwritable_dir_gives_none(self, tmp_path: Path) -> None:
        if is_root():
            pytest.skip("root ignores directory permissions")
        tmp_path.chmod(0o500)
        try:
            assert load_or_create_baseline(tmp_path, CURRENT) is None
        finally:
            tmp_path.chmod(0o700)
        assert not (tmp_path / BASELINE_FILE_NAME).exists()

    def test_missing_dir_gives_none(self, tmp_path: Path) -> None:
        assert load_or_create_baseline(tmp_path / "none", CURRENT) is None

    def test_current_with_bad_name_gives_none(self, tmp_path: Path) -> None:
        """A name outside the pattern cannot be written into a valid file."""
        assert load_or_create_baseline(tmp_path, frozenset({"event3"})) is None
        assert not (tmp_path / BASELINE_FILE_NAME).exists()


class TestReadStrikes:
    def test_missing_is_zero(self, tmp_path: Path) -> None:
        assert read_strikes(tmp_path / "erase-strikes") == 0

    def test_missing_parent_is_zero(self, tmp_path: Path) -> None:
        assert read_strikes(tmp_path / "none" / "erase-strikes") == 0

    @pytest.mark.parametrize(("content", "count"), [(b"0\n", 0), (b"2\n", 2), (b"999\n", 999)])
    def test_reads_back(self, tmp_path: Path, content: bytes, count: int) -> None:
        path = tmp_path / "s"
        path.write_bytes(content)
        assert read_strikes(path) == count

    def test_leading_zero(self, tmp_path: Path) -> None:
        path = tmp_path / "s"
        path.write_bytes(b"03\n")
        assert read_strikes(path) == 3

    @pytest.mark.parametrize(
        "content",
        [b"3", b"-1\n", b"1000\n", b"1 \n", b"\n", b"", b"1\n\n", b"0000000000000001\n", b"\xff\n"],
    )
    def test_invalid_is_none(self, tmp_path: Path, content: bytes) -> None:
        path = tmp_path / "s"
        path.write_bytes(content)
        assert read_strikes(path) is None

    def test_symlink_is_none(self, tmp_path: Path) -> None:
        target = tmp_path / "t"
        target.write_bytes(b"1\n")
        (tmp_path / "s").symlink_to(target)
        assert read_strikes(tmp_path / "s") is None

    def test_directory_is_none(self, tmp_path: Path) -> None:
        (tmp_path / "s").mkdir()
        assert read_strikes(tmp_path / "s") is None

    def test_unreadable_is_none(self, tmp_path: Path) -> None:
        path = tmp_path / "s"
        path.write_bytes(b"1\n")
        path.chmod(0)
        try:
            try:
                path.read_bytes()
            except PermissionError:
                pass
            else:
                pytest.skip("chmod 0 does not deny access here")
            assert read_strikes(path) is None
        finally:
            path.chmod(0o600)


class TestWriteStrikes:
    def test_creates_parent_and_file(self, tmp_path: Path) -> None:
        path = tmp_path / "state" / STRIKE_DIR_NAME / STRIKE_FILE_NAME
        assert write_strikes(path, 2) is None
        assert path.read_bytes() == b"2\n"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert leftovers(path.parent, ".erase-strikes-") == []
        assert read_strikes(path) == 2

    def test_replaces_atomically(self, tmp_path: Path) -> None:
        path = tmp_path / "s"
        assert write_strikes(path, 0) is None
        assert write_strikes(path, 999) is None
        assert path.read_bytes() == b"999\n"
        assert leftovers(tmp_path, ".erase-strikes-") == []

    def test_read_only_parent_returns_reason(self, tmp_path: Path) -> None:
        if is_root():
            pytest.skip("root ignores directory permissions")
        tmp_path.chmod(0o500)
        try:
            reason = write_strikes(tmp_path / "s", 1)
        finally:
            tmp_path.chmod(0o700)
        assert reason is not None
        assert "PermissionError" in reason
        assert leftovers(tmp_path, ".erase-strikes-") == []

    def test_parent_is_file_returns_reason(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        reason = write_strikes(blocker / "s", 1)
        assert reason is not None

    @pytest.mark.parametrize("count", [-1, 1000])
    def test_out_of_range_raises(self, tmp_path: Path, count: int) -> None:
        with pytest.raises(ValueError, match="count"):
            write_strikes(tmp_path / "s", count)
        assert not (tmp_path / "s").exists()
