"""Shared Pydantic v2 data contracts for leadagent.

Defines every model referenced in the AGENTS.md §4 data-contracts section.
These models are used at input/LLM/graph-state/output boundaries.

Section references: §4.1–4.8, §3.4.4.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from leadagent.errors import RejectionReason

# ── §4.1 Enums (expressed as Literals for Pydantic v2 compatibility) ─────────

Category = Literal["Sponsor", "College Delegation"]
"""The only two allowed external category values (§1.4)."""

VerificationStatus = Literal["Verified", "Pattern-Derived"]
"""Accepted rows must always be 'Verified'. 'Pattern-Derived' exists only
because it appears in the source schema; this system must never emit it."""

ChannelType = Literal["email", "profile"]

SourceKind = Literal[
    "official_site",
    "team_page",
    "contact_page",
    "leadership_page",
    "sponsorship_page",
    "college_society_page",
    "serp",
    "press",
    "past_sponsor_list",
    "social_profile",
    "brochure",
    "other",
]

AcquisitionMethod = Literal[
    "direct_fetch",
    "serp_snippet",
    "provider_extraction",
    "archive",
    "pdf_text",
    "pdf_vision",
]

FetchStatus = Literal[
    "fetched",
    "search_snippet_only",
    "provider_extracted",
    "archived",
    "pdf_text_extracted",
    "pdf_vision_extracted",
]

DomainTrustState = Literal["trusted", "tentative", "organization_unresolved"]

InputType = Literal["prompt", "pdf"]

EmailPolicy = Literal["published_first_party", "require_mailbox_confirmed"]

MailboxStatus = Literal["confirmed", "undeliverable", "unknown", "not_checked"]

Currentness = Literal["current", "plausibly_current", "stale", "unknown"]

SearchFacetAngle = Literal[
    "official_website",
    "past_sponsors",
    "leadership",
    "csr",
    "partnerships",
    "college_societies",
    "office_bearers",
]

ResearchActionKind = Literal[
    "FETCH",
    "RETRY",
    "NEXT_URL",
    "SEARCH",
    "EXTRACT",
    "RANK",
    "FIND_CHANNEL",
    "NEXT_CHANNEL",
    "NEXT_CANDIDATE",
    "VERIFY",
    "RESOLVE_LOCATION",
    "GATE",
    "ACCEPT",
    "GIVE_UP",
]

StopReason = Literal[
    "max_leads_reached",
    "budget_exhausted",
    "round_cap_reached",
    "low_yield",
    "no_new_targets",
    "user_cancelled",
    "invalid_input",
    "config_error",
    "internal_error",
]

RunStatus = Literal["running", "completed", "failed", "cancelled"]


# ── UTC timestamp enforcement (§2 principle 11, §4.2) ─────────────────────────

def _require_aware_utc(v: datetime | None) -> datetime | None:
    """Reject naive datetimes; convert aware datetimes to UTC."""
    if v is None:
        return None
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware (use UTC)")
    return v.astimezone(timezone.utc)


class UTCModel(BaseModel):
    """Base class for models with timestamps.

    Every named datetime field is validated. check_fields=False lets one
    validator cover fields that only some subclasses define.
    """

    @field_validator(
        "fetched_at",
        "published_at",
        "checked_at",
        "accepted_at",
        "rejected_at",
        "timestamp",
        "retrieved_at",
        "started_at",
        "finished_at",
        check_fields=False,
    )
    @classmethod
    def _timestamps_utc_aware(cls, v: datetime | None) -> datetime | None:
        return _require_aware_utc(v)


# ── §4.2 Evidence ─────────────────────────────────────────────────────────────

class Evidence(UTCModel):
    """A single piece of evidence supporting a claim.

    ``source_uri`` is always required and stable. For brochure evidence use
    a synthetic URI like ``brochure:<sha256>#page=3`` (never a fabricated URL).
    ``url`` is only populated for genuine HTTP(S) evidence.
    """

    id: str
    source_uri: str
    url: HttpUrl | None = None
    source_kind: SourceKind
    acquisition_method: AcquisitionMethod
    fetched_at: datetime | None = None
    title: str | None = None
    published_at: datetime | None = None
    snippet: str  # exact supporting text span, not an LLM paraphrase
    page_number: int | None = None
    content_hash: str | None = None
    is_first_party: bool = False
    fetch_status: FetchStatus
    registrable_domain: str | None = None

    @field_validator("snippet")
    @classmethod
    def snippet_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Evidence snippet must not be empty")
        return v


# ── §4.3 Organization target ──────────────────────────────────────────────────

class Target(BaseModel):
    """A candidate organization to research."""

    id: str
    name: str
    category: Category
    city_hint: str | None = None  # research hint only; never a lead location
    official_domain: str | None = None
    origin: Literal["prompt_search", "brochure", "lookalike_search"]
    relevance_score: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Target name must not be empty")
        return v


# ── §4.4 Person candidate ─────────────────────────────────────────────────────

class PersonCandidate(BaseModel):
    """An extracted person/role candidate grounded in source evidence."""

    name: str
    role: str
    organization_name: str
    city: str | None = None
    evidence_ids: list[str]
    supporting_quote: str
    currentness: Currentness = "unknown"
    role_match_score: float = Field(ge=0.0, le=1.0)
    identity_match_score: float = Field(ge=0.0, le=1.0)

    @field_validator("name", "role", "supporting_quote")
    @classmethod
    def field_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("PersonCandidate fields must not be empty")
        return v

    @field_validator("evidence_ids")
    @classmethod
    def at_least_one_evidence(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("PersonCandidate must have at least one evidence_id")
        return v


# ── §4.5 Contact verification ─────────────────────────────────────────────────

class EmailVerificationReport(UTCModel):
    """Detailed email verification results (§4.5)."""

    channel: str  # normalized email address
    email_syntax: Literal["valid", "invalid"]
    domain_relation: Literal["trusted_org_domain", "subdomain", "alias", "other", "unknown"]
    dns_status: Literal["resolved", "nxdomain", "timeout", "error"]
    mx_status: Literal["has_mx", "null_mx", "no_mx", "timeout", "error"]
    generic_inbox: bool
    disposable_domain: bool
    mailbox_status: MailboxStatus = "not_checked"
    validator_provider: str = "none"
    channel_authenticity: Literal["verified", "rejected", "unknown"] = "unknown"
    person_match: Literal["verified", "rejected", "unknown"] = "unknown"
    organization_match: Literal["verified", "rejected", "unknown"] = "unknown"
    official_domain_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    checked_at: datetime | None = None
    notes: str = ""


class ProfileVerificationReport(UTCModel):
    """Detailed profile-URL verification results (§4.5)."""

    channel: str  # canonicalized profile URL
    allowed_host: bool
    profile_url_found_in_source: bool
    name_match: Literal["verified", "rejected", "unknown"] = "unknown"
    organization_match: Literal["verified", "rejected", "unknown"] = "unknown"
    live_access_status: Literal["accessible", "blocked", "not_found", "not_checked"] = (
        "not_checked"
    )
    channel_authenticity: Literal["verified", "rejected", "unknown"] = "unknown"
    person_match: Literal["verified", "rejected", "unknown"] = "unknown"
    official_domain_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    checked_at: datetime | None = None
    notes: str = ""


# ── §4.6 Accepted lead ────────────────────────────────────────────────────────

class Lead(UTCModel):
    """A fully verified, accepted lead ready for sheet output.

    The external sheet writer maps this to exactly six cells in the Leads tab.
    Internal fields (evidence_ids, verifier_report) go to the Audit tab only.
    """

    org_name: str
    category: Category
    city: str
    contact_name: str
    contact_role: str
    channel_type: ChannelType
    channel: str
    status: Literal["Verified"]
    evidence_ids: list[str]
    verifier_report: dict[str, Any] = Field(default_factory=dict)
    lead_key: str
    accepted_at: datetime

    @field_validator("org_name", "city", "contact_name", "contact_role", "channel", "lead_key")
    @classmethod
    def field_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Lead fields must not be empty")
        return v

    @field_validator("evidence_ids")
    @classmethod
    def at_least_one_evidence(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("Lead must have at least one evidence_id")
        return v

    def to_sheet_row(self) -> list[str]:
        """Serialize to exactly six cells matching the Leads tab schema (§1.5).

        Column order: Organization Name, Category, Location,
                      Contact Person, Contact Channel, Verification Status.
        """
        return [
            self.org_name,
            self.category,
            self.city,
            f"{self.contact_name}, {self.contact_role}",
            self.channel,
            self.status,
        ]


# ── §4.7 Rejection ────────────────────────────────────────────────────────────

class Rejection(UTCModel):
    """A rejected candidate with a stable, machine-readable reason code."""

    org_key: str
    candidate_key: str
    reason: RejectionReason
    details: str = ""
    rejected_at: datetime


# ── Run input and persisted run record ────────────────────────────────────────

class RunInput(BaseModel):
    """Validated top-level input: exactly one of a prompt or a PDF path (§5.1)."""

    input_type: InputType
    prompt: str | None = None
    pdf_path: str | None = None  # absolute local path, validated by input_router
    raw_prompt: str = ""  # original, unsanitized prompt for audit

    @field_validator("prompt")
    @classmethod
    def prompt_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("prompt must not be blank")
        return v

    @model_validator(mode="after")
    def exactly_one_input_mode(self) -> RunInput:
        if self.input_type == "prompt":
            if not self.prompt or self.pdf_path:
                raise ValueError("prompt mode requires prompt and no pdf_path")
        elif self.input_type == "pdf":
            if not self.pdf_path or self.prompt:
                raise ValueError("pdf mode requires pdf_path and no prompt")
        return self


class RunRecord(UTCModel):
    """Persisted run status for the storage layer (§9.1)."""

    run_id: str
    input_type: InputType
    input_hash: str  # hash of the sanitized input, never raw brochure text
    status: RunStatus = "running"
    stop_reason: StopReason | None = None
    started_at: datetime
    finished_at: datetime | None = None
    config_snapshot: dict[str, Any] = Field(default_factory=dict)  # non-secret values only


# ── Research / orchestration models ───────────────────────────────────────────

class QueryFacet(BaseModel):
    """One structured search facet produced by the query planner."""

    industry: str | None = None
    organization_type: str | None = None
    city: str | None = None
    angle: SearchFacetAngle
    category: Category


class EventProfile(BaseModel):
    """Structured event context extracted from a prompt or brochure."""

    event_name: str | None = None
    audience: str | None = None
    scale: str | None = None
    theme: str | None = None
    event_dates: str | None = None
    target_city: str | None = None  # search hint only; never copied to org location
    categories: list[Category] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class QueryPlan(BaseModel):
    """Structured search plan built by the query planner (§5.2)."""

    event_profile: EventProfile
    facets: list[QueryFacet]
    negative_constraints: list[str] = Field(default_factory=list)


class OrgInput(BaseModel):
    """Minimal payload sent to each org_researcher subgraph instance (§3.4.5)."""

    run_id: str
    target: Target
    event_profile_summary: str  # clipped summary, never the full RunState
    category: Category

    @model_validator(mode="after")
    def category_matches_target(self) -> OrgInput:
        if self.category != self.target.category:
            raise ValueError("OrgInput.category must match target.category")
        return self


class ResearchAction(BaseModel):
    """An action returned by the deterministic router (§5.8)."""

    kind: ResearchActionKind
    reason: str = ""
    url: str | None = None
    query: str | None = None


class ResearchEvent(UTCModel):
    """One step in the per-organization research trail (§4.7, §5.8)."""

    event_id: str  # stable ID computed by the repository (run_id + org_key + sequence)
    org_key: str
    action_kind: ResearchActionKind
    source_or_query: str = ""
    outcome: str = ""
    error_class: str = ""
    next_action_kind: ResearchActionKind | None = None
    timestamp: datetime


class RoundYield(BaseModel):
    """Accepted-lead yield data for one research round (§10.1 stop logic)."""

    round_number: int
    orgs_researched: int
    leads_accepted: int
    leads_rejected: int


class BudgetSnapshot(BaseModel):
    """Read-only budget counters embedded in RunState for checkpoints/reporting.

    The authoritative counters live in BudgetLedger (budget.py).
    """

    searches_used: int = 0
    fetches_used: int = 0
    llm_calls_used: int = 0
    searches_remaining: int = 0
    fetches_remaining: int = 0
    llm_calls_remaining: int = 0
    cancelled: bool = False


class SheetWriteState(BaseModel):
    """Tracks which leads have been written to the sheet (§3.4.9, §9.4)."""

    audit_written_keys: list[str] = Field(default_factory=list)
    leads_written_keys: list[str] = Field(default_factory=list)
    run_log_written: bool = False
    last_write_cursor: int = 0


class SearchResult(UTCModel):
    """Normalized search result from any provider (§5.4)."""

    title: str
    url: HttpUrl  # http/https only; rejects javascript:, file:, etc.
    snippet: str
    source_provider: str
    rank: int = Field(ge=1)
    retrieved_at: datetime