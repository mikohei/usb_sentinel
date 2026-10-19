from dataclasses import dataclass, replace

from .config import PortMapping
from .models import AttachedDisk, ResultRecord, ResultSummary, Slot, SlotState
from .result_file import serial_for_dir
from .units import ACTIVE_STATES, unit_name

# Key that identifies a disk across ticks (D12). It includes attached_at, so a re-plug
# always changes it.
DiskKey = tuple[str, str, float]


@dataclass(frozen=True)
class Retained:
    """Result kept on display after removal, and the pre-removal disk display attributes."""

    summary: ResultSummary
    removed_at: float
    fallback: bool
    kernel_name: str


def disk_key(disk: AttachedDisk) -> DiskKey:
    """Return the identity key of a disk."""
    return (disk.kernel_name, disk.port_path, disk.attached_at)


def bind_result(disk: AttachedDisk, records: list[ResultRecord]) -> ResultRecord | None:
    """Pick the readable result bound to a disk (design doc 3.5 plus port_path priority).

    Candidates are readable with mtime at or after the connect time. The primary key is port_path
    (kernel_name if the result has none). With several, prefer a dir-name serial match;
    on a tie, the largest directory name.
    """
    candidates: list[ResultRecord] = []
    for record in records:
        summary = record.summary
        if summary is None or record.mtime < disk.attached_at:
            continue
        if summary.port_path is not None:
            matched = summary.port_path == disk.port_path
        else:
            matched = summary.kernel_name == disk.kernel_name
        if matched:
            candidates.append(record)
    if not candidates:
        return None
    wanted = serial_for_dir(disk.serial, disk.kernel_name)
    preferred = [record for record in candidates if record.serial_part == wanted]
    return max(preferred or candidates, key=lambda record: record.name)


def bind_broken(disk: AttachedDisk, records: list[ResultRecord]) -> ResultRecord | None:
    """Pick the unreadable (broken) result bound to a disk (D8)."""
    wanted = serial_for_dir(disk.serial, disk.kernel_name)
    matches = [
        record
        for record in records
        if record.broken and record.mtime >= disk.attached_at and record.serial_part == wanted
    ]
    return max(matches, key=lambda record: record.name) if matches else None


def bind_started(disk: AttachedDisk, records: list[ResultRecord]) -> ResultRecord | None:
    """Pick the "started but no final result" record bound to a disk.

    Only the DB path (ResultStore.collect) creates started rows; directory scans never show them.
    Candidates have mtime (start time) at or after the connect time. Bind by port_path if present,
    else kernel_name; with several, take the max name.
    """
    matches: list[ResultRecord] = []
    for record in records:
        if not record.started or record.mtime < disk.attached_at:
            continue
        if record.port_path is not None:
            matched = record.port_path == disk.port_path
        else:
            matched = record.kernel_name == disk.kernel_name
        if matched:
            matches.append(record)
    return max(matches, key=lambda record: record.name) if matches else None


class Mapper:
    """Build the slot list from disks, results, and unit states. Swaps internal state at once."""

    def __init__(
        self,
        ports: tuple[PortMapping, ...],
        *,
        retain_s: float,
        waiting_s: float = 20.0,
        slot_count: int = 4,
    ) -> None:
        """Take the port mapping and time thresholds, and start with empty internal state.

        Args:
            ports: Port mapping in slot order (label, paths).
            retain_s: Seconds to keep showing a result after removal.
            waiting_s: Seconds after connect counted as "waiting"; later, no result is "no result".
            slot_count: Number of slots (tiles on screen).
        """
        self.ports = ports
        self.retain_s = retain_s
        self.waiting_s = waiting_s
        self.slot_count = slot_count
        self._path_to_index = {
            path: index for index, port in enumerate(ports) for path in port.paths
        }
        self._fallback: dict[DiskKey, int] = {}
        self._retained: dict[int, Retained] = {}
        self._last_done: dict[int, Retained] = {}

    def _label(self, index: int) -> str:
        """Display name of a slot. Slots not in the mapping are P<n>."""
        return self.ports[index].label if index < len(self.ports) else f"P{index + 1}"

    def _assign(
        self, disks: list[AttachedDisk]
    ) -> tuple[dict[int, AttachedDisk], dict[DiskKey, int], int]:
        """Assign disks to slots.

        Returns:
            (slot index -> disk, new fallback table, overflow count).
        """
        assigned: dict[int, AttachedDisk] = {}
        overflow = 0
        unknown: list[AttachedDisk] = []
        for disk in sorted(disks, key=lambda disk: disk.kernel_name):
            index = self._path_to_index.get(disk.port_path)
            if index is None:
                unknown.append(disk)
            elif index in assigned:
                overflow += 1
            else:
                assigned[index] = disk
        # Keep existing fallback assignments unless a mapped disk has taken the slot.
        fallback: dict[DiskKey, int] = {}
        pending: list[AttachedDisk] = []
        for disk in unknown:
            index = self._fallback.get(disk_key(disk))
            if index is not None and index not in assigned:
                assigned[index] = disk
                fallback[disk_key(disk)] = index
            else:
                pending.append(disk)
        # Pushed-out and newly seen disks fill empty slots, then retained slots (oldest first).
        # Done slots removed in the same tick (in _last_done, not yet in _retained) are about to
        # be retained, so they are not treated as free.
        free = [
            i
            for i in range(self.slot_count)
            if i not in assigned and i not in self._retained and i not in self._last_done
        ]
        retained = sorted(
            (i for i in range(self.slot_count) if i not in assigned and i in self._retained),
            key=lambda i: self._retained[i].removed_at,
        )
        for disk in pending:
            if free:
                index = free.pop(0)
            elif retained:
                index = retained.pop(0)
            else:
                overflow += 1
                continue
            assigned[index] = disk
            fallback[disk_key(disk)] = index
        return assigned, fallback, overflow

    def _state_for(
        self,
        disk: AttachedDisk,
        result: ResultRecord | None,
        broken: ResultRecord | None,
        started: ResultRecord | None,
        unit_state: str | None,
        now: float,
    ) -> SlotState:
        """Decide the state of a connected disk's slot in the order of design doc 3.4.

        A started row (only the DB path creates one) is inspecting if the unit state is unknown,
        and no result if known and not active (it ended without writing result.json).
        """
        if unit_state in ACTIVE_STATES:
            return SlotState.INSPECTING
        if result is not None:
            return SlotState.DONE
        if broken is not None:
            return SlotState.BROKEN
        if started is not None:
            return SlotState.INSPECTING if unit_state is None else SlotState.NO_RESULT
        if now - disk.attached_at < self.waiting_s:
            return SlotState.WAITING
        return SlotState.NO_RESULT

    def update(
        self,
        disks: list[AttachedDisk],
        records: list[ResultRecord],
        unit_states: dict[str, str],
        unit_template: str,
        now: float,
    ) -> tuple[list[Slot], int]:
        """Compute the slot list for one tick and swap in the internal state on success.

        Args:
            disks: Connected disks.
            records: Result records.
            unit_states: Unit name -> ActiveState.
            unit_template: Unit name template.
            now: Current time (epoch seconds).

        Returns:
            (slot list, count of disks that could not be shown).
        """
        assigned, fallback, overflow = self._assign(disks)
        retained: dict[int, Retained] = {}
        last_done: dict[int, Retained] = {}
        slots: list[Slot] = []
        for index in range(self.slot_count):
            disk = assigned.get(index)
            label = self._label(index)
            if disk is None:
                kept = self._retained.get(index)
                if kept is None and index in self._last_done:
                    # The removal tick. Set removed_at to now and start retaining.
                    kept = replace(self._last_done[index], removed_at=now)
                if kept is not None and now - kept.removed_at < self.retain_s:
                    retained[index] = kept
                    slots.append(
                        Slot(
                            index,
                            label,
                            SlotState.REMOVED,
                            None,
                            kept.summary,
                            None,
                            kept.fallback,
                            kept.kernel_name,
                        )
                    )
                else:
                    slots.append(Slot(index, label, SlotState.EMPTY, None, None, None, False))
                continue
            unit_state = unit_states.get(unit_name(unit_template, disk.kernel_name))
            result = bind_result(disk, records)
            broken = bind_broken(disk, records)
            started = bind_started(disk, records)
            state = self._state_for(disk, result, broken, started, unit_state, now)
            summary = result.summary if result is not None and state is SlotState.DONE else None
            is_fallback = disk_key(disk) in fallback
            if summary is not None:
                last_done[index] = Retained(summary, now, is_fallback, disk.kernel_name)
            slots.append(Slot(index, label, state, disk, summary, unit_state, is_fallback))
        self._fallback = fallback
        self._retained = retained
        self._last_done = last_done
        return slots, overflow
