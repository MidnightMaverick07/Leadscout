"""Unit tests for leadagent/models.py — Pydantic v2 data contracts.

Tests cover:
- Valid construction of each model.
- Field validators reject invalid/empty values.
- Lead.to_sheet_row() maps to exactly six cells in the correct order.
- Category and VerificationStatus only accept allowed Literal values.
- Evidence snippet must not be empty.
- PersonCandidate must have at least one evidence_id.
- Lead must have at least one evidence_id and non-empty required fields.
- Rejection reason is stored as a plain string (enum value).

Section references: §4.1–4.7, §13.1.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from leadagent.errors import RejectionReason
from leadagent.models import (
    BudgetSnapshot,
    Evidence,
    EventProfile,
    Lead,
    OrgInput,
    PersonCandidate,
    QueryFacet,
    QueryPlan,
    Rejection,
    ResearchEvent,
    RoundYield,
    RunInput,
    SearchResult,
    SheetWriteState,
    Target,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 10, 20, 7, 0, 0, tzinfo=timezone.utc)


def _evidence(**kwargs) -> Evidence:
    defaults = dict(
        id="ev-001",
        source_uri="https://example.com/team",
        source_kind="team_page",
        acquisition_method="direct_fetch",
        snippet="Jane Doe, Head of Marketing",
        is_first_party=True,
        fetch_status="fetched",
    )
    defaults.update(kwargs)
    return Evidence(**defaults)


def _target(**kwargs) -> Target:
    defaults = dict(
        id="t-001",
        name="Acme Corp",
        category="Sponsor",
        origin="prompt_search",
        relevance_score=0.8,
    )
    defaults.update(kwargs)
    return Target(**defaults)


def _person_candidate(**kwargs) -> PersonCandidate:
    defaults = dict(
        name="Jane Doe",
        role="Head of Marketing",
        organization_name="Acme Corp",
        evidence_ids=["ev-001"],
        supporting_quote="Jane Doe, Head of Marketing",
        role_match_score=0.9,
        identity_match_score=0.95,
    )
    defaults.update(kwargs)
    return PersonCandidate(**defaults)


def _lead(**kwargs) -> Lead:
    defaults = dict(
        org_name="Acme Corp",
        category="Sponsor",
        city="Kolkata",
        contact_name="Jane Doe",
        contact_role="Head of Marketing",
        channel_type="email",
        channel="jane.doe@acme.com",
        status="Verified",
        evidence_ids=["ev-001"],
        lead_key="lk-001",
        accepted_at=_NOW,
    )
    defaults.update(kwargs)
    return Lead(**defaults)


# ── Evidence ──────────────────────────────────────────────────────────────────


class TestEvidence:
    def test_valid_construction(self) -> None:
        ev = _evidence()
        assert ev.id == "ev-001"
        assert ev.is_first_party is True

    def test_brochure_uri_allowed(self) -> None:
        ev = _evidence(
            source_uri="brochure:abc123#page=3",
            source_kind="brochure",
            acquisition_method="pdf_text",
            fetch_status="pdf_text_extracted",
            url=None,
        )
        assert ev.url is None
        assert "brochure" in ev.source_uri

    def test_snippet_empty_rejected(self) -> None:
        with pytest.raises(ValidationError, match="snippet"):
            _evidence(snippet="   ")

    def test_snippet_whitespace_only_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _evidence(snippet="\t\n")

    def test_optional_fields_default_none(self) -> None:
        ev = _evidence()
        assert ev.fetched_at is None
        assert ev.published_at is None
        assert ev.content_hash is None


# ── Target ────────────────────────────────────────────────────────────────────


class TestTarget:
    def test_valid_sponsor(self) -> None:
        t = _target()
        assert t.category == "Sponsor"

    def test_valid_college_delegation(self) -> None:
        t = _target(category="College Delegation", name="IIT Kharagpur Tech Fest")
        assert t.category == "College Delegation"

    def test_invalid_category_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _target(category="Company")  # type: ignore[arg-type]

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="name"):
            _target(name="   ")

    def test_relevance_score_bounds(self) -> None:
        with pytest.raises(ValidationError):
            _target(relevance_score=1.5)
        with pytest.raises(ValidationError):
            _target(relevance_score=-0.1)

    def test_default_evidence_ids_empty(self) -> None:
        t = _target()
        assert t.evidence_ids == []


# ── PersonCandidate ───────────────────────────────────────────────────────────


class TestPersonCandidate:
    def test_valid_construction(self) -> None:
        p = _person_candidate()
        assert p.name == "Jane Doe"
        assert p.currentness == "unknown"

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _person_candidate(name="")

    def test_empty_role_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _person_candidate(role="   ")

    def test_empty_quote_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _person_candidate(supporting_quote="")

    def test_no_evidence_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="evidence_id"):
            _person_candidate(evidence_ids=[])

    def test_role_match_score_bounds(self) -> None:
        with pytest.raises(ValidationError):
            _person_candidate(role_match_score=1.1)

    def test_currentness_valid_values(self) -> None:
        for val in ("current", "plausibly_current", "stale", "unknown"):
            p = _person_candidate(currentness=val)  # type: ignore[arg-type]
            assert p.currentness == val

    def test_currentness_invalid_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _person_candidate(currentness="old")  # type: ignore[arg-type]


# ── Lead ──────────────────────────────────────────────────────────────────────


class TestLead:
    def test_valid_construction(self) -> None:
        lead = _lead()
        assert lead.status == "Verified"

    def test_status_must_be_verified(self) -> None:
        with pytest.raises(ValidationError):
            _lead(status="Pattern-Derived")  # type: ignore[arg-type]

    def test_category_must_be_valid(self) -> None:
        with pytest.raises(ValidationError):
            _lead(category="Company")  # type: ignore[arg-type]

    def test_empty_org_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _lead(org_name="")

    def test_empty_channel_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _lead(channel="   ")

    def test_empty_city_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _lead(city="")

    def test_no_evidence_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="evidence_id"):
            _lead(evidence_ids=[])

    def test_to_sheet_row_six_columns(self) -> None:
        row = _lead().to_sheet_row()
        assert len(row) == 6

    def test_to_sheet_row_column_order(self) -> None:
        lead = _lead(
            org_name="Acme Corp",
            category="Sponsor",
            city="Kolkata",
            contact_name="Jane Doe",
            contact_role="Head of Marketing",
            channel="jane.doe@acme.com",
            status="Verified",
        )
        row = lead.to_sheet_row()
        assert row[0] == "Acme Corp"  # Organization Name
        assert row[1] == "Sponsor"  # Category
        assert row[2] == "Kolkata"  # Location
        assert row[3] == "Jane Doe, Head of Marketing"  # Contact Person
        assert row[4] == "jane.doe@acme.com"  # Contact Channel
        assert row[5] == "Verified"  # Verification Status

    def test_to_sheet_row_contact_person_format(self) -> None:
        row = _lead(contact_name="Alice", contact_role="CEO").to_sheet_row()
        assert row[3] == "Alice, CEO"

    def test_channel_type_email(self) -> None:
        lead = _lead(channel_type="email")
        assert lead.channel_type == "email"

    def test_channel_type_profile(self) -> None:
        lead = _lead(channel_type="profile", channel="https://linkedin.com/in/janedoe")
        assert lead.channel_type == "profile"


# ── Rejection ─────────────────────────────────────────────────────────────────


class TestRejection:
    def test_valid_construction(self) -> None:
        r = Rejection(
            org_key="acme-corp",
            candidate_key="jane-doe",
            reason=RejectionReason.GENERIC_EMAIL.value,
            rejected_at=_NOW,
        )
        assert r.reason == "generic_email"

    def test_reason_is_stored_as_string(self) -> None:
        r = Rejection(
            org_key="org",
            candidate_key="cand",
            reason=RejectionReason.BUDGET_EXHAUSTED.value,
            rejected_at=_NOW,
        )
        assert isinstance(r.reason, str)
        assert r.reason == "budget_exhausted"


# ── RunInput ──────────────────────────────────────────────────────────────────


class TestRunInput:
    def test_prompt_mode(self) -> None:
        ri = RunInput(input_type="prompt", prompt="Find sponsors for a tech fest")
        assert ri.input_type == "prompt"

    def test_pdf_mode(self) -> None:
        ri = RunInput(input_type="pdf", pdf_path="/tmp/brochure.pdf")
        assert ri.input_type == "pdf"

    def test_blank_prompt_rejected(self) -> None:
        with pytest.raises(ValidationError, match="prompt"):
            RunInput(input_type="prompt", prompt="   ")

    def test_invalid_input_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RunInput(input_type="unknown")  # type: ignore[arg-type]


# ── QueryFacet / QueryPlan / EventProfile ─────────────────────────────────────


class TestQueryPlan:
    def test_valid_facet(self) -> None:
        facet = QueryFacet(angle="leadership", category="Sponsor")
        assert facet.category == "Sponsor"

    def test_invalid_angle_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QueryFacet(angle="bad_angle", category="Sponsor")  # type: ignore[arg-type]

    def test_query_plan_construction(self) -> None:
        profile = EventProfile(event_name="TechFest 2027", target_city="Kolkata")
        plan = QueryPlan(
            event_profile=profile,
            facets=[QueryFacet(angle="leadership", category="Sponsor")],
        )
        assert plan.event_profile.event_name == "TechFest 2027"
        assert len(plan.facets) == 1


# ── OrgInput ──────────────────────────────────────────────────────────────────


class TestOrgInput:
    def test_valid_construction(self) -> None:
        oi = OrgInput(
            run_id="run-001",
            target=_target(),
            event_profile_summary="TechFest 2027 in Kolkata",
            category="Sponsor",
        )
        assert oi.run_id == "run-001"


# ── BudgetSnapshot ────────────────────────────────────────────────────────────


class TestBudgetSnapshot:
    def test_default_values(self) -> None:
        snap = BudgetSnapshot()
        assert snap.searches_used == 0
        assert snap.cancelled is False

    def test_cancelled_flag(self) -> None:
        snap = BudgetSnapshot(cancelled=True, searches_used=5)
        assert snap.cancelled is True


# ── SheetWriteState ───────────────────────────────────────────────────────────


class TestSheetWriteState:
    def test_default_empty(self) -> None:
        sws = SheetWriteState()
        assert sws.audit_written_keys == []
        assert sws.leads_written_keys == []
        assert sws.run_log_written is False

    def test_tracking_keys(self) -> None:
        sws = SheetWriteState(audit_written_keys=["lk-1"], leads_written_keys=["lk-1"])
        assert "lk-1" in sws.audit_written_keys


# ── SearchResult ──────────────────────────────────────────────────────────────


class TestSearchResult:
    def test_valid_construction(self) -> None:
        sr = SearchResult(
            title="Acme Corp — Official Site",
            url="https://acmecorp.example.com",
            snippet="Acme Corp is a leading sponsor...",
            source_provider="tavily",
            rank=1,
            retrieved_at=_NOW,
        )
        assert sr.rank == 1


# ── ResearchEvent ─────────────────────────────────────────────────────────────


class TestResearchEvent:
    def test_valid_construction(self) -> None:
        ev = ResearchEvent(
            org_key="acme-corp",
            action_kind="FETCH",
            source_or_query="https://acmecorp.example.com/team",
            outcome="200 OK",
            timestamp=_NOW,
        )
        assert ev.action_kind == "FETCH"

    def test_invalid_action_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ResearchEvent(
                org_key="acme",
                action_kind="INVALID_ACTION",  # type: ignore[arg-type]
                timestamp=_NOW,
            )


# ── RejectionReason enum ──────────────────────────────────────────────────────


class TestRejectionReason:
    def test_all_reasons_are_snake_case_strings(self) -> None:
        for reason in RejectionReason:
            assert isinstance(reason.value, str)
            assert reason.value == reason.value.lower()
            assert " " not in reason.value

    def test_specific_codes_exist(self) -> None:
        """Ensure the minimum set from §4.7 is present."""
        required = {
            "organization_unresolved",
            "no_people_found",
            "no_relevant_decision_maker",
            "stale_or_conflicting_role",
            "no_contact_channel_found",
            "generic_email",
            "disposable_email",
            "email_domain_mismatch",
            "official_domain_untrusted",
            "robots_disallowed",
            "profile_not_authentic",
            "city_unverified",
            "duplicate_lead",
            "budget_exhausted",
            "schema_validation_failed",
        }
        values = {r.value for r in RejectionReason}
        missing = required - values
        assert not missing, f"Missing reason codes: {missing}"
