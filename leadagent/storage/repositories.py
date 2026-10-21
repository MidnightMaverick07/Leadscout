"""Repositories for durable storage of evidence, research events, and runs.

Follows the dependency injection pattern with all database connections or managers
passed through constructors. No global singletons are used.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from leadagent.errors import EvidenceNotFoundError, LeadscoutError
from leadagent.models import Evidence, ResearchEvent, RunRecord
from leadagent.storage.sqlite import SQLiteDatabase, open_connection


class RunNotFoundError(LeadscoutError, KeyError):
    """Raised when a run ID does not exist in the store."""


class EvidenceRepository:
    """Repository managing evidence, research events, and run records."""

    def __init__(self, db: SQLiteDatabase | sqlite3.Connection | str | Path) -> None:
        if isinstance(db, SQLiteDatabase):
            self._db: SQLiteDatabase | None = db
            self._conn: sqlite3.Connection = db.connection
            self._lock: threading.Lock = db.lock
        elif isinstance(db, sqlite3.Connection):
            self._db = None
            self._conn = db
            self._lock = getattr(db, "_write_lock", None) or threading.Lock()
        elif isinstance(db, (str, Path)):
            self._db = SQLiteDatabase(db)
            self._conn = self._db.connection
            self._lock = self._db.lock
        else:
            raise TypeError(f"Unsupported db dependency type: {type(db)}")

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the underlying SQLite connection for inspection and read operations."""
        return self._conn

    def save_evidence(self, evidence: Evidence) -> str:
        """Save evidence record to the database.

        ID is computed as sha256(source_uri + content_hash) in hex format.
        Saving the same record twice returns the same ID without creating a duplicate row.
        """
        source_uri = evidence.source_uri or ""
        content_hash = evidence.content_hash or ""
        evidence_id = hashlib.sha256(f"{source_uri}{content_hash}".encode("utf-8")).hexdigest()

        fetched_at_str = (
            evidence.fetched_at.astimezone(timezone.utc).isoformat()
            if evidence.fetched_at is not None
            else None
        )
        published_at_str = (
            evidence.published_at.astimezone(timezone.utc).isoformat()
            if evidence.published_at is not None
            else None
        )
        url_str = str(evidence.url) if evidence.url is not None else None

        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO evidence (
                        id, source_uri, url, source_kind, acquisition_method,
                        fetched_at, title, published_at, snippet, page_number,
                        content_hash, is_first_party, fetch_status, registrable_domain
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO NOTHING;
                    """,
                    (
                        evidence_id,
                        evidence.source_uri,
                        url_str,
                        evidence.source_kind,
                        evidence.acquisition_method,
                        fetched_at_str,
                        evidence.title,
                        published_at_str,
                        evidence.snippet,
                        evidence.page_number,
                        evidence.content_hash,
                        1 if evidence.is_first_party else 0,
                        evidence.fetch_status,
                        evidence.registrable_domain,
                    ),
                )

        return evidence_id

    def get_evidence(self, id: str) -> Evidence:
        """Retrieve evidence by ID.

        Raises EvidenceNotFoundError if the record does not exist.
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM evidence WHERE id = ?;", (id,))
        row = cursor.fetchone()
        if row is None:
            raise EvidenceNotFoundError(f"Evidence record with id {id!r} not found")

        return Evidence(
            id=row["id"],
            source_uri=row["source_uri"],
            url=row["url"],
            source_kind=row["source_kind"],
            acquisition_method=row["acquisition_method"],
            fetched_at=(
                datetime.fromisoformat(row["fetched_at"]) if row["fetched_at"] is not None else None
            ),
            title=row["title"],
            published_at=(
                datetime.fromisoformat(row["published_at"])
                if row["published_at"] is not None
                else None
            ),
            snippet=row["snippet"],
            page_number=row["page_number"],
            content_hash=row["content_hash"],
            is_first_party=bool(row["is_first_party"]),
            fetch_status=row["fetch_status"],
            registrable_domain=row["registrable_domain"],
        )

    def append_research_event(self, event: ResearchEvent) -> None:
        """Append a research event. Idempotent on event_id."""
        ts_str = event.timestamp.astimezone(timezone.utc).isoformat()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO research_events (
                        event_id, org_key, action_kind, source_or_query,
                        outcome, error_class, next_action_kind, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO NOTHING;
                    """,
                    (
                        event.event_id,
                        event.org_key,
                        event.action_kind,
                        event.source_or_query,
                        event.outcome,
                        event.error_class,
                        event.next_action_kind,
                        ts_str,
                    ),
                )

    def get_research_events(self, org_key: str | None = None) -> list[ResearchEvent]:
        """Retrieve research events, optionally filtered by org_key."""
        cursor = self._conn.cursor()
        if org_key is not None:
            cursor.execute(
                "SELECT * FROM research_events WHERE org_key = ? ORDER BY timestamp ASC;",
                (org_key,),
            )
        else:
            cursor.execute("SELECT * FROM research_events ORDER BY timestamp ASC;")

        rows = cursor.fetchall()
        events: list[ResearchEvent] = []
        for row in rows:
            events.append(
                ResearchEvent(
                    event_id=row["event_id"],
                    org_key=row["org_key"],
                    action_kind=row["action_kind"],
                    source_or_query=row["source_or_query"],
                    outcome=row["outcome"],
                    error_class=row["error_class"],
                    next_action_kind=row["next_action_kind"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                )
            )
        return events

    def create_run(self, run: RunRecord) -> None:
        """Create or update a RunRecord."""
        started_at_str = run.started_at.astimezone(timezone.utc).isoformat()
        finished_at_str = (
            run.finished_at.astimezone(timezone.utc).isoformat()
            if run.finished_at is not None
            else None
        )
        config_snapshot_json = json.dumps(run.config_snapshot)

        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO runs (
                        run_id, input_type, input_hash, status,
                        stop_reason, started_at, finished_at, config_snapshot
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        input_type = excluded.input_type,
                        input_hash = excluded.input_hash,
                        status = excluded.status,
                        stop_reason = excluded.stop_reason,
                        started_at = excluded.started_at,
                        finished_at = excluded.finished_at,
                        config_snapshot = excluded.config_snapshot;
                    """,
                    (
                        run.run_id,
                        run.input_type,
                        run.input_hash,
                        run.status,
                        run.stop_reason,
                        started_at_str,
                        finished_at_str,
                        config_snapshot_json,
                    ),
                )

    def get_run(self, run_id: str) -> RunRecord:
        """Retrieve a RunRecord by run_id.

        Raises RunNotFoundError if the run does not exist.
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM runs WHERE run_id = ?;", (run_id,))
        row = cursor.fetchone()
        if row is None:
            raise RunNotFoundError(f"Run {run_id!r} not found in store")

        return RunRecord(
            run_id=row["run_id"],
            input_type=row["input_type"],
            input_hash=row["input_hash"],
            status=row["status"],
            stop_reason=row["stop_reason"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=(
                datetime.fromisoformat(row["finished_at"])
                if row["finished_at"] is not None
                else None
            ),
            config_snapshot=(
                json.loads(row["config_snapshot"]) if row["config_snapshot"] else {}
            ),
        )


# Aliases for flexible injection across subsystems
EvidenceStore = EvidenceRepository
StorageRepository = EvidenceRepository
RunRepository = EvidenceRepository
ResearchEventRepository = EvidenceRepository
