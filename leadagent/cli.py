"""CLI for leadagent — the evidence-backed lead-finder agent.

Commands
--------
``leadagent run``    Run the lead-finding workflow (prompt or PDF mode).
``leadagent doctor`` Validate configuration and provider connectivity.

Exit codes (§11):
  0  — Workflow completed (even if some candidates were rejected).
  2  — Invalid input or conflicting/incomplete CLI configuration.
  3  — External provider/credentials failed at startup.
  4  — Unexpected internal failure.

Rules:
- ``--output`` is the single authority for sink selection.
- ``--dry-run`` conflicts with ``--output sheets`` and ``--output both``
  (exit code 2 with a clear explanation).
- Startup prints input type, requested/effective lead target, categories,
  output sink, enabled capabilities, and non-secret budget limits.
- Secrets (API keys, credential paths) are never printed.

Section references: §11, §14A.2 M0.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

import typer

from leadagent import __version__
from leadagent.config import Settings, configure_logging, get_settings

app = typer.Typer(
    name="leadagent",
    help="Evidence-backed lead-finder agent.",
    add_completion=False,
    no_args_is_help=True,
)

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _abort(message: str, exit_code: int) -> None:
    """Print a user-facing error and exit with the given code."""
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=exit_code)


def _print_budget_snapshot(settings: Settings) -> None:
    """Print non-secret budget configuration at startup."""
    snap = settings.budget_snapshot()
    typer.echo("── Budget snapshot ───────────────────────────────────────────")
    for key, value in snap.items():
        typer.echo(f"  {key}: {value}")
    typer.echo("─────────────────────────────────────────────────────────────")


# ── run command ───────────────────────────────────────────────────────────────


@app.command()
def run(
    prompt: Optional[str] = typer.Option(
        None,
        "--prompt",
        help="Natural-language brief describing target leads.",
    ),
    pdf: Optional[str] = typer.Option(
        None,
        "--pdf",
        help="Path to event brochure PDF.",
    ),
    max_leads: Optional[int] = typer.Option(
        None,
        "--max-leads",
        help="Maximum number of accepted leads to produce (overrides config).",
        min=1,
        max=300,
    ),
    output: str = typer.Option(
        "csv",
        "--output",
        help="Output sink: csv | sheets | both.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="No external output writes. Incompatible with --output sheets or both.",
    ),
    category: Optional[str] = typer.Option(
        None,
        "--category",
        help='Target category filter: "Sponsor", "College Delegation", or omit for both.',
    ),
) -> None:
    """Run the lead-finding workflow from a prompt or a brochure PDF."""
    # Load and configure
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001
        _abort(f"Configuration error: {exc}", exit_code=2)
        return  # unreachable; satisfies type checker

    configure_logging(settings)
    logger.info("leadagent %s starting", __version__)

    # ── Validate input mode (exactly one of --prompt / --pdf) ─────────────────
    if prompt and pdf:
        _abort("Specify exactly one of --prompt or --pdf, not both.", exit_code=2)
    if not prompt and not pdf:
        _abort("Specify one of --prompt or --pdf.", exit_code=2)

    # ── Validate --output ─────────────────────────────────────────────────────
    output = output.lower().strip()
    allowed_outputs = {"csv", "sheets", "both"}
    if output not in allowed_outputs:
        _abort(
            f"--output must be one of: {', '.join(sorted(allowed_outputs))}. Got {output!r}.",
            exit_code=2,
        )

    # ── Validate --dry-run conflict ───────────────────────────────────────────
    if dry_run and output in {"sheets", "both"}:
        _abort(
            "--dry-run conflicts with --output sheets or --output both. "
            "Use --output csv for a local dry-run without writing to Google Sheets.",
            exit_code=2,
        )

    # ── Validate Sheets credentials at startup when required ─────────────────
    if output in {"sheets", "both"} and not dry_run:
        if not settings.google_sheets_spreadsheet_id:
            _abort(
                "GOOGLE_SHEETS_SPREADSHEET_ID is not set. "
                "Required for --output sheets or --output both.",
                exit_code=3,
            )
        if not settings.google_application_credentials:
            _abort(
                "GOOGLE_APPLICATION_CREDENTIALS is not set. "
                "Required for --output sheets or --output both.",
                exit_code=3,
            )

    # ── Validate category filter ──────────────────────────────────────────────
    valid_categories = {"Sponsor", "College Delegation"}
    if category and category not in valid_categories:
        _abort(
            f"--category must be one of: {', '.join(sorted(valid_categories))}. "
            f"Got {category!r}.",
            exit_code=2,
        )

    # ── Apply max-leads override ───────────────────────────────────────────────
    if max_leads is not None:
        object.__setattr__(settings, "max_leads", max_leads)

    # ── Print startup summary ─────────────────────────────────────────────────
    input_type = "prompt" if prompt else "pdf"
    categories_active = [category] if category else list(valid_categories)

    typer.echo(f"leadagent {__version__}")
    typer.echo(f"  Input mode   : {input_type}")
    typer.echo(f"  Categories   : {', '.join(categories_active)}")
    typer.echo(f"  Output sink  : {output}")
    typer.echo(f"  Dry run      : {dry_run}")
    typer.echo(f"  Requested leads : {settings.max_leads}")
    _print_budget_snapshot(settings)

    # ── M0: workflow not yet implemented ──────────────────────────────────────
    # The full LangGraph workflow is implemented starting from M1.
    # M0's exit criterion is: leadagent --help and pytest -q pass offline.
    typer.echo(
        "\n[M0] Workflow not yet implemented. "
        "Run leadagent doctor to validate configuration."
    )
    raise typer.Exit(code=0)


# ── doctor command ────────────────────────────────────────────────────────────


@app.command()
def doctor() -> None:
    """Validate configuration and (optionally) provider connectivity.

    Checks performed in M0 (offline, no external calls):
      • Settings load without errors.
      • Non-secret budget snapshot is printable.
      • LLM model names are non-empty.
      • Sheets credentials path is set when a Sheets sink is expected.

    Later milestones add live provider checks (LLM test call, search ping,
    DNS resolution, Sheets access).  See §9 for the full doctor spec.
    """
    exit_code = 0
    issues: list[str] = []
    oks: list[str] = []

    # ── Load settings ─────────────────────────────────────────────────────────
    try:
        settings = get_settings()
        configure_logging(settings)
        oks.append("Settings loaded successfully")
    except Exception as exc:  # noqa: BLE001
        issues.append(f"Settings load failed: {exc}")
        _report_and_exit(oks, issues, exit_code=2)
        return

    # ── Budget snapshot printable ─────────────────────────────────────────────
    try:
        snap = settings.budget_snapshot()
        assert isinstance(snap, dict)
        oks.append(f"Budget snapshot OK ({len(snap)} parameters)")
    except Exception as exc:  # noqa: BLE001
        issues.append(f"Budget snapshot failed: {exc}")

    # ── LLM model names non-empty ─────────────────────────────────────────────
    for attr, label in [
        ("llm_model_planner", "LLM_MODEL_PLANNER"),
        ("llm_model_extraction", "LLM_MODEL_EXTRACTION"),
        ("llm_model_ranking", "LLM_MODEL_RANKING"),
    ]:
        value = getattr(settings, attr, "")
        if value:
            oks.append(f"{label}={value!r}")
        else:
            issues.append(f"{label} is not set (empty string)")

    # ── API key present (not validated, never logged) ─────────────────────────
    if settings.llm_api_key:
        oks.append("LLM_API_KEY is set (value redacted)")
    else:
        issues.append(
            "LLM_API_KEY is not set. Live runs will fail. "
            "Set it in .env or as an environment variable."
        )

    if settings.search_api_key:
        oks.append("SEARCH_API_KEY is set (value redacted)")
    else:
        issues.append(
            "SEARCH_API_KEY is not set. "
            "Search will fail unless a keyless fallback is available."
        )

    # ── Sheets config (advisory when not sheets/both output) ──────────────────
    if settings.google_sheets_spreadsheet_id:
        oks.append("GOOGLE_SHEETS_SPREADSHEET_ID is set")
    else:
        issues.append(
            "GOOGLE_SHEETS_SPREADSHEET_ID is not set "
            "(required for --output sheets or --output both)."
        )

    if settings.google_application_credentials:
        oks.append("GOOGLE_APPLICATION_CREDENTIALS is set (path redacted)")
    else:
        issues.append(
            "GOOGLE_APPLICATION_CREDENTIALS is not set "
            "(required for --output sheets or --output both)."
        )

    # ── Threshold sanity ──────────────────────────────────────────────────────
    t = settings.official_domain_tentative_threshold
    tt = settings.official_domain_trust_threshold
    if t < tt:
        oks.append(
            f"Domain trust thresholds OK "
            f"(tentative={t}, trusted={tt})"
        )
    else:
        issues.append(
            f"OFFICIAL_DOMAIN_TENTATIVE_THRESHOLD ({t}) must be < "
            f"OFFICIAL_DOMAIN_TRUST_THRESHOLD ({tt})"
        )

    _report_and_exit(oks, issues, exit_code=0 if not issues else 3)


def _report_and_exit(oks: list[str], issues: list[str], exit_code: int) -> None:
    """Print doctor results and exit."""
    typer.echo(f"\nleadagent doctor — v{__version__}")
    for msg in oks:
        typer.echo(f"  ✓ {msg}")
    for msg in issues:
        typer.echo(f"  ✗ {msg}", err=True)
    if issues:
        typer.echo(f"\n{len(issues)} issue(s) found.", err=True)
    else:
        typer.echo("\nAll checks passed.")
    raise typer.Exit(code=exit_code)


# ── Version callback ──────────────────────────────────────────────────────────

def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"leadagent {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Evidence-backed lead-finder agent."""
if __name__ == "__main__":
    app()