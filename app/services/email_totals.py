"""
Deterministic dollar figures for the proposal emails (and a checker for the text the model writes around them).

WHY THIS EXISTS
---------------
The two emails used to be handed a handful of loosely-computed numbers ("$X/month x N months", one "Total Net
Investment") and asked to write around them. Every way those numbers could be wrong ended up in a client's inbox:
the wording was always monthly (a weekly or quarterly plan read "$500/month x 12 months"), the total skipped the
Add-Ons the workbook's grand-total row includes, a customised Step 06 breakdown was flattened into an average, the
union of several options was added into one meaningless total, and "Regenerate emails" re-priced the plan from only
the ACTIVE option's lines. This module is now the single place the figures are worked out, in Python, and the prompt
tells the model to use them verbatim and to do no arithmetic of its own.

THE FIGURES EQUAL THE EXCEL EXPORT
----------------------------------
They mirror what proposal_generator / excel_template write, cell for cell:
  * per period   TOTAL DIGITAL row      = ROUNDDOWN(SUM(NET BUDGET column), 0)
  * flight       TOTAL DIGITAL — N-...  = ROUNDDOWN(that x the sheet's period count + SUMIF(add-ons > 0), 0)
  * gross        the same with every NET budget / add-on divided by (1 - agency fee), on the Gross sheet
  * the period count is the sheet's own "Months:" cell (proposal_generator's tier_total_months): the lines' shared
    `months` when they agree, else the flight's calendar periods, else the request's total_months; exactly 1 for
    Full Flight, where the flight is ONE period and nothing is multiplied (see period_copy.py)
  * a customised Step 06 breakdown (any paid line with monthly_allocations) yields a per-period amount for every
    period of the option (merged buckets included); a line that was never customised contributes its default split
    (monthly_allocation.compute_default_allocation, in the plan's distribution mode) exactly as the Excel breakdown
    row does.
Nothing is re-derived: the period list, merges and default split all come from monthly_allocation.

Dependency-light on purpose (monthly_allocation + period_copy + text_utils): it takes duck-typed line items (anything
with product_name / monthly_budget / months / is_added_value / monthly_allocations), so ai_enricher and main can both
use it without importing the workbook builders.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from app.services import monthly_allocation as mo
from app.services import period_copy
from app.services.line_modifier import line_modifier_of
from app.services.text_utils import split_sentences

logger = logging.getLogger(__name__)

__all__ = [
    "LineFigure", "PeriodFigure", "AddonFigure", "OptionTotals", "EmailTotals", "FigureIssue",
    "compute_email_totals", "totals_from_legacy", "fmt_money", "periods_phrase", "unit_noun", "unit_adjective",
    "render_prompt_block", "option_summary_line", "allowed_amounts", "check_text", "check_texts",
    "amounts_in_text", "format_issue", "line_label", "label_for",
]

_UNIT_WORDS = {
    "week": {"noun": "week", "plural": "weeks", "adj": "weekly"},
    "month": {"noun": "month", "plural": "months", "adj": "monthly"},
    "quarter": {"noun": "quarter", "plural": "quarters", "adj": "quarterly"},
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _num(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0


def _floor(x: float) -> int:
    """Excel's ROUNDDOWN(x, 0) — with a hair of tolerance so 14999.999999999998 (float noise) reads 15000 like Excel."""
    return int(math.floor(x + 1e-9))


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def fmt_money(x: float) -> str:
    """"$2,000" for a whole-dollar figure, "$21,666.67" when it carries cents (the workbook's money cells show two
    decimals; a whole-dollar amount reads naturally without them)."""
    x = round(_num(x), 2)
    if abs(x - round(x)) < 0.005:
        return f"${int(round(x)):,}"
    return f"${x:,.2f}"


def periods_phrase(n: int, unit: str) -> str:
    """"12 weeks" / "1 month" / "2 quarters"."""
    words = _UNIT_WORDS.get(unit, _UNIT_WORDS["month"])
    return f"{n} {words['noun'] if n == 1 else words['plural']}"


def unit_noun(unit: str) -> str:
    """"week" / "month" / "quarter" (anything else — Full Flight has no period noun — reads as "month")."""
    return _UNIT_WORDS.get(unit, _UNIT_WORDS["month"])["noun"]


def unit_adjective(unit: str) -> str:
    """"weekly" / "monthly" / "quarterly"."""
    return _UNIT_WORDS.get(unit, _UNIT_WORDS["month"])["adj"]


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

@dataclass
class LineFigure:
    product_name: str
    line_id: Optional[str]
    is_added_value: bool
    per_period: float        # NET, one period of the plan's unit (the WHOLE flight in Full Flight)
    periods: int             # how many periods this line runs (always 1 in Full Flight)
    total: float             # NET, per_period x periods
    custom: bool             # the line carries its own Step 06 amounts, so per_period is only an average
    gross_per_period: float
    gross_total: float
    # The planner's Step 04 line note ("Hispanic, Phoenix only"), already sanitized: a LABEL that tells two lines of the
    # same product apart in the prompt. Never a figure, and never read by any calculation here.
    modifier: str = ""


@dataclass
class PeriodFigure:
    label: str               # "October 2026" / "Week 3" / "Oct–Dec 2026"
    date_range: str          # "Oct 1 – 31, 2026"
    net: float
    gross: float

    def text(self, unit: str) -> str:
        return f"{self.label} ({self.date_range})" if unit == "week" and self.date_range else self.label


@dataclass
class AddonFigure:
    name: str
    net: float
    gross: float


@dataclass
class OptionTotals:
    label: str
    name: str                                  # planner-given display name, "" when none
    unit: str
    n_periods: int                             # the workbook's period multiplier for this option (1 in Full Flight)
    flight_label: str                          # "Oct 1 – Dec 31, 2026" ("" when the dates can't be read)
    lines: list = field(default_factory=list)  # list[LineFigure]
    per_period_net: float = 0.0                # Excel: TOTAL DIGITAL (per period; the flight total in Full Flight)
    media_net: float = 0.0                     # per_period_net x n_periods
    addons: list = field(default_factory=list)  # list[AddonFigure] — paid ones only (amount > 0)
    free_addons: list = field(default_factory=list)   # names of add-ons at no charge
    addons_net: float = 0.0
    total_net: float = 0.0                     # Excel: TOTAL DIGITAL — N-… CAMPAIGN (media + paid add-ons)
    per_period_gross: float = 0.0
    media_gross: float = 0.0
    addons_gross: float = 0.0
    total_gross: float = 0.0
    periods: Optional[list] = None             # list[PeriodFigure]; only when the option has customised per-period amounts
    custom_allocation: bool = False
    mixed_period_counts: bool = False
    notes: list = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.name or f"Option {self.label}"

    @property
    def full_flight(self) -> bool:
        return self.unit == period_copy.FULL_FLIGHT

    @property
    def varies_by_period(self) -> bool:
        """True when no single "per period" figure is honest: the planner customised the per-period amounts."""
        return self.custom_allocation and not self.full_flight


@dataclass
class EmailTotals:
    unit: str
    agency_fee: Optional[float]
    options: list = field(default_factory=list)   # list[OptionTotals]

    @property
    def full_flight(self) -> bool:
        return self.unit == period_copy.FULL_FLIGHT

    @property
    def multi_option(self) -> bool:
        return len(self.options) > 1

    @property
    def has_gross(self) -> bool:
        return self.agency_fee is not None

    def to_dict(self) -> dict:
        """JSON-able copy for the API response (the SPA only displays / compares it)."""
        def line(l: LineFigure) -> dict:
            return {"product_name": l.product_name, "is_added_value": l.is_added_value, "per_period": l.per_period,
                    "periods": l.periods, "total": l.total, "custom": l.custom}

        def option(o: OptionTotals) -> dict:
            return {
                "label": o.label, "name": o.display_name, "n_periods": o.n_periods, "flight": o.flight_label,
                "per_period_net": o.per_period_net, "media_net": o.media_net, "addons_net": o.addons_net,
                "total_net": o.total_net, "per_period_gross": o.per_period_gross, "media_gross": o.media_gross,
                "addons_gross": o.addons_gross, "total_gross": o.total_gross,
                "custom_allocation": o.custom_allocation,
                "periods": [{"label": p.label, "date_range": p.date_range, "net": p.net, "gross": p.gross}
                            for p in (o.periods or [])] if o.periods is not None else None,
                "lines": [line(l) for l in o.lines],
                "addons": [{"name": a.name, "net": a.net, "gross": a.gross} for a in o.addons],
                "notes": list(o.notes),
            }

        return {"unit": self.unit, "agency_fee": self.agency_fee, "options": [option(o) for o in self.options]}


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------

def _valid_fee(request: Any) -> Optional[float]:
    fee = _get(request, "agency_fee")
    try:
        fee = float(fee) if fee is not None else None
    except (TypeError, ValueError):
        return None
    return fee if fee is not None and 0 < fee < 1 else None


def _addon_rows(addons: Optional[Iterable[Any]]) -> list[tuple[str, float]]:
    rows = []
    for a in addons or []:
        name = _clean(_get(a, "product_name")) or _clean(_get(a, "name")) or "Add-on"
        rows.append((name, _num(_get(a, "amount"))))
    return rows


def _tier_periods(request: Any, tier: Any, unit: str) -> tuple[list, list, str]:
    """(base periods, merged periods, flight label) for one option — its own date override first, the campaign's
    dates otherwise, exactly like proposal_generator's tier_start_date / tier_end_date."""
    start_raw = _clean(_get(tier, "start_date")) or _clean(_get(request, "start_date"))
    end_raw = _clean(_get(tier, "end_date")) or _clean(_get(request, "end_date"))
    start, end = mo.parse_flexible_date(start_raw), mo.parse_flexible_date(end_raw)
    if not (start and end):
        return [], [], ""
    if end < start:
        start, end = end, start
    base = mo.periods_between(start, end, unit)
    merged = mo.apply_period_merges(base, _get(tier, "period_merge_groups")) if base else []
    label = f"{start.strftime('%b')} {start.day}, {start.year} – {end.strftime('%b')} {end.day}, {end.year}"
    return base, merged, label


def _option(request: Any, tier: Any, index: int, *, unit: str, mode: str, addon_rows: list, fee: Optional[float],
            known_product: Optional[Callable[[str], bool]]) -> OptionTotals:
    full = unit == period_copy.FULL_FLIGHT
    label = (_clean(_get(tier, "label")) or chr(ord("A") + index)).upper()
    name = _clean(_get(tier, "name"))
    raw_lines = list(_get(tier, "line_items") or [])
    base, merged, flight_label = _tier_periods(request, tier, unit)
    notes: list[str] = []

    # The period multiplier the sheet's "Months:" cell holds (proposal_generator: tier_total_months) — taken from the
    # RAW lines, before products unknown to the catalog are skipped, as the generator does.
    own_months = {int(_num(_get(li, "months"))) for li in raw_lines
                  if _num(_get(li, "months")) > 0 and not _get(li, "is_added_value")}
    mixed = len(own_months) > 1 and not full
    if full:
        n_periods = 1
    elif len(own_months) == 1:
        n_periods = next(iter(own_months))
    else:
        n_periods = len(base) or int(_num(_get(request, "total_months"))) or 3
    if mixed:
        notes.append("the lines carry different period counts, so the workbook multiplies by the flight's calendar span")

    kept = [li for li in raw_lines if known_product is None or known_product(_clean(_get(li, "product_name")))]
    factor = 1.0 / (1.0 - fee) if fee else 1.0

    lines: list[LineFigure] = []
    paid: list[Any] = []
    for li in kept:
        is_av = bool(_get(li, "is_added_value"))
        budget = 0.0 if is_av else _num(_get(li, "monthly_budget"))
        months_i = int(_num(_get(li, "months"))) if _num(_get(li, "months")) > 0 else 1
        # Full Flight folds a line that still carries several periods into ONE (the total is unchanged), exactly like
        # proposal_generator.normalize_full_flight_lines.
        if full:
            per_period, periods = round(budget * months_i, 2), 1
        else:
            per_period, periods = budget, months_i
        custom = bool(_get(li, "monthly_allocations")) and not is_av and not full
        lines.append(LineFigure(
            product_name=_clean(_get(li, "product_name")), line_id=_get(li, "id"), is_added_value=is_av,
            per_period=per_period, periods=periods, total=per_period * periods, custom=custom,
            gross_per_period=per_period * factor, gross_total=per_period * periods * factor,
            modifier=line_modifier_of(li)))
        if not is_av:
            paid.append((li, per_period, months_i))

    per_period_net = _floor(math.fsum(p for _, p, _ in paid))
    per_period_gross = _floor(math.fsum(p * factor for _, p, _ in paid)) if fee else per_period_net
    media_net = per_period_net * n_periods
    media_gross = per_period_gross * n_periods

    addons = [AddonFigure(n, a, a * factor) for n, a in addon_rows if a > 0]
    free_addons = [n for n, a in addon_rows if a <= 0]
    addons_net = math.fsum(a.net for a in addons)
    addons_gross = math.fsum(a.gross for a in addons)
    total_net = _floor(media_net + addons_net)
    total_gross = _floor(media_gross + addons_gross) if fee else total_net

    # Per-period amounts — only when the planner customised at least one line (otherwise the workbook has no breakdown
    # block at all, and "per period x N" is the whole story).
    has_custom = any(l.custom for l in lines)
    periods_out: Optional[list] = None
    if has_custom and merged:
        sums = {p["key"]: 0.0 for p in merged}
        for li, per_period, months_i in paid:
            allocations = _get(li, "monthly_allocations") or mo.compute_default_allocation(
                per_period * months_i, merged, mode)
            for p in merged:
                sums[p["key"]] += _num(allocations.get(p["key"]))
        tolerance = max(2.0, n_periods + 1.0)
        if abs(sum(sums.values()) - media_net) <= tolerance:
            periods_out = [PeriodFigure(p["label"], p.get("date_range_label", ""), round(sums[p["key"]], 2),
                                        round(sums[p["key"]] * factor, 2)) for p in merged]
        else:
            notes.append("the per-period amounts don't add up to the option's total, so none are quoted")
    elif has_custom:
        notes.append("the flight dates couldn't be read, so no per-period amounts are quoted")

    return OptionTotals(
        label=label, name=name, unit=unit, n_periods=n_periods, flight_label=flight_label, lines=lines,
        per_period_net=float(per_period_net), media_net=float(media_net), addons=addons, free_addons=free_addons,
        addons_net=addons_net, total_net=float(total_net), per_period_gross=float(per_period_gross),
        media_gross=float(media_gross), addons_gross=addons_gross, total_gross=float(total_gross),
        periods=periods_out, custom_allocation=has_custom, mixed_period_counts=mixed, notes=notes)


def compute_email_totals(request: Any, tiers: Optional[list], addons: Optional[Iterable[Any]] = None,
                         time_unit: Optional[str] = "month", distribution_mode: str = "even",
                         known_product: Optional[Callable[[str], bool]] = None) -> EmailTotals:
    """Every dollar figure an email may state, per option.

    tiers: [{"label", "name", "start_date", "end_date", "period_merge_groups", "line_items": [...]}, ...] — the
    shape /api/generate already builds. addons: the proposal-wide Add-Ons picks (objects or dicts with
    product_name / amount); the same list is on every option's sheet. known_product: pass
    `lambda n: by_name(n) is not None` to skip, like the workbook does, a product that isn't in the catalog.
    """
    unit = period_copy.resolve_time_unit(time_unit, where="email figures")
    fee = _valid_fee(request)
    rows = _addon_rows(addons)
    tier_list = list(tiers) if tiers else [{"label": "A", "line_items": []}]
    options = [_option(request, t, i, unit=unit, mode=distribution_mode if distribution_mode in ("even", "prorated") else "even",
                       addon_rows=rows, fee=fee, known_product=known_product)
               for i, t in enumerate(tier_list)]
    return EmailTotals(unit=unit, agency_fee=fee, options=options)


def totals_from_legacy(request: Any, line_items: Iterable[Any], tiers: Optional[list] = None,
                       time_unit: Optional[str] = "month", addons: Optional[Iterable[Any]] = None,
                       distribution_mode: str = "even") -> EmailTotals:
    """The same figures from the older (line_items, tiers=None|[...]) call shape ai_enricher still accepts: `tiers`
    is only ever passed for a multi-option proposal, otherwise `line_items` is the one option."""
    if tiers and len(tiers) > 1:
        tier_list = [{"label": t.get("label"), "name": t.get("name"), "line_items": t.get("line_items") or [],
                      "start_date": t.get("start_date"), "end_date": t.get("end_date"),
                      "period_merge_groups": t.get("period_merge_groups")} for t in tiers]
    else:
        tier_list = [{"label": "A", "line_items": list(line_items or [])}]
    return compute_email_totals(request, tier_list, addons, time_unit, distribution_mode)


# ---------------------------------------------------------------------------
# Prompt text
# ---------------------------------------------------------------------------

def _unit_sentence(unit: str) -> str:
    if unit == period_copy.FULL_FLIGHT:
        return ("Billing period: FULL FLIGHT — the whole flight is billed as ONE period, so every figure below is a flat "
                "total for the entire flight. Nothing is monthly and nothing is multiplied by a number of months.")
    if unit == "week":
        return ("Billing period: WEEKLY — the plan is priced per 7-day week counted from launch, so a \"per week\" figure "
                "is ONE WEEK's budget. Never call it monthly and never multiply it by a number of months.")
    if unit == "quarter":
        return ("Billing period: QUARTERLY — the plan is priced per 3-month quarter counted from launch, so a \"per quarter\" "
                "figure is ONE QUARTER's budget. Never call it monthly and never multiply it by a number of months.")
    return "Billing period: MONTHLY — a \"per month\" figure is one calendar month of the flight."


def label_for(product_name: str, modifier: str = "") -> str:
    """"Display" — or, for a line the planner gave a note, "Display [line note: Hispanic, Phoenix only]": the label that
    tells two lines of the same product apart. A line without a note is just its product name, as it always was."""
    return f"{product_name} [line note: {modifier}]" if modifier else product_name


def line_label(line: LineFigure) -> str:
    return label_for(line.product_name, line.modifier)


def _line_phrase(line: LineFigure, opt: OptionTotals) -> str:
    if line.is_added_value:
        return "added value — no cost to the client, and in no total"
    if opt.full_flight:
        return f"{fmt_money(line.per_period)} flat for the whole flight"
    words = _UNIT_WORDS[opt.unit]
    if line.custom:
        return (f"{fmt_money(line.total)} for the flight over {periods_phrase(line.periods, opt.unit)} "
                f"(the amount differs by {words['noun']} — see the {words['noun']}-by-{words['noun']} line)")
    return (f"{fmt_money(line.per_period)} per {words['noun']} × {periods_phrase(line.periods, opt.unit)} "
            f"= {fmt_money(line.total)}")


def _option_block(opt: OptionTotals, totals: EmailTotals) -> list[str]:
    words = _UNIT_WORDS.get(opt.unit)
    out: list[str] = []
    if totals.multi_option:
        out.append(opt.display_name.upper() + (f" (option {opt.label})" if opt.name else ""))
    if opt.flight_label:
        span = "one billing period" if opt.full_flight else periods_phrase(opt.n_periods, opt.unit)
        out.append(f"  Flight: {opt.flight_label} — {span}")
    out.append("  Line items (net):")
    for line in opt.lines:
        out.append(f"    - {line_label(line)}: {_line_phrase(line, opt)}")
    if not opt.lines:
        out.append("    - (no products)")
    if opt.periods:
        out.append(f"  {words['noun'].capitalize()}-by-{words['noun']} total, all lines (net): "
                   + " · ".join(f"{p.text(opt.unit)} {fmt_money(p.net)}" for p in opt.periods)
                   + f" — these amounts differ; never present one as a flat {words['adj']} figure")
    elif words and not opt.custom_allocation:
        out.append(f"  All lines together, per {words['noun']} (net): {fmt_money(opt.per_period_net)}")
    elif words:
        out.append(f"  The per-{words['noun']} amounts were customised and can't be quoted; state only the totals below")
    label_media = "Media total for the flight (net)" if not opt.full_flight else "Media total, flat for the whole flight (net)"
    out.append(f"  {label_media}: {fmt_money(opt.media_net)}")
    if opt.addons:
        out.append("  One-time add-ons (net, the same on every option): "
                   + "; ".join(f"{a.name} {fmt_money(a.net)}" for a in opt.addons) + f" — add-ons total {fmt_money(opt.addons_net)}")
    if opt.free_addons:
        out.append("  Add-ons at no charge: " + "; ".join(opt.free_addons))
    out.append(f"  TOTAL INVESTMENT, net — the Excel's campaign grand-total row"
               f"{' (media + one-time add-ons)' if opt.addons else ''}: {fmt_money(opt.total_net)}")
    if totals.agency_fee:
        gross_bits = []
        if words and not opt.custom_allocation:
            gross_bits.append(f"all lines per {words['noun']} {fmt_money(opt.per_period_gross)}")
        gross_bits.append(f"media total {fmt_money(opt.media_gross)}")
        if opt.addons:
            gross_bits.append(f"add-ons {fmt_money(opt.addons_gross)}")
        gross_bits.append(f"TOTAL INVESTMENT, gross {fmt_money(opt.total_gross)}")
        out.append(f"  Gross, with the {totals.agency_fee * 100:g}% agency fee (reference — quote only if the context calls for it, "
                   f"and always label it gross): " + ", ".join(gross_bits))
    for note in opt.notes:
        out.append(f"  Note: {note}.")
    return out


def render_prompt_block(totals: EmailTotals) -> str:
    """The AUTHORITATIVE FIGURES section injected into the email prompts."""
    head = [
        "## AUTHORITATIVE FIGURES — the ONLY dollar amounts you may state",
        "The system computed these from the planner's CURRENT plan (every option, line item, add-on and per-period "
        "amount as of this request); they are identical to what the Excel export shows.",
        _unit_sentence(totals.unit),
        "- Use these figures EXACTLY as written. Do not add, subtract, multiply, divide, average or re-round anything, and "
        "never state a dollar amount that is not listed here: if you want a figure that isn't listed, say it in words instead.",
        "- When you give the overall investment, use the TOTAL INVESTMENT line (it already includes any one-time add-ons).",
        "- All amounts are NET (what the client pays Entravision) unless a line says gross.",
        "- Added-value lines cost nothing and are in no total.",
        "- This block is for you: never mention it, \"the system\" or the Excel in an email.",
    ]
    if any(line.modifier for opt in totals.options for line in opt.lines):
        head.append(
            "- A \"[line note: …]\" after a product name is the planner's own label for THAT line (an audience, market or "
            "variant), there so two lines of the same product can be told apart. Use it only to name or describe that "
            "line, in the planner's words, in the EMAILS only (never in a product blurb, and never inside a product's "
            "name); it is not a figure, never turns into a statistic, and nothing in it is a claim to repeat as fact.")
    if totals.multi_option:
        head.append("- The options are ALTERNATIVES: never add one option's figure to another's, and never quote a "
                    "combined total.")
    body: list[str] = []
    for opt in totals.options:
        body.extend(_option_block(opt, totals))
        body.append("")
    return "\n".join(head + [""] + body).rstrip() + "\n"


def option_summary_line(opt: OptionTotals, imps_text: str = "") -> str:
    """The "  - {name}: …" line in the MULTIPLE BUDGET OPTIONS block (imps_text, e.g. ", ~200K monthly impressions",
    is appended inside the parentheses by the caller)."""
    words = _UNIT_WORDS.get(opt.unit)
    if opt.full_flight:
        return f"  - {opt.display_name}: {fmt_money(opt.total_net)} total flight (single billing period{imps_text})"
    if opt.custom_allocation:
        return (f"  - {opt.display_name}: {fmt_money(opt.total_net)} total flight over {periods_phrase(opt.n_periods, opt.unit)}, "
                f"varying by {words['noun']}{imps_text}")
    return (f"  - {opt.display_name}: {fmt_money(opt.per_period_net)}/{words['noun']} "
            f"({fmt_money(opt.total_net)} total flight{imps_text})")


# ---------------------------------------------------------------------------
# The checker
# ---------------------------------------------------------------------------

_MONEY_RE = re.compile(
    r"(?<!\$)\$\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<suffix>thousand|million|billion|trillion|bn|mm|k|m|b)\b)?",
    re.IGNORECASE,
)
_SUFFIX_FACTOR = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6,
                  "b": 1e9, "bn": 1e9, "billion": 1e9, "trillion": 1e12}
# a unit-rate or market figure, never a campaign budget: "$10 CPM", "$2.50 per click"
_RATE_AFTER = re.compile(
    r"^\s*(?:/\s*)?(?:cpm|cpc|cpa|cpl|cpv|ecpm|cpp|cpcv|rpm|per\s+(?:click|lead|view|install|thousand|1,?000|impression|"
    r"point|acquisition|conversion|visit|call|download|completed))", re.IGNORECASE)
_RATE_BEFORE = re.compile(r"(?:cpm|cpc|cpa|cpl|cpv|ecpm|cpp|cpcv|rpm)\W{0,6}(?:of|at|is|around|about|:)?\W{0,3}$", re.IGNORECASE)
# a sentence is about the PLAN (so its dollar amounts must be plan figures) when it says so ...
_PLAN_STRONG = re.compile(r"\b(?:invest\w*|budget\w*|options?|proposal|flight|net|gross|add-?ons?|one-?time|package)\b", re.IGNORECASE)
_PLAN_WEAK = re.compile(
    r"\b(?:totals?|per\s+(?:month|week|quarter)|monthly|weekly|quarterly|each\s+(?:month|week|quarter)|"
    r"a\s+(?:month|week|quarter))\b|/\s?(?:mo|month|wk|week|qtr|quarter)\b", re.IGNORECASE)
# ... and is a market statistic ("households spend an average of $1,200 a year") when it reads like one
_STAT = re.compile(
    r"\b(?:average|avg|median|households?|consumers?|shoppers?|listeners?|viewers?|according|surveys?|study|studies|"
    r"reports?|nielsen|emarketer|statista|pew|census|ipsos|comscore|forecast\w*|projected|worth|buying\s+power|"
    r"annual\w*|per\s+year|a\s+year|each\s+year)\b", re.IGNORECASE)
_MIN_CHECKED_VALUE = 50.0


@dataclass
class FigureIssue:
    amount: str
    value: float
    sentence: str
    where: str = ""


def _money_value(match: "re.Match") -> tuple[float, Optional[str]]:
    value = float(match.group("num").replace(",", ""))
    suffix = (match.group("suffix") or "").lower() or None
    return (value * _SUFFIX_FACTOR[suffix] if suffix else value), suffix


def amounts_in_text(text: str) -> list[float]:
    """Every dollar amount written in `text` (no filtering) — used to learn which figures a prompt legitimately
    contained (rate-card descriptions, the AE's own comments)."""
    out = []
    for m in _MONEY_RE.finditer(text or ""):
        out.append(_money_value(m)[0])
    return out


def allowed_amounts(totals: EmailTotals) -> list[float]:
    """Every dollar figure an email may legitimately state for these totals."""
    vals: list[float] = []
    for o in totals.options:
        vals += [o.per_period_net, o.media_net, o.addons_net, o.total_net, o.per_period_gross, o.media_gross,
                 o.addons_gross, o.total_gross]
        for l in o.lines:
            vals += [l.per_period, l.total, l.gross_per_period, l.gross_total]
            vals += amounts_in_text(l.modifier)       # a "$25 CPA cap" the planner typed in a line note is theirs to quote
        for a in o.addons:
            vals += [a.net, a.gross]
        for p in o.periods or []:
            vals += [p.net, p.gross]
    return [v for v in vals if v]


def _is_allowed(value: float, suffix: Optional[str], allowed: Iterable[float]) -> bool:
    for a in allowed:
        # a "$30K"-style shorthand may round (1%); an exact figure may differ by a dollar (whole-dollar display)
        tol = max(1.0, abs(a) * 0.01) if suffix else 1.0
        if abs(value - a) <= tol:
            return True
    return False


def _segments(text: str) -> list[tuple[int, int, str]]:
    """(start, end, sentence) for every sentence of every line, abbreviation-aware."""
    out: list[tuple[int, int, str]] = []
    pos = 0
    for line in text.split("\n"):
        offset = pos
        for seg in split_sentences(line):
            out.append((offset, offset + len(seg), seg.strip()))
            offset += len(seg)
        pos += len(line) + 1
    return out


def check_text(text: str, allowed: Iterable[float], *, plan_terms: Iterable[str] = (), where: str = "") -> list[FigureIssue]:
    """Dollar amounts in `text` that belong to the plan but match none of the `allowed` figures.

    Deliberately conservative — a market statistic, a "$10 CPM" and a "$1.2 billion" must never raise a false alarm:
    an amount is only judged when its own sentence talks about the plan (budget / investment / option / per month /
    a product or option name…), it isn't a unit rate, it carries no million/billion suffix, and it is at least $50.
    Never raises on odd formatting."""
    try:
        text = text or ""
        allowed = [float(a) for a in allowed]
        terms = [t.lower() for t in plan_terms if t and len(t.strip()) >= 3]
        segs = _segments(text)
        issues: list[FigureIssue] = []
        seen: set = set()
        for m in _MONEY_RE.finditer(text):
            value, suffix = _money_value(m)
            if value < _MIN_CHECKED_VALUE or (suffix and _SUFFIX_FACTOR[suffix] >= 1e6):
                continue
            if _is_allowed(value, suffix, allowed):
                continue
            after = text[m.end():m.end() + 36]
            before = text[max(0, m.start() - 16):m.start()]
            if _RATE_AFTER.search(after) or _RATE_BEFORE.search(before):
                continue
            sentence = next((s for a, b, s in segs if a <= m.start() < b), "")
            low = sentence.lower()
            about_plan = bool(_PLAN_STRONG.search(sentence)) or any(t in low for t in terms) or (
                bool(_PLAN_WEAK.search(sentence)) and not _STAT.search(sentence))
            if not about_plan:
                continue
            key = (m.group(0).strip(), sentence)
            if key in seen:
                continue
            seen.add(key)
            issues.append(FigureIssue(amount=m.group(0).strip(), value=value, sentence=sentence, where=where))
        return issues
    except Exception:       # a checker must never take an email down with it
        logger.exception("email figure check failed; skipping it")
        return []


def check_texts(texts: dict, totals: EmailTotals, extra_allowed: Iterable[float] = ()) -> list[FigureIssue]:
    """check_text over {"internal email": text, ...} with this proposal's allowed figures and names."""
    allowed = allowed_amounts(totals) + [float(a) for a in extra_allowed]
    terms: list[str] = []
    for o in totals.options:
        terms.append(o.name)
        terms.extend(l.product_name for l in o.lines)
        terms.extend(a.name for a in o.addons)
    issues: list[FigureIssue] = []
    for where, text in texts.items():
        issues.extend(check_text(text or "", allowed, plan_terms=terms, where=where))
    return issues


def format_issue(issue: FigureIssue, width: int = 150) -> str:
    sentence = " ".join(issue.sentence.split())
    if len(sentence) > width:
        sentence = sentence[:width].rsplit(" ", 1)[0] + "…"
    where = f" ({issue.where})" if issue.where else ""
    return f"{issue.amount}{where}: “{sentence}”"
