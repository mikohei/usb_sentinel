"""Storage backends for inspection records (design doc section 5.7).

Recording must not affect whether an inspection succeeds. Each method never raises; it returns
a failure reason string instead. Only orchestrator and cli import this package, never the
reverse.
"""

from __future__ import annotations

from typing import Protocol

from ..config import Config
from ..models import InspectionResult
from .sqlite_row import row_from_result
from .sqlite_store import SchemaTooNewError, SqliteStore

__all__ = [
    "InspectionStore",
    "NullStore",
    "SchemaTooNewError",
    "SqliteStore",
    "row_from_result",
    "store_for",
]


class InspectionStore(Protocol):
    """Record the start and completion of an inspection. Returns a failure reason (None on OK)."""

    def record_started(self, result: InspectionResult) -> str | None:
        """Write the start record. Called once right after output_dir is decided."""
        ...

    def record_completed(self, result: InspectionResult, digest: str) -> str | None:
        """Write the completion record. digest is the SHA-256 of result.json."""
        ...


class NullStore:
    """Record nothing (sqlite_path = "")."""

    def record_started(self, result: InspectionResult) -> str | None:
        """Do nothing and return success (None)."""
        return None

    def record_completed(self, result: InspectionResult, digest: str) -> str | None:
        """Do nothing and return success (None)."""
        return None


def store_for(config: Config) -> InspectionStore:
    """Choose a store from the config. NullStore when sqlite_path is None."""
    if config.sqlite_path is None:
        return NullStore()
    return SqliteStore(config.sqlite_path)
