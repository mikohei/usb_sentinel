from dataclasses import dataclass, replace

from .config import PortMapping
from .models import AttachedDisk, ResultRecord, ResultSummary, Slot, SlotState
from .result_file import serial_for_dir
from .units import ACTIVE_STATES, unit_name

# tick間でディスクを同一視するキー（D12）。attached_atが入るので抜き差しで必ず変わる。
DiskKey = tuple[str, str, float]


@dataclass(frozen=True)
class Retained:
    """抜去後も表示し続ける結果と、抜去前のディスクの表示属性。"""

    summary: ResultSummary
    removed_at: float
    fallback: bool
    kernel_name: str


def disk_key(disk: AttachedDisk) -> DiskKey:
    """ディスクの同一性キーを返す。"""
    return (disk.kernel_name, disk.port_path, disk.attached_at)


def bind_result(disk: AttachedDisk, records: list[ResultRecord]) -> ResultRecord | None:
    """ディスクに結び付く読めた結果を選ぶ（設計書3.5＋port_path優先）。

    候補はmtimeが接続時刻以降で読めているもの。主キーはport_path（結果に無ければ
    kernel_name）。複数ならディレクトリ名のシリアル一致を優先し、同点なら名前最大。
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
    """ディスクに結び付く読み取り不能（broken）な結果を選ぶ（D8）。"""
    wanted = serial_for_dir(disk.serial, disk.kernel_name)
    matches = [
        record
        for record in records
        if record.broken and record.mtime >= disk.attached_at and record.serial_part == wanted
    ]
    return max(matches, key=lambda record: record.name) if matches else None


def bind_started(disk: AttachedDisk, records: list[ResultRecord]) -> ResultRecord | None:
    """ディスクに結び付く「開始したが完了結果が無い」記録を選ぶ。

    started行はDB経路（ResultStore.collect）だけが作る。ディレクトリ走査では従来どおり現れない。
    候補はmtime（開始時刻）が接続時刻以降のもの。port_pathがあればport_path、無ければ
    kernel_nameで結び付け、複数なら名前最大。
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
    """ディスク・結果・ユニット状態からスロット一覧を組む。内部状態は一括で差し替える。"""

    def __init__(
        self,
        ports: tuple[PortMapping, ...],
        *,
        retain_s: float,
        waiting_s: float = 20.0,
        slot_count: int = 4,
    ) -> None:
        """ポート対応表と時間の閾値を受け取り、内部状態を空で始める。

        Args:
            ports: スロット順のポート対応表（label、paths）。
            retain_s: 抜去後に結果を表示し続ける秒数。
            waiting_s: 接続直後に「待機中」とみなす秒数。これを過ぎて結果が無ければ「結果なし」。
            slot_count: スロット数（画面のタイル数）。
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
        """スロットの表示名。対応表に無いスロットはP<n>。"""
        return self.ports[index].label if index < len(self.ports) else f"P{index + 1}"

    def _assign(
        self, disks: list[AttachedDisk]
    ) -> tuple[dict[int, AttachedDisk], dict[DiskKey, int], int]:
        """ディスクをスロットに割り当てる。

        Returns:
            (スロット番号→ディスク、新しいフォールバック表、あふれた台数)。
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
        # 既存のフォールバック割り当てを、確定ディスクに取られていなければ維持する。
        fallback: dict[DiskKey, int] = {}
        pending: list[AttachedDisk] = []
        for disk in unknown:
            index = self._fallback.get(disk_key(disk))
            if index is not None and index not in assigned:
                assigned[index] = disk
                fallback[disk_key(disk)] = index
            else:
                pending.append(disk)
        # 押し出された・初見のディスクは、空のスロット→保持中のスロット（古い順）の順で埋める。
        # 同じtickで抜去された完了スロット（_last_doneにあり、まだ_retainedに無い）は
        # これから保持に入るので空きとして扱わない。
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
        """接続中ディスクのスロット状態を設計書3.4の順で決める。

        started行（DB経路だけが作る）は、ユニット状態が分からなければ検査中、
        分かっていて非activeなら結果なし（result.jsonを書けずに終わった）とみなす。
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
        """1tick分のスロット一覧を計算し、成功したら内部状態を差し替える。

        Args:
            disks: 接続中のディスク。
            records: 結果レコード。
            unit_states: ユニット名→ActiveState。
            unit_template: ユニット名のテンプレート。
            now: 現在時刻（エポック秒）。

        Returns:
            (スロット一覧、表示できなかった台数)。
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
                    # 抜去したtick。removed_atを今に差し替えて保持を始める。
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
