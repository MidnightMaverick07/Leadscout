"""Storage subsystem for leadagent.

Provides SQLite-backed persistence for runs, evidence, and research events.
"""

from leadagent.storage.repositories import (
    EvidenceRepository,
    EvidenceStore,
    ResearchEventRepository,
    RunNotFoundError,
    RunRepository,
    StorageRepository,
)
from leadagent.storage.sqlite import SQLiteDatabase, open_connection

__all__ = [
    "SQLiteDatabase",
    "open_connection",
    "EvidenceRepository",
    "EvidenceStore",
    "StorageRepository",
    "RunRepository",
    "ResearchEventRepository",
    "RunNotFoundError",
]
