"""Step 5 of install-all.sh: the frontend venv on the system Python and the Xft check."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from tests.test_install_kiosk import InstallRun, run_install_all_to_end

# Fake uv: logs argv with UV_PYTHON_PREFERENCE and prints $FAKE_TKINTER for the _tkinter probe.
UV_TK = (
    "#!/bin/sh\n"
    'echo "uv $* pref=${UV_PYTHON_PREFERENCE:-}" >> "$FAKE_LOG"\n'
    '[ "$1" = --version ] && echo "uv 0.0"\n'
    'case "$*" in *_tkinter*) echo "$FAKE_TKINTER";; esac\n'
    "exit 0\n"
)
LDD_NO_XFT = {"ldd": 'echo "libtk8.6.so => /usr/lib/libtk8.6.so"'}
LDD_XFT = {"ldd": 'echo "libXft.so.2 => /usr/lib/libXft.so.2"'}


def _venv(home_line: str) -> Callable[[InstallRun], None]:
    """pyvenv.cfg を指定の home 行で置く before フックを返す。

    Args:
        home_line: pyvenv.cfg の home 行。

    Returns:
        run_install_all_to_end の before に渡す関数。
    """

    def write(run: InstallRun) -> None:
        venv = run.repo / "frontend" / ".venv"
        venv.mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text(f"{home_line}\nversion_info = 3.14.4\n")

    return write


def _tkinter(tmp_path: Path, body: str) -> dict[str, str]:
    """偽の _tkinter ファイルを作り、その場所を FAKE_TKINTER で渡す環境を返す。

    Args:
        tmp_path: テスト用ディレクトリ。
        body: ファイルの中身。

    Returns:
        install-all.sh に渡す環境変数。
    """
    path = tmp_path / "_tkinter.so"
    path.write_text(body)
    return {"INSTALL_YES": "1", "FAKE_TKINTER": str(path)}


def test_sync_uses_system_python_only(tmp_path: Path) -> None:
    """uv sync はシステムの Python だけを使う指定で走る。"""
    env = _tkinter(tmp_path, "XftDrawString")
    run = run_install_all_to_end(tmp_path, "", env, uv_fake=UV_TK, extra_fakes=LDD_NO_XFT)
    assert run.code == 0, run.output
    sync = next(line for line in run.log.splitlines() if line.startswith("uv sync"))
    assert "--frozen" in sync
    assert "--python /usr/bin/python3" in sync
    assert sync.endswith("pref=only-system")


def test_venv_on_uv_python_is_recreated(tmp_path: Path) -> None:
    """uv 管理の Python で作った venv は sync の前に消す。"""
    env = _tkinter(tmp_path, "XftDrawString")
    before = _venv("home = /home/kiosk/.local/share/uv/python/cpython-3.13-linux/bin")
    run = run_install_all_to_end(
        tmp_path, "", env, uv_fake=UV_TK, extra_fakes=LDD_NO_XFT, before=before
    )
    assert run.code == 0, run.output
    assert not (run.repo / "frontend" / ".venv").exists()
    assert "frontend venv: recreating on the system Python" in run.output


def test_venv_on_system_python_is_kept(tmp_path: Path) -> None:
    """システムの Python で作った venv はそのまま使う。"""
    env = _tkinter(tmp_path, "XftDrawString")
    run = run_install_all_to_end(
        tmp_path, "", env, uv_fake=UV_TK, extra_fakes=LDD_NO_XFT, before=_venv("home = /usr/bin")
    )
    assert run.code == 0, run.output
    assert (run.repo / "frontend" / ".venv" / "pyvenv.cfg").is_file()
    assert "recreating" not in run.output


def test_xft_found_via_ldd(tmp_path: Path) -> None:
    """_tkinter が libXft を読み込むなら xft と表示する（画面は不要）。"""
    env = _tkinter(tmp_path, "no symbols")
    run = run_install_all_to_end(tmp_path, "", env, uv_fake=UV_TK, extra_fakes=LDD_XFT)
    assert run.code == 0, run.output
    assert "Tk font system: xft" in run.output
    assert "NO Xft" not in run.output


def test_xft_found_via_symbol(tmp_path: Path) -> None:
    """Tk を静的に含む _tkinter に Xft の記号があれば xft と表示する。"""
    env = _tkinter(tmp_path, "...XftDrawString...")
    run = run_install_all_to_end(tmp_path, "", env, uv_fake=UV_TK, extra_fakes=LDD_NO_XFT)
    assert run.code == 0, run.output
    assert "Tk font system: xft" in run.output


def test_missing_xft_warns_loudly(tmp_path: Path) -> None:
    """Xft が無ければ、文字化けと直し方を複数行で警告する。導入は止めない。"""
    env = _tkinter(tmp_path, "no symbols")
    run = run_install_all_to_end(tmp_path, "", env, uv_fake=UV_TK, extra_fakes=LDD_NO_XFT)
    assert run.code == 0, run.output
    assert "Tk font system: NO Xft" in run.output
    assert "Japanese text will show as boxes" in run.output
    assert "python3-tk" in run.output
