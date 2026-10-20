"""Unit tests for leadagent/state.py — reducers and state helpers.

Tests cover §3.4.4 reducer rules:
1. Pure, deterministic, order-independent, idempotent.
2. merge_leads: collision keeps the entry with more evidence; duplicate is noted.
3. merge_rejections: dedupes on (org_key, candidate_key, reason).
4. research_events uses operator.add (append-only).
5. Nodes return partial updates; reducers never mutate inputs.

Permutation tests: for each reducer, apply all orderings of a set of updates
and assert every ordering produces an identical result.

Section references: §3.4.4, §13.1.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone

import pytest

from leadagent.errors import RejectionReason
from leadagent.models import (
    Lead,
    OrgInput,
    Rejection,
    Target,
)
from leadagent.state import (
    make_empty_org_state,
    make_empty_run_state,
    make_rejection,
    merge_leads,
    merge_rejections,
    merge_targets,
)

_NOW = datetime(2026, 10, 20, 7, 0, 0, tzinfo=timezone.utc)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _target(org_key: str, score: float = 0.8) -> Target:
    return Target(
        id=org_key,
        name=org_key.replace("-", " ").title(),
        category="Sponsor",
        origin="prompt_search",
        relevance_score=score,
    )


def _lead(lead_key: str, n_evidence: int = 1) -> Lead:
    return Lead(
        org_name=lead_key,
        category="Sponsor",
        city="Kolkata",
        contact_name="Jane Doe",
        contact_role="CMO",
        channel_type="email",
        channel=f"jane@{lead_key}.example.com",
        status="Verified",
        evidence_ids=[f"ev-{i}" for i in range(n_evidence)],
        lead_key=lead_key,
        accepted_at=_NOW,
    )


def _rejection(org_key: str, candidate_key: str, reason: str) -> Rejection:
    return Rejection(
        org_key=org_key,
        candidate_key=candidate_key,
        reason=reason,
        rejected_at=_NOW,
    )


# ── merge_targets ─────────────────────────────────────────────────────────────


class TestMergeTargets:
    def test_empty_plus_empty_is_empty(self) -> None:
        assert merge_targets({}, {}) == {}

    def test_new_key_added(self) -> None:
        result = merge_targets({}, {"acme": _target("acme")})
        assert "acme" in result

    def test_existing_key_preserved_when_update_has_lower_score(self) -> None:
        orig = {"acme": _target("acme", score=0.9)}
        update = {"acme": _target("acme", score=0.5)}
        result = merge_targets(orig, update)
        assert result["acme"].relevance_score == 0.9

    def test_higher_score_in_update_wins(self) -> None:
        orig = {"acme": _target("acme", score=0.5)}
        update = {"acme": _target("acme", score=0.9)}
        result = merge_targets(orig, update)
        assert result["acme"].relevance_score == 0.9

    def test_idempotent(self) -> None:
        d = {"acme": _target("acme", score=0.8)}
        r1 = merge_targets(d, d)
        r2 = merge_targets(r1, d)
        assert r1 == r2

    def test_does_not_mutate_input(self) -> None:
        orig = {"acme": _target("acme", score=0.8)}
        update = {"beta": _target("beta", score=0.7)}
        orig_copy = dict(orig)
        merge_targets(orig, update)
        assert orig == orig_copy

    def test_permutation_order_independent(self) -> None:
        """Merging any permutation of updates produces the same result."""
        targets = {
            f"org-{i}": _target(f"org-{i}", score=round(0.1 * i, 1))
            for i in range(1, 5)
        }
        updates = list(targets.items())
        results = []
        for perm in itertools.permutations(updates):
            state: dict[str, Target] = {}
            for key, t in perm:
                state = merge_targets(state, {key: t})
            results.append(state)
        # All permutations yield the same set of keys and scores
        for r in results[1:]:
            assert r.keys() == results[0].keys()
            for k in results[0]:
                assert r[k].relevance_score == results[0][k].relevance_score

    def test_collision_same_score_keeps_first(self) -> None:
        orig = {"acme": _target("acme", score=0.8)}
        update = {"acme": _target("acme", score=0.8)}
        result = merge_targets(orig, update)
        assert result["acme"] is orig["acme"]


# ── merge_leads ───────────────────────────────────────────────────────────────


class TestMergeLeads:
    def test_empty_plus_empty_is_empty(self) -> None:
        assert merge_leads({}, {}) == {}

    def test_new_lead_added(self) -> None:
        result = merge_leads({}, {"lk-1": _lead("lk-1")})
        assert "lk-1" in result

    def test_collision_keeps_more_evidence(self) -> None:
        weak = _lead("lk-1", n_evidence=1)
        strong = _lead("lk-1", n_evidence=3)
        # strong in existing, weak in update → keep strong
        result = merge_leads({"lk-1": strong}, {"lk-1": weak})
        assert len(result["lk-1"].evidence_ids) == 3
        # strong in update, weak in existing → keep strong
        result2 = merge_leads({"lk-1": weak}, {"lk-1": strong})
        assert len(result2["lk-1"].evidence_ids) == 3

    def test_idempotent(self) -> None:
        d = {"lk-1": _lead("lk-1")}
        r1 = merge_leads(d, d)
        r2 = merge_leads(r1, d)
        assert r1.keys() == r2.keys()
        for k in r1:
            assert r1[k].lead_key == r2[k].lead_key

    def test_does_not_mutate_input(self) -> None:
        existing = {"lk-1": _lead("lk-1")}
        update = {"lk-2": _lead("lk-2")}
        existing_copy = dict(existing)
        merge_leads(existing, update)
        assert existing == existing_copy

    def test_permutation_order_independent(self) -> None:
        """For non-colliding keys all permutations yield the same result."""
        leads = {f"lk-{i}": _lead(f"lk-{i}", n_evidence=i) for i in range(1, 5)}
        items = list(leads.items())
        results = []
        for perm in itertools.permutations(items):
            state: dict[str, Lead] = {}
            for key, lead in perm:
                state = merge_leads(state, {key: lead})
            results.append(frozenset(state.keys()))
        assert all(r == results[0] for r in results)

    def test_permutation_collision_order_independent(self) -> None:
        """Collision resolution must be order-independent.

        Both (weak then strong) and (strong then weak) must keep
        the entry with more evidence IDs.
        """
        weak = _lead("lk-1", n_evidence=1)
        strong = _lead("lk-1", n_evidence=5)

        result_a = merge_leads({"lk-1": weak}, {"lk-1": strong})
        result_b = merge_leads({"lk-1": strong}, {"lk-1": weak})

        # Both should keep the strong lead
        assert len(result_a["lk-1"].evidence_ids) == 5
        assert len(result_b["lk-1"].evidence_ids) == 5

    def test_multiple_distinct_leads_all_preserved(self) -> None:
        a = _lead("lk-a")
        b = _lead("lk-b")
        c = _lead("lk-c")
        result = merge_leads({"lk-a": a}, {"lk-b": b, "lk-c": c})
        assert set(result.keys()) == {"lk-a", "lk-b", "lk-c"}


# ── merge_rejections ──────────────────────────────────────────────────────────


class TestMergeRejections:
    def test_empty_plus_empty_is_empty(self) -> None:
        assert merge_rejections([], []) == []

    def test_new_rejection_added(self) -> None:
        r = _rejection("org-1", "cand-1", "generic_email")
        result = merge_rejections([], [r])
        assert len(result) == 1

    def test_duplicate_on_triple_not_added(self) -> None:
        r = _rejection("org-1", "cand-1", "generic_email")
        result = merge_rejections([r], [r])
        assert len(result) == 1

    def test_same_org_different_reason_both_kept(self) -> None:
        r1 = _rejection("org-1", "cand-1", "generic_email")
        r2 = _rejection("org-1", "cand-1", "email_domain_mismatch")
        result = merge_rejections([r1], [r2])
        assert len(result) == 2

    def test_same_reason_different_candidate_both_kept(self) -> None:
        r1 = _rejection("org-1", "cand-1", "generic_email")
        r2 = _rejection("org-1", "cand-2", "generic_email")
        result = merge_rejections([r1], [r2])
        assert len(result) == 2

    def test_idempotent(self) -> None:
        r = _rejection("org-1", "cand-1", "no_people_found")
        r1 = merge_rejections([r], [r])
        r2 = merge_rejections(r1, [r])
        assert len(r2) == 1

    def test_does_not_mutate_input(self) -> None:
        r1 = _rejection("org-1", "cand-1", "generic_email")
        existing = [r1]
        existing_copy = list(existing)
        r2 = _rejection("org-2", "cand-2", "no_people_found")
        merge_rejections(existing, [r2])
        assert existing == existing_copy

    def test_permutation_order_independent(self) -> None:
        """All orderings of distinct rejections produce the same set."""
        rejections = [
            _rejection(f"org-{i}", f"cand-{i}", code)
            for i, code in enumerate(
                ["generic_email", "no_people_found", "budget_exhausted", "city_unverified"]
            )
        ]
        items = [([r], []) for r in rejections]

        # Build combined result in all 24 orders
        results = []
        for perm in itertools.permutations(rejections):
            state: list[Rejection] = []
            for r in perm:
                state = merge_rejections(state, [r])
            triple_set = frozenset(
                (r.org_key, r.candidate_key, r.reason) for r in state
            )
            results.append(triple_set)

        assert all(r == results[0] for r in results)


# ── make_empty_run_state ──────────────────────────────────────────────────────


class TestMakeEmptyRunState:
    def test_returns_valid_state(self) -> None:
        state = make_empty_run_state("run-001")
        assert state["run_id"] == "run-001"
        assert state["leads"] == {}
        assert state["rejected"] == []
        assert state["round_number"] == 0
        assert state["stop_reason"] is None

    def test_sets_are_empty(self) -> None:
        state = make_empty_run_state("run-002")
        assert state["visited_org_keys"] == set()
        assert state["visited_urls"] == set()
        assert state["evidence_ids"] == set()


# ── make_empty_org_state ──────────────────────────────────────────────────────


class TestMakeEmptyOrgState:
    def test_returns_valid_state(self) -> None:
        t = _target("acme-corp")
        oi = OrgInput(
            run_id="run-001",
            target=t,
            event_profile_summary="TechFest 2027",
            category="Sponsor",
        )
        state = make_empty_org_state(oi)
        assert state["run_id"] == "run-001"
        assert state["domain_trust_state"] == "organization_unresolved"
        assert state["steps_used"] == 0
        assert state["accepted_lead"] is None
        assert state["rejection"] is None

    def test_inherits_target_domain(self) -> None:
        t = Target(
            id="t-1",
            name="Acme",
            category="Sponsor",
            official_domain="acme.example.com",
            origin="prompt_search",
            relevance_score=0.8,
        )
        oi = OrgInput(
            run_id="run-001",
            target=t,
            event_profile_summary="test",
            category="Sponsor",
        )
        state = make_empty_org_state(oi)
        assert state["official_domain"] == "acme.example.com"


# ── make_rejection ────────────────────────────────────────────────────────────


class TestMakeRejection:
    def test_creates_rejection_with_utc_timestamp(self) -> None:
        r = make_rejection("org-1", "cand-1", RejectionReason.GENERIC_EMAIL)
        assert r.org_key == "org-1"
        assert r.reason == "generic_email"
        assert r.rejected_at.tzinfo is not None

    def test_includes_details(self) -> None:
        r = make_rejection(
            "org-1", "cand-1", RejectionReason.BUDGET_EXHAUSTED, details="search cap"
        )
        assert r.details == "search cap"
