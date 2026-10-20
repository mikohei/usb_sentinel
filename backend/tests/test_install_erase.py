"""scripts/keyfile.sh and scripts/erase-setting.sh, sourced in bash (macOS bash 3.2 works)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from tests.test_run_docker import bash_path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
EXAMPLE = ROOT / "deploy" / "config.example.toml"
PRELUDE = 'set -euo pipefail; source "$1"; shift; "$@"'
OTHER_ENV = "USB_SENTINEL_IMAGE=usb-sentinel:local\n# keep me\nFOO=bar\n"


def _call(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Source script and run one function with args."""
    return subprocess.run(
        [bash_path(), "-c", PRELUDE, "_", str(SCRIPTS / script), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _etc(tmp_path: Path, config: str | None = None, env: str | None = None) -> Path:
    """Create an etc dir with config.toml (example by default) and docker.env."""
    etc = tmp_path / "etc"
    etc.mkdir()
    if config is None:
        shutil.copy(EXAMPLE, etc / "config.toml")
    else:
        (etc / "config.toml").write_text(config)
    (etc / "docker.env").write_text(
        OTHER_ENV + "USB_SENTINEL_DEVICE_ACCESS=ro\n" if env is None else env
    )
    return etc


def _apply(etc: Path, allow: str) -> subprocess.CompletedProcess[str]:
    return _call("erase-setting.sh", "apply_erase_setting", str(etc), allow)


def _lines(path: Path, key: str) -> list[str]:
    return [line for line in path.read_text().splitlines() if line.startswith(key)]


def test_default_enables_erase_on_fresh_files(tmp_path: Path) -> None:
    """既定（1）で新規の config.toml と docker.env が消去許可になる。"""
    etc = tmp_path / "etc"
    etc.mkdir()
    shutil.copy(EXAMPLE, etc / "config.toml")
    written = _call("erase-setting.sh", "docker_env_write", str(etc / "docker.env"), "img", "rw")
    assert written.returncode == 0, written.stderr
    result = _apply(etc, "1")
    assert result.returncode == 0, result.stderr
    assert _lines(etc / "config.toml", "allow_destructive") == ["allow_destructive = true"]
    assert _lines(etc / "docker.env", "USB_SENTINEL_DEVICE_ACCESS") == [
        "USB_SENTINEL_DEVICE_ACCESS=rw"
    ]
    assert "erase on host: enabled" in result.stdout
    # The fresh docker.env already had rw, so it was not rewritten.
    assert not (etc / "docker.env.bak").exists()


def test_default_enables_erase_on_existing_files_keeps_other_keys(tmp_path: Path) -> None:
    """既存ファイルは2つのキー行だけが変わり、.bak に元の内容が残る。"""
    etc = _etc(tmp_path)
    old_config = (etc / "config.toml").read_text()
    old_env = (etc / "docker.env").read_text()
    assert _apply(etc, "1").returncode == 0
    assert (etc / "config.toml").read_text() == old_config.replace(
        "allow_destructive = false", "allow_destructive = true"
    )
    assert (etc / "docker.env").read_text() == OTHER_ENV + "USB_SENTINEL_DEVICE_ACCESS=rw\n"
    assert (etc / "config.toml.bak").read_text() == old_config
    assert (etc / "docker.env.bak").read_text() == old_env


def test_allow_erase_0_sets_false_and_ro(tmp_path: Path) -> None:
    """INSTALL_ALLOW_ERASE=0 は既存の true/rw を false/ro に戻す。"""
    etc = _etc(
        tmp_path,
        config="allow_destructive = true\n[capacity]\nnominal_bytes = 1\n",
        env=OTHER_ENV + "USB_SENTINEL_DEVICE_ACCESS=rw\n",
    )
    result = _apply(etc, "0")
    assert result.returncode == 0, result.stderr
    assert (etc / "config.toml").read_text() == (
        "allow_destructive = false\n[capacity]\nnominal_bytes = 1\n"
    )
    assert _lines(etc / "docker.env", "USB_SENTINEL_DEVICE_ACCESS") == [
        "USB_SENTINEL_DEVICE_ACCESS=ro"
    ]
    assert "erase on host: disabled" in result.stdout


def test_idempotent_second_run_changes_nothing(tmp_path: Path) -> None:
    """2回目は書き込まず、.bak も最初の内容のまま。"""
    etc = _etc(tmp_path)
    assert _apply(etc, "1").returncode == 0
    snapshot = {p.name: (p.read_text(), p.stat().st_mtime_ns) for p in etc.iterdir()}
    assert _apply(etc, "1").returncode == 0
    assert {p.name: (p.read_text(), p.stat().st_mtime_ns) for p in etc.iterdir()} == snapshot


def test_missing_key_is_inserted_before_first_table(tmp_path: Path) -> None:
    """キーが無い config.toml は最初の表の前に、docker.env は末尾に追加する。"""
    etc = _etc(
        tmp_path,
        config='results_dir = "/r"\n[capacity]\nallow_destructive = false\n',
        env=OTHER_ENV,
    )
    assert _apply(etc, "1").returncode == 0
    # The table-local line is not a top-level key and stays untouched.
    assert (etc / "config.toml").read_text() == (
        'results_dir = "/r"\nallow_destructive = true\n[capacity]\nallow_destructive = false\n'
    )
    assert (etc / "docker.env").read_text() == OTHER_ENV + "USB_SENTINEL_DEVICE_ACCESS=rw\n"


def test_missing_key_without_tables_is_appended(tmp_path: Path) -> None:
    """表の無い config.toml では末尾に追加する。"""
    etc = _etc(tmp_path, config='results_dir = "/r"\n')
    assert _apply(etc, "1").returncode == 0
    assert (etc / "config.toml").read_text() == 'results_dir = "/r"\nallow_destructive = true\n'


def test_invalid_value_dies(tmp_path: Path) -> None:
    """0/1 以外は失敗し、ファイルを変えない。"""
    etc = _etc(tmp_path)
    before = (etc / "config.toml").read_text()
    result = _apply(etc, "yes")
    assert result.returncode == 1
    assert "INSTALL_ALLOW_ERASE must be 0 or 1" in result.stderr
    assert (etc / "config.toml").read_text() == before


def test_set_key_line_keeps_first_backup_in_one_shell(tmp_path: Path) -> None:
    """同じシェルで2回書き換えても .bak は最初の内容を保つ。"""
    path = tmp_path / "f"
    path.write_text("A=1\n")
    script = 'set_key_line "$F" A A=2; set_key_line "$F" A A=3'
    result = subprocess.run(
        [bash_path(), "-c", f'set -eu; source "{SCRIPTS / "keyfile.sh"}"; F="{path}"; {script}'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert path.read_text() == "A=3\n"
    assert (tmp_path / "f.bak").read_text() == "A=1\n"


def test_install_docker_wires_erase_setting() -> None:
    """install-docker.sh が INSTALL_ALLOW_ERASE を既定1で読み、共有関数を呼ぶ。"""
    text = (SCRIPTS / "install-docker.sh").read_text()
    assert 'readonly INSTALL_ALLOW_ERASE="${INSTALL_ALLOW_ERASE:-1}"' in text
    assert 'source "${REPO_DIR}/scripts/erase-setting.sh"' in text
    assert 'apply_erase_setting /etc/usb-sentinel "${INSTALL_ALLOW_ERASE}"' in text
    assert "kept as-is" not in text
    install_all = (SCRIPTS / "install-all.sh").read_text()
    assert "export INSTALL_ALLOW_ERASE\n" in install_all
