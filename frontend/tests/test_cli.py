from pathlib import Path

import pytest

from tests.conftest import FakeSysfs
from usb_sentinel_display.libs import cli as cli_mod
from usb_sentinel_display.libs.cli import main
from usb_sentinel_display.libs.config import DisplayConfig


class TestParser:
    def test_help_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as info:
            main(["--help"])
        assert info.value.code == 0
        assert "calibrate" in capsys.readouterr().out

    def test_windowed_and_kiosk_exclusive(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as info:
            main(["run", "--windowed", "--kiosk"])
        assert info.value.code == 2
        capsys.readouterr()

    def test_run_calls_run_app(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """runはCLI値で上書きした設定とオプション、runnerをrun_appに渡す。"""
        from usb_sentinel_display.libs.ui import app as app_mod

        calls: list[tuple[object, object, object]] = []

        def fake_run_app(config: object, options: object, runner: object) -> int:
            calls.append((config, options, runner))
            return 0

        monkeypatch.setattr(app_mod, "run_app", fake_run_app)
        code = main(
            [
                "--config",
                str(tmp_path / "none.toml"),
                "run",
                "--windowed",
                "--no-systemctl",
                "--results-dir",
                str(tmp_path / "results"),
            ]
        )
        assert code == 0
        config, options, runner = calls[0]
        assert isinstance(config, DisplayConfig)
        assert config.results_dir == tmp_path / "results"
        assert options == app_mod.AppOptions(windowed=True, kiosk=False)
        assert runner is None

    def test_run_without_no_systemctl_uses_subprocess_runner(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """--no-systemctlが無ければSubprocessRunnerを渡す。"""
        from usb_sentinel_display.libs.ui import app as app_mod

        seen: list[object] = []
        monkeypatch.setattr(
            app_mod, "run_app", lambda config, options, runner: seen.append(runner) or 0
        )
        assert main(["--config", str(tmp_path / "none.toml"), "run", "--kiosk"]) == 0
        assert isinstance(seen[0], cli_mod.SubprocessRunner)


class TestConfigErrors:
    def test_invalid_config_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config = tmp_path / "display.toml"
        config.write_text("poll_interval_s = 99\n", encoding="utf-8")
        assert main(["--config", str(config), "calibrate"]) == 1
        assert "poll_interval_s" in capsys.readouterr().err

    def test_config_is_directory_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--configにディレクトリを渡すとOSErrorを設定エラーとして1を返す。"""
        assert main(["--config", str(tmp_path), "calibrate"]) == 1
        assert "設定エラー" in capsys.readouterr().err


class TestCalibrate:
    def test_prints_new_disks_once(
        self,
        fake_sysfs: FakeSysfs,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """初見のディスクを1回だけ印字し、KeyboardInterruptで0を返す。"""
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) == 2:
                raise KeyboardInterrupt

        monkeypatch.setattr(cli_mod.time, "sleep", fake_sleep)
        code = main(
            [
                "--config",
                str(tmp_path / "none.toml"),
                "calibrate",
                "--sys-block",
                str(fake_sysfs.sys_block),
                "--interval",
                "0.5",
            ]
        )
        assert code == 0
        out = capsys.readouterr().out.splitlines()
        assert out == ["sda 1-1.3 ABC123", "sdb 2-1 XYZ789"]
        assert sleeps == [0.5, 0.5]

    def test_missing_sys_block_reports_and_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """sys_blockが無ければ標準エラーに知らせ、ループは続ける。"""

        def stop(seconds: float) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(cli_mod.time, "sleep", stop)
        code = main(
            [
                "--config",
                str(tmp_path / "none.toml"),
                "calibrate",
                "--sys-block",
                str(tmp_path / "none"),
            ]
        )
        assert code == 0
        assert "sysfs" in capsys.readouterr().err
