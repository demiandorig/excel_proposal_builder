"""
Period Breakdown (product name "Monthly Breakdown" predates the Week/
Quarter toggle — internal names here still say "month" in places for the
same reason) — optional per-line-item distribution of a curated line
item's total budget across the WEEK/MONTH/QUARTER periods its flight
actually touches, at whichever granularity the proposal-wide Step 04
toggle currently selects (Step 04 curates monthly_budget x months as ONE
flat total; this is a planner-optional, finer-grained view of THAT SAME
total, never a second source of truth for it — see reconcile_allocation()
below, which is what enforces that programmatically rather than just
showing it in the UI).

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
{period_key: dollars, ...} — DOLLARS are the persisted source of truth;
percentage is always DERIVED (dollars / line_item_total * 100) for
display, never stored separately. That's deliberate: storing both
independently would let them drift apart, which is exactly the
"monthly breakdown becomes a second, driftable source of truth" failure
mode this feature has to avoid. reconcile_allocation() is the single
place that checks a line item's monthly dollars actually sum to its
curated total; nothing else re-implements that check. `period_key`'s
FORMAT depends on the proposal's granularity ("YYYY-MM" for month,
"W{n}-YYYY-MM-DD" for week, "YYYY-MM+YYYY-MM+YYYY-MM" for quarter — see
periods_between() below) but every function in this module treats it as
an opaque string key throughout; nothing parses it.

An empty/absent monthly_allocations dict means "this line item doesn't
use Monthly Breakdown" — the feature is entirely opt-in per line item,
with no separate enabled/disabled flag to fall out of sync with that.

CALENDAR PERIODS vs. periods_between()
---------------------------------------------------------------------
`months` (LineItem.months, an int) is the flat flight-length figure the
rest of the app already uses (total_budget() = monthly_budget * months,
the Excel total-row multiplication, etc.) — it is unit-agnostic
arithmetic (just a multiplier) and does NOT get renamed/reinterpreted
per granularity. Monthly Breakdown does NOT replace or reconcile against
it either way. It derives its own period list purely from the tier's
effective start/end CALENDAR dates via periods_between() below, which
can have a different count than `months` (e.g. `months=3` with a
start/end date pair that only actually spans 2 calendar months, or spans
a partial 4th) — that's an expected, harmless mismatch: the dollar total
these periods must sum to is still LineItem.total_budget() either way,
never `months` itself.

WEEK / QUARTER GRANULARITY, AND MERGING ADJACENT PERIODS
---------------------------------------------------------------------
periods_between(start, end, granularity) is the one entry point that
dispatches to months_between() (calendar months, unchanged/original
behavior), weeks_between() (7-day chunks anchored to the campaign's OWN
start date, not ISO weeks), or quarters_between() (months_between()'s
output grouped into chunks of 3, anchored the same way) — see each
function's own docstring. Every period dict shares the same shape
({key, label, date_range_label, start, end, days_in_month, active_days,
period_count}) regardless of granularity, so default_allocation(),
even_allocation(), reconcile_allocation(), and check_minimum_violations()
are all already generic over "a list of period dicts" and need no
granularity-specific branches of their own.

A campaign that starts mid-period (e.g. Sep 28) gets a genuinely tiny
first period (3 active days of September) that can fail a product's
minimum spend on its own even though the campaign overall is fine.
apply_period_merges() lets a planner combine that stub with an adjacent
period into one combined bucket (shown as "September–October" rather
than two separate, one-too-small lines) — see its own docstring. This is
the ONLY mechanism for handling a too-small period; deliberately NOT
solved by auto-prorating the minimum down for a partial period (see next
section) — an unprompted stub is still meant to be visibly flagged so
the planner makes an explicit merge decision, not silently waved through.

MINIMUM SPEND, BY GRANULARITY
---------------------------------------------------------------------
Product.minimum_spend is already used elsewhere in this codebase
(app/services/recommender.py) as a per-MONTH figure, compared directly
against LineItem.monthly_budget. _effective_minimum_for_period() scales
that monthly figure by granularity (week ≈ ÷4.33, month unchanged,
quarter x3 — see _GRANULARITY_MINIMUM_SCALE) and by how many base
periods a bucket represents (period_count — always 1 unless merged).
Nothing in the existing rate-card data distinguishes a full vs. partial
period, so — per an explicit instruction to document rather than invent
a rule when the data doesn't specify one — a PARTIAL period (one whose
active_days is less than a full period's worth) still gets held to the
FULL per-granularity minimum, never further prorated down by its own
active-day count (the conservative choice: it can't under-flag a real
problem, and it's exactly what makes apply_period_merges() necessary
rather than redundant). That choice lives ONLY in
`_effective_minimum_for_period` below; change that one function if a
day-prorated minimum is wanted later.
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


def _date_range_label(start: date, end: date) -> str:
    """'Sep 28 – Oct 4, 2026' / 'Sep 1 – 30, 2026' (same month/year — don't
    repeat it) / 'Dec 15, 2026 – Jan 4, 2027' (crosses a year). Shown
    alongside every period's own key/label in Step 05 so a planner can see
    exactly which real dates a period (week, month, OR quarter) covers —
    this matters most for the Prorated-by-days mode, where WHY a period
    got a given share is otherwise invisible."""
    # Built with .strftime('%b') + plain .day (not the '%-d'/'%#d' no-
    # leading-zero directives) — those are platform-specific (glibc vs.
    # MSVC use different flags), and this runs on both Windows dev
    # machines and Replit's Linux deploy target.
    if start.year == end.year and start.month == end.month:
        return f"{start.strftime('%b')} {start.day} – {end.day}, {start.year}"
    if start.year == end.year:
        return f"{start.strftime('%b')} {start.day} – {end.strftime('%b')} {end.day}, {start.year}"
    return f"{start.strftime('%b')} {start.day}, {start.year} – {end.strftime('%b')} {end.day}, {end.year}"


def months_between(start: date, end: date) -> list[dict]:
    """
    Every calendar month that overlaps [start, end] (inclusive), in
    order — crossing a calendar-year boundary transparently (Nov 2026 ->
    Feb 2027 yields exactly those 4 months). Each entry:
        {key, label, date_range_label, start, end, days_in_month, active_days}
    active_days is how many of THIS month's days actually fall inside
    [start, end] — the campaign's real day-count in that month, used for
    the day-prorated default allocation (see default_allocation below).
    `start`/`end` are the period's real active bounds as date objects
    (not just the pre-rendered label) — quarters_between() and
    apply_period_merges() combine periods by reading these directly
    rather than re-parsing date_range_label's display string.
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
            "date_range_label": _date_range_label(active_start, active_end),
            "start": active_start,
            "end": active_end,
            "days_in_month": (month_end - cursor).days + 1,
            "active_days": active_days,
            "period_count": 1,
        })
        cursor = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)
    return months


def weeks_between(start: date, end: date) -> list[dict]:
    """
    Every 7-day period from [start, end] (inclusive), anchored to the
    campaign's OWN start date — NOT ISO/calendar weeks (Mon-Sun). A media
    flight doesn't care about ISO week numbers, it cares about "7 days
    from launch"; a campaign starting Sep 28 gets a Sep 28 – Oct 4 first
    week, not a truncated stub week ending on the nearest Sunday. The
    LAST week may be shorter than 7 days if the flight doesn't end on an
    exact 7-day boundary — active_days reflects that (never more than 7).
    Same {key, label, date_range_label, start, end, days_in_month,
    active_days} shape as months_between() — every caller downstream
    (allocation math, reconciliation, minimum-check) is already generic
    over "a period", keyed by `key`, and needs no changes to work with
    either.
    """
    if end < start:
        start, end = end, start
    weeks = []
    cursor = start
    idx = 1
    while cursor <= end:
        week_end = min(cursor + timedelta(days=6), end)
        weeks.append({
            "key": f"W{idx}-{cursor.isoformat()}",
            "label": f"Week {idx}",
            "date_range_label": _date_range_label(cursor, week_end),
            "start": cursor,
            "end": week_end,
            "days_in_month": (week_end - cursor).days + 1,
            "active_days": (week_end - cursor).days + 1,
            "period_count": 1,
        })
        cursor = cursor + timedelta(days=7)
        idx += 1
    return weeks


def quarters_between(start: date, end: date) -> list[dict]:
    """
    Groups months_between()'s own calendar months into chunks of 3, in
    order, from the campaign's own start month — NOT aligned to standard
    calendar quarters (Jan-Mar/Apr-Jun/...). A campaign starting in
    September gets a Sep-Oct-Nov "quarter", not a truncated Q3 stub plus a
    separate Q4 — "3-month lines" from the campaign's own start, per
    explicit request. The last chunk may be 1-2 months if the flight
    doesn't end on an exact 3-month boundary.
    """
    months = months_between(start, end)
    quarters = []
    for i in range(0, len(months), 3):
        chunk = months[i:i + 3]
        first, last = chunk[0], chunk[-1]
        if len(chunk) == 1:
            label = first["label"]
        elif first["start"].year == last["end"].year:
            # "Sep–Nov 2026" — both abbreviated, year stated once.
            label = f"{first['start'].strftime('%b')}–{last['end'].strftime('%b')} {last['end'].year}"
        else:
            # Crosses a year boundary — state each month's own year so
            # "Dec–Feb" doesn't silently hide that Dec is the EARLIER year.
            label = f"{first['start'].strftime('%b %Y')}–{last['end'].strftime('%b %Y')}"
        quarters.append({
            "key": "+".join(m["key"] for m in chunk),
            "label": label,
            "date_range_label": _date_range_label(first["start"], last["end"]),
            "start": first["start"],
            "end": last["end"],
            "days_in_month": sum(m["days_in_month"] for m in chunk),
            "active_days": sum(m["active_days"] for m in chunk),
            # Always 1 — a quarter bucket is ONE granularity unit for
            # minimum-spend scaling (see _effective_minimum_for_period)
            # regardless of how many real calendar months compose it. A
            # short trailing 1-2 month "quarter" stub is still just as
            # susceptible to a too-strict flat minimum as a partial month
            # is — the SAME apply_period_merges() mechanism (merging it
            # into the preceding quarter) is the fix, not a different one.
            "period_count": 1,
        })
    return quarters


_GRANULARITY_FNS = {"week": weeks_between, "month": months_between, "quarter": quarters_between}


def periods_between(start: date, end: date, granularity: str) -> list[dict]:
    """THE one place a caller asks for "the periods this flight touches"
    without hardcoding which granularity that means — week/month/quarter,
    defaulting to month for any unrecognized value (matches this app's
    original, pre-toggle behavior exactly when nothing overrides it)."""
    return _GRANULARITY_FNS.get(granularity, months_between)(start, end)


def apply_period_merges(periods: list[dict], merge_groups: Optional[list[list[str]]]) -> list[dict]:
    """
    Combines specific ADJACENT periods into one bucket — e.g. a flight
    starting Sep 28 gives September just ~3 active days, which can fail a
    product's minimum spend on its own even though the campaign overall
    is fine; merging September into October fixes that by making the
    minimum-check (and the allocation, and the export) treat them as one
    "September–October" total instead of two separate, one-too-small
    periods.

    merge_groups: e.g. [["2026-09", "2026-10"]] — each inner list must be
    2+ period KEYS that are CONTIGUOUS in `periods` (not necessarily
    adjacent calendar dates — contiguous in the list this function is
    given, whatever granularity that's already in). A group referencing
    keys that aren't actually next to each other in `periods` is ignored
    entirely (defensive — a merge spanning a gap would misrepresent the
    date range and isn't something the UI should ever be able to produce,
    but this function doesn't trust that blindly).

    Returns a NEW list — never mutates `periods` — with merged groups
    collapsed into one combined entry (key = keys joined with "+", label/
    date_range_label spanning the first-to-last period, days/active_days
    summed) and every other period untouched. Every downstream consumer
    (default_allocation, reconcile_allocation, check_minimum_violations)
    already operates generically on "a list of period dicts keyed by
    `key`" — pass THIS function's output to them instead of the raw
    periods list, and merging needs no changes anywhere else at all.
    """
    if not merge_groups:
        return list(periods)
    key_to_idx = {p["key"]: i for i, p in enumerate(periods)}
    valid_groups = []
    for group in merge_groups:
        if len(group) < 2:
            continue
        idxs = [key_to_idx[k] for k in group if k in key_to_idx]
        if len(idxs) != len(group):
            continue  # a key that doesn't exist in this periods list — ignore the whole group
        idxs.sort()
        if idxs != list(range(idxs[0], idxs[-1] + 1)):
            continue  # not contiguous — ignore rather than misrepresent the date range
        valid_groups.append(idxs)

    merged_idx_to_group = {}
    for idxs in valid_groups:
        for i in idxs:
            merged_idx_to_group[i] = idxs

    result = []
    consumed = set()
    for i, p in enumerate(periods):
        if i in consumed:
            continue
        group = merged_idx_to_group.get(i)
        if group is None:
            result.append(p)
            continue
        chunk = [periods[j] for j in group]
        consumed.update(group)
        first, last = chunk[0], chunk[-1]
        result.append({
            "key": "+".join(m["key"] for m in chunk),
            "label": _combined_label(first, last),
            "date_range_label": _date_range_label(first["start"], last["end"]) if "start" in first and "end" in last
                else f"{first['date_range_label']} – {last['date_range_label']}",
            "start": first.get("start"),
            "end": last.get("end"),
            "days_in_month": sum(m["days_in_month"] for m in chunk),
            "active_days": sum(m["active_days"] for m in chunk),
            # Summed, not left at 1 — a merged bucket spanning N base
            # periods must clear N times the per-period minimum (see
            # _effective_minimum_for_period): merging September+October
            # means the combined total is held to BOTH months' minimums
            # added together, not just one.
            "period_count": sum(m.get("period_count", 1) for m in chunk),
        })
    return result


def _combined_label(first: dict, last: dict) -> str:
    """'September–October 2026' for two merged MONTHS (matches the
    planner's own phrasing — "show up... as a 'september-october'
    total"); falls back to a generic '{label}–{label}' join for any other
    granularity (weeks/quarters), which is a much rarer real use case for
    merging."""
    first_words, last_words = first["label"].split(), last["label"].split()
    is_month_style = (
        len(first_words) == 2 and len(last_words) == 2
        and first_words[1].isdigit() and last_words[1].isdigit()
    )
    if is_month_style and first_words[1] == last_words[1]:
        return f"{first_words[0]}–{last['label']}"
    return f"{first['label']}–{last['label']}"


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

    `total_budget` is normally `monthly_budget * months` — monthly_budget
    is itself a rounded-to-cents rate (see app.js's _mbSyncBudgetToAllocation,
    which derives it from a planner-typed monthly breakdown), so multiplying
    it back out by `months` periods can legitimately land up to
    `len(allocations) * _CENT` away from the real, exactly-entered sum
    (e.g. $65,000 split into a 3-month rate rounds to $21,666.67/mo, and
    21666.67 * 3 = $65,000.01 — a genuine $0.01 gap with nothing wrong).
    The tolerance scales with the period count so that expected rounding
    slop is never reported as an under/over-allocation error.
    """
    allocated = round(sum(allocations.values()), 2) if allocations else 0.0
    remaining = round(total_budget - allocated, 2)
    allocated_pct = (allocated / total_budget * 100) if total_budget else 0.0
    tolerance = _CENT * max(1, len(allocations))
    return {
        "allocated": allocated,
        "remaining": remaining,
        "allocated_pct": round(allocated_pct, 4),
        "remaining_pct": round(100 - allocated_pct, 4),
        "balanced": abs(remaining) <= tolerance,
        "over_allocated": remaining < -tolerance,
    }


# catalog.py's minimum_spend is documented/entered as a MONTHLY figure.
# Scales it to "one unit of this granularity" — a week is ~1/4.33 of a
# month (52 weeks / 12 months), a quarter is 3 months. Deliberately NOT
# calendar-quarter-exact (a quarter is always treated as exactly 3x,
# never 3.04x or whatever a specific 3-month span's real day-count
# would give) — simple, predictable, and matches how a rate card actually
# states minimums (a flat monthly figure, not a per-day rate).
_GRANULARITY_MINIMUM_SCALE = {"week": 12 / 52, "month": 1.0, "quarter": 3.0}


def granularity_scale(granularity: str) -> float:
    """
    catalog Product.minimum_spend (and any other figure stated as a flat
    MONTHLY number — e.g. recommender.py seeding a suggested budget off a
    product's minimum) scaled to "one unit of `granularity`". Public
    because it's the one place this ratio is defined — recommender.py's
    Suggest Mix imports this rather than keeping its own copy, which
    would drift the moment this file's own constant changes. See
    _effective_minimum_for_period below for the ALLOCATION-CHECK-specific
    use of the same ratio (which also accounts for a merged period's
    period_count — this function alone does not).
    """
    return _GRANULARITY_MINIMUM_SCALE.get(granularity, 1.0)


def _effective_minimum_for_period(minimum_spend: Optional[float], granularity: str, period: dict) -> float:
    """
    Isolated on purpose (see module docstring) — the ONE place minimum-
    spend scaling happens. Two deliberate, documented choices baked in:
      1. Scaled by GRANULARITY (week/month/quarter), via granularity_scale()
         — a week's own minimum is ~1/4.33 of the monthly figure, a
         quarter's is 3x it.
      2. NOT further prorated by how many of a period's days are actually
         active — a partial first/last period (e.g. a flight starting
         Sep 28 gives September ~3 active days) is still held to the FULL
         per-granularity minimum on its own. That's deliberate: it's
         exactly the scenario apply_period_merges() exists to solve
         (merge the too-small stub into its neighbor instead), not a gap
         to quietly paper over here with proration.
    `period["period_count"]` (defaults to 1) multiplies the result — a
    MERGED period spanning N base periods must clear N times the
    per-period minimum, added together, not just one.
    """
    base = (minimum_spend or 0.0) * granularity_scale(granularity)
    return base * period.get("period_count", 1)


def check_minimum_violations(
    line_item_label: str,
    product_name: str,
    minimum_spend: Optional[float],
    is_added_value: bool,
    months: list[dict],
    allocations: dict[str, float],
    granularity: str = "month",
) -> list[dict]:
    """
    One violation dict per period where this line item's allocation is
    below the product's (granularity-scaled) minimum — Added Value lines
    are exempt, matching the SAME exemption Step 04's own below-minimum
    highlight already gives them (LineItem.is_added_value's docstring)
    rather than inventing an inconsistent second rule here.

    `months` is named for backward compatibility with existing callers
    (and the common case) but accepts ANY period list — week/month/
    quarter, merged or not, from periods_between()/apply_period_merges().
    `granularity` defaults to "month" (a no-op scale of 1.0) so every
    pre-existing caller that doesn't pass it keeps its exact original
    behavior.
    """
    if is_added_value or not minimum_spend:
        return []
    violations = []
    for m in months:
        allocated = allocations.get(m["key"], 0.0)
        required = _effective_minimum_for_period(minimum_spend, granularity, m)
        if allocated + _CENT < required:
            violations.append({
                "line_item_label": line_item_label,
                "product_name": product_name,
                "month_key": m["key"],
                "month_label": m["label"],
                "date_range_label": m.get("date_range_label", m["label"]),
                "allocated": allocated,
                "minimum_required": required,
                "shortfall": round(required - allocated, 2),
            })
    return violations
