"""SQLite connection and schema management for leadagent storage.

Sets PRAGMA journal_mode=WAL and busy_timeout=5000.
Maintains schema_version and creates tables: runs, evidence, research_events.
Uses a threading.Lock to serialize write transactions and keep transactions short.
Contains no network or model calls.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1


def open_connection(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection given a path argument (never hardcoded).

    Configures journal_mode=WAL and busy_timeout=5000, and ensures the
    schema_version, runs, evidence, and research_events tables exist.
    """
    path_str = str(path)
    if path_str != ":memory:":
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p), check_same_thread=False, timeout=5.0)
    else:
        conn = sqlite3.connect(":memory:", check_same_thread=False, timeout=5.0)

    conn.row_factory = sqlite3.Row

    # Configure pragmas
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")

    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """Initialize schema tables and apply versioning in a short transaction."""
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL,
                description TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                input_type TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                stop_reason TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                config_snapshot TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY,
                source_uri TEXT NOT NULL,
                url TEXT,
                source_kind TEXT NOT NULL,
                acquisition_method TEXT NOT NULL,
                fetched_at TEXT,
                title TEXT,
                published_at TEXT,
                snippet TEXT NOT NULL,
                page_number INTEGER,
                content_hash TEXT,
                is_first_party INTEGER NOT NULL DEFAULT 0,
                fetch_status TEXT NOT NULL,
                registrable_domain TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_evidence_source_uri ON evidence(source_uri);

            CREATE TABLE IF NOT EXISTS research_events (
                event_id TEXT PRIMARY KEY,
                org_key TEXT NOT NULL,
                action_kind TEXT NOT NULL,
                source_or_query TEXT NOT NULL DEFAULT '',
                outcome TEXT NOT NULL DEFAULT '',
                error_class TEXT NOT NULL DEFAULT '',
                next_action_kind TEXT,
                timestamp TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_research_events_org ON research_events(org_key);
            """
        )

        now_utc = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT OR IGNORE INTO schema_version (version, applied_at, description)
            VALUES (?, ?, ?);
            """,
            (SCHEMA_VERSION, now_utc, "Initial schema: runs, evidence, research_events"),
        )


class SQLiteDatabase:
    """Thread-safe SQLite database manager.

    Maintains one threading.Lock for writes to ensure serialized write transactions
    without blocking readers.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path) if str(path) != ":memory:" else path
        self.lock = threading.Lock()
        self.connection = open_connection(self.path)

    @contextmanager
    def write_transaction(self) -> Iterator[sqlite3.Connection]:
        """Context manager acquiring the write lock and executing a short transaction."""
        with self.lock:
            with self.connection:
                yield self.connection

    def close(self) -> None:
        """Close database connection safely."""
        with self.lock:
            self.connection.close()

    def __enter__(self) -> SQLiteDatabase:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
