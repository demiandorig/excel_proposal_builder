"""
Billing-period wording shared by the non-Excel outputs (the AI prompts, the
Strategy Brief .docx and the PowerPoint deck).

Why this exists: the proposal-wide billing period ("time_unit": week | month |
quarter | full_flight) used to be spelled out by hand in each consumer, and the
hand-written copies drifted (the Strategy Brief prompt and its .docx each had
their own "Budget: $X/month x N months" sentence). Anything that has to SAY
something about the billing period now asks this module, so two outputs
describing the same budget can never disagree.

Dependency-free on purpose (only the date/period constants of
monthly_allocation, which itself imports nothing from the app) so any service
can import it without a cycle.

Full Flight ("full_flight"): the whole flight is billed as ONE period. A dollar
figure is a flat total for the entire flight — never a monthly rate — and nothing
is multiplied by a month count: the Step 02 budget figure (and Tier #1-#4) is the
flight's budget exactly as typed, the same way Step 04 seeds it. Full-flight wording
therefore states only that flat total, never "$X x N months".
"""
from __future__ import annotations

import logging
from typing import Optional

from app.services.monthly_allocation import FULL_FLIGHT, TIME_UNITS, normalize_time_unit

logger = logging.getLogger(__name__)

__all__ = [
    "FULL_FLIGHT", "TIME_UNITS", "is_full_flight", "resolve_time_unit",
    "months_phrase", "has_budget_sentence", "budget_sentence",
]


def is_full_flight(time_unit: Optional[str]) -> bool:
    """Quiet predicate (never logs) — call resolve_time_unit() once at an
    entry point when an unrecognized value deserves a warning."""
    return normalize_time_unit(time_unit) == FULL_FLIGHT


def resolve_time_unit(value: Optional[str], *, where: str = "") -> str:
    """The billing period to WORD for: one of TIME_UNITS.

    None / "" mean "not given" and quietly resolve to "month" (the app's
    original behavior; what a proposal saved before the toggle existed means).
    An unrecognized non-empty value also resolves to "month" — these outputs
    are optional extras that must never fail a Generate — but, unlike the old
    silent `dict.get(unit, month)`, it logs a warning so a unit nobody wrote
    wording for can't ship month wording unnoticed. Case/whitespace are
    forgiven ("Full_Flight " -> "full_flight") without a warning."""
    unit = normalize_time_unit(value)
    given = value.strip().lower() if isinstance(value, str) else value
    if given not in (None, "") and given != unit:
        logger.warning(
            "Unknown billing period %r%s; using month wording. Known periods: %s.",
            value, f" ({where})" if where else "", ", ".join(TIME_UNITS),
        )
    return unit


def months_phrase(n) -> str:
    """"1 month" / "3 months" — the count and its correctly pluralized noun."""
    return f"{n} month" if n == 1 else f"{n} months"


def has_budget_sentence(monthly_budget, total_months, time_unit: Optional[str] = "month") -> bool:
    """Whether there is enough to state a budget at all (the .docx omits the
    line otherwise). A full-flight total needs only the budget figure (there is
    no month count to multiply by); a monthly sentence needs both figures."""
    if is_full_flight(time_unit):
        return bool(monthly_budget)
    return bool(monthly_budget and total_months)


def budget_sentence(monthly_budget, total_months, time_unit: Optional[str] = "month") -> str:
    """The one sentence describing the Step 02 intake budget — used verbatim by
    the Strategy Brief prompt and the Strategy Brief .docx.

    Month / week / quarter (the intake figure has always been a MONTHLY one):
        "Budget: $5,000/month × 3 months = $15,000 total flight"
    Full flight (the figure is the whole flight's budget as typed — Step 04 seeds
    it unchanged — and the flight is billed as one period, so only the flat total
    is stated):
        "Budget: $15,000 total flight (single billing period)"
    """
    monthly = monthly_budget or 0
    if is_full_flight(time_unit):
        return f"Budget: ${monthly:,.0f} total flight (single billing period)"
    months = total_months
    return (
        f"Budget: ${monthly:,.0f}/month × {months_phrase(months)} "
        f"= ${monthly * months:,.0f} total flight"
    )
