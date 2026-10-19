import builtins
import os
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import PCIE, FakeSysfs
from usb_sentinel_display.libs import sysfs as sysfs_mod
from usb_sentinel_display.libs.sysfs import (
    KERNEL_NAME_RE,
    SysfsMissingError,
    SysfsPermissionError,
    boot_time,
    list_usb_disks,
    sanitize_text,
)


def fixed_fallback(_kernel: str) -> float:
    """テスト用の固定フォールバック時刻。"""
    return 12345.0


class TestSanitizeText:
    def test_bytes_control_chars(self) -> None:
        """改行・タブ・制御文字は?になり前後の空白は落ちる。"""
        assert sanitize_text(b" AB\nC\tD\x01E \n") == "AB?C?D?E"

    def test_str_input(self) -> None:
        """strも同じ規則で処理する。"""
        assert sanitize_text("A\rB") == "A?B"

    def test_truncates_bytes(self) -> None:
        """bytesは先頭limitバイトだけ使う。"""
        assert sanitize_text(b"a" * 300) == "a" * 256
        assert sanitize_text(b"a" * 300, limit=10) == "a" * 10

    def test_truncates_str(self) -> None:
        """strは先頭limit文字だけ使う。"""
        assert sanitize_text("あ" * 300) == "あ" * 256

    def test_invalid_utf8(self) -> None:
        """不正なUTF-8は置換文字になり例外にならない。"""
        assert sanitize_text(b"A\xffB") == "A\ufffdB"

    def test_keeps_printable_non_ascii(self) -> None:
        """全角文字など印字可能な非ASCIIは残す。"""
        assert sanitize_text("ＡＢＣ") == "ＡＢＣ"

    def test_format_characters_replaced(self) -> None:
        """ゼロ幅スペースなどの書式文字（isprintableでない）は?になる。"""
        assert sanitize_text("A\u200bB") == "A?B"


class TestKernelNameRe:
    @pytest.mark.parametrize("name", ["sda", "sdz", "sdaa", "sdabc"])
    def test_accepts(self, name: str) -> None:
        assert KERNEL_NAME_RE.fullmatch(name)

    @pytest.mark.parametrize("name", ["sda1", "sr0", "sdA", "sdabcd", "sd", "../sda", "sda; rm"])
    def test_rejects(self, name: str) -> None:
        assert KERNEL_NAME_RE.fullmatch(name) is None


class TestListUsbDisks:
    def test_lists_usb_only(self, fake_sysfs: FakeSysfs) -> None:
        """mmcblk0は除外され、USBの2台がカーネル名順に並ぶ。"""
        disks = list_usb_disks(fake_sysfs.sys_block, fallback_time=fixed_fallback)
        assert [d.kernel_name for d in disks] == ["sda", "sdb"]
        assert disks[0].port_path == "1-1.3"
        assert disks[0].serial == "ABC123"
        assert disks[1].port_path == "2-1"
        assert disks[1].serial == "XYZ789"

    def test_attached_at_is_port_dir_mtime(self, fake_sysfs: FakeSysfs) -> None:
        """attached_atはポートディレクトリのst_mtime。"""
        os.utime(fake_sysfs.sda_port_dir, (1000.0, 1000.0))
        os.utime(fake_sysfs.sdb_port_dir, (2000.0, 2000.0))
        disks = list_usb_disks(fake_sysfs.sys_block, fallback_time=fixed_fallback)
        assert disks[0].attached_at == 1000.0
        assert disks[1].attached_at == 2000.0

    def test_missing_serial_is_empty(self, fake_sysfs: FakeSysfs) -> None:
        """serialファイルが無ければ空文字。"""
        (fake_sysfs.sda_port_dir / "serial").unlink()
        disks = list_usb_disks(fake_sysfs.sys_block, fallback_time=fixed_fallback)
        assert disks[0].serial == ""

    def test_serial_sanitized_and_truncated(self, fake_sysfs: FakeSysfs) -> None:
        """制御文字入り・256バイト超・不正UTF-8のserialでも落ちない。"""
        (fake_sysfs.sda_port_dir / "serial").write_bytes(b"A\tB\x00C\xff" + b"x" * 300)
        disks = list_usb_disks(fake_sysfs.sys_block, fallback_time=fixed_fallback)
        serial = disks[0].serial
        assert serial.startswith("A?B?C\ufffd")
        assert len(serial) <= 256

    def test_non_sd_names_excluded(self, fake_sysfs: FakeSysfs) -> None:
        """sdz1、sr0、nvme0n1のような名前は無視する。"""
        for name in ("sdz1", "sr0", "nvme0n1"):
            (fake_sysfs.sys_block / name).symlink_to(fake_sysfs.root / PCIE / "usb1/1-1/1-1.3")
        disks = list_usb_disks(fake_sysfs.sys_block, fallback_time=fixed_fallback)
        assert [d.kernel_name for d in disks] == ["sda", "sdb"]

    def test_non_usb_sd_excluded(self, fake_sysfs: FakeSysfs) -> None:
        """USBバスを通らないsdXは無視する。"""
        target = fake_sysfs.root / "devices/platform/sata/host9/target9:0:0/9:0:0:0/block/sdc"
        target.mkdir(parents=True)
        (fake_sysfs.sys_block / "sdc").symlink_to(target)
        disks = list_usb_disks(fake_sysfs.sys_block, fallback_time=fixed_fallback)
        assert [d.kernel_name for d in disks] == ["sda", "sdb"]

    def test_dangling_link_ignored(self, fake_sysfs: FakeSysfs) -> None:
        """リンク切れのエントリは無視し、他は列挙する。"""
        (fake_sysfs.sys_block / "sdc").symlink_to(fake_sysfs.root / "devices/gone/usb1/block/sdc")
        disks = list_usb_disks(fake_sysfs.sys_block, fallback_time=fixed_fallback)
        assert [d.kernel_name for d in disks] == ["sda", "sdb"]

    def test_unreadable_serial_entry_ignored(
        self, fake_sysfs: FakeSysfs, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """serialの読み取りがOSErrorになるエントリだけ無視する。"""
        bad = fake_sysfs.sda_port_dir / "serial"
        original_open = builtins.open

        def failing_open(file: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            if Path(str(file)) == bad:
                raise PermissionError(str(file))
            return original_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", failing_open)
        disks = list_usb_disks(fake_sysfs.sys_block, fallback_time=fixed_fallback)
        assert [d.kernel_name for d in disks] == ["sdb"]

    def test_stat_failure_uses_fallback(
        self, fake_sysfs: FakeSysfs, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ポートディレクトリのst_mtimeが取れなければfallback_timeの値。"""
        original_stat = os.stat
        seen: list[str] = []

        def failing_stat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:  # noqa: ANN401
            if Path(str(path)) == fake_sysfs.sda_port_dir:
                raise OSError("stat failed")
            return original_stat(path, *args, **kwargs)

        def fallback(kernel: str) -> float:
            seen.append(kernel)
            return 777.0

        monkeypatch.setattr(sysfs_mod.os, "stat", failing_stat)
        disks = list_usb_disks(fake_sysfs.sys_block, fallback_time=fallback)
        assert disks[0].attached_at == 777.0
        assert seen == ["sda"]

    def test_missing_sys_block(self, tmp_path: Path) -> None:
        """sys_blockが無ければSysfsMissingError。"""
        with pytest.raises(SysfsMissingError):
            list_usb_disks(tmp_path / "none", fallback_time=fixed_fallback)

    def test_sys_block_is_file(self, tmp_path: Path) -> None:
        """sys_blockがファイルでもSysfsMissingError。"""
        path = tmp_path / "file"
        path.write_text("x")
        with pytest.raises(SysfsMissingError):
            list_usb_disks(path, fallback_time=fixed_fallback)

    def test_sys_block_permission(self, tmp_path: Path) -> None:
        """読めないsys_blockはSysfsPermissionError。"""
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
            with pytest.raises(SysfsPermissionError):
                list_usb_disks(root, fallback_time=fixed_fallback)
        finally:
            root.chmod(0o700)


class TestBootTime:
    def test_returns_float_or_none(self) -> None:
        """環境により値が無いこともあるが、型はfloatかNone。"""
        value = boot_time()
        assert value is None or isinstance(value, float)
