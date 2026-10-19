import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass

from .config import DisplayConfig
from .mapper import Mapper
from .models import AttachedDisk, ResultRecord, Slot, SlotState
from .results import ResultsMissingError, ResultsPermissionError, ResultStore
from .sysfs import SysfsMissingError, SysfsPermissionError, boot_time, list_usb_disks
from .units import Runner, query_unit_states, unit_name

# Info strip notice keys (text is "notice." + key in ui/i18n.py). The SQLite index's
# db_missing/db_open_failed/db_schema_new are NOTICE_DB_* in store.py, returned by collect().
NOTICE_RESULTS_MISSING = "results_missing"
NOTICE_RESULTS_PERMISSION = "results_permission"
NOTICE_SYSFS_MISSING = "sysfs_missing"
NOTICE_SYSFS_PERMISSION = "sysfs_permission"


@dataclass(frozen=True)
class Snapshot:
    """Display contents for one tick. notice is a notice key; error is an exception class name."""

    slots: list[Slot]
    overflow: int = 0
    notice: str | None = None
    error: str | None = None
    tick_count: int = 0
    # Whether the same exception repeated repeat_threshold times (changes the info strip text).
    error_repeated: bool = False


def _empty_slots(mapper: Mapper) -> list[Slot]:
    """Empty slots shown right after startup."""
    return [
        Slot(
            i,
            mapper.ports[i].label if i < len(mapper.ports) else f"P{i + 1}",
            SlotState.EMPTY,
            None,
            None,
            None,
            False,
        )
        for i in range(mapper.slot_count)
    ]


class Monitor:
    """Tick body (sysfs -> results -> systemctl -> mapper) and exception rules (design doc 4.1)."""

    def __init__(
        self,
        config: DisplayConfig,
        *,
        store: ResultStore,
        runner: Runner | None,
        mapper: Mapper,
        list_disks: Callable[..., list[AttachedDisk]] = list_usb_disks,
        now: Callable[[], float] = time.time,
        repeat_threshold: int = 30,
    ) -> None:
        """Inject dependencies. All external calls (sysfs listing, systemctl, time) are swappable.

        Args:
            config: Display config (uses sys_block and unit_template).
            store: Source of results.
            runner: systemctl runner. If None, unit states are not queried.
            mapper: Slot assignment.
            list_disks: Function that lists disks from sysfs.
            now: Current time (epoch seconds).
            repeat_threshold: How many consecutive identical exceptions count as "repeating".
        """
        self.config = config
        self.store = store
        self.runner = runner
        self.mapper = mapper
        self.list_disks = list_disks
        self.now = now
        self.repeat_threshold = repeat_threshold
        self._first_seen: dict[str, float] = {}
        self._last: Snapshot | None = None
        self._error_class: type[BaseException] | None = None
        self._error_count = 0
        self._tick_count = 0

    def _fallback_time(self, kernel_name: str) -> float:
        """Connect time when st_mtime is unavailable: boot time, else first-seen time (D9)."""
        booted = boot_time()
        if booted is not None:
            return booted
        return self._first_seen.setdefault(kernel_name, self.now())

    def force_reload(self) -> None:
        """Drop the result cache and reload every result.json on the next tick (F5)."""
        self.store.reset()

    def tick_body(self) -> Snapshot:
        """One tick. Missing/denied sysfs or results root becomes a notice; other errors raise."""
        notice: str | None = None
        disks: list[AttachedDisk] = []
        try:
            disks = self.list_disks(self.config.sys_block, fallback_time=self._fallback_time)
        except SysfsMissingError:
            notice = NOTICE_SYSFS_MISSING
        except SysfsPermissionError:
            notice = NOTICE_SYSFS_PERMISSION
        records: list[ResultRecord] = []
        db_notice: str | None = None
        min_mtime = min((disk.attached_at for disk in disks), default=None)
        try:
            records, db_notice = self.store.collect(min_mtime)
        except ResultsMissingError:
            notice = notice or NOTICE_RESULTS_MISSING
        except ResultsPermissionError:
            notice = notice or NOTICE_RESULTS_PERMISSION
        # SQLite index problems (store.NOTICE_DB_*) rank below the sysfs and results-root notices.
        notice = notice or db_notice
        unit_states: dict[str, str] = {}
        if self.runner is not None and disks:
            units = [unit_name(self.config.unit_template, disk.kernel_name) for disk in disks]
            unit_states = query_unit_states(self.runner, units)
        slots, overflow = self.mapper.update(
            disks, records, unit_states, self.config.unit_template, self.now()
        )
        return Snapshot(slots, overflow, notice, None, self._tick_count)

    def tick(self) -> Snapshot:
        """Wrap tick_body() in the exception rules. On an exception, return the previous slots."""
        self._tick_count += 1
        try:
            snapshot = self.tick_body()
        except Exception as exc:
            traceback.print_exc()
            if type(exc) is self._error_class:
                self._error_count += 1
            else:
                self._error_class = type(exc)
                self._error_count = 1
            error = type(exc).__name__
            repeated = self._error_count >= self.repeat_threshold
            previous = self._last
            slots = previous.slots if previous is not None else _empty_slots(self.mapper)
            overflow = previous.overflow if previous is not None else 0
            notice = previous.notice if previous is not None else None
            snapshot = Snapshot(slots, overflow, notice, error, self._tick_count, repeated)
        else:
            self._error_class = None
            self._error_count = 0
            snapshot = Snapshot(
                snapshot.slots, snapshot.overflow, snapshot.notice, None, self._tick_count
            )
        self._last = snapshot
        return snapshot
