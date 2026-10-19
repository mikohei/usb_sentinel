import subprocess

import pytest

from tests.conftest import FakeRunner
from usb_sentinel_display.libs import units as units_mod
from usb_sentinel_display.libs.units import (
    ACTIVE_STATES,
    CommandOutput,
    SubprocessRunner,
    parse_show_output,
    query_unit_states,
    unit_name,
)

SHOW_ARGV = ["systemctl", "show", "-p", "Id", "-p", "ActiveState"]
TEMPLATE = "usb-sentinel-docker@{kernel}.service"


class TestUnitName:
    def test_formats_template(self) -> None:
        assert unit_name("x@{kernel}.service", "sda") == "x@sda.service"

    def test_other_braces_are_left_as_is(self) -> None:
        """Braces and attribute access other than {kernel} are not interpreted by str.format."""
        assert unit_name("{kernel.__class__}@{kernel}{0}", "sda") == "{kernel.__class__}@sda{0}"

    @pytest.mark.parametrize("kernel", ["sda; rm", "../sda", "sdA", "sda1", ""])
    def test_rejects_invalid_kernel(self, kernel: str) -> None:
        """ValueError when the kernel name fails re-validation."""
        with pytest.raises(ValueError, match="kernel"):
            unit_name(TEMPLATE, kernel)


class TestParseShowOutput:
    def test_two_blocks(self) -> None:
        """Two blocks separated by a blank line become dicts."""
        text = "Id=a.service\nActiveState=active\n\nId=b.service\nActiveState=inactive\n"
        assert parse_show_output(text) == {"a.service": "active", "b.service": "inactive"}

    def test_block_without_active_state_skipped(self) -> None:
        """A block without ActiveState is skipped; the other is parsed."""
        text = "Id=a.service\n\nId=b.service\nActiveState=failed\n"
        assert parse_show_output(text) == {"b.service": "failed"}

    def test_empty(self) -> None:
        assert parse_show_output("") == {}

    def test_value_with_equals(self) -> None:
        """Split on the first = even when the value contains =."""
        text = "Id=a=b.service\nActiveState=active\n"
        assert parse_show_output(text) == {"a=b.service": "active"}


class TestQueryUnitStates:
    def test_active_and_inactive(self) -> None:
        """Query two units in one call; they are matched even if the order differs."""
        runner = FakeRunner()
        runner.register(
            SHOW_ARGV,
            stdout=(
                f"Id={unit_name(TEMPLATE, 'sdb')}\nActiveState=inactive\n\n"
                f"Id={unit_name(TEMPLATE, 'sda')}\nActiveState=active\n"
            ),
        )
        units = [unit_name(TEMPLATE, "sda"), unit_name(TEMPLATE, "sdb")]
        states = query_unit_states(runner, units)
        assert states == {units[0]: "active", units[1]: "inactive"}
        assert runner.calls == [tuple([*SHOW_ARGV, *units])]

    def test_missing_unit_not_in_dict(self) -> None:
        """Units missing from the output are not added to the dict."""
        runner = FakeRunner()
        runner.register(SHOW_ARGV, stdout="Id=a.service\nActiveState=active\n")
        assert query_unit_states(runner, ["a.service", "b.service"]) == {"a.service": "active"}

    def test_timeout_returns_empty(self) -> None:
        runner = FakeRunner()
        runner.register(SHOW_ARGV, timed_out=True)
        assert query_unit_states(runner, ["a.service"]) == {}

    def test_not_found_returns_empty(self) -> None:
        runner = FakeRunner()
        runner.register(SHOW_ARGV, returncode=127)
        assert query_unit_states(runner, ["a.service"]) == {}

    def test_nonzero_returns_empty(self) -> None:
        runner = FakeRunner()
        runner.register(SHOW_ARGV, returncode=1, stdout="Id=a.service\nActiveState=active\n")
        assert query_unit_states(runner, ["a.service"]) == {}

    def test_empty_list_does_not_call(self) -> None:
        runner = FakeRunner()
        assert query_unit_states(runner, []) == {}
        assert runner.calls == []

    def test_timeout_passed(self) -> None:
        """timeout_s is passed to the Runner."""
        seen: list[float] = []

        class Recording:
            def run(self, argv: list[str], *, timeout_s: float) -> CommandOutput:
                seen.append(timeout_s)
                return CommandOutput(0, "", False)

        query_unit_states(Recording(), ["a.service"], timeout_s=3.5)
        assert seen == [3.5]


class TestActiveStates:
    def test_members(self) -> None:
        assert sorted(ACTIVE_STATES) == ["activating", "active"]


class TestSubprocessRunner:
    def test_missing_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing command returns 127 without raising."""

        def raise_not_found(*args: object, **kwargs: object) -> None:
            raise FileNotFoundError("systemctl")

        monkeypatch.setattr(units_mod.subprocess, "run", raise_not_found)
        out = SubprocessRunner().run(["systemctl"], timeout_s=1.0)
        assert out == CommandOutput(127, "", False)

    def test_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_timeout(*args: object, **kwargs: object) -> None:
            raise subprocess.TimeoutExpired(["systemctl"], 1.0)

        monkeypatch.setattr(units_mod.subprocess, "run", raise_timeout)
        out = SubprocessRunner().run(["systemctl"], timeout_s=1.0)
        assert out.timed_out is True

    def test_success(self) -> None:
        """A real subprocess returns its stdout."""
        out = SubprocessRunner().run(["echo", "Id=x"], timeout_s=5.0)
        assert out == CommandOutput(0, "Id=x\n", False)
