"""Thread-safe budget ledger for all external resource consumption.

The ``BudgetLedger`` is the single authoritative source for budget counters.
State only carries a ``BudgetSnapshot`` for checkpoints and reporting.
Every search, fetch, LLM call, and PDF/vision call must call
``ledger.try_consume(kind)`` *before* the call; a ``False`` result means
the budget is exhausted and the caller must raise ``BudgetExhausted``.

This prevents parallel org branches from overspending via racing
read-modify-write on state (§3.4.5).

The ledger is injected through ``config["configurable"]["ledger"]`` so tests
can pass a controlled instance without live providers.

Section references: §3.4.5, §3.4.6, §10.1–10.5.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Literal

from leadagent.errors import BudgetExhausted
from leadagent.models import BudgetSnapshot

ResourceKind = Literal["search", "fetch", "llm", "pdf_page"]


@dataclass
class BudgetLedger:
    """Thread-safe ledger tracking consumed resources against hard caps.

    All public methods are safe to call from multiple threads (parallel
    org_researcher branches).  The underlying lock is non-reentrant; callers
    must not hold it across network I/O.

    Attributes
    ----------
    max_searches:
        Hard cap on total search calls (``MAX_SEARCH_CALLS_HARD_CAP``).
    max_fetches:
        Hard cap on total fetch attempts including retries
        (``MAX_TOTAL_FETCH_ATTEMPTS_HARD_CAP``).
    max_llm_calls:
        Hard cap on total LLM calls (``MAX_LLM_CALLS_HARD_CAP``).
    max_pdf_pages:
        Hard cap on PDF pages processed with vision (derived from
        ``PDF_VISION_MAX_PAGES`` × estimated runs).
    """

    max_searches: int = 800
    max_fetches: int = 2000
    max_llm_calls: int = 150
    max_pdf_pages: int = 100

    _searches_used: int = field(default=0, init=False, repr=False)
    _fetches_used: int = field(default=0, init=False, repr=False)
    _llm_calls_used: int = field(default=0, init=False, repr=False)
    _pdf_pages_used: int = field(default=0, init=False, repr=False)
    _cancelled: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    # ── Public interface ───────────────────────────────────────────────────────

    def try_consume(self, kind: ResourceKind, amount: int = 1) -> bool:
        """Attempt to consume ``amount`` units of ``kind``.

        Returns ``True`` and increments the counter if budget allows.
        Returns ``False`` if the hard cap would be exceeded or if the run has
        been cancelled.

        Callers MUST raise ``BudgetExhausted(kind)`` on ``False``; they must
        *not* proceed with the external call.

        Thread-safe; does not block on I/O.
        """
        with self._lock:
            if self._cancelled:
                return False
            if kind == "search":
                if self._searches_used + amount > self.max_searches:
                    return False
                self._searches_used += amount
            elif kind == "fetch":
                if self._fetches_used + amount > self.max_fetches:
                    return False
                self._fetches_used += amount
            elif kind == "llm":
                if self._llm_calls_used + amount > self.max_llm_calls:
                    return False
                self._llm_calls_used += amount
            elif kind == "pdf_page":
                if self._pdf_pages_used + amount > self.max_pdf_pages:
                    return False
                self._pdf_pages_used += amount
            else:
                raise ValueError(f"Unknown resource kind: {kind!r}")
            return True

    def consume_or_raise(self, kind: ResourceKind, amount: int = 1) -> None:
        """Consume ``amount`` units of ``kind`` or raise ``BudgetExhausted``.

        Convenience wrapper around ``try_consume``.
        """
        if not self.try_consume(kind, amount):
            raise BudgetExhausted(kind)

    def cancel(self) -> None:
        """Signal cancellation (e.g. SIGINT handler).

        After this, ``try_consume`` always returns ``False``.
        The in-flight round finishes its current atomic step; accepted rows
        are flushed before the run ends (§3.4.6).
        """
        with self._lock:
            self._cancelled = True

    @property
    def cancelled(self) -> bool:
        """Whether a cancellation has been requested."""
        with self._lock:
            return self._cancelled

    def snapshot(self) -> BudgetSnapshot:
        """Return a non-mutating point-in-time snapshot for state/reporting.

        The returned ``BudgetSnapshot`` may be embedded in ``RunState`` for
        checkpointing; the ledger itself is not serializable.
        """
        with self._lock:
            return BudgetSnapshot(
                searches_used=self._searches_used,
                fetches_used=self._fetches_used,
                llm_calls_used=self._llm_calls_used,
                searches_remaining=max(0, self.max_searches - self._searches_used),
                fetches_remaining=max(0, self.max_fetches - self._fetches_used),
                llm_calls_remaining=max(0, self.max_llm_calls - self._llm_calls_used),
                cancelled=self._cancelled,
            )

    def is_exhausted(self, kind: ResourceKind) -> bool:
        """Return ``True`` if the budget for ``kind`` is at or over cap."""
        with self._lock:
            if kind == "search":
                return self._searches_used >= self.max_searches
            elif kind == "fetch":
                return self._fetches_used >= self.max_fetches
            elif kind == "llm":
                return self._llm_calls_used >= self.max_llm_calls
            elif kind == "pdf_page":
                return self._pdf_pages_used >= self.max_pdf_pages
            raise ValueError(f"Unknown resource kind: {kind!r}")

    # ── Per-org convenience helpers ────────────────────────────────────────────

    def remaining(self, kind: ResourceKind) -> int:
        """Return how many units of ``kind`` remain (≥ 0)."""
        with self._lock:
            if kind == "search":
                return max(0, self.max_searches - self._searches_used)
            elif kind == "fetch":
                return max(0, self.max_fetches - self._fetches_used)
            elif kind == "llm":
                return max(0, self.max_llm_calls - self._llm_calls_used)
            elif kind == "pdf_page":
                return max(0, self.max_pdf_pages - self._pdf_pages_used)
            raise ValueError(f"Unknown resource kind: {kind!r}")

    def used(self, kind: ResourceKind) -> int:
        """Return how many units of ``kind`` have been consumed."""
        with self._lock:
            if kind == "search":
                return self._searches_used
            elif kind == "fetch":
                return self._fetches_used
            elif kind == "llm":
                return self._llm_calls_used
            elif kind == "pdf_page":
                return self._pdf_pages_used
            raise ValueError(f"Unknown resource kind: {kind!r}")

    def __repr__(self) -> str:  # safe to log — no secrets
        snap = self.snapshot()
        return (
            f"BudgetLedger("
            f"searches={snap.searches_used}/{self.max_searches}, "
            f"fetches={snap.fetches_used}/{self.max_fetches}, "
            f"llm={snap.llm_calls_used}/{self.max_llm_calls}, "
            f"cancelled={snap.cancelled})"
        )


def ledger_from_settings(settings: object) -> BudgetLedger:
    """Construct a BudgetLedger from a ``Settings`` instance.

    Accepts ``object`` so this file need not import ``config`` (avoids
    circular dependencies in tests that patch settings).
    """
    return BudgetLedger(
        max_searches=getattr(settings, "max_search_calls_hard_cap", 800),
        max_fetches=getattr(settings, "max_total_fetch_attempts_hard_cap", 2000),
        max_llm_calls=getattr(settings, "max_llm_calls_hard_cap", 150),
        max_pdf_pages=getattr(settings, "pdf_vision_max_pages", 100),
    )
