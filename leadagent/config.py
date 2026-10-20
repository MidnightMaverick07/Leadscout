"""Application configuration using pydantic-settings.

All settings are loaded from environment variables (with .env file support).
Sensitive values are never logged; the ``budget_snapshot()`` method returns
only non-secret, numeric configuration for reporting.

Section references: §10, §10.1–10.9, §3.4.5.
"""

from __future__ import annotations

import logging
import math
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """All runtime configuration for leadagent.

    Loaded from environment variables and/or a .env file.
    Secrets (API keys, credentials paths) are never exposed in logs or reports.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── General ───────────────────────────────────────────────────────────────
    environment: Literal["development", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # ── Run profile ───────────────────────────────────────────────────────────
    run_profile: Literal["free_tier", "standard"] = "free_tier"

    # ── Lead targeting ────────────────────────────────────────────────────────
    max_leads: int = Field(default=30, ge=1, le=300)
    expected_yield_prior: float = Field(default=0.20, ge=0.01, le=1.0)
    budget_safety_multiplier: float = Field(default=1.30, ge=1.0, le=5.0)
    max_research_rounds: int = Field(default=20, ge=1, le=20)
    max_orgs_per_round: int = Field(default=10, ge=1, le=50)
    max_concurrent_orgs: int = Field(default=3, ge=1, le=10)
    max_research_steps_per_org: int = Field(default=25, ge=5, le=100)
    run_timeout_seconds: int = Field(default=900, ge=30)

    # ── Search ────────────────────────────────────────────────────────────────
    search_provider: str = "tavily"
    search_api_key: str = ""
    search_timeout_seconds: int = Field(default=20, ge=1)
    search_credit_budget: int | None = None
    max_search_calls_hard_cap: int = Field(default=800, ge=1)
    max_results_per_query: int = Field(default=10, ge=1, le=50)
    search_cache_ttl_seconds: int = Field(default=86400, ge=0)
    max_searches_per_org: int = Field(default=3, ge=1)

    # ── Fetching ──────────────────────────────────────────────────────────────
    fetch_timeout_seconds: int = Field(default=15, ge=1)
    max_fetches_per_org: int = Field(default=10, ge=1)
    max_total_fetch_attempts_hard_cap: int = Field(default=2000, ge=1)
    max_page_bytes: int = Field(default=2_000_000, ge=1)
    max_redirects: int = Field(default=5, ge=0)
    fetch_cache_ttl_seconds: int = Field(default=86400, ge=0)
    robots_cache_ttl_seconds: int = Field(default=86400, ge=0)
    user_agent: str = "leadagent/0.1 (+https://example.com/leadagent-policy)"

    # ── LLM ───────────────────────────────────────────────────────────────────
    llm_provider: Literal["gemini", "ollama"] = "gemini"
    llm_api_key: str = ""
    llm_base_url: str | None = None
    llm_model_planner: str = "gemini-2.0-flash-lite"
    llm_model_extraction: str = "gemini-2.0-flash-lite"
    llm_model_ranking: str = "gemini-2.0-flash-lite"
    llm_timeout_seconds: int = Field(default=45, ge=1)
    max_llm_calls_hard_cap: int = Field(default=150, ge=1)
    max_llm_calls_per_org: int = Field(default=4, ge=1)
    max_input_chars: int = Field(default=20_000, ge=100)
    max_output_tokens: int = Field(default=2000, ge=64)
    llm_requests_per_minute: int = Field(default=8, ge=1)
    llm_daily_request_cap: int | None = None

    # ── PDF ───────────────────────────────────────────────────────────────────
    pdf_max_bytes: int = Field(default=20_000_000, ge=1)
    pdf_max_pages: int = Field(default=80, ge=1)
    pdf_max_text_chars: int = Field(default=250_000, ge=1)
    pdf_vision_enabled: bool = False
    pdf_vision_max_pages: int = Field(default=10, ge=1)

    # ── Verification ──────────────────────────────────────────────────────────
    email_policy: Literal["published_first_party", "require_mailbox_confirmed"] = (
        "published_first_party"
    )
    email_validator_provider: str = "none"
    email_validator_api_key: str = ""
    smtp_probe_enabled: bool = False
    allow_free_email_for_college: bool = True
    verification_cache_ttl_seconds: int = Field(default=86400, ge=0)
    official_domain_trust_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    official_domain_tentative_threshold: float = Field(default=0.40, ge=0.0, le=1.0)
    profile_name_match_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    profile_org_match_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    email_context_window_chars: int = Field(default=500, ge=0)

    # ── Output / Google Sheets ────────────────────────────────────────────────
    google_sheets_spreadsheet_id: str = ""
    google_application_credentials: str = ""
    google_sheets_leads_tab: str = "Leads"
    google_sheets_audit_tab: str = "Audit"
    google_sheets_run_log_tab: str = "Run Log"
    sheets_batch_size: int = Field(default=20, ge=1, le=100)

    # ── Orchestration (LangGraph) ─────────────────────────────────────────────
    checkpoint_db_path: str = "./.leadagent/checkpoints.sqlite"
    app_db_path: str = "./.leadagent/app.sqlite"

    # ── Derived / read-only properties ────────────────────────────────────────

    @property
    def org_recursion_limit(self) -> int:
        """Subgraph recursion limit: 2 * steps + 10 (§3.4.5)."""
        return 2 * self.max_research_steps_per_org + 10

    @property
    def top_recursion_limit(self) -> int:
        """Top-level graph recursion limit: 6 * rounds + 20 (§3.4.5)."""
        return 6 * self.max_research_rounds + 20

    @property
    def estimated_orgs_needed(self) -> int:
        """Estimated organizations to research to hit max_leads (§10.1)."""
        return math.ceil(
            self.max_leads / self.expected_yield_prior * self.budget_safety_multiplier
        )

    @model_validator(mode="after")
    def _apply_free_tier_defaults(self) -> "Settings":
        """Lower caps when RUN_PROFILE=free_tier unless explicitly overridden."""
        # The validator runs after all fields are set; profile adjustments only
        # apply when the field still holds its library-level default.
        # We cannot detect "was it explicitly set" easily, so we clamp hard caps.
        if self.run_profile == "free_tier":
            if self.max_llm_calls_hard_cap > 150:
                object.__setattr__(self, "max_llm_calls_hard_cap", 150)
            if self.max_concurrent_orgs > 2:
                object.__setattr__(self, "max_concurrent_orgs", 2)
            if self.max_leads > 15:
                object.__setattr__(self, "max_leads", 15)
            if self.pdf_vision_enabled:
                object.__setattr__(self, "pdf_vision_enabled", False)
        return self

    @model_validator(mode="after")
    def _validate_thresholds(self) -> "Settings":
        if self.official_domain_tentative_threshold >= self.official_domain_trust_threshold:
            raise ValueError(
                "official_domain_tentative_threshold must be < official_domain_trust_threshold"
            )
        return self

    def budget_snapshot(self) -> dict[str, object]:
        """Return a non-secret snapshot of budget parameters for startup printing.

        Secrets (API keys, credentials paths) are never included here.
        """
        return {
            "run_profile": self.run_profile,
            "max_leads": self.max_leads,
            "expected_yield_prior": self.expected_yield_prior,
            "budget_safety_multiplier": self.budget_safety_multiplier,
            "estimated_orgs_needed": self.estimated_orgs_needed,
            "max_research_rounds": self.max_research_rounds,
            "max_orgs_per_round": self.max_orgs_per_round,
            "max_concurrent_orgs": self.max_concurrent_orgs,
            "max_research_steps_per_org": self.max_research_steps_per_org,
            "run_timeout_seconds": self.run_timeout_seconds,
            "max_searches_per_org": self.max_searches_per_org,
            "max_search_calls_hard_cap": self.max_search_calls_hard_cap,
            "max_fetches_per_org": self.max_fetches_per_org,
            "max_total_fetch_attempts_hard_cap": self.max_total_fetch_attempts_hard_cap,
            "max_page_bytes": self.max_page_bytes,
            "max_llm_calls_per_org": self.max_llm_calls_per_org,
            "max_llm_calls_hard_cap": self.max_llm_calls_hard_cap,
            "llm_requests_per_minute": self.llm_requests_per_minute,
            "llm_daily_request_cap": self.llm_daily_request_cap,
            "email_policy": self.email_policy,
            "pdf_vision_enabled": self.pdf_vision_enabled,
            "org_recursion_limit": self.org_recursion_limit,
            "top_recursion_limit": self.top_recursion_limit,
        }


def configure_logging(settings: Settings) -> None:
    """Set up structured root logging.  Never logs API keys or credentials."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    # Suppress noisy third-party loggers in development
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.WARNING)


# Module-level singleton — created lazily so tests can patch it.
_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the application-wide Settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
