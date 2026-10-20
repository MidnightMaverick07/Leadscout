"""Shared pytest fixtures and configuration for the leadagent test suite.

The test suite must run offline — no live API keys, no network calls,
no Google Sheets writes.  Mock providers are injected via fixtures.

Section references: §13.1, §13.2.
"""

from __future__ import annotations

import pytest

from leadagent.config import Settings


@pytest.fixture(autouse=False)
def fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Return a Settings instance with safe test defaults.

    Resets the module-level singleton so tests do not bleed into each other.
    """
    # Reset singleton
    import leadagent.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "_settings", None)

    settings = Settings(
        environment="test",
        run_profile="standard",  # standard so free-tier clamping doesn't interfere
        log_level="WARNING",
        llm_api_key="test-llm-key",
        search_api_key="test-search-key",
        google_sheets_spreadsheet_id="test-sheet-id",
        google_application_credentials="./test-creds.json",
        official_domain_tentative_threshold=0.40,
        official_domain_trust_threshold=0.70,
    )
    monkeypatch.setattr(cfg_mod, "_settings", settings)
    return settings
