"""scripts/clamav-db.sh (sourced with a fake docker) and its wiring in install-all.sh."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.test_install_kiosk import LOG, run_install_all_to_end
from tests.test_run_docker import bash_path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
PRELUDE = 'set -euo pipefail; source "$1"; shift; "$@"'
FILES = ("main.cvd", "daily.cvd", "bytecode.cvd")


def _fake_docker(tmp_path: Path, body: str) -> Path:
    """Write a fake docker that logs its calls and then runs body."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text(f'#!/bin/sh\necho "docker $*" >> "{tmp_path}/calls.log"\n{body}\n')
    docker.chmod(0o755)
    return bindir


def _call(tmp_path: Path, bindir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [bash_path(), "-c", PRELUDE, "_", str(SCRIPTS / "clamav-db.sh"), *args],
        capture_output=True,
        text=True,
        env={"PATH": f"{bindir}:/usr/bin:/bin", "CLAMAV_RETRY_DELAY": "0"},
        check=False,
    )


def _calls(tmp_path: Path) -> int:
    log = tmp_path / "calls.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


@pytest.mark.parametrize("suffix", ["cvd", "cld"])
def test_db_present_requires_all_three_non_empty(tmp_path: Path, suffix: str) -> None:
    """main・daily・bytecode が全部そろい空でないときだけ成功する。"""
    db = tmp_path / "db"
    db.mkdir()
    bindir = _fake_docker(tmp_path, "exit 0")
    for name in ("main", "daily"):
        (db / f"{name}.{suffix}").write_text("x")
    assert _call(tmp_path, bindir, "clamav_db_present", str(db)).returncode == 1
    (db / f"bytecode.{suffix}").write_text("")
    # 空ファイルは欠落と同じ扱い
    assert _call(tmp_path, bindir, "clamav_db_present", str(db)).returncode == 1
    (db / f"bytecode.{suffix}").write_text("x")
    assert _call(tmp_path, bindir, "clamav_db_present", str(db)).returncode == 0


def test_fetch_retries_three_times_then_fails(tmp_path: Path) -> None:
    """freshclam が失敗し続けると3回試して失敗を返す。"""
    db = tmp_path / "db"
    db.mkdir()
    bindir = _fake_docker(tmp_path, "exit 1")
    result = _call(tmp_path, bindir, "clamav_fetch", "img", str(db))
    assert result.returncode == 1
    assert _calls(tmp_path) == 3
    assert "freshclam failed 3 times" in result.stderr


def test_fetch_succeeds_on_second_attempt(tmp_path: Path) -> None:
    """2回目で成功しDBができれば成功を返す。"""
    db = tmp_path / "db"
    db.mkdir()
    marker = tmp_path / "tried"
    files = " ".join(f'"{db}/{name}"' for name in FILES)
    body = (
        f'if [ ! -e "{marker}" ]; then touch "{marker}"; exit 1; fi\n'
        f"for f in {files}; do echo sig > $f; done\nexit 0"
    )
    bindir = _fake_docker(tmp_path, body)
    result = _call(tmp_path, bindir, "clamav_fetch", "img", str(db))
    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path) == 2


def test_fetch_success_without_files_fails(tmp_path: Path) -> None:
    """freshclam が0で終わってもDBが無ければ失敗とする。"""
    db = tmp_path / "db"
    db.mkdir()
    bindir = _fake_docker(tmp_path, "exit 0")
    assert _call(tmp_path, bindir, "clamav_fetch", "img", str(db)).returncode == 1
    assert _calls(tmp_path) == 1


def test_update_clamav_script_sources_shared_lib() -> None:
    """update-clamav.sh は共有関数を使い、freshclam の呼び出しを重複させない。"""
    text = (SCRIPTS / "update-clamav.sh").read_text()
    assert 'source "${REPO_DIR}/scripts/clamav-db.sh"' in text
    assert 'clamav_fetch "${IMAGE}" "${CLAMAV_DB_DIR}"' in text
    assert "clamav_die_hint" in text
    assert "--entrypoint freshclam" not in text
    assert "--entrypoint freshclam" not in (SCRIPTS / "install-all.sh").read_text()
    assert (SCRIPTS / "update-clamav.sh").stat().st_mode & 0o111


def test_install_all_dies_when_database_stays_missing(tmp_path: Path) -> None:
    """DBが取得できないとき install-all.sh は再実行コマンドを示して止まる。"""
    run = run_install_all_to_end(
        tmp_path,
        "",
        {"INSTALL_YES": "1", "CLAMAV_DB_DIR": str(tmp_path / "empty-clamav")},
        extra_fakes={"docker": LOG.format(name="docker") + "\nexit 1"},
    )
    assert run.code == 1
    assert "ClamAV signature database missing" in run.output
    assert "scripts/update-clamav.sh" in run.output
    assert run.log.count("--entrypoint freshclam") == 3
    assert "9/10" not in run.output
