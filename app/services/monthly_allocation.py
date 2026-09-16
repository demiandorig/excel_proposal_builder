"""
Monthly Breakdown — optional per-line-item distribution of a curated line
item's total budget across the calendar months its flight actually
touches (Step 04 curates monthly_budget x months as ONE flat total; this
is a planner-optional, finer-grained view of THAT SAME total, never a
second source of truth for it — see reconcile_allocation() below, which is
what enforces that programmatically rather than just showing it in the UI).

WHY A SEPARATE MODULE (not folded into proposal_generator.py/catalog.py)
---------------------------------------------------------------------
This is pure date/dollar arithmetic with no openpyxl or Postgres
dependency of its own — it's called from proposal_generator.py (to
compute what the Excel export shows) AND from main.py (to validate a
planner's submitted allocation before generating), so it needs to import
cleanly into both without dragging either along. It reads catalog.Product
for minimum_spend rather than duplicating that number anywhere.

DATA MODEL / SOURCE OF TRUTH
---------------------------------------------------------------------
A LineItem's monthly_allocations field (proposal_generator.py) is
{"YYYY-MM": dollars, ...} — DOLLARS are the persisted source of truth;
percentage is always DERIVED (dollars / line_item_total * 100) for
display, never stored separately. That's deliberate: storing both
independently would let them drift apart, which is exactly the
"monthly breakdown becomes a second, driftable source of truth" failure
mode this feature has to avoid. reconcile_allocation() is the single
place that checks a line item's monthly dollars actually sum to its
curated total; nothing else re-implements that check.

An empty/absent monthly_allocations dict means "this line item doesn't
use Monthly Breakdown" — the feature is entirely opt-in per line item,
with no separate enabled/disabled flag to fall out of sync with that.

CALENDAR MONTHS vs. months_between()
---------------------------------------------------------------------
`months` (LineItem.months, an int) is the flat flight-length figure the
rest of the app already uses (total_budget() = monthly_budget * months,
the Excel total-row multiplication, etc.) — Monthly Breakdown does NOT
replace or reconcile against it. It derives its own month list purely
from the tier's effective start/end CALENDAR dates via months_between()
below, which can have a different count than `months` (e.g. `months=3`
with a start/end date pair that only actually spans 2 calendar months,
or spans a partial 4th) — that's an expected, harmless mismatch: the
dollar total these calendar months must sum to is still
LineItem.total_budget() either way, never `months` itself.

MINIMUM-MONTHLY-SPEND, PARTIAL MONTHS
---------------------------------------------------------------------
Product.minimum_spend is already used elsewhere in this codebase
(app/services/recommender.py) as a per-MONTH figure, compared directly
against LineItem.monthly_budget — so comparing it against a Monthly
Breakdown month's own dollar allocation is the same existing rule, not a
new one. Nothing in the existing rate-card data distinguishes a full vs.
partial calendar month, so — per an explicit instruction to document
rather than invent a rule when the data doesn't specify one — this
applies the FULL minimum_spend even to a month where the campaign is only
active a few days (the conservative choice: it can't under-flag a real
problem). That choice lives ONLY in `_effective_minimum_for_month` below;
change that one function if a prorated minimum is wanted later.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

# A tolerance for float/currency rounding — two dollar figures within a
# cent of each other are "equal" for reconciliation purposes; anything
# wider is a real mismatch, not rounding noise.
_CENT = 0.005

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d")


def parse_flexible_date(raw: Optional[str]) -> Optional[date]:
    """Best-effort parse of this app's free-text date fields (parsed from
    a Notion paste, or typed directly — never guaranteed to be strict
    ISO). Returns None (never raises) on anything unparseable, so a
    caller can surface "please enter valid dates" instead of a 500."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    from datetime import datetime
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _month_label(d: date) -> str:
    return d.strftime("%B %Y")


def _last_day_of_month(d: date) -> date:
    if d.month == 12:
        return date(d.year, 12, 31)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)


def months_between(start: date, end: date) -> list[dict]:
    """
    Every calendar month that overlaps [start, end] (inclusive), in
    order — crossing a calendar-year boundary transparently (Nov 2026 ->
    Feb 2027 yields exactly those 4 months). Each entry:
        {key, label, days_in_month, active_days}
    active_days is how many of THIS month's days actually fall inside
    [start, end] — the campaign's real day-count in that month, used for
    the day-prorated default allocation (see default_allocation below).
    """
    if end < start:
        start, end = end, start  # defensive — callers should never hit this, but don't crash over swapped dates
    months = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        month_end = _last_day_of_month(cursor)
        active_start = max(cursor, start)
        active_end = min(month_end, end)
        active_days = (active_end - active_start).days + 1
        months.append({
            "key": _month_key(cursor),
            "label": _month_label(cursor),
            "days_in_month": (month_end - cursor).days + 1,
            "active_days": active_days,
        })
        cursor = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)
    return months


def default_allocation(total_budget: float, months: list[dict]) -> dict[str, float]:
    """
    Day-prorated split: each month's share of `total_budget` is
    proportional to its active_days out of the campaign's total active
    days (see months_between) — a half-active first/last month gets
    roughly half a full month's share, not a naive equal split. Degrades
    to ~equal-split on its own for a campaign whose months are all full
    anyway.

    One of two selectable default modes (see compute_default_allocation
    below) — the app's own default is EVEN (even_allocation), per explicit
    planner preference; this one is the opt-in alternative for a planner
    who specifically wants day-weighting, not the recommended choice for
    everyone. Kept under its original name for backward compatibility with
    anything already calling it directly.

    The LAST month absorbs whatever's left after every earlier month is
    rounded to the cent, so the dollars always sum to EXACTLY
    total_budget — never rely on percentage math alone for the final
    figure (see module docstring on why dollars are the source of truth).
    """
    if not months:
        return {}
    total_active_days = sum(m["active_days"] for m in months) or 1
    allocations: dict[str, float] = {}
    running = 0.0
    for m in months[:-1]:
        share = round(total_budget * (m["active_days"] / total_active_days), 2)
        allocations[m["key"]] = share
        running += share
    allocations[months[-1]["key"]] = round(total_budget - running, 2)
    return allocations


def even_allocation(total_budget: float, months: list[dict]) -> dict[str, float]:
    """
    Equal split: every month gets the same share of total_budget,
    regardless of how many of its days actually fall inside the flight.
    This app's own DEFAULT mode (see compute_default_allocation below) —
    simpler and more predictable for a planner who just wants a flat
    monthly figure without thinking about partial first/last months.

    Same "last month absorbs the rounding remainder" rule as
    default_allocation, for the same reason (dollars always sum to EXACTLY
    total_budget).
    """
    if not months:
        return {}
    share = round(total_budget / len(months), 2)
    allocations: dict[str, float] = {}
    running = 0.0
    for m in months[:-1]:
        allocations[m["key"]] = share
        running += share
    allocations[months[-1]["key"]] = round(total_budget - running, 2)
    return allocations


def compute_default_allocation(total_budget: float, months: list[dict], mode: str = "even") -> dict[str, float]:
    """
    THE one place a caller should ask for "the default split" without
    hardcoding which mode that means — mirrors app.js's own
    _mbDefaultAllocation dispatcher exactly, so a planner's Step 05 choice
    (sent as `monthly_distribution_mode` on /api/generate) produces the
    SAME numbers server-side (proposal_generator.py's export fallback for
    an uncustomized line) as it already showed them client-side.
    """
    return default_allocation(total_budget, months) if mode == "prorated" else even_allocation(total_budget, months)


def reconcile_allocation(total_budget: float, allocations: dict[str, float]) -> dict:
    """
    THE single programmatic check that a line item's monthly dollars
    actually sum to its curated total — everything else (the UI's running
    total, /api/generate's own gate) calls this rather than
    re-implementing the sum/compare itself.
    """
    allocated = round(sum(allocations.values()), 2) if allocations else 0.0
    remaining = round(total_budget - allocated, 2)
    allocated_pct = (allocated / total_budget * 100) if total_budget else 0.0
    return {
        "allocated": allocated,
        "remaining": remaining,
        "allocated_pct": round(allocated_pct, 4),
        "remaining_pct": round(100 - allocated_pct, 4),
        "balanced": abs(remaining) <= _CENT,
        "over_allocated": remaining < -_CENT,
    }


def _effective_minimum_for_month(minimum_spend: Optional[float], month: dict) -> float:
    """
    Isolated on purpose (see module docstring) — the ONE place a future
    "prorate the minimum for a partial month" rule would go. Current,
    documented assumption: the full monthly minimum applies regardless of
    how many of the month's days are actually active.
    """
    return minimum_spend or 0.0


def check_minimum_violations(
    line_item_label: str,
    product_name: str,
    minimum_spend: Optional[float],
    is_added_value: bool,
    months: list[dict],
    allocations: dict[str, float],
) -> list[dict]:
    """
    One violation dict per (month) where this line item's allocation is
    below the product's monthly minimum — Added Value lines are exempt,
    matching the SAME exemption Step 04's own below-minimum highlight
    already gives them (LineItem.is_added_value's docstring) rather than
    inventing an inconsistent second rule here.
    """
    if is_added_value or not minimum_spend:
        return []
    violations = []
    for m in months:
        allocated = allocations.get(m["key"], 0.0)
        required = _effective_minimum_for_month(minimum_spend, m)
        if allocated + _CENT < required:
            violations.append({
                "line_item_label": line_item_label,
                "product_name": product_name,
                "month_key": m["key"],
                "month_label": m["label"],
                "allocated": allocated,
                "minimum_required": required,
                "shortfall": round(required - allocated, 2),
            })
    return violations
