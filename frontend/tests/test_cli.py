import logging
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
        """run passes the CLI-overridden config, options, and runner to run_app."""
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
        """Without --no-systemctl, a SubprocessRunner is passed."""
        from usb_sentinel_display.libs.ui import app as app_mod

        seen: list[object] = []
        monkeypatch.setattr(
            app_mod, "run_app", lambda config, options, runner: seen.append(runner) or 0
        )
        assert main(["--config", str(tmp_path / "none.toml"), "run", "--kiosk"]) == 0
        assert isinstance(seen[0], cli_mod.SubprocessRunner)


class TestConfigErrors:
    def test_invalid_config_returns_one(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A config error is logged at ERROR and returns 1."""
        config = tmp_path / "display.toml"
        config.write_text("poll_interval_s = 99\n", encoding="utf-8")
        assert main(["--config", str(config), "calibrate"]) == 1
        (record,) = caplog.records
        assert record.name == "usb_sentinel_display.libs.cli"
        assert record.levelno == logging.ERROR
        assert record.getMessage().startswith("Configuration error: ")
        assert "poll_interval_s" in record.getMessage()

    def test_config_is_directory_returns_one(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Passing a directory to --config returns 1, treating the OSError as a config error."""
        assert main(["--config", str(tmp_path), "calibrate"]) == 1
        (record,) = caplog.records
        assert record.levelno == logging.ERROR
        assert "Configuration error" in record.getMessage()


class TestCalibrate:
    def test_prints_new_disks_once(
        self,
        fake_sysfs: FakeSysfs,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """Print each newly seen disk exactly once, and return 0 on KeyboardInterrupt."""
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
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """If sys_block is missing, log a warning and keep looping; stdout stays empty."""

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
        (record,) = caplog.records
        assert record.levelno == logging.WARNING
        assert "sysfs" in record.getMessage()
        assert capsys.readouterr().out == ""
