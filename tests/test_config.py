"""Unit tests for leadagent/config.py — Settings validation.

Tests cover:
- Settings loads with defaults when no .env file is present.
- Free-tier profile clamps max_leads, max_llm_calls_hard_cap, max_concurrent_orgs.
- Standard profile preserves configured values.
- Threshold validation rejects tentative >= trust.
- Derived properties (org_recursion_limit, top_recursion_limit,
  estimated_orgs_needed) compute correctly.
- budget_snapshot() contains no secret keys.
- configure_logging does not raise.
- get_settings() returns the same singleton on repeated calls.

Section references: §10, §13.1.
"""

from __future__ import annotations

import pytest

from leadagent.config import Settings, configure_logging, get_settings


# ── Helpers ───────────────────────────────────────────────────────────────────


def _std(**kwargs) -> Settings:
    """Create a standard-profile settings with safe test overrides."""
    base = dict(
        environment="test",
        run_profile="standard",
        official_domain_tentative_threshold=0.40,
        official_domain_trust_threshold=0.70,
    )
    base.update(kwargs)
    return Settings(**base)


def _free(**kwargs) -> Settings:
    """Create a free-tier settings with safe test overrides."""
    base = dict(
        environment="test",
        run_profile="free_tier",
        official_domain_tentative_threshold=0.40,
        official_domain_trust_threshold=0.70,
    )
    base.update(kwargs)
    return Settings(**base)


# ── Default construction ───────────────────────────────────────────────────────


class TestSettingsDefaults:
    def test_loads_without_env_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings should load even when no .env file exists."""
        # Patch env_file to a non-existent path so it silently skips
        s = Settings(
            _env_file=None,  # type: ignore[call-arg]
            environment="test",
            official_domain_tentative_threshold=0.40,
            official_domain_trust_threshold=0.70,
        )
        assert s.environment == "test"

    def test_default_email_policy(self) -> None:
        s = _std()
        assert s.email_policy == "published_first_party"

    def test_default_smtp_disabled(self) -> None:
        s = _std()
        assert s.smtp_probe_enabled is False

    def test_default_pdf_vision_disabled(self) -> None:
        s = _std()
        assert s.pdf_vision_enabled is False

    def test_max_leads_within_bounds(self) -> None:
        s = _std(max_leads=50)
        assert s.max_leads == 50

    def test_max_leads_above_300_rejected(self) -> None:
        with pytest.raises(Exception):  # ValidationError from pydantic
            _std(max_leads=301)

    def test_max_leads_below_1_rejected(self) -> None:
        with pytest.raises(Exception):
            _std(max_leads=0)

    def test_expected_yield_prior_bounds(self) -> None:
        with pytest.raises(Exception):
            _std(expected_yield_prior=0.0)
        with pytest.raises(Exception):
            _std(expected_yield_prior=1.1)


# ── Free-tier profile clamping ────────────────────────────────────────────────


class TestFreeTierClamping:
    def test_max_leads_clamped_to_15(self) -> None:
        s = _free(max_leads=30)
        assert s.max_leads == 15

    def test_max_llm_calls_hard_cap_clamped(self) -> None:
        s = _free(max_llm_calls_hard_cap=800)
        assert s.max_llm_calls_hard_cap <= 150

    def test_max_concurrent_orgs_clamped(self) -> None:
        s = _free(max_concurrent_orgs=5)
        assert s.max_concurrent_orgs <= 2

    def test_pdf_vision_disabled_on_free_tier(self) -> None:
        s = _free(pdf_vision_enabled=True)
        assert s.pdf_vision_enabled is False

    def test_standard_profile_not_clamped(self) -> None:
        s = _std(max_leads=30, max_llm_calls_hard_cap=800, max_concurrent_orgs=5)
        assert s.max_leads == 30
        assert s.max_llm_calls_hard_cap == 800
        assert s.max_concurrent_orgs == 5


# ── Threshold validation ──────────────────────────────────────────────────────


class TestThresholdValidation:
    def test_tentative_lt_trust_is_ok(self) -> None:
        s = _std(
            official_domain_tentative_threshold=0.40,
            official_domain_trust_threshold=0.70,
        )
        assert s.official_domain_tentative_threshold < s.official_domain_trust_threshold

    def test_tentative_equal_trust_rejected(self) -> None:
        with pytest.raises(Exception, match="tentative"):
            _std(
                official_domain_tentative_threshold=0.70,
                official_domain_trust_threshold=0.70,
            )

    def test_tentative_greater_than_trust_rejected(self) -> None:
        with pytest.raises(Exception):
            _std(
                official_domain_tentative_threshold=0.80,
                official_domain_trust_threshold=0.70,
            )


# ── Derived properties ────────────────────────────────────────────────────────


class TestDerivedProperties:
    def test_org_recursion_limit(self) -> None:
        s = _std(max_research_steps_per_org=25)
        assert s.org_recursion_limit == 2 * 25 + 10  # 60

    def test_top_recursion_limit(self) -> None:
        s = _std(max_research_rounds=20)
        assert s.top_recursion_limit == 6 * 20 + 20  # 140

    def test_estimated_orgs_needed(self) -> None:
        import math

        s = _std(max_leads=30, expected_yield_prior=0.20, budget_safety_multiplier=1.30)
        expected = math.ceil(30 / 0.20 * 1.30)  # ceil(195) = 195
        assert s.estimated_orgs_needed == expected

    def test_org_recursion_limit_changes_with_steps(self) -> None:
        s = _std(max_research_steps_per_org=10)
        assert s.org_recursion_limit == 30

    def test_top_recursion_limit_changes_with_rounds(self) -> None:
        s = _std(max_research_rounds=5)
        assert s.top_recursion_limit == 50


# ── budget_snapshot security ──────────────────────────────────────────────────


SECRET_FIELD_SUBSTRINGS = (
    "api_key",
    "llm_api",
    "search_api",
    "credentials",
    "spreadsheet_id",
    "validator_api",
)


class TestBudgetSnapshot:
    def test_returns_dict(self) -> None:
        s = _std()
        snap = s.budget_snapshot()
        assert isinstance(snap, dict)

    def test_no_secret_keys(self) -> None:
        s = _std(
            llm_api_key="super-secret-key",
            search_api_key="search-secret",
            google_application_credentials="./secret-creds.json",
            google_sheets_spreadsheet_id="sheet-id-secret",
        )
        snap = s.budget_snapshot()
        for secret_substr in SECRET_FIELD_SUBSTRINGS:
            for key in snap:
                assert secret_substr not in key.lower(), (
                    f"Secret field {key!r} found in budget_snapshot()"
                )

    def test_snapshot_contains_expected_keys(self) -> None:
        s = _std()
        snap = s.budget_snapshot()
        required_keys = {
            "run_profile",
            "max_leads",
            "max_research_rounds",
            "max_concurrent_orgs",
            "max_llm_calls_hard_cap",
            "email_policy",
        }
        assert required_keys.issubset(snap.keys())

    def test_snapshot_values_are_serializable(self) -> None:
        """All values must be JSON-safe primitives (no secrets, no objects)."""
        import json

        s = _std()
        snap = s.budget_snapshot()
        # Should not raise
        json.dumps(snap)


# ── configure_logging ─────────────────────────────────────────────────────────


class TestConfigureLogging:
    def test_does_not_raise(self) -> None:
        s = _std(log_level="WARNING")
        configure_logging(s)  # should not raise

    def test_accepts_all_levels(self) -> None:
        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            s = _std(log_level=level)  # type: ignore[arg-type]
            configure_logging(s)


# ── get_settings singleton ────────────────────────────────────────────────────


class TestGetSettings:
    def test_returns_settings_instance(self, fresh_settings: Settings) -> None:
        s = get_settings()
        assert isinstance(s, Settings)

    def test_same_singleton_on_repeated_calls(self, fresh_settings: Settings) -> None:
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2
