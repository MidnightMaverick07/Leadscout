"""LangGraph graph-state definitions and reducer functions.

Defines ``RunState`` (top-level graph) and ``OrgState`` (org subgraph),
plus all reducer functions required by §3.4.4.

Design rules (§3.4.4):
- Reducers are pure, deterministic, order-independent and idempotent.
- Applying the same update twice must not duplicate anything.
- ``merge_leads`` keys on ``lead_key``; collisions keep the entry with more
  evidence and record a ``duplicate_lead`` rejection for the other.
- ``merge_rejections`` dedupes on ``(org_key, candidate_key, reason)``.
- ``research_events`` is append-only (bounded tail kept in state; full trail
  goes to SQLite by spoke nodes — never inside a held DB transaction).
- Nodes return partial updates only; they never mutate input state in place.

LangGraph (L2) never contains policy. All reducers in this file are plain
Python functions testable without LangGraph, a network, or an LLM.

Section references: §3.4.4, §3.4.3, §4.8.
"""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, Literal, TypedDict

from leadagent.errors import RejectionReason
from leadagent.models import (
    BudgetSnapshot,
    EventProfile,
    Lead,
    OrgInput,
    QueryPlan,
    Rejection,
    ResearchAction,
    ResearchActionKind,
    ResearchEvent,
    RoundYield,
    RunInput,
    SheetWriteState,
    Target,
)

# ── Reducer implementations ───────────────────────────────────────────────────


def merge_targets(
    existing: dict[str, Target],
    update: dict[str, Target],
) -> dict[str, Target]:
    """Merge two target dicts keyed by normalized org key.

    On collision keep the entry with the higher relevance_score.
    Pure, idempotent, order-independent.
    """
    result = dict(existing)
    for key, new_target in update.items():
        if key not in result or new_target.relevance_score > result[key].relevance_score:
            result[key] = new_target
    return result


def merge_leads(
    existing: dict[str, Lead],
    update: dict[str, Lead],
) -> dict[str, Lead]:
    """Merge two lead dicts keyed by ``lead_key``.

    On collision keep the entry with the larger evidence set, per §3.4.4 rule 2.
    The 'losing' entry is not tracked here; the ``merge_results`` node logs
    a duplicate_lead rejection separately.
    """
    result = dict(existing)
    for key, new_lead in update.items():
        if key not in result:
            result[key] = new_lead
        else:
            # Keep whichever has more evidence IDs as the stronger entry.
            if len(new_lead.evidence_ids) > len(result[key].evidence_ids):
                result[key] = new_lead
    return result


def merge_rejections(
    existing: list[Rejection],
    update: list[Rejection],
) -> list[Rejection]:
    """Merge two rejection lists, deduplicating on (org_key, candidate_key, reason).

    Pure, idempotent, order-independent per §3.4.4 rule 3.
    """
    seen: set[tuple[str, str, str]] = set()
    result: list[Rejection] = []
    for r in existing:
        key = (r.org_key, r.candidate_key, r.reason)
        if key not in seen:
            seen.add(key)
            result.append(r)
    for r in update:
        key = (r.org_key, r.candidate_key, r.reason)
        if key not in seen:
            seen.add(key)
            result.append(r)
    return result


# ── RunState (top-level LangGraph graph) ─────────────────────────────────────


class RunState(TypedDict, total=False):
    """Typed state for the top-level LangGraph ``StateGraph``.

    Keys annotated with a reducer are written by multiple branches; the rest
    are owned by a single node and replaced wholesale.  Never put raw page
    text, file handles, locks, or provider clients here.

    Section reference: §3.4.4.
    """

    run_id: str
    input: RunInput  # validated prompt or PDF reference
    input_type: Literal["prompt", "pdf"]
    event_profile: EventProfile | None
    query_plan: QueryPlan | None

    # Parallel-branch keys — must have reducers
    targets: Annotated[dict[str, Target], merge_targets]  # key: normalized org key
    pending_targets: list[Target]  # replaced each round by lead_discoverer
    leads: Annotated[dict[str, Lead], merge_leads]  # key: lead_key (accepted only)
    rejected: Annotated[list[Rejection], merge_rejections]  # dedupe on triple
    research_events: Annotated[list[ResearchEvent], operator.add]  # append-only trail
    visited_org_keys: Annotated[set[str], operator.or_]
    visited_urls: Annotated[set[str], operator.or_]
    evidence_ids: Annotated[set[str], operator.or_]  # raw text lives in SQLite

    # Single-writer keys
    round_yields: Annotated[list[RoundYield], operator.add]
    budget_snapshot: BudgetSnapshot  # read-only snapshot; authority is BudgetLedger
    round_number: int
    stop_reason: str | None
    sheet_write_cursor: SheetWriteState


# ── OrgState (org_researcher subgraph) ───────────────────────────────────────


class OrgState(TypedDict, total=False):
    """Private state for the per-organization research subgraph.

    Only the final Lead/Rejection, ResearchEvents and evidence IDs flow back
    to RunState.  Everything else is internal to the subgraph (§3.4.3).
    """

    # Input (set by the wrapper node from OrgInput)
    run_id: str
    target: Target
    event_profile_summary: str
    category: str  # Category Literal

    # Domain resolution
    official_domain: str | None
    domain_trust_state: Literal["trusted", "tentative", "organization_unresolved"]
    domain_evidence_ids: list[str]
    official_domain_confidence: float

    # Page work
    page_queue: list[str]  # URLs to fetch, in priority order
    tried_urls: set[str]
    retries_by_url: dict[str, int]  # url -> retry count

    # People/channel work
    candidates: list[dict]  # list of PersonCandidate dicts, max 2
    channels_tried: list[str]  # normalized channel values already tried
    current_candidate_index: int

    # Budget counters (per-org; global authority lives in BudgetLedger)
    searches_used: int
    fetches_used: int
    llm_calls_used: int
    steps_used: int

    # Router state
    last_failure_class: str
    pending_action: ResearchAction | None
    gate_failure_reason: str | None

    # Evidence collected this org
    evidence_ids: list[str]

    # Output accumulated during research
    events: list[ResearchEvent]  # append-only trail for this org

    # Terminal outputs (one of these is set when the subgraph ends)
    accepted_lead: Lead | None
    rejection: Rejection | None


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_empty_run_state(run_id: str) -> RunState:
    """Return a minimal valid RunState for a new run."""
    return RunState(
        run_id=run_id,
        input_type="prompt",
        event_profile=None,
        query_plan=None,
        targets={},
        pending_targets=[],
        leads={},
        rejected=[],
        research_events=[],
        visited_org_keys=set(),
        visited_urls=set(),
        evidence_ids=set(),
        round_yields=[],
        budget_snapshot=BudgetSnapshot(),
        round_number=0,
        stop_reason=None,
        sheet_write_cursor=SheetWriteState(),
    )


def make_empty_org_state(org_input: OrgInput) -> OrgState:
    """Return a minimal valid OrgState initialised from an OrgInput."""
    return OrgState(
        run_id=org_input.run_id,
        target=org_input.target,
        event_profile_summary=org_input.event_profile_summary,
        category=org_input.category,
        official_domain=org_input.target.official_domain,
        domain_trust_state="organization_unresolved",
        domain_evidence_ids=[],
        official_domain_confidence=0.0,
        page_queue=[],
        tried_urls=set(),
        retries_by_url={},
        candidates=[],
        channels_tried=[],
        current_candidate_index=0,
        searches_used=0,
        fetches_used=0,
        llm_calls_used=0,
        steps_used=0,
        last_failure_class="",
        pending_action=None,
        gate_failure_reason=None,
        evidence_ids=[],
        events=[],
        accepted_lead=None,
        rejection=None,
    )


def make_rejection(
    org_key: str,
    candidate_key: str,
    reason: RejectionReason,
    details: str = "",
) -> Rejection:
    """Convenience factory for Rejection model with UTC timestamp."""
    return Rejection(
        org_key=org_key,
        candidate_key=candidate_key,
        reason=reason.value,
        details=details,
        rejected_at=datetime.now(tz=timezone.utc),
    )
