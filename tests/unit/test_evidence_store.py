"""Unit tests for the evidence, event, and run storage layer.

All test fixtures are strictly synthetic.
Verifies WAL mode, duplicate handling, missing record errors, concurrency,
zero socket use, and UTC timestamp round trips.
"""

from __future__ import annotations

import socket
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from leadagent.errors import EvidenceNotFoundError
from leadagent.models import Evidence, ResearchEvent, RunRecord
from leadagent.storage.repositories import EvidenceRepository, RunNotFoundError
from leadagent.storage.sqlite import open_connection


# ── Synthetic fixtures (clearly marked as synthetic) ───────────────────────────


@pytest.fixture
def synthetic_evidence() -> Evidence:
    """Return a synthetic Evidence model instance with explicit synthetic markers."""
    return Evidence(
        id="",  # will be computed by repository
        source_uri="https://synthetic.example.org/test-sponsorship-page",
        url="https://synthetic.example.org/test-sponsorship-page",
        source_kind="official_site",
        acquisition_method="direct_fetch",
        fetched_at=datetime.now(timezone.utc),
        title="Synthetic Sponsor Leadership",
        snippet="Synthetic exact quote snippet for sponsorship contact person.",
        content_hash="synthetic_content_hash_abc123456789",
        fetch_status="fetched",
        is_first_party=True,
        registrable_domain="example.org",
    )


@pytest.fixture
def synthetic_research_event() -> ResearchEvent:
    """Return a synthetic ResearchEvent model instance."""
    return ResearchEvent(
        event_id="synthetic_event_run1_org1_seq1",
        org_key="synthetic_org_acme",
        action_kind="FETCH",
        source_or_query="https://synthetic.example.org/about",
        outcome="success",
        error_class="",
        next_action_kind="EXTRACT",
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def synthetic_run_record() -> RunRecord:
    """Return a synthetic RunRecord model instance."""
    return RunRecord(
        run_id="synthetic_run_20261021_001",
        input_type="prompt",
        input_hash="synthetic_input_hash_xyz987",
        status="running",
        started_at=datetime.now(timezone.utc),
        config_snapshot={"max_leads": 5, "profile": "free_tier"},
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_save_then_reload_returns_identical_snippet_and_content_hash(
    tmp_path: Path, synthetic_evidence: Evidence
) -> None:
    """Save then reload returns identical snippet and content_hash."""
    db_path = tmp_path / "reload_test.db"
    repo = EvidenceRepository(db_path)

    saved_id = repo.save_evidence(synthetic_evidence)
    reloaded = repo.get_evidence(saved_id)

    assert reloaded.id == saved_id
    assert reloaded.snippet == synthetic_evidence.snippet
    assert reloaded.content_hash == synthetic_evidence.content_hash
    assert reloaded.source_uri == synthetic_evidence.source_uri
    assert str(reloaded.url) == str(synthetic_evidence.url)
    assert reloaded.is_first_party is True


def test_missing_id_raises_evidence_not_found(tmp_path: Path) -> None:
    """Missing evidence id raises EvidenceNotFoundError."""
    db_path = tmp_path / "missing_test.db"
    repo = EvidenceRepository(db_path)

    missing_id = "synthetic_non_existent_id_99999"
    with pytest.raises(EvidenceNotFoundError) as exc_info:
        repo.get_evidence(missing_id)

    assert missing_id in str(exc_info.value)


def test_duplicate_save_creates_one_row(
    tmp_path: Path, synthetic_evidence: Evidence
) -> None:
    """Duplicate save creates exactly one row and returns the identical ID."""
    db_path = tmp_path / "dedupe_test.db"
    repo = EvidenceRepository(db_path)

    id1 = repo.save_evidence(synthetic_evidence)
    id2 = repo.save_evidence(synthetic_evidence)

    assert id1 == id2

    cursor = repo.connection.cursor()
    cursor.execute("SELECT COUNT(*) FROM evidence WHERE id = ?;", (id1,))
    count = cursor.fetchone()[0]
    assert count == 1


def test_journal_mode_is_wal(tmp_path: Path) -> None:
    """PRAGMA journal_mode is configured as wal on file-backed databases."""
    db_path = tmp_path / "wal_mode_test.db"
    conn = open_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode;")
    mode = cursor.fetchone()[0]
    conn.close()

    assert mode.lower() == "wal"


def test_concurrent_writes_do_not_raise_database_locked(tmp_path: Path) -> None:
    """Two threads writing concurrently do not raise 'database is locked'."""
    db_path = tmp_path / "concurrent_test.db"
    repo = EvidenceRepository(db_path)

    errors: list[Exception] = []

    def writer_worker(thread_num: int) -> None:
        try:
            for i in range(30):
                ev = Evidence(
                    id="",
                    source_uri=f"https://synthetic.example.org/worker_{thread_num}/item_{i}",
                    source_kind="official_site",
                    acquisition_method="direct_fetch",
                    snippet=f"Synthetic concurrent snippet from worker {thread_num} step {i}",
                    content_hash=f"synthetic_concurrent_hash_{thread_num}_{i}",
                    fetch_status="fetched",
                )
                repo.save_evidence(ev)

                evt = ResearchEvent(
                    event_id=f"synthetic_evt_worker_{thread_num}_{i}",
                    org_key=f"synthetic_org_{thread_num}",
                    action_kind="FETCH",
                    outcome="success",
                    timestamp=datetime.now(timezone.utc),
                )
                repo.append_research_event(evt)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=writer_worker, args=(1,))
    t2 = threading.Thread(target=writer_worker, args=(2,))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Concurrent write raised unexpected errors: {errors}"

    # Verify all records were stored
    cursor = repo.connection.cursor()
    cursor.execute("SELECT COUNT(*) FROM evidence;")
    ev_count = cursor.fetchone()[0]
    assert ev_count == 60

    cursor.execute("SELECT COUNT(*) FROM research_events;")
    evt_count = cursor.fetchone()[0]
    assert evt_count == 60


def test_no_socket_use(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No socket creation occurs during repository and SQLite operations."""

    def _forbid_socket(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Network socket call is forbidden in storage layer")

    monkeypatch.setattr(socket, "socket", _forbid_socket)

    db_path = tmp_path / "offline_isolation_test.db"
    repo = EvidenceRepository(db_path)

    # Save and reload evidence
    ev = Evidence(
        id="",
        source_uri="https://synthetic.example.org/isolated",
        source_kind="official_site",
        acquisition_method="direct_fetch",
        snippet="Synthetic isolated snippet text",
        content_hash="synthetic_isolated_hash",
        fetch_status="fetched",
    )
    ev_id = repo.save_evidence(ev)
    loaded_ev = repo.get_evidence(ev_id)
    assert loaded_ev.snippet == ev.snippet

    # Append research event
    evt = ResearchEvent(
        event_id="synthetic_event_isolated_1",
        org_key="synthetic_isolated_org",
        action_kind="SEARCH",
        outcome="ok",
        timestamp=datetime.now(timezone.utc),
    )
    repo.append_research_event(evt)

    # Create and retrieve run
    run = RunRecord(
        run_id="synthetic_run_isolated_1",
        input_type="prompt",
        input_hash="synthetic_hash_iso",
        started_at=datetime.now(timezone.utc),
    )
    repo.create_run(run)
    loaded_run = repo.get_run("synthetic_run_isolated_1")
    assert loaded_run.input_hash == "synthetic_hash_iso"


def test_aware_timestamp_survives_round_trip_as_utc(tmp_path: Path) -> None:
    """An aware timestamp (even with non-UTC offset) survives a round trip as UTC."""
    db_path = tmp_path / "timestamp_roundtrip_test.db"
    repo = EvidenceRepository(db_path)

    # Synthetic non-UTC timezone: IST (UTC+05:30)
    ist = timezone(timedelta(hours=5, minutes=30))
    aware_fetched_at = datetime(2026, 10, 21, 16, 45, 30, tzinfo=ist)
    aware_published_at = datetime(2026, 10, 15, 9, 15, 0, tzinfo=ist)

    evidence = Evidence(
        id="",
        source_uri="https://synthetic.example.org/timezone-check",
        source_kind="official_site",
        acquisition_method="direct_fetch",
        fetched_at=aware_fetched_at,
        published_at=aware_published_at,
        snippet="Synthetic snippet checking timezone conversion.",
        content_hash="synthetic_tz_hash_555",
        fetch_status="fetched",
    )

    ev_id = repo.save_evidence(evidence)
    reloaded = repo.get_evidence(ev_id)

    # Must be timezone-aware with UTC tzinfo
    assert reloaded.fetched_at is not None
    assert reloaded.fetched_at.tzinfo == timezone.utc
    assert reloaded.fetched_at == aware_fetched_at

    assert reloaded.published_at is not None
    assert reloaded.published_at.tzinfo == timezone.utc
    assert reloaded.published_at == aware_published_at


def test_research_event_append_is_idempotent(
    tmp_path: Path, synthetic_research_event: ResearchEvent
) -> None:
    """Appending the same research event twice does not produce duplicates."""
    db_path = tmp_path / "event_idempotency_test.db"
    repo = EvidenceRepository(db_path)

    repo.append_research_event(synthetic_research_event)
    repo.append_research_event(synthetic_research_event)

    events = repo.get_research_events("synthetic_org_acme")
    assert len(events) == 1
    assert events[0].event_id == synthetic_research_event.event_id


def test_create_run_and_get_run_round_trip(
    tmp_path: Path, synthetic_run_record: RunRecord
) -> None:
    """Creating a run record allows retrieving it with all metadata preserved."""
    db_path = tmp_path / "run_test.db"
    repo = EvidenceRepository(db_path)

    repo.create_run(synthetic_run_record)
    loaded_run = repo.get_run(synthetic_run_record.run_id)

    assert loaded_run.run_id == synthetic_run_record.run_id
    assert loaded_run.input_type == synthetic_run_record.input_type
    assert loaded_run.input_hash == synthetic_run_record.input_hash
    assert loaded_run.status == "running"
    assert loaded_run.started_at.tzinfo == timezone.utc
    assert loaded_run.config_snapshot == synthetic_run_record.config_snapshot


def test_get_run_missing_raises_error(tmp_path: Path) -> None:
    """Missing run_id raises RunNotFoundError."""
    db_path = tmp_path / "run_missing_test.db"
    repo = EvidenceRepository(db_path)

    with pytest.raises(RunNotFoundError):
        repo.get_run("synthetic_missing_run_id")
