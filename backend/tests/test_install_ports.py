"""Step 6 of install-all.sh: the built-in Raspberry Pi 4 port table in display.toml."""

from __future__ import annotations

from pathlib import Path

from tests.test_install_kiosk import InstallRun, run_install_all_to_end

PI4 = "Raspberry Pi 4 Model B Rev 1.5\0"
ACTIVE = '[[ports]]\nlabel = "X1"\npaths = ["9-9"]\n'


def _env(tmp_path: Path, model: str | None, **extra: str) -> dict[str, str]:
    """機種名ファイルを置き、install-all.sh に渡す環境を返す。

    Args:
        tmp_path: テスト用ディレクトリ。
        model: /proc/device-tree/model の中身。None ならファイルを置かない。
        **extra: 追加の環境変数。

    Returns:
        環境変数の辞書。
    """
    path = tmp_path / "model"
    if model is not None:
        path.write_text(model)
    return {"INSTALL_YES": "1", "DEVICE_MODEL_FILE": str(path), **extra}


def _conf(run: InstallRun) -> Path:
    """表示ユーザーの display.toml の場所を返す。

    Args:
        run: 実行結果。

    Returns:
        display.toml のパス。
    """
    return run.home / ".config" / "usb-sentinel" / "display.toml"


def _count(run: InstallRun) -> int:
    """有効な [[ports]] 見出しの数を返す。

    Args:
        run: 実行結果。

    Returns:
        行頭が [[ports]] の行数。
    """
    return sum(line == "[[ports]]" for line in _conf(run).read_text().splitlines())


def test_pi4_appends_table_once(tmp_path: Path) -> None:
    """Pi 4 なら表を 1 回だけ追記し、.bak を残す。"""
    run = run_install_all_to_end(tmp_path, "", _env(tmp_path, PI4))
    assert run.code == 0, run.output
    assert _count(run) == 4
    text = _conf(run).read_text()
    assert 'label = "P1"\npaths = ["1-1.3"]' in text
    assert 'label = "P2"\npaths = ["1-1.1", "2-1"]' in text
    assert 'label = "P3"\npaths = ["1-1.4"]' in text
    assert 'label = "P4"\npaths = ["1-1.2", "2-2"]' in text
    assert "[[ports]]" not in Path(f"{_conf(run)}.bak").read_text().replace("# [[ports]]", "")
    assert "Raspberry Pi 4 layout written" in run.output


def test_pi4_second_run_keeps_single_table(tmp_path: Path) -> None:
    """表がある display.toml には二度と追記しない。"""
    first = run_install_all_to_end(tmp_path / "a", "", _env(tmp_path, PI4))
    assert first.code == 0, first.output
    written = _conf(first).read_text()

    def keep(run: InstallRun) -> None:
        _conf(run).parent.mkdir(parents=True)
        _conf(run).write_text(written)

    second = run_install_all_to_end(tmp_path / "b", "", _env(tmp_path, PI4), before=keep)
    assert second.code == 0, second.output
    assert _conf(second).read_text() == written
    assert "existing [[ports]] table kept" in second.output


def test_other_model_is_untouched(tmp_path: Path) -> None:
    """Pi 4 以外の機種では表を書かない。"""
    run = run_install_all_to_end(tmp_path, "", _env(tmp_path, "Raspberry Pi 5 Model B\0"))
    assert run.code == 0, run.output
    assert _count(run) == 0
    assert not Path(f"{_conf(run)}.bak").exists()
    assert "calibrate needed" in run.output


def test_existing_table_is_untouched(tmp_path: Path) -> None:
    """有効な表がすでにあれば中身を変えない。"""

    def own(run: InstallRun) -> None:
        _conf(run).parent.mkdir(parents=True)
        _conf(run).write_text(ACTIVE)

    run = run_install_all_to_end(tmp_path, "", _env(tmp_path, PI4), before=own)
    assert run.code == 0, run.output
    assert _conf(run).read_text() == ACTIVE


def test_install_ports_0_opts_out(tmp_path: Path) -> None:
    """INSTALL_PORTS=0 なら Pi 4 でも書かない。"""
    run = run_install_all_to_end(tmp_path, "", _env(tmp_path, PI4, INSTALL_PORTS="0"))
    assert run.code == 0, run.output
    assert _count(run) == 0
    assert "INSTALL_PORTS=0" in run.output
