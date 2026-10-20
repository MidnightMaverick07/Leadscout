"""Stable, machine-readable rejection reason codes and domain exceptions.

All rejection reasons are defined here as a single enum so they can be
imported by the router, quality gate, verifiers, and tests without creating
circular dependencies.

Section references: §4.7, §5.8.
"""

from __future__ import annotations

from enum import Enum


class RejectionReason(str, Enum):
    """Stable reason codes for rejected candidates and research failures.

    String values are snake_case identifiers that are safe to store in
    SQLite, logs, and the Audit worksheet.  Never change an existing value
    (doing so breaks idempotency across runs).
    """

    # Organization-level failures
    ORGANIZATION_UNRESOLVED = "organization_unresolved"
    ORGANIZATION_IRRELEVANT = "organization_irrelevant"

    # Person / role failures
    NO_PEOPLE_FOUND = "no_people_found"
    NO_RELEVANT_DECISION_MAKER = "no_relevant_decision_maker"
    PERSON_EVIDENCE_MISSING = "person_evidence_missing"
    STALE_OR_CONFLICTING_ROLE = "stale_or_conflicting_role"

    # Contact channel failures
    NO_CONTACT_CHANNEL_FOUND = "no_contact_channel_found"
    GENERIC_EMAIL = "generic_email"
    DISPOSABLE_EMAIL = "disposable_email"
    EMAIL_DOMAIN_MISMATCH = "email_domain_mismatch"
    EMAIL_DELIVERABILITY_UNCONFIRMED = "email_deliverability_unconfirmed"
    EMAIL_UNDELIVERABLE = "email_undeliverable"

    # Domain / trust failures
    OFFICIAL_DOMAIN_UNTRUSTED = "official_domain_untrusted"

    # Fetch / robots failures
    ROBOTS_POLICY_UNAVAILABLE = "robots_policy_unavailable"
    ROBOTS_DISALLOWED = "robots_disallowed"
    FETCH_EXHAUSTED = "fetch_exhausted"
    UNSAFE_URL = "unsafe_url"

    # Profile failures
    PROFILE_SNIPPET_NOT_CORROBORATED = "profile_snippet_not_corroborated"
    PROFILE_NOT_AUTHENTIC = "profile_not_authentic"
    PERSON_ORG_MATCH_FAILED = "person_org_match_failed"

    # Location failures
    CITY_UNVERIFIED = "city_unverified"

    # Deduplication
    DUPLICATE_LEAD = "duplicate_lead"

    # Budget / system
    BUDGET_EXHAUSTED = "budget_exhausted"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"


class LeadscoutError(Exception):
    """Base class for all expected domain errors in leadagent.

    Subclasses must carry enough context to produce a useful audit record.
    Unexpected programming errors should *not* be caught as LeadscoutError —
    they must remain visible in development and tests.
    """


class BudgetExhausted(LeadscoutError):
    """Raised by BudgetLedger.try_consume when a hard cap is reached.

    The router maps this to GIVE_UP(budget_exhausted).
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"Budget exhausted for kind={kind!r}")


class UnsafeURL(LeadscoutError):
    """Raised by url_policy when a URL fails the SSRF / safety check."""

    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"Unsafe URL {url!r}: {reason}")


class RobotsDisallowed(LeadscoutError):
    """Raised when robots.txt disallows fetching a URL."""

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"robots.txt disallows fetching {url!r}")


class InputValidationError(LeadscoutError):
    """Raised for invalid CLI input; maps to exit code 2."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ProviderError(LeadscoutError):
    """Raised for external-provider failures; maps to exit code 3 at startup."""

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"Provider {provider!r} error: {message}")


class QualityGateFailure(LeadscoutError):
    """Raised when a candidate fails the quality gate.

    ``reason`` is one of the stable ``RejectionReason`` codes.
    """

    def __init__(self, reason: RejectionReason, details: str = "") -> None:
        self.reason = reason
        self.details = details
        super().__init__(f"Quality gate failed: {reason.value} — {details}")


class GraphRecursionLimitReached(LeadscoutError):
    """Caught from LangGraph's GraphRecursionError; converted to GIVE_UP."""

    def __init__(self, context: str) -> None:
        self.context = context
        super().__init__(f"Recursion limit reached in {context!r}")
