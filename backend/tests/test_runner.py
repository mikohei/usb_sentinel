"""`FakeRunner` の挙動。テスト基盤自体が正しく動くことを保証する。"""

from __future__ import annotations

import pytest

from usb_sentinel.libs import CommandNotFoundError, FakeRunner


def test_longer_prefix_wins() -> None:
    """より具体的（長い）prefix の登録が優先される。"""
    fake = FakeRunner()
    fake.register(["clamscan"], returncode=1, stdout="scan output")
    fake.register(["clamscan", "--version"], stdout="ClamAV 1.0.3/27200/Mon Feb 12 09:23:12 2024")

    version = fake.run(["clamscan", "--version"])
    assert version.stdout.startswith("ClamAV")
    assert version.returncode == 0

    scan = fake.run(["clamscan", "--recursive", "/mnt/sda1"])
    assert scan.stdout == "scan output"
    assert scan.returncode == 1


def test_later_registration_overrides_same_prefix() -> None:
    """同じ prefix を再登録すると後の方が勝つ。フィクスチャの既定を上書きするため。"""
    fake = FakeRunner()
    fake.register(["lsblk"], stdout="first")
    fake.register(["lsblk"], stdout="second")

    assert fake.run(["lsblk", "/dev/sda"]).stdout == "second"


def test_returns_actual_argv() -> None:
    """返される CommandResult の argv は実際に呼ばれたものになる。"""
    fake = FakeRunner()
    fake.register(["lsblk"])

    result = fake.run(["lsblk", "--json", "/dev/sda"])
    assert result.argv == ("lsblk", "--json", "/dev/sda")


def test_records_calls_in_order() -> None:
    """呼び出し順序が記録される。オーケストレータの検証に使う。"""
    fake = FakeRunner()
    fake.register(["mount"])
    fake.register(["umount"])

    fake.run(["mount", "-o", "ro", "/dev/sda1", "/mnt"])
    fake.run(["umount", "/mnt"])

    assert [call[0] for call in fake.calls] == ["mount", "umount"]


def test_missing_command_raises() -> None:
    """`missing` に入れたコマンドは which() が None を返し、実行で例外になる。"""
    fake = FakeRunner(missing={"f3probe"})
    fake.register(["f3probe"])

    assert fake.which("f3probe") is None
    assert fake.which("clamscan") == "/usr/bin/clamscan"
    with pytest.raises(CommandNotFoundError):
        fake.run(["f3probe", "/dev/sda"])


def test_unregistered_command_raises() -> None:
    """未登録のコマンドは黙って成功させず、テストを失敗させる。"""
    fake = FakeRunner()
    with pytest.raises(AssertionError, match="unregistered"):
        fake.run(["fio", "--version"])


def test_check_raises_on_failure() -> None:
    """`check()` は異常終了で例外を投げる。"""
    fake = FakeRunner()
    fake.register(["lsblk"], returncode=32, stderr="no such device")

    result = fake.run(["lsblk", "/dev/sdz"])
    assert not result.ok
    with pytest.raises(RuntimeError, match="no such device"):
        result.check()


def test_timed_out_is_not_ok() -> None:
    """タイムアウトは returncode に関わらず失敗として扱う。"""
    fake = FakeRunner()
    fake.register(["clamscan"], returncode=0, timed_out=True)

    assert not fake.run(["clamscan", "/mnt"]).ok
