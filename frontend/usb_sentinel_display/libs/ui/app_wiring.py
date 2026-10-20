"""Startup options and Monitor construction used by app.py (split out to keep app.py short)."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ..config import DisplayConfig
from ..mapper import Mapper
from ..monitor import Monitor, Snapshot
from ..results import ResultStore
from ..store import SqliteIndex
from ..units import Runner


class MonitorLike(Protocol):
    """Monitor operations used by App (replaced by a fake in tests)."""

    def tick(self) -> Snapshot:
        """Return the display contents for one tick. Never raises (returns the previous slots)."""
        ...

    def force_reload(self) -> None:
        """Drop the result cache and reload every result.json on the next tick (F5)."""
        ...


@dataclass(frozen=True)
class AppOptions:
    """Startup options. windowed and kiosk are never given together (D13)."""

    windowed: bool
    kiosk: bool


def build_monitor(
    config: DisplayConfig, runner: Runner | None, now: Callable[[], float]
) -> Monitor:
    """Build the Monitor from the config (FEAT-002 injection point). Empty sqlite_path: no index."""
    index = SqliteIndex(config.sqlite_path) if config.sqlite_path is not None else None
    return Monitor(
        config,
        store=ResultStore(config.results_dir, index=index),
        runner=runner,
        mapper=Mapper(config.ports, retain_s=config.retain_minutes * 60),
        now=now,
    )
