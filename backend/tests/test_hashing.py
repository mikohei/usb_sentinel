"""ハッシュ台帳（hashing.inventory）。

時間上限・読み取り不能ファイルの継続・台帳自体のダイジェストを検証する。
`ctx`フィクスチャのマウント先は`tmp_path/mnt/sda1`、出力先は`tmp_path/out`。
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from usb_sentinel.libs import StepContext, StepStatus
from usb_sentinel.libs.hashing import CHUNK_SIZE, inventory


def _ledger(ctx: StepContext) -> Path:
    return ctx.output_dir / "hash-inventory.tsv"


def _mountpoint(ctx: StepContext) -> Path:
    return ctx.mountpoints[0]


@pytest.fixture
def hctx(ctx: StepContext) -> StepContext:
    """出力先ディレクトリを作成済みにしたStepContext。"""
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    return ctx


class TestInventory:
    def test_empty_mountpoint_passes_with_zero_lines(self, hctx: StepContext) -> None:
        """ファイル0件は正常。0行の台帳を作りPASS（空ドライブでpermittedを偽にしない）。"""
        result = inventory(hctx)

        assert result.status is StepStatus.PASS
        assert result.summary == "0件"
        assert _ledger(hctx).read_text() == ""
        assert result.details["lines"] == 0
        assert result.details["bytes_hashed"] == 0
        assert result.details["digest"] == hashlib.sha256(b"").hexdigest()
        assert result.details["timed_out"] is False
        assert result.details["read_error_count"] == 0
        assert result.details["read_errors"] == []

    def test_multiple_files_lines_and_digests(self, hctx: StepContext) -> None:
        root = _mountpoint(hctx)
        (root / "b.txt").write_bytes(b"bravo")
        (root / "sub").mkdir()
        (root / "sub" / "a.bin").write_bytes(b"alpha")

        result = inventory(hctx)

        assert result.status is StepStatus.PASS
        assert result.summary == "2件"
        lines = _ledger(hctx).read_text(encoding="utf-8").splitlines()
        assert lines == [
            f'{hashlib.sha256(b"bravo").hexdigest()}\t"0/b.txt"',
            f'{hashlib.sha256(b"alpha").hexdigest()}\t"0/sub/a.bin"',
        ]
        assert result.details["lines"] == 2
        assert result.details["bytes_hashed"] == len(b"bravo") + len(b"alpha")

    def test_large_file_is_read_in_chunks(self, hctx: StepContext) -> None:
        """1MiB超のファイルはチャンクで読み、bytes_hashedは実サイズになる。"""
        payload = bytes(range(256)) * ((CHUNK_SIZE // 256) + 3)
        assert len(payload) > CHUNK_SIZE
        (_mountpoint(hctx) / "big.bin").write_bytes(payload)

        result = inventory(hctx)

        assert result.status is StepStatus.PASS
        assert result.details["bytes_hashed"] == len(payload)
        line = _ledger(hctx).read_text().splitlines()[0]
        assert line.split("\t")[0] == hashlib.sha256(payload).hexdigest()

    def test_budget_exceeded_keeps_completed_rows(self, hctx: StepContext) -> None:
        """時間上限を超えたら途中のファイルは書かず、それまでの行を残してERROR。"""
        root = _mountpoint(hctx)
        (root / "a.txt").write_bytes(b"a")
        (root / "b.txt").write_bytes(b"b")
        (root / "c.txt").write_bytes(b"c")
        hctx.config = replace(hctx.config, hash_inventory_timeout_s=10.0)
        # Budget生成→1ファイル目のループ先頭→チャンク確認→2ファイル目のループ先頭は時間内、
        # 2ファイル目のチャンク確認で上限超過にする。
        clock = iter([0.0, 0.0, 0.0, 0.0, 11.0, 11.0])

        result = inventory(hctx, monotonic=lambda: next(clock))

        assert result.status is StepStatus.ERROR
        assert result.summary == "タイムアウト: 1件まで作成"
        assert result.details["timed_out"] is True
        assert result.details["lines"] == 1
        text = _ledger(hctx).read_text()
        assert '"0/a.txt"' in text
        assert '"0/b.txt"' not in text
        assert '"0/c.txt"' not in text

    def test_budget_exceeded_before_first_file_writes_nothing(self, hctx: StepContext) -> None:
        """0バイトファイルはチャンク確認を通らないので、ループ先頭の予算確認で止める。"""
        root = _mountpoint(hctx)
        (root / "a.txt").write_bytes(b"")
        (root / "b.txt").write_bytes(b"")
        (root / "c.txt").write_bytes(b"")
        hctx.config = replace(hctx.config, hash_inventory_timeout_s=10.0)
        # Budget生成で1回、1ファイル目のループ先頭で1回（ここで上限超過）。
        clock = iter([0.0, 11.0, 11.0, 11.0, 11.0, 11.0])

        result = inventory(hctx, monotonic=lambda: next(clock))

        assert result.status is StepStatus.ERROR
        assert result.summary == "タイムアウト: 0件まで作成"
        assert result.details["timed_out"] is True
        assert result.details["lines"] == 0
        assert _ledger(hctx).read_text() == ""

    def test_unreadable_file_is_skipped_and_reported(
        self, hctx: StepContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """読めないファイルは飛ばして記録し、他のファイルは台帳に残す。最終状態はERROR。"""
        root = _mountpoint(hctx)
        (root / "ok.txt").write_bytes(b"ok")
        (root / "locked.txt").write_bytes(b"secret")
        original_open = Path.open

        def fake_open(self: Path, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 -- Path.openの引き渡し
            if self.name == "locked.txt":
                raise PermissionError(13, "Permission denied", str(self))
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fake_open)

        result = inventory(hctx)

        assert result.status is StepStatus.ERROR
        assert result.summary == "読み取り不能ファイル1件"
        assert result.details["read_error_count"] == 1
        assert result.details["read_errors"] == [{"path": "0/locked.txt", "errno": 13}]
        assert result.details["lines"] == 1
        assert '"0/ok.txt"' in _ledger(hctx).read_text()
        assert "locked" not in _ledger(hctx).read_text()

    def test_symlinks_are_excluded(self, hctx: StepContext) -> None:
        root = _mountpoint(hctx)
        (root / "real.txt").write_bytes(b"real")
        (root / "link.txt").symlink_to(root / "real.txt")

        result = inventory(hctx)

        assert result.details["lines"] == 1
        assert "link.txt" not in _ledger(hctx).read_text()

    def test_digest_matches_ledger_file(self, hctx: StepContext) -> None:
        """details.digestは台帳ファイル自体のSHA-256（改ざん検知の連鎖の起点）。"""
        root = _mountpoint(hctx)
        (root / "x.txt").write_bytes(b"x")
        (root / "y.txt").write_bytes(b"y")

        result = inventory(hctx)

        assert result.details["digest"] == hashlib.sha256(_ledger(hctx).read_bytes()).hexdigest()

    def test_timeout_s_records_configured_value(self, hctx: StepContext) -> None:
        hctx.config = replace(hctx.config, hash_inventory_timeout_s=123.0)

        result = inventory(hctx)

        assert result.details["timeout_s"] == 123.0

    def test_unwritable_output_dir_is_error(self, ctx: StepContext) -> None:
        """台帳ファイル自体を開けないときは全体ERROR（現行どおり）。"""
        assert not ctx.output_dir.exists()

        result = inventory(ctx)

        assert result.status is StepStatus.ERROR
        assert "digest" not in result.details
