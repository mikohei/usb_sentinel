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

# 情報ストリップの通知キー（文章はui/i18n.pyの"notice." + キー）。SQLite索引の
# db_missing／db_open_failed／db_schema_newはstore.pyのNOTICE_DB_*で定義し、collect()が返す。
NOTICE_RESULTS_MISSING = "results_missing"
NOTICE_RESULTS_PERMISSION = "results_permission"
NOTICE_SYSFS_MISSING = "sysfs_missing"
NOTICE_SYSFS_PERMISSION = "sysfs_permission"


@dataclass(frozen=True)
class Snapshot:
    """1tick分の表示内容。noticeは通知キー、errorは例外クラス名（文章化はGUI層）。"""

    slots: list[Slot]
    overflow: int = 0
    notice: str | None = None
    error: str | None = None
    tick_count: int = 0
    # 同じ例外がrepeat_threshold回続いたか（情報ストリップの文言を変える）。
    error_repeated: bool = False


def _empty_slots(mapper: Mapper) -> list[Slot]:
    """起動直後の表示に使う未接続スロット。"""
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
    """tick本体（sysfs列挙→結果列挙→systemctl→mapper）と例外規則（設計書4.1）。"""

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
        """依存を注入する。外部に触る関数（sysfs列挙、systemctl、時刻）はすべて差し替えられる。

        Args:
            config: 表示設定（sys_block、unit_templateを使う）。
            store: 結果の読み込み元。
            runner: systemctlの実行器。Noneならユニット状態を問い合わせない。
            mapper: スロットの割り当て。
            list_disks: sysfsからディスクを列挙する関数。
            now: 現在時刻（エポック秒）。
            repeat_threshold: 同じ例外が何回続いたら「繰り返し」とみなすか。
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
        """st_mtimeが取れないときの接続時刻。起動時刻、無ければ初見時刻（D9）。"""
        booted = boot_time()
        if booted is not None:
            return booted
        return self._first_seen.setdefault(kernel_name, self.now())

    def force_reload(self) -> None:
        """結果キャッシュを捨て、次のtickで全result.jsonを読み直す（F5）。"""
        self.store.reset()

    def tick_body(self) -> Snapshot:
        """1tick分の処理。sysfs／結果ルートの不在・権限はnoticeにし、例外は投げる。"""
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
        # SQLite索引の不調（store.NOTICE_DB_*）はsysfs・結果ルートの通知より後回し。
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
        """tick_body()を例外規則で包む。例外時は前回のスロットを保持して返す。"""
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
