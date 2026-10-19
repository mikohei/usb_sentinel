"""Mapper.update()（ディスク・結果・ユニット状態からSlot4つを組む）のテスト。

disk()／summary()／record()／started()などの材料はtest_mapperから借りる。
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
        """started行がありユニット状態が不明（runner無し）なら、起動待ちを過ぎても検査中。"""
        slots, _ = self.mapper().update([disk()], [started()], {}, TEMPLATE, 1500.0)
        assert slots[0].state is SlotState.INSPECTING
        assert slots[0].result is None

    def test_started_with_inactive_is_no_result(self) -> None:
        """started行があってもユニットがinactiveなら接続直後でも結果なし（USB切断の疑い）。"""
        slots, _ = self.mapper().update([disk()], [started()], inactive("sda"), TEMPLATE, 1005.0)
        assert slots[0].state is SlotState.NO_RESULT

    def test_started_never_done_and_completed_wins(self) -> None:
        """started行だけではDONEにならず、completed結果があればDONEが勝つ。"""
        m = self.mapper()
        slots, _ = m.update([disk()], [started()], {}, TEMPLATE, 1500.0)
        assert slots[0].state is not SlotState.DONE
        records = [started(), record(name="20250601T120500Z-ABC123")]
        slots, _ = m.update([disk()], records, inactive("sda"), TEMPLATE, 1500.0)
        assert slots[0].state is SlotState.DONE
        assert slots[0].result is not None

    def test_started_port_path_mismatch_not_bound(self) -> None:
        """port_pathが違うstarted行は結び付かず、従来どおり起動待ち→結果なし。"""
        rec = started(port_path="1-1.2")
        slots, _ = self.mapper().update([disk(port="1-1.1")], [rec], {}, TEMPLATE, 1005.0)
        assert slots[0].state is SlotState.WAITING
        slots, _ = self.mapper().update([disk(port="1-1.1")], [rec], {}, TEMPLATE, 1500.0)
        assert slots[0].state is SlotState.NO_RESULT

    def test_broken_wins_over_started(self) -> None:
        """同じディスクにbroken結果とstarted行があればBROKEN。"""
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
        """USB 3.0（2-1）と2.0（1-1.1）の両パスが同じP1に入る。"""
        m = self.mapper()
        slots, _ = m.update([disk(port="2-1")], [], {}, TEMPLATE, 1005.0)
        assert slots[0].disk is not None and slots[0].fallback is False
        slots, _ = m.update([disk(port="1-1.1", at=1010.0)], [], {}, TEMPLATE, 1015.0)
        assert slots[0].disk is not None and slots[0].state is SlotState.WAITING

    def test_transition_waiting_inspecting_done_removed(self) -> None:
        """起動待ち→検査中→完了→抜去→retain経過でEMPTY。"""
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
        """inactiveで結果が無く20秒経過なら結果なし。"""
        slots, _ = self.mapper().update([disk()], [], inactive("sda"), TEMPLATE, 1025.0)
        assert slots[0].state is SlotState.NO_RESULT

    def test_old_result_not_adopted_even_when_inactive(self) -> None:
        """接続時刻より古いsdaの結果があり、inactiveになっても結果なしのまま。"""
        m = self.mapper()
        old = record(mtime=900.0)
        slots, _ = m.update([disk(at=1000.0)], [old], active("sda"), TEMPLATE, 1010.0)
        assert slots[0].state is SlotState.INSPECTING
        slots, _ = m.update([disk(at=1000.0)], [old], inactive("sda"), TEMPLATE, 1030.0)
        assert slots[0].state is SlotState.NO_RESULT

    def test_result_before_attached_after_restart(self) -> None:
        """再起動を模してattached_atが結果より前なら即DONE。"""
        slots, _ = self.mapper().update(
            [disk(at=900.0)], [record(mtime=1100.0)], {}, TEMPLATE, 5000.0
        )
        assert slots[0].state is SlotState.DONE

    def test_broken_record(self) -> None:
        slots, _ = self.mapper().update([disk()], [record(broken=True)], {}, TEMPLATE, 1005.0)
        assert slots[0].state is SlotState.BROKEN

    def test_inspecting_wins_over_result(self) -> None:
        """ユニットがactiveなら結果があっても検査中。"""
        slots, _ = self.mapper().update([disk()], [record()], active("sda"), TEMPLATE, 1200.0)
        assert slots[0].state is SlotState.INSPECTING

    def test_fallback_without_ports(self) -> None:
        """対応表が空なら全部フォールバックで、同tickの複数はカーネル名順。"""
        m = self.mapper(())
        disks = [disk("sdb", "9-9"), disk("sda", "8-8")]
        slots, overflow = m.update(disks, [], {}, TEMPLATE, 1005.0)
        assert slots[0].disk is not None and slots[0].disk.kernel_name == "sda"
        assert slots[1].disk is not None and slots[1].disk.kernel_name == "sdb"
        assert all(s.fallback for s in slots[:2])
        assert overflow == 0

    def test_fallback_slot_stable_after_removal(self) -> None:
        """sdaとsdbの後にsdaを抜いてもsdbのスロットが変わらない。"""
        m = self.mapper(())
        sda, sdb = disk("sda", "8-8"), disk("sdb", "9-9")
        m.update([sda, sdb], [], {}, TEMPLATE, 1005.0)
        slots, _ = m.update([sdb], [], {}, TEMPLATE, 1006.0)
        assert slots[0].state is SlotState.EMPTY
        assert slots[1].disk is sdb

    def test_mapped_disk_displaces_fallback(self) -> None:
        """確定ディスクが未知ディスクのスロットに挿されたら未知が別の空きへ移る。"""
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
        """同一シリアル2台はポートパスで分かれ、結果はkernel_nameで区別される。"""
        a, b = disk("sda", "1-1.1", "SAME"), disk("sdb", "1-1.2", "SAME")
        records = [
            record("sda", "SAME", name="20250601T120000Z-SAME"),
            record("sdb", "SAME", name="20250601T120100Z-SAME"),
        ]
        slots, _ = self.mapper().update([a, b], records, {}, TEMPLATE, 1200.0)
        assert slots[0].result is not None and slots[0].result.kernel_name == "sda"
        assert slots[1].result is not None and slots[1].result.kernel_name == "sdb"

    def test_new_disk_clears_retained(self) -> None:
        """保持中のスロットに新ディスクが来たら保持が消える。"""
        m = self.mapper()
        m.update([disk()], [record()], {}, TEMPLATE, 1200.0)
        slots, _ = m.update([], [record()], {}, TEMPLATE, 1300.0)
        assert slots[0].state is SlotState.REMOVED
        slots, _ = m.update([disk(at=1400.0, serial="NEW")], [record()], {}, TEMPLATE, 1405.0)
        assert slots[0].state is SlotState.WAITING
        assert slots[0].result is None

    def test_removed_keeps_fallback_and_kernel(self) -> None:
        """抜去後のREMOVEDスロットはfallbackと抜去前のカーネル名を引き継ぐ（D22）。"""
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
        """結果なしで抜去されたら保持せずEMPTY。"""
        m = self.mapper()
        m.update([disk()], [], {}, TEMPLATE, 1005.0)
        slots, _ = m.update([], [], {}, TEMPLATE, 1006.0)
        assert slots[0].state is SlotState.EMPTY

    def test_fallback_prefers_empty_over_retained(self) -> None:
        """新しい未知ディスクは保持中のスロットより空のスロットを選ぶ。"""
        m = self.mapper(())
        first = disk("sda", "8-8")
        m.update([first], [record()], {}, TEMPLATE, 1200.0)
        m.update([], [record()], {}, TEMPLATE, 1300.0)
        second = disk("sda", "8-8", "NEW", at=1400.0)
        slots, _ = m.update([second], [record()], {}, TEMPLATE, 1405.0)
        assert slots[0].state is SlotState.REMOVED
        assert slots[1].disk is second

    def test_removed_slot_not_taken_by_new_disk_same_tick(self) -> None:
        """同じtickでP1の完了ディスクが抜去され未知ポートのディスクが初見でも、P1はREMOVED。"""
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
        """途中で例外が出ても内部状態は前回のまま。"""
        m = self.mapper(())
        m.update([disk("sda", "8-8")], [], {}, TEMPLATE, 1005.0)
        with pytest.raises(ValueError, match="kernel"):
            m.update([disk("sda", "8-8"), disk("bad name", "9-9")], [], {}, TEMPLATE, 1006.0)
        slots, _ = m.update([disk("sda", "8-8")], [], {}, TEMPLATE, 1007.0)
        assert slots[0].disk is not None and slots[0].disk.kernel_name == "sda"
