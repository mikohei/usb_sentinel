"""Tests for Mapper.update() (building 4 Slots from disks, results, and unit states).

Inputs such as disk(), summary(), record(), and started() are borrowed from test_mapper.
"""

import pytest

from tests.test_mapper import (
    PORTS,
    TEMPLATE,
    active,
    disk,
    inactive,
    record,
    started,
    states,
)
from usb_sentinel_display.libs.config import PortMapping
from usb_sentinel_display.libs.mapper import Mapper
from usb_sentinel_display.libs.models import SlotState


class TestMapper:
    def mapper(self, ports: tuple[PortMapping, ...] = PORTS) -> Mapper:
        return Mapper(ports, retain_s=3600.0)

    def test_started_without_unit_state_is_inspecting(self) -> None:
        """Started row + unknown unit state (no runner): still inspecting past the startup wait."""
        slots, _ = self.mapper().update([disk()], [started()], {}, TEMPLATE, 1500.0)
        assert slots[0].state is SlotState.INSPECTING
        assert slots[0].result is None

    def test_started_with_inactive_is_no_result(self) -> None:
        """Started row but inactive unit: no result even right after connect (USB lost?)."""
        slots, _ = self.mapper().update([disk()], [started()], inactive("sda"), TEMPLATE, 1005.0)
        assert slots[0].state is SlotState.NO_RESULT

    def test_started_never_done_and_completed_wins(self) -> None:
        """A started row alone does not give DONE; a completed result makes DONE win."""
        m = self.mapper()
        slots, _ = m.update([disk()], [started()], {}, TEMPLATE, 1500.0)
        assert slots[0].state is not SlotState.DONE
        records = [started(), record(name="20250601T120500Z-ABC123")]
        slots, _ = m.update([disk()], records, inactive("sda"), TEMPLATE, 1500.0)
        assert slots[0].state is SlotState.DONE
        assert slots[0].result is not None

    def test_started_port_path_mismatch_not_bound(self) -> None:
        """A started row on another port_path doesn't bind; startup wait -> no result as before."""
        rec = started(port_path="1-1.2")
        slots, _ = self.mapper().update([disk(port="1-1.1")], [rec], {}, TEMPLATE, 1005.0)
        assert slots[0].state is SlotState.WAITING
        slots, _ = self.mapper().update([disk(port="1-1.1")], [rec], {}, TEMPLATE, 1500.0)
        assert slots[0].state is SlotState.NO_RESULT

    def test_broken_wins_over_started(self) -> None:
        """A broken result and a started row on the same disk give BROKEN."""
        slots, _ = self.mapper().update(
            [disk()], [record(broken=True), started()], {}, TEMPLATE, 1500.0
        )
        assert slots[0].state is SlotState.BROKEN

    def test_all_empty(self) -> None:
        slots, overflow = self.mapper().update([], [], {}, TEMPLATE, 2000.0)
        assert states(slots) == [SlotState.EMPTY] * 4
        assert [s.label for s in slots] == ["P1", "P2", "P3", "P4"]
        assert overflow == 0

    def test_mapped_ports_both_generations(self) -> None:
        """Both the USB 3.0 (2-1) and 2.0 (1-1.1) paths go to the same P1."""
        m = self.mapper()
        slots, _ = m.update([disk(port="2-1")], [], {}, TEMPLATE, 1005.0)
        assert slots[0].disk is not None and slots[0].fallback is False
        slots, _ = m.update([disk(port="1-1.1", at=1010.0)], [], {}, TEMPLATE, 1015.0)
        assert slots[0].disk is not None and slots[0].state is SlotState.WAITING

    def test_transition_waiting_inspecting_done_removed(self) -> None:
        """Startup wait -> inspecting -> done -> removed -> EMPTY after retain."""
        m = self.mapper()
        d = disk()
        slots, _ = m.update([d], [], {}, TEMPLATE, 1005.0)
        assert slots[0].state is SlotState.WAITING
        slots, _ = m.update([d], [], active("sda"), TEMPLATE, 1030.0)
        assert slots[0].state is SlotState.INSPECTING
        assert slots[0].unit_state == "active"
        slots, _ = m.update([d], [record()], inactive("sda"), TEMPLATE, 1200.0)
        assert slots[0].state is SlotState.DONE
        assert slots[0].result is not None
        slots, _ = m.update([], [record()], {}, TEMPLATE, 1300.0)
        assert slots[0].state is SlotState.REMOVED
        assert slots[0].result is not None
        assert slots[0].disk is None
        slots, _ = m.update([], [record()], {}, TEMPLATE, 1300.0 + 3600.0)
        assert slots[0].state is SlotState.EMPTY

    def test_no_result_after_waiting(self) -> None:
        """Inactive with no result after 20 seconds gives no result."""
        slots, _ = self.mapper().update([disk()], [], inactive("sda"), TEMPLATE, 1025.0)
        assert slots[0].state is SlotState.NO_RESULT

    def test_old_result_not_adopted_even_when_inactive(self) -> None:
        """An sda result older than the connect time stays no result even after going inactive."""
        m = self.mapper()
        old = record(mtime=900.0)
        slots, _ = m.update([disk(at=1000.0)], [old], active("sda"), TEMPLATE, 1010.0)
        assert slots[0].state is SlotState.INSPECTING
        slots, _ = m.update([disk(at=1000.0)], [old], inactive("sda"), TEMPLATE, 1030.0)
        assert slots[0].state is SlotState.NO_RESULT

    def test_result_before_attached_after_restart(self) -> None:
        """Simulating a restart, attached_at before the result gives DONE immediately."""
        slots, _ = self.mapper().update(
            [disk(at=900.0)], [record(mtime=1100.0)], {}, TEMPLATE, 5000.0
        )
        assert slots[0].state is SlotState.DONE

    def test_broken_record(self) -> None:
        slots, _ = self.mapper().update([disk()], [record(broken=True)], {}, TEMPLATE, 1005.0)
        assert slots[0].state is SlotState.BROKEN

    def test_inspecting_wins_over_result(self) -> None:
        """If the unit is active, it is inspecting even with a result."""
        slots, _ = self.mapper().update([disk()], [record()], active("sda"), TEMPLATE, 1200.0)
        assert slots[0].state is SlotState.INSPECTING

    def test_fallback_without_ports(self) -> None:
        """With an empty mapping all go to fallback; several in one tick go by kernel name."""
        m = self.mapper(())
        disks = [disk("sdb", "9-9"), disk("sda", "8-8")]
        slots, overflow = m.update(disks, [], {}, TEMPLATE, 1005.0)
        assert slots[0].disk is not None and slots[0].disk.kernel_name == "sda"
        assert slots[1].disk is not None and slots[1].disk.kernel_name == "sdb"
        assert all(s.fallback for s in slots[:2])
        assert overflow == 0

    def test_fallback_slot_stable_after_removal(self) -> None:
        """Removing sda after sda and sdb does not change sdb's slot."""
        m = self.mapper(())
        sda, sdb = disk("sda", "8-8"), disk("sdb", "9-9")
        m.update([sda, sdb], [], {}, TEMPLATE, 1005.0)
        slots, _ = m.update([sdb], [], {}, TEMPLATE, 1006.0)
        assert slots[0].state is SlotState.EMPTY
        assert slots[1].disk is sdb

    def test_mapped_disk_displaces_fallback(self) -> None:
        """A mapped disk inserted into an unknown disk's slot moves the unknown to another slot."""
        m = self.mapper(PORTS[:1])
        unknown = disk("sdb", "9-9")
        slots, _ = m.update([unknown], [], {}, TEMPLATE, 1005.0)
        assert slots[0].disk is unknown
        known = disk("sda", "1-1.1")
        slots, _ = m.update([known, unknown], [], {}, TEMPLATE, 1006.0)
        assert slots[0].disk is known and slots[0].fallback is False
        assert slots[1].disk is unknown and slots[1].fallback is True

    def test_five_disks_overflow(self) -> None:
        m = self.mapper(())
        disks = [disk(f"sd{c}", f"7-{i}") for i, c in enumerate("abcde")]
        slots, overflow = m.update(disks, [], {}, TEMPLATE, 1005.0)
        assert all(s.disk is not None for s in slots)
        assert overflow == 1

    def test_same_serial_two_disks(self) -> None:
        """Two disks with the same serial split by port path; results differ by kernel_name."""
        a, b = disk("sda", "1-1.1", "SAME"), disk("sdb", "1-1.2", "SAME")
        records = [
            record("sda", "SAME", name="20250601T120000Z-SAME"),
            record("sdb", "SAME", name="20250601T120100Z-SAME"),
        ]
        slots, _ = self.mapper().update([a, b], records, {}, TEMPLATE, 1200.0)
        assert slots[0].result is not None and slots[0].result.kernel_name == "sda"
        assert slots[1].result is not None and slots[1].result.kernel_name == "sdb"

    def test_new_disk_clears_retained(self) -> None:
        """A new disk arriving at a retained slot clears the retention."""
        m = self.mapper()
        m.update([disk()], [record()], {}, TEMPLATE, 1200.0)
        slots, _ = m.update([], [record()], {}, TEMPLATE, 1300.0)
        assert slots[0].state is SlotState.REMOVED
        slots, _ = m.update([disk(at=1400.0, serial="NEW")], [record()], {}, TEMPLATE, 1405.0)
        assert slots[0].state is SlotState.WAITING
        assert slots[0].result is None

    def test_removed_keeps_fallback_and_kernel(self) -> None:
        """A REMOVED slot keeps fallback and the pre-removal kernel name (D22)."""
        m = self.mapper(())
        m.update([disk("sdb", "8-8")], [record("sdb")], {}, TEMPLATE, 1200.0)
        for now in (1300.0, 1400.0):
            slots, _ = m.update([], [record("sdb")], {}, TEMPLATE, now)
            assert slots[0].state is SlotState.REMOVED
            assert slots[0].fallback is True and slots[0].retained_kernel == "sdb"
        m = self.mapper()
        m.update([disk()], [record()], {}, TEMPLATE, 1200.0)
        slots, _ = m.update([], [record()], {}, TEMPLATE, 1300.0)
        assert slots[0].fallback is False and slots[0].retained_kernel == "sda"

    def test_removed_without_result_is_empty(self) -> None:
        """Removed without a result: not retained, EMPTY."""
        m = self.mapper()
        m.update([disk()], [], {}, TEMPLATE, 1005.0)
        slots, _ = m.update([], [], {}, TEMPLATE, 1006.0)
        assert slots[0].state is SlotState.EMPTY

    def test_fallback_prefers_empty_over_retained(self) -> None:
        """A new unknown disk prefers an empty slot over a retained one."""
        m = self.mapper(())
        first = disk("sda", "8-8")
        m.update([first], [record()], {}, TEMPLATE, 1200.0)
        m.update([], [record()], {}, TEMPLATE, 1300.0)
        second = disk("sda", "8-8", "NEW", at=1400.0)
        slots, _ = m.update([second], [record()], {}, TEMPLATE, 1405.0)
        assert slots[0].state is SlotState.REMOVED
        assert slots[1].disk is second

    def test_removed_slot_not_taken_by_new_disk_same_tick(self) -> None:
        """P1 is REMOVED even if its done disk leaves while an unknown appears in the same tick."""
        m = self.mapper()
        m.update([disk()], [record()], {}, TEMPLATE, 1200.0)
        newcomer = disk("sdb", "9-9", "NEW", at=1290.0)
        slots, overflow = m.update([newcomer], [record()], {}, TEMPLATE, 1300.0)
        assert slots[0].state is SlotState.REMOVED
        assert slots[0].result is not None
        assert slots[0].retained_kernel == "sda"
        assert slots[1].disk is newcomer and slots[1].fallback is True
        assert overflow == 0

    def test_state_not_changed_on_exception(self) -> None:
        """An exception midway leaves the internal state as before."""
        m = self.mapper(())
        m.update([disk("sda", "8-8")], [], {}, TEMPLATE, 1005.0)
        with pytest.raises(ValueError, match="kernel"):
            m.update([disk("sda", "8-8"), disk("bad name", "9-9")], [], {}, TEMPLATE, 1006.0)
        slots, _ = m.update([disk("sda", "8-8")], [], {}, TEMPLATE, 1007.0)
        assert slots[0].disk is not None and slots[0].disk.kernel_name == "sda"
