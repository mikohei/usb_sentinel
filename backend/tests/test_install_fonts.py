"""Step 2 of install-all.sh: font packages, fc-cache and the text-console font."""

from __future__ import annotations

from pathlib import Path

from tests.test_install_kiosk import LOG, run_install_all_to_end

CONSOLE = 'ACTIVE_CONSOLES="/dev/tty[1-6]"\nCHARMAP="UTF-8"\nFONTFACE="Fixed"\nFONTSIZE="8x16"\n'
FC_CACHE: dict[str, str] = {"fc-cache": LOG.format(name="fc-cache") + "\nexit 0"}


def test_apt_installs_fontconfig_console_setup_and_runs_fc_cache(tmp_path: Path) -> None:
    """apt-get install の後に fc-cache -f が走り、必要なパッケージを入れる。"""
    run = run_install_all_to_end(tmp_path, "", {"INSTALL_YES": "1"}, extra_fakes=FC_CACHE)
    assert run.code == 0, run.output
    install = next(line for line in run.log.splitlines() if line.startswith("apt-get install"))
    for package in ("fonts-noto-cjk", "fontconfig", "console-setup"):
        assert f" {package}" in install
    assert "fonts-noto-cjk-extra" not in install
    assert run.log.index("apt-get install") < run.log.index("fc-cache -f")


def test_console_font_setup_is_idempotent(tmp_path: Path) -> None:
    """2つのキーだけを書き換え、2回目は何も変えない。"""
    console = tmp_path / "console-setup"
    console.write_text(CONSOLE)
    run = run_install_all_to_end(tmp_path, "", {"INSTALL_YES": "1"})
    assert run.code == 0, run.output
    expected = CONSOLE.replace('"Fixed"', '"Terminus"').replace('"8x16"', '"16x32"')
    assert console.read_text() == expected
    assert (tmp_path / "console-setup.bak").read_text() == CONSOLE
    assert "console font: Terminus 16x32" in run.output


def test_console_font_second_run_changes_nothing(tmp_path: Path) -> None:
    """設定済みのファイルは書き込まず .bak も作らない。"""
    console = tmp_path / "console-setup"
    done = CONSOLE.replace('"Fixed"', '"Terminus"').replace('"8x16"', '"16x32"')
    console.write_text(done)
    mtime = console.stat().st_mtime_ns
    run = run_install_all_to_end(tmp_path, "", {"INSTALL_YES": "1"})
    assert run.code == 0, run.output
    assert console.read_text() == done
    assert console.stat().st_mtime_ns == mtime
    assert not (tmp_path / "console-setup.bak").exists()


def test_install_console_font_0_skips(tmp_path: Path) -> None:
    """INSTALL_CONSOLE_FONT=0 ならファイルに触れない。"""
    console = tmp_path / "console-setup"
    console.write_text(CONSOLE)
    run = run_install_all_to_end(tmp_path, "", {"INSTALL_YES": "1", "INSTALL_CONSOLE_FONT": "0"})
    assert run.code == 0, run.output
    assert console.read_text() == CONSOLE
    assert "console font: unchanged (INSTALL_CONSOLE_FONT=0)" in run.output


def test_missing_console_setup_only_warns(tmp_path: Path) -> None:
    """設定ファイルが無くても導入は止まらない。"""
    run = run_install_all_to_end(tmp_path, "", {"INSTALL_YES": "1"})
    assert run.code == 0, run.output
    assert "console font unchanged" in run.output
