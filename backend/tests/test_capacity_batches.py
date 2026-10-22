"""Batched f3write / f3read of the capacity step and the early stops on clear failures.

Each batch is registered by its `--start-at` argument, so the FakeRunner returns a different
output per batch.
"""

from __future__ import annotations

from dataclasses import replace

from tests.conftest import capacity_step, f3read_output
from usb_sentinel.libs import FakeRunner, StepContext, StepStatus
from usb_sentinel.libs.runner import CommandResult
from usb_sentinel.libs.steps.capacity_fs import parse_f3write_batch

FILE_SECTORS = 2_097_152  # one 1 GiB f3 file


def f3write_batch(
    files: range, mb_s: float | None = 20.0, *, full: bool = False, failure_at: int = 0
) -> str:
    """stdout of one f3write batch."""
    lines = ["Free space: 100.00 GB"]
    for n in files:
        outcome = "Write failure: Input/output error" if n == failure_at else "OK!"
        lines.append(f"Creating file {n}.h2w ... {outcome}")
    lines.append("Free space: 0.00 Byte" if full else "Free space: 50.00 GB")
    if mb_s is not None:
        lines.append(f"Average writing speed: {mb_s:.2f} MB/s")
    return "\n".join(lines) + "\n"


def _batched(ctx: StepContext, **speed: object) -> StepContext:
    """Batches of 8 files; `speed` overrides [speed] keys (for example device_class)."""
    capacity = replace(ctx.config.capacity, batch_files=8)
    ctx.config = replace(ctx.config, capacity=capacity, speed=replace(ctx.config.speed, **speed))
    return ctx


def _writes(runner: FakeRunner, *batches: tuple[int, str]) -> None:
    for start, stdout in batches:
        runner.register(["f3write", f"--start-at={start}"], stdout=stdout)


def _reads(runner: FakeRunner, *batches: tuple[int, str]) -> None:
    for start, stdout in batches:
        runner.register(["f3read", f"--start-at={start}"], stdout=stdout)


def _clean_reads(runner: FakeRunner, last: int) -> None:
    for start in range(1, last + 1, 8):
        files = range(start, min(start + 7, last) + 1)
        _reads(runner, (start, f3read_output(len(files) * FILE_SECTORS, files=files)))


def _calls(runner: FakeRunner, command: str) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if call[0] == command]


class RecordingRunner(FakeRunner):
    """FakeRunner sharing the registrations of `source` that also records timeout_s."""

    def __init__(self, source: FakeRunner) -> None:
        super().__init__(source.missing)
        self.registered = source.registered
        self.timeouts: list[tuple[str, float | None]] = []

    def run(self, argv: list[str], *, timeout_s: float | None = None) -> CommandResult:
        self.timeouts.append((argv[0], timeout_s))
        return super().run(argv, timeout_s=timeout_s)


class TestBatches:
    def test_writes_and_reads_in_batches(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        _writes(
            f3_runner,
            (1, f3write_batch(range(1, 9))),
            (9, f3write_batch(range(9, 17))),
            (17, f3write_batch(range(17, 25))),
            (25, f3write_batch(range(25, 30), full=True)),
        )
        _clean_reads(f3_runner, 29)

        result = capacity_step().run(_batched(ctx))

        assert result.status is StepStatus.PASS
        assert [c[1:3] for c in _calls(f3_runner, "f3write")] == [
            ("--start-at=1", "--end-at=8"),
            ("--start-at=9", "--end-at=16"),
            ("--start-at=17", "--end-at=24"),
            ("--start-at=25", "--end-at=32"),
        ]
        assert [c[1:3] for c in _calls(f3_runner, "f3read")][-1] == (
            "--start-at=25",
            "--end-at=29",
        )
        fs = result.details["filesystems"][0]
        assert fs["files_written"] == 29
        assert fs["write_batches_mb_s"] == [20.0, 20.0, 20.0, 20.0]
        assert result.details["verified_bytes"] == 29 * FILE_SECTORS * 512

    def test_no_space_after_an_exactly_full_batch_ends_writing(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        _writes(f3_runner, (1, f3write_batch(range(1, 9))))
        f3_runner.register(
            ["f3write", "--start-at=9"], returncode=1, stdout="Free space: 0.00 Byte\nNo space!\n"
        )
        _clean_reads(f3_runner, 8)

        result = capacity_step().run(_batched(ctx))

        assert result.details["filesystems"][0]["files_written"] == 8
        assert len(_calls(f3_runner, "f3write")) == 2
        assert result.warnings == []


class TestWriteStops:
    def test_write_failure_is_defect_and_cleans_up(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        _writes(
            f3_runner,
            (1, f3write_batch(range(1, 9))),
            (9, f3write_batch(range(9, 17), failure_at=12)),
        )

        result = capacity_step().run(_batched(ctx))

        assert result.status is StepStatus.FAIL
        assert result.summary == "Write error in files 9-16: Input/output error"
        assert result.details["kind"] == "defect"
        assert result.details["defect"] is True
        assert result.details["stopped_early"] is True
        assert _calls(f3_runner, "f3read") == []
        assert f3_runner.calls[-1][0] == "umount"

    def test_create_error_exit_is_defect(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        f3_runner.register(
            ["f3write"],
            returncode=5,
            stdout="Free space: 30.00 GB\nCreating file 1.h2w ... ",
            stderr="f3write: Can't create file /mnt/1.h2w: Input/output error",
        )

        result = capacity_step().run(_batched(ctx))

        assert result.status is StepStatus.FAIL
        assert result.summary == "Write error in files 1-8: Input/output error"

    def test_slow_batches_in_a_row_stop_as_slow(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        # usb2_flash write floor 5.0 x pass_ratio 0.80 = 4.0 MB/s.
        _writes(
            f3_runner,
            (1, f3write_batch(range(1, 9), 3.0)),
            (9, f3write_batch(range(9, 17), 3.5)),
            (17, f3write_batch(range(17, 25), 2.0)),
        )

        result = capacity_step().run(_batched(ctx, device_class="usb2_flash"))

        assert result.status is StepStatus.FAIL
        assert result.details["kind"] == "slow"
        assert result.details["defect"] is False
        assert result.details["counterfeit"] is False
        assert result.summary == (
            "Write speed shortfall: 3 batches in a row below threshold 4.0MB/s"
            " (files 1-24: 3.0 / 3.5 / 2.0MB/s)"
        )
        assert _calls(f3_runner, "f3read") == []

    def test_fast_batch_resets_the_count(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        _writes(
            f3_runner,
            (1, f3write_batch(range(1, 9), 3.0)),
            (9, f3write_batch(range(9, 17), 3.0)),
            (17, f3write_batch(range(17, 25), 20.0)),
            (25, f3write_batch(range(25, 30), 3.0, full=True)),
        )
        _clean_reads(f3_runner, 29)

        result = capacity_step().run(_batched(ctx, device_class="usb2_flash"))

        assert result.status is StepStatus.PASS
        assert len(_calls(f3_runner, "f3write")) == 4

    def test_no_threshold_never_stops_as_slow(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        # No nominal speed, no device class, and an unknown link speed: no reference.
        _writes(
            f3_runner,
            (1, f3write_batch(range(1, 9), 0.5)),
            (9, f3write_batch(range(9, 17), 0.5)),
            (17, f3write_batch(range(17, 25), 0.5)),
            (25, f3write_batch(range(25, 30), 0.5, full=True)),
        )
        _clean_reads(f3_runner, 29)

        result = capacity_step().run(_batched(ctx))

        assert result.status is StepStatus.PASS
        assert result.details["filesystems"][0]["write_threshold_mb_s"] is None

    def test_stalled_write_is_slow(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        runner = RecordingRunner(f3_runner)
        runner.register(["f3write"], returncode=-1, timed_out=True)
        ctx.runner = runner

        result = capacity_step().run(_batched(ctx))

        assert result.status is StepStatus.FAIL
        assert result.details["kind"] == "slow"
        assert result.summary == "Write stalled: files 1-8 not finished within 8590s (below 1MB/s)"
        # 8 GiB at 1 MB/s; the default timeout_s = 0 adds no other limit.
        assert [t for name, t in runner.timeouts if name == "f3write"] == [8 * (1 << 30) / 1e6]
        assert "timed_out" not in result.details
        assert runner.calls[-1][0] == "umount"

    def test_every_command_gets_a_finite_timeout(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        # With no overall limit (timeout_s = 0), each command still needs its own cap.
        runner = RecordingRunner(f3_runner)
        ctx.runner = runner

        capacity_step().run(ctx)

        budgeted = [(name, t) for name, t in runner.timeouts if name not in ("mount", "umount")]
        assert [name for name, _t in budgeted] == [
            "f3probe",
            "wipefs",
            "mkfs.exfat",
            "f3write",
            "f3read",
        ]
        assert all(t is not None and t < float("inf") for _name, t in budgeted)


def _three_written(runner: FakeRunner) -> None:
    _writes(
        runner,
        (1, f3write_batch(range(1, 9))),
        (9, f3write_batch(range(9, 17))),
        (17, f3write_batch(range(17, 21), full=True)),
    )


class TestReadStops:
    def test_corruption_stops_the_remaining_reads(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        _three_written(f3_runner)
        _reads(f3_runner, (1, f3read_output(8 * FILE_SECTORS - 100, 100, files=range(1, 9))))

        result = capacity_step().run(_batched(ctx))

        assert result.status is StepStatus.FAIL
        assert result.details["kind"] == "defect"
        assert result.summary == (
            "Data corruption in read-back of files 1-8: 100 sectors lost"
            " (corrupted 100 / slightly changed 0 / overwritten 0); remaining files not read"
        )
        assert result.details["bad_sectors"] == 100
        assert len(_calls(f3_runner, "f3read")) == 1

    def test_mostly_lost_read_back_is_counterfeit(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        _three_written(f3_runner)
        lost = 6 * FILE_SECTORS
        _reads(
            f3_runner,
            (1, f3read_output(2 * FILE_SECTORS, overwritten_sectors=lost, files=range(1, 9))),
        )

        result = capacity_step().run(_batched(ctx))

        assert result.status is StepStatus.FAIL
        assert result.details["kind"] == "counterfeit"
        assert result.details["counterfeit_reasons"] == ["lost_ratio"]
        assert result.details["lost_ratio"] == 0.75
        assert result.summary.startswith("Suspected counterfeit: lost 75.0% in read-back up to")

    def test_corruption_in_the_last_batch_gets_the_full_judgment(
        self, ctx: StepContext, f3_runner: FakeRunner
    ) -> None:
        ctx.device = replace(ctx.device, advertised_bytes=14_000_000_000)
        _writes(
            f3_runner,
            (1, f3write_batch(range(1, 9))),
            (9, f3write_batch(range(9, 13), full=True)),
        )
        _reads(
            f3_runner,
            (1, f3read_output(8 * FILE_SECTORS, files=range(1, 9))),
            (9, f3read_output(4 * FILE_SECTORS - 10, 10, files=range(9, 13))),
        )

        result = capacity_step().run(_batched(ctx))

        assert result.details["kind"] == "defect"
        assert result.summary.startswith("Data corruption: 10 sectors")
        assert "stopped_early" not in result.details

    def test_missing_file_is_defect(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        _three_written(f3_runner)
        _reads(f3_runner, (1, f3read_output(7 * FILE_SECTORS, files=range(1, 8))))

        result = capacity_step().run(_batched(ctx))

        assert result.details["kind"] == "defect"
        assert "1 file missing" in result.summary

    def test_read_io_error_is_defect(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        _three_written(f3_runner)
        stdout = f3read_output(8 * FILE_SECTORS, files=range(1, 9))
        stdout += "WARNING: Not all data was read due to I/O error(s)\n"
        _reads(f3_runner, (1, stdout))

        result = capacity_step().run(_batched(ctx))

        assert result.details["kind"] == "defect"
        assert result.summary == (
            "Data corruption in read-back of files 1-8: read I/O error; remaining files not read"
        )

    def test_stalled_read_is_slow(self, ctx: StepContext, f3_runner: FakeRunner) -> None:
        _three_written(f3_runner)
        f3_runner.register(["f3read"], returncode=-1, timed_out=True)

        result = capacity_step().run(_batched(ctx))

        assert result.details["kind"] == "slow"
        assert result.summary.startswith("Read stalled: files 1-8 not finished within")


class TestParseF3WriteBatch:
    def test_no_space_outcome_is_not_written(self) -> None:
        batch = parse_f3write_batch(
            "Creating file 1.h2w ... OK!\nCreating file 2.h2w ... No space left.\n"
        )
        assert batch.written == (1,)
        assert batch.attempted == 2
        assert batch.disk_full is True

    def test_no_space_at_start(self) -> None:
        batch = parse_f3write_batch("Free space: 0.00 Byte\nNo space!\n")
        assert batch.written == ()
        assert batch.disk_full is True

    def test_write_failure_is_collected(self) -> None:
        batch = parse_f3write_batch(
            "Free space: 9.00 GB\nCreating file 3.h2w ... Write failure: Input/output error\n"
            "Free space: 8.00 GB\nAverage writing speed: 9.00 MB/s\n"
        )
        assert batch.written == (3,)
        assert batch.failures == ("Input/output error",)
        assert batch.disk_full is False
        assert batch.write_mb_s == 9.0
