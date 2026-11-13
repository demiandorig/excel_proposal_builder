"""
Duplicate a proposal for a NEW client: the pure transform behind POST /api/proposal/{id}/duplicate.

A planner picks a past proposal in the history, types the basics of the new opportunity (client, flight dates, Notion ID...)
and gets a fresh DRAFT that already holds the old proposal's products, prices and options. Everything that makes the copy
usable lives here, as plain functions over plain dicts — no database, no clock, no network, never a change to the dict it is
handed — so every rule can be tested offline:

  * transform_reopen_state(source, basics, product_known=...) -> DuplicateResult   the whole copy (state + notes + numbers)
  * describe_source(state)                                                          the facts + form defaults of GET .../duplicate-defaults
  * derive_proposal_id(email, source_id, request_id, basics)                        the new row's id (a double click finds its draft)
  * plain_text(value, limit)                                                        the sanitiser the request model uses
  * the helpers the tests pin one by one: scrub_tokens, mentions, website_token, shape_signature, translate_merges,
    follows_default, option_totals

What carries and what resets (the long version, with the reasons, is docs/duplicate_proposal.md):

  carries    products, prices, per-period budgets, add-ons, options (letters), billing period, split mode, targeting text,
             hand-typed avails numbers, a hand-made period split when the new flight has the SAME SHAPE as the old one
  resets     the client identity, Notion ID, agency and fee (they come from the form), everything the AI wrote (strategy brief,
             roadblocks, emails), the pasted request, per-product answers, internal CCs, export-tab choices, per-option date
             overrides, Constanza estimates, and a hand-made split that cannot be carried to a different flight
  clears     any free text that names the OLD client or agency (a note, a target, an option name): it is counted and reported,
             never silently kept. A false positive costs one retyped note; a false negative is the leak this exists to stop.

Dates: a period key embeds absolute dates ("2026-09", "W1-2026-09-01"), so a new flight cannot reuse the old keys, and the front
end's own reconcile (_mbReconcileMonthsForDateChange) would zero every allocation of a shifted flight. A duplicate needs a re-key
BY POSITION, which exists nowhere else: period i of the old flight becomes period i of the new one, but only when the two flights
have the same SHAPE (same number of periods, same whole/partial pattern). Anything else starts from the default split.

The output has the shape of proposals.reopen_state, so the route pushes it through DraftSaveRequest -> main._build_reopen_state (the
builder every draft save uses) and the stored row cannot drift from a saved draft. tests/test_duplicate_reopen_fixed_point.py proves,
in a real JavaScript engine, that the front end's reopen and Step 06 code leave that output exactly as it is.
"""
from __future__ import annotations

import base64
import copy
import dataclasses
import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from typing import Any, Callable, Iterable, Optional

from app.services import monthly_allocation as mo
from app.services.notion_parser import ProductSpecifics

__all__ = [
    "DuplicateBasics", "DuplicateResult", "MAX_FLIGHT_DAYS", "NOTE_CODES", "ESTIMATE_MARKERS",
    "transform_reopen_state", "describe_source", "derive_proposal_id", "plain_text", "option_totals",
    "scrub_tokens", "mentions", "website_token", "shape_signature", "translate_merges", "follows_default",
]

MAX_FLIGHT_DAYS = 3660                # the same ceiling as app.js _constanzaFlightDays: ten years
DEFAULT_SLACK = 1.0                   # app.js _MB_DEFAULT_SLACK: a split within $1 of a default "follows the default"
_CENT = 0.005                         # app.js _MB_CENT: the tolerance of "these dollars add up to that total"

# What a Constanza estimate leaves on an avails entry (AvailsEntry in main.py). `max_imps_estimated` alone is NOT a marker:
# Fixed / estimated-CPM products show "Est." text derived from a number the planner typed.
ESTIMATE_MARKERS = ("forecast_snapshot", "meta_forecast", "geo_resolution", "win_rate", "hispanic_share")
# The dict-valued markers can also carry text (a target, a geography) that names the old client when the estimates are kept.
_SNAPSHOT_FIELDS = ("forecast_snapshot", "meta_forecast", "geo_resolution")
# What a planner can type by hand into an avails entry: numbers, or the free-form text of "free-form" mode.
_MANUAL_AVAILS_NUMBERS = ("max_imps", "max_spend", "est_uniques")
_MANUAL_AVAILS_TEXTS = ("max_imps_text", "max_spend_text", "est_uniques_text")

# The notes the route returns (frozen: the page and the tests key off the codes). `level` is "info" or "check".
NOTE_CODES = (
    "dates_moved", "dates_same", "dates_set", "total_changed", "budgets_carried", "rates_carried", "tier_dates_cleared",
    "split_reset", "merges_dropped", "avails_cleared", "avails_manual_kept", "avails_kept", "texts_cleared",
    "products_missing", "request_type_blanked", "ai_dropped",
)

# Free text on a line that goes into the export or tells two lines apart: cleared when it names the old client or agency.
_LINE_TEXT_FIELDS = ("notes_override", "line_modifier", "target_override", "target_secondary", "objective_override")
# Request fields that carry targeting / campaign words across: cleared one by one when they name the old client or agency.
_REQUEST_TEXT_FIELDS = ("campaign_goal", "language", "other_languages", "geo", "demo", "behavioral", "contextual", "products_selected_raw")
# Request fields with no Step 02 input (or that describe the OLD deal): the planner could not see or fix them, so they reset.
_RENEWAL_FIELDS = ("renewal_ae_requesting", "renewal_change_type", "renewal_client", "renewal_changes_description",
                   "renewal_campaign_dates", "renewal_budget", "renewal_due_date")

# Words of a client or agency name too generic to identify it ("Acme Motors" -> "acme", never "motors"). Only individual
# words are filtered: the whole name is always a token, so a client literally called "Auto Group" is still recognised.
_GENERIC_WORDS = frozenset("""
    inc llc ltd corp co company the and group of for usa us auto autos motors services service solutions systems media digital
    global national american restaurant restaurants bank health care home homes center centers store stores shop club
    grupo banco tienda servicios seguros clinica hospital farmacia restaurante
""".split())

_WORD = re.compile(r"[^\W_][\w'&.-]*")
_UNWANTED_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})      # controls, zero-width / bidi marks, the BOM, lone surrogates


# ======================================================================================================================
# Inputs and outputs
# ======================================================================================================================

@dataclass(frozen=True)
class DuplicateBasics:
    """The validated quick form (main.DuplicateProposalRequest minus its request id): what the planner typed for the NEW opportunity.

    Dates are ISO strings, `notion_id` is "EVC-12345" or "", `agency_fee` a fraction (0 < f < 1) or None. A field that is None
    (requested_by, salesperson_email, salesperson_market, request_type) means "keep the source's value"; "" means "blank"."""
    client_name: str
    start_date: str
    end_date: str
    notion_id: str = ""
    agency_name: str = ""
    agency_fee: Optional[float] = None
    client_website: str = ""
    requested_by: Optional[str] = None
    salesperson_email: Optional[str] = None
    salesperson_market: Optional[str] = None
    request_type: Optional[str] = None
    campaign_name: str = ""
    keep_avails: bool = False


@dataclass
class DuplicateResult:
    state: dict                                        # reopen_state shape, ready for DraftSaveRequest -> main._build_reopen_state
    notes: list[dict] = field(default_factory=list)    # [{"code", "level": "info" | "check", "text"}], plain text
    stats: dict = field(default_factory=dict)          # totals_before / totals_after per option label, period count, counters


# ======================================================================================================================
# Small helpers
# ======================================================================================================================

def plain_text(value: Any, limit: int, what: str = "This field") -> str:
    """User-typed text made safe to store and to put in a title: controls, zero-width and bidi marks and lone surrogates (which
    cannot be encoded to UTF-8) become a space, runs of whitespace collapse, the ends are trimmed. None is "". Raises ValueError
    (plain words, shown to the planner as they are) for something that is not text, or longer than `limit` after cleaning."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{what} must be text.")
    if len(value) > limit * 4 + 64:      # no real entry is padded to four times its limit: refuse it before walking every character
        raise ValueError(f"{what} can be at most {limit} characters.")
    text = " ".join("".join(" " if unicodedata.category(ch) in _UNWANTED_CATEGORIES else ch for ch in value).split())
    if len(text) > limit:
        raise ValueError(f"{what} can be at most {limit} characters.")
    return text


def _num(value: Any) -> float:
    """A stored number as a float; anything else (None, text, a bool, NaN) counts as 0 so a damaged line never raises."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return 0.0
    return float(value)


def _months_of(line: dict) -> float:
    """The multiplier of a line's per-period budget: its `months` (1 when it has none), as the totals everywhere else read it."""
    months = line.get("months")
    if isinstance(months, bool) or not isinstance(months, (int, float)) or not math.isfinite(months):
        return 1.0
    return float(months)


def _dicts(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _fmt(d: date) -> str:
    # No %-d / %#d: those directives are platform specific (the repo runs on macOS, Linux and Windows).
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def _fmt_window(start: Optional[date], end: Optional[date]) -> str:
    return f"{_fmt(start)} – {_fmt(end)}" if start and end else "dates that could not be read"


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _date(raw: Any) -> Optional[date]:
    return mo.parse_flexible_date(raw) if isinstance(raw, str) else None


def _window(start: Optional[date], end: Optional[date]) -> Optional[tuple[date, date]]:
    """A flight periods can be built for: both dates read, not backwards, at most ten years. Anything else is "no readable dates"
    (a backwards or absurdly long source flight is not something a split can be carried across, and an absurd one would be slow)."""
    if start is None or end is None or end < start or (end - start).days + 1 > MAX_FLIGHT_DAYS:
        return None
    return start, end


def _base_periods(window: Optional[tuple[date, date]], unit: str) -> Optional[list[dict]]:
    """The BASE periods of a flight (before any merges): None for no flight, or for a date so near year 9999 that the calendar
    arithmetic itself overflows."""
    if window is None:
        return None
    try:
        return mo.periods_between(window[0], window[1], unit)
    except (ValueError, OverflowError):
        return None


# ======================================================================================================================
# Which free text names the old client or agency
# ======================================================================================================================

def _fold(text: str) -> str:
    """Lower-case with the accents taken off ("Café" and "cafe" are the same word): the matcher errs toward clearing."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def scrub_tokens(*names: Any) -> list[str]:
    """The words that identify a client or agency: each whole name (3+ characters) and each significant word of it (4+ characters,
    not a generic company word), folded to lower case without accents. A possessive also gives its root ("Joe's" -> "joe's" and
    "joe", when long enough). A two-letter name never becomes a token on its own (it would match half the language), and the words
    "motors", "group", "restaurant"... never do either, but the WHOLE name "Acme Motors" always does."""
    out: list[str] = []

    def add(token: str) -> None:
        if token and token not in out:
            out.append(token)

    for raw in names:
        if not isinstance(raw, str):
            continue
        name = " ".join(_fold(raw).split())
        if len(name) >= 3:
            add(name)
        for word in _WORD.findall(name):
            word = word.strip(".'&-")
            for candidate in (word, word[:-2] if word.endswith("'s") else ""):
                if len(candidate) >= 4 and candidate not in _GENERIC_WORDS:
                    add(candidate)
    return out


@lru_cache(maxsize=256)
def _pattern(tokens: tuple[str, ...]) -> "re.Pattern[str]":
    alternation = "|".join(re.escape(token) for token in sorted(tokens, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)")


def mentions(text: Any, tokens: Iterable[str]) -> bool:
    """True when `text` contains one of the tokens as a WHOLE word, in any case and with or without accents ("Acme's" matches
    "acme"; "Acmeville" does not). Non-text and empty text never mention anything."""
    if not isinstance(text, str) or not text.strip():
        return False
    folded = tuple(sorted({_fold(token) for token in tokens if isinstance(token, str) and token.strip()}))
    if not folded:
        return False
    return _pattern(folded).search(_fold(text)) is not None


_SECOND_LEVEL_TLDS = frozenset({"co", "com", "org", "net", "gov", "edu", "ac"})      # acme.co.uk, acme.com.mx


def website_token(url: Any) -> list[str]:
    """The name part of a website as a one-word token list (empty when there is none worth matching): "https://www.acmemotors.com/x"
    gives ["acmemotors"], "shop.acme.co.uk" gives ["acme"]. A label shorter than 4 characters or a generic word is not a token."""
    if not isinstance(url, str) or not url.strip():
        return []
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", url.strip().lower()).split("/")[0].split("?")[0].split("#")[0]
    host = host.rsplit("@", 1)[-1].split(":")[0]
    labels = [label for label in host.split(".") if label]
    if labels and labels[0] == "www":
        labels = labels[1:]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL_TLDS:
        label = labels[-3]
    elif len(labels) >= 2:
        label = labels[-2]
    else:
        label = labels[0] if labels else ""
    label = _fold(label)
    return [label] if len(label) >= 4 and label not in _GENERIC_WORDS else []


# ======================================================================================================================
# Periods: shape, merges, defaults
# ======================================================================================================================

def shape_signature(periods: list[dict]) -> tuple[bool, ...]:
    """Which periods are WHOLE and which are partial (a flight that starts on the 17th has a 12-day first month). Two flights have
    the same SHAPE when they have the same number of periods and the same whole/partial pattern: only then does "the third period"
    mean the same thing in both, so a hand-made split and the combined-period groups can be carried by position. (A week is always
    whole: the last, short week counts as one, as everywhere else in the app.)"""
    return tuple(p["active_days"] == p["days_in_month"] for p in periods)


def _merge_positions(groups: Any, base: list[dict]) -> list[list[int]]:
    """The merge groups monthly_allocation.apply_period_merges would honour, as sorted position lists: two or more known keys,
    contiguous in `base`. A group that overlaps an earlier one is ignored too (two overlapping groups are not a valid split)."""
    index = {p["key"]: i for i, p in enumerate(base)}
    taken: set[int] = set()
    out: list[list[int]] = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, (list, tuple)) or len(group) < 2 or not all(isinstance(k, str) and k in index for k in group):
            continue
        positions = sorted(index[k] for k in group)
        if positions != list(range(positions[0], positions[-1] + 1)) or taken.intersection(positions):
            continue
        taken.update(positions)
        out.append(positions)
    return out


def translate_merges(groups: Any, old_base: list[dict], new_base: list[dict]) -> list[list[str]]:
    """Merge groups (lists of base-period keys) carried to the SAME POSITIONS of another flight. Only meaningful when both flights
    have the same number of base periods; with a different count nothing is carried (an empty list). Groups
    apply_period_merges would ignore (unknown keys, a gap, fewer than two periods, an overlap) are not carried either."""
    if len(old_base) != len(new_base):
        return []
    return [[new_base[i]["key"] for i in positions] for positions in _merge_positions(groups, old_base)]


def follows_default(allocations: dict, total: float, periods: list[dict]) -> bool:
    """True when every period of `allocations` is within $1 of the EVEN split or of the DAY-PRORATED split of `total` over `periods`:
    the question app.js _mbPlanRedistribution asks ("custom" means it matches neither). Such a line holds no planner work, so a
    copy lets Step 06 derive the default for the new dates instead of freezing weights computed for the old ones."""
    if not periods:
        return False
    for target in (mo.even_allocation(total, periods), mo.default_allocation(total, periods)):
        if all(abs(_num(allocations.get(key)) - _num(target.get(key))) <= DEFAULT_SLACK for key in set(allocations) | set(target)):
            return True
    return False


def _carry_allocation(line: dict, old_effective: Optional[list[dict]], new_effective: list[dict], same_shape: bool, new_months: int) -> tuple[Optional[dict], str]:
    """What a line's `monthly_allocations` becomes in the copy -> (allocations or None, why):

      "none"     the line has no split (or is Added Value, which Step 06 does not split): nothing to carry
      "default"  the split is just a default: None, Step 06 derives it again for the new dates
      "carried"  a hand-made split on a flight of the same shape: the same dollars, period i to period i
      "reset"    a hand-made split that cannot be carried (different shape, keys the old flight does not have, or dollars that do
                 not add up to the line's total): None, and the planner is told

    A split is carried only when it will still BALANCE against the copy's line total (per-period budget x new months): a carried
    split that does not add up is exactly the state Step 06 flags as broken."""
    allocations = line.get("monthly_allocations")
    if not isinstance(allocations, dict) or not allocations or line.get("is_added_value"):
        return None, "none"
    numbers = {str(key): _num(value) for key, value in allocations.items()}
    if old_effective is None or set(numbers) != {p["key"] for p in old_effective}:
        return None, "reset"
    total = sum(numbers.values())
    if follows_default(numbers, total, old_effective):
        return None, "default"
    balanced = abs(total - _num(line.get("monthly_budget")) * new_months) <= _CENT * (len(old_effective) + 1)
    if same_shape and balanced:
        return {new_effective[i]["key"]: numbers[old_effective[i]["key"]] for i in range(len(old_effective))}, "carried"
    return None, "reset"


def option_totals(state: dict) -> dict[str, float]:
    """Each option's total for a stored reopen_state: the sum of per-period budget x months over its lines, by option letter (a legacy
    single-option state is option "A"), rounded to cents. What the notes and the response compare before and after."""
    tiers = _dicts(state.get("tiers")) if isinstance(state, dict) else []
    if not tiers:
        lines = _dicts(state.get("line_items")) if isinstance(state, dict) else []
        return {"A": round(sum(_num(li.get("monthly_budget")) * _months_of(li) for li in lines), 2)}
    return {str(t.get("label") or "A"): round(sum(_num(li.get("monthly_budget")) * _months_of(li) for li in _dicts(t.get("line_items"))), 2) for t in tiers}


# ======================================================================================================================
# The transform
# ======================================================================================================================

@dataclass
class _Tally:
    """What happened, counted while the copy is built, for the notes."""
    texts_cleared: int = 0
    split_reset: int = 0
    merges_kept: int = 0
    merges_dropped: int = 0
    tier_windows: list[tuple[str, Optional[date], Optional[date]]] = field(default_factory=list)     # options that had their own dates
    avails_cleared: int = 0
    avails_kept: int = 0              # estimates the planner asked to keep (the form's escape valve)
    avails_manual: int = 0
    custom_rates: int = 0
    products_missing: list[str] = field(default_factory=list)


def transform_reopen_state(source: dict, basics: DuplicateBasics, *, product_known: Callable[[str], bool] = lambda name: True) -> DuplicateResult:
    """The copy of a stored reopen_state for a new client (see the module docstring for the rules). `source` should already have its
    product names brought up to date (main._with_current_product_names); it is never changed. `product_known(name)` says whether a
    product is still in the catalog (one that is not stays on its line and is listed in a note). Raises ValueError when `basics`
    carries dates that are not a usable flight: the request model refuses those first, so that is a bug, not user input."""
    if not isinstance(source, dict):
        raise TypeError("the source state must be a dict")
    new_start, new_end = _date(basics.start_date), _date(basics.end_date)
    new_window = _window(new_start, new_end)
    if new_window is None:
        raise ValueError("the new flight is not a usable date range")
    src = copy.deepcopy(source)
    req = dict(src["request"]) if isinstance(src.get("request"), dict) else {}
    unit = mo.normalize_time_unit(src.get("time_unit"))
    mode = "prorated" if src.get("monthly_distribution_mode") == "prorated" else "even"
    new_base = _base_periods(new_window, unit)
    if new_base is None:
        raise ValueError("the new flight's periods cannot be built")
    full_flight = unit == mo.FULL_FLIGHT
    tally = _Tally()

    # Words that identify the OLD client / agency, minus the words the NEW opportunity legitimately shares with it (duplicating
    # "Acme Motors" for "Acme Boats", or keeping the same agency, must not clear notes that are still true).
    shared = set(scrub_tokens(basics.client_name, basics.agency_name))
    tokens = [t for t in scrub_tokens(req.get("client_name"), req.get("agency_name")) + website_token(req.get("client_website")) if t not in shared]

    def scrub(value: Any, empty: Any) -> Any:
        """`value`, or `empty` (and one more cleared text on the tally) when it names the old client or agency."""
        if mentions(value, tokens):
            tally.texts_cleared += 1
            return empty
        return value

    # ---- the request -------------------------------------------------------------------------------------------------------
    old_window = _window(_date(req.get("start_date")), _date(req.get("end_date")))
    request_type = basics.request_type if basics.request_type is not None else str(req.get("request_type") or "")
    request_type_blanked = "renewal" in request_type.lower()
    if request_type_blanked:
        request_type = ""               # a renewal type adds renewal CCs and a "Digital Renewal Plan" doc type: wrong for a new client
    new_req = dict(req)
    new_req.update({
        "notion_id": basics.notion_id,
        "client_name": basics.client_name,
        "client_website": basics.client_website,
        "agency_name": basics.agency_name,
        "agency_fee": basics.agency_fee,
        "start_date": basics.start_date,
        "end_date": basics.end_date,
        "total_months": len(mo.months_between(new_start, new_end)),        # what Step 02 derives, so no "saved N" flag is raised
        "requested_by": basics.requested_by if basics.requested_by is not None else req.get("requested_by", ""),
        "salesperson_email": basics.salesperson_email if basics.salesperson_email is not None else req.get("salesperson_email", ""),
        "salesperson_market": basics.salesperson_market if basics.salesperson_market is not None else req.get("salesperson_market", ""),
        "request_type": request_type,
        "ccs": "",                      # no Step 02 input: the planner could not see or change it (market CCs come from the market)
        "salesperson_comments": "",
        "question_details": "",
        "specifics": dataclasses.asdict(ProductSpecifics()),     # per-product answers typed for the OLD client (a Facebook page, an address)
        "warnings": [],                 # about the old paste
        **{key: "" for key in _RENEWAL_FIELDS},
    })
    for key in _REQUEST_TEXT_FIELDS:
        if key in new_req:
            new_req[key] = scrub(new_req[key], "")

    # ---- options, lines, avails ----------------------------------------------------------------------------------------------
    had_tiers = bool(_dicts(src.get("tiers")))
    if had_tiers:
        tiers = _dicts(src["tiers"])
    else:      # a legacy single-option state: the top-level lines are option A (the output keeps `tiers: null`)
        tiers = [{"label": "A", "name": None, "geo": None, "start_date": None, "end_date": None, "period_merge_groups": None,
                  "line_items": _dicts(src.get("line_items")), "avails_data": src.get("avails_data") if isinstance(src.get("avails_data"), dict) else {}}]
    totals_before = option_totals({"tiers": tiers})
    for tier in tiers:
        _transform_tier(tier, unit=unit, full_flight=full_flight, campaign_window=old_window, new_window=new_window, new_base=new_base,
                        basics=basics, tally=tally, scrub=scrub, tokens=tokens, product_known=product_known)
    totals_after = option_totals({"tiers": tiers})

    addons = _dicts(src.get("addons"))
    for addon in addons:
        if addon.get("notes_override"):
            addon["notes_override"] = scrub(addon["notes_override"], None)

    first = tiers[0]
    state = {
        "request": new_req,
        "line_items": first["line_items"],               # a mirror of option A, as main._build_reopen_state writes it
        "avails_data": first["avails_data"] if isinstance(first.get("avails_data"), dict) else {},
        "tiers": tiers if had_tiers else None,
        "strategy_brief": None,         # written about the old client; Step 03 makes a fresh one on first visit
        "roadblocks": None,             # a paid AI check keyed to the old vertical; Step 07 runs again on a pick
        "force_tabs": {},               # only the boxes toggled for the OLD fee and products; {} means "suggest from the final plan"
        "addons": addons,
        "raw_notion_text": None,        # the old client's whole request
        "enrichment": None,             # emails, blurbs, Gamma text: all written for the old client
        "time_unit": unit,
        "monthly_distribution_mode": mode,
        "campaign_name_override": basics.campaign_name.strip() or None,
        "wizard_step": 2,
    }
    notes = _notes(old_window=old_window, new_window=new_window, totals_before=totals_before, totals_after=totals_after, full_flight=full_flight,
                   tally=tally, keep_avails=basics.keep_avails, request_type_blanked=request_type_blanked)
    stats = {
        "totals_before": totals_before, "totals_after": totals_after, "periods": len(new_base), "time_unit": unit,
        "split_reset": tally.split_reset, "merges_kept": tally.merges_kept, "merges_dropped": tally.merges_dropped,
        "avails_cleared": tally.avails_cleared, "texts_cleared": tally.texts_cleared,
    }
    return DuplicateResult(state=state, notes=notes, stats=stats)


def _transform_tier(tier: dict, *, unit: str, full_flight: bool, campaign_window: Optional[tuple[date, date]], new_window: tuple[date, date],
                    new_base: list[dict], basics: DuplicateBasics, tally: _Tally, scrub: Callable[[Any, Any], Any], tokens: list[str],
                    product_known: Callable[[str], bool]) -> None:
    """One option, in place (the caller owns a deep copy): re-dates its lines, re-keys or resets their splits, carries or drops its
    merge groups, clears its own date window, scrubs its free text and settles its avails."""
    own_start, own_end = _date(tier.get("start_date")), _date(tier.get("end_date"))
    if tier.get("start_date") or tier.get("end_date"):
        # An option may run its own window. Keeping it is "wrong dates" and shifting it is a guess (from the start or from the end?
        # they differ when the lengths differ), so it is cleared and the option follows the flight the planner just typed.
        tally.tier_windows.append((str(tier.get("label") or "A"), own_start or (campaign_window[0] if campaign_window else None),
                                   own_end or (campaign_window[1] if campaign_window else None)))
    tier["start_date"] = tier["end_date"] = None
    tier["name"] = scrub(tier.get("name"), None)
    tier["geo"] = scrub(tier.get("geo"), None)
    lines = tier["line_items"] = _dicts(tier.get("line_items"))

    # The option's OLD periods: its own window when it had one, else the campaign's. The effective ones apply its merges.
    old_start = own_start or (campaign_window[0] if campaign_window else None)
    old_end = own_end or (campaign_window[1] if campaign_window else None)
    old_base = None if full_flight else _base_periods(_window(old_start, old_end), unit)
    old_merges = tier.get("period_merge_groups") if isinstance(tier.get("period_merge_groups"), list) else []
    same_shape = (not full_flight and old_base is not None and len(old_base) == len(new_base)
                  and shape_signature(old_base) == shape_signature(new_base))
    if full_flight:
        new_merges: list[list[str]] = []         # one period: nothing to combine (the page clears merges in Full Flight too)
    elif same_shape:
        new_merges = translate_merges(old_merges, old_base, new_base)
        tally.merges_kept += len(new_merges)
        tally.merges_dropped += len(old_merges) - len(new_merges)
    else:
        new_merges = []                           # they exist to rescue a too-small stub period, which is a property of the dates
        tally.merges_dropped += len(old_merges)
    tier["period_merge_groups"] = new_merges or None
    # The old periods are built from the groups apply_period_merges honours (the same ones that were translated above), so the old and the
    # new effective lists always pair up position by position, whatever junk a stored group list holds.
    honoured = [[old_base[i]["key"] for i in positions] for positions in _merge_positions(old_merges, old_base)] if old_base is not None else []
    old_effective = mo.apply_period_merges(old_base, honoured) if old_base is not None else None
    new_effective = mo.apply_period_merges(new_base, new_merges)

    for line in lines:
        if full_flight:
            periods = max(_months_of(line), 1.0)
            if periods > 1 and not line.get("is_added_value"):        # app.js _foldLineIntoOnePeriod: the whole-flight total never changes
                line["monthly_budget"] = round(_num(line.get("monthly_budget")) * periods, 2)
            line["months"] = 1
            line["monthly_allocations"] = None
        else:
            allocations, why = _carry_allocation(line, old_effective, new_effective, same_shape, len(new_base))
            line["months"] = len(new_base)        # what _resyncAllTierMonths derives; the per-period budget stays, so the total moves with the flight
            line["monthly_allocations"] = allocations
            if why == "reset":
                tally.split_reset += 1
        for key in _LINE_TEXT_FIELDS:
            if line.get(key):
                line[key] = scrub(line[key], None)
        if line.get("rate_override") is not None or line.get("estimated_cpm_override") is not None:
            tally.custom_rates += 1
        name = line.get("product_name")
        if isinstance(name, str) and name and not product_known(name) and name not in tally.products_missing:
            tally.products_missing.append(name)

    if isinstance(tier.get("avails_data"), dict):
        _settle_avails(tier["avails_data"], keep=basics.keep_avails, tally=tally, scrub=scrub, tokens=tokens)


def _settle_avails(avails: dict, *, keep: bool, tally: _Tally, scrub: Callable[[Any, Any], Any], tokens: list[str]) -> None:
    """Constanza's estimates describe the OLD client's geography, audience and budget and print in the export as estimates; the staleness
    check cannot rescue them (it compares product, months, geo text, target text, budget and CPM, never the flight dates or the client).
    So an entry with any estimate marker is removed; an entry with only numbers or text the planner typed is kept (nothing can regenerate
    it) and counted. `keep` (the form's escape valve) keeps everything, but still drops an estimate's snapshot that names the old client."""
    for key in list(avails):
        entry = avails[key]
        if not isinstance(entry, dict):
            continue
        if keep:
            if any(entry.get(marker) not in (None, {}, "") for marker in ESTIMATE_MARKERS):
                tally.avails_kept += 1
            for fld in _SNAPSHOT_FIELDS:
                if entry.get(fld) is not None and mentions(json.dumps(entry[fld], ensure_ascii=False, default=str), tokens):
                    entry[fld] = None
                    tally.texts_cleared += 1
        elif any(entry.get(marker) not in (None, {}, "") for marker in ESTIMATE_MARKERS):
            del avails[key]
            tally.avails_cleared += 1
            continue
        elif any(entry.get(k) not in (None, "") for k in _MANUAL_AVAILS_NUMBERS + _MANUAL_AVAILS_TEXTS):
            tally.avails_manual += 1
        for fld in _MANUAL_AVAILS_TEXTS:       # free-form avails text is typed by hand: it can name the old client too
            if entry.get(fld):
                entry[fld] = scrub(entry[fld], None)


def _notes(*, old_window: Optional[tuple[date, date]], new_window: tuple[date, date], totals_before: dict, totals_after: dict, full_flight: bool,
           tally: _Tally, keep_avails: bool, request_type_blanked: bool) -> list[dict]:
    """The notes the planner reads in Step 02, most important first. `check` = something to look at; `info` = what was done."""
    notes: list[dict] = []

    def note(code: str, text: str, level: str = "info") -> None:
        notes.append({"code": code, "level": level, "text": text})

    new_text = _fmt_window(*new_window)
    if old_window is not None and old_window == new_window:
        note("dates_same", f"Flight dates are unchanged: {new_text}.")
    elif old_window is not None:
        note("dates_moved", f"Flight moved from {_fmt_window(*old_window)} to {new_text}.")
    else:
        note("dates_set", f"The original had no readable flight dates; the flight is {new_text}.")
    if totals_before != totals_after and not full_flight:
        changes = "; ".join(f"Option {label}: ${totals_before.get(label, 0):,.0f} to ${total:,.0f}" for label, total in totals_after.items()
                            if total != totals_before.get(label))
        note("total_changed", "The number of billing periods changed, and every line keeps its per-period budget, so the totals changed: "
             f"{changes}. Review the budgets in Step 04.", "check")
    else:
        note("budgets_carried", "Budgets, products and prices were copied unchanged. Review them in Step 04.", "check")
    if tally.custom_rates:
        note("rates_carried", f"{tally.custom_rates} {_plural(tally.custom_rates, 'line carries', 'lines carry')} a custom rate from the original. "
             "Check that it still applies.", "check")
    if tally.tier_windows:
        n = len(tally.tier_windows)
        windows = "; ".join(f"Option {label} ran {_fmt_window(start, end)}" for label, start, end in tally.tier_windows)
        note("tier_dates_cleared", f"{n} {_plural(n, 'option', 'options')} had {_plural(n, 'its', 'their')} own dates and now "
             f"{_plural(n, 'follows', 'follow')} the new flight ({windows}). Set them again in Step 04 if needed.", "check")
    if tally.split_reset:
        n = tally.split_reset
        note("split_reset", f"{n} {_plural(n, 'line had', 'lines had')} a hand-made period split that couldn't be carried to the new flight; "
             f"{_plural(n, 'it starts', 'they start')} from the default split in Step 06.", "check")
    if tally.merges_dropped:
        n = tally.merges_dropped
        note("merges_dropped", f"{n} combined period {_plural(n, 'group was', 'groups were')} removed because the new flight's periods are different. "
             "Combine again in Step 06 if a period is too small.", "check")
    if tally.products_missing:
        note("products_missing", "These products are no longer in the catalog: " + ", ".join(tally.products_missing)
             + ". Replace or remove them in Step 04.", "check")
    if keep_avails:
        if tally.avails_kept:
            note("avails_kept", "Avails estimates were kept as you asked; they were calculated for the original client's geography and audience.", "check")
    elif tally.avails_cleared:
        n = tally.avails_cleared
        note("avails_cleared", f"Avails estimates from Constanza were cleared on {n} {_plural(n, 'line', 'lines')}: they were calculated for the "
             "original client. Estimate again in Step 05.", "check")
    if tally.avails_manual and not keep_avails:
        n = tally.avails_manual
        note("avails_manual_kept", f"{n} {_plural(n, 'line still carries', 'lines still carry')} avails numbers typed by hand. Check them in Step 05.", "check")
    if tally.texts_cleared:
        n = tally.texts_cleared
        note("texts_cleared", f"{n} {_plural(n, 'note or targeting field', 'notes or targeting fields')} mentioned the original client or agency "
             "and " + _plural(n, "was", "were") + " cleared.", "check")
    if request_type_blanked:
        note("request_type_blanked", "The original was a renewal request, which doesn't fit a new client; the request type is blank "
             "(Digital Media Proposal is the default).")
    note("ai_dropped", "The strategy brief, roadblocks and email drafts were not copied, because they were written for the original client. "
         "They are created fresh when you reach those steps.")
    return notes


# ======================================================================================================================
# The two other pure helpers the routes use
# ======================================================================================================================

def describe_source(state: dict) -> dict:
    """The facts about a stored reopen_state and the quick form's defaults, for GET .../duplicate-defaults (the route adds the row's
    id, title, client name and status):

      {"source":   {start_date, end_date (ISO or None), time_unit, periods (None when the dates cannot be read), option_count, line_count},
       "defaults": {requested_by, salesperson_email, salesperson_market, request_type, agency_name, agency_fee, client_website}}

    A request type containing "renewal" defaults to blank (a renewal type is wrong for a new client). `agency_fee` is a fraction or None."""
    state = state if isinstance(state, dict) else {}
    req = state.get("request") if isinstance(state.get("request"), dict) else {}
    unit = mo.normalize_time_unit(state.get("time_unit"))
    start, end = _date(req.get("start_date")), _date(req.get("end_date"))
    window = _window(start, end)
    base = _base_periods(window, unit)
    tiers = _dicts(state.get("tiers"))
    lines = sum(len(_dicts(t.get("line_items"))) for t in tiers) if tiers else len(_dicts(state.get("line_items")))

    def text(key: str) -> str:
        value = req.get(key)
        return value.strip() if isinstance(value, str) else ""

    fee = req.get("agency_fee")
    fee = float(fee) if isinstance(fee, (int, float)) and not isinstance(fee, bool) and 0 < fee < 1 else None
    request_type = text("request_type")
    return {
        "source": {
            "start_date": start.isoformat() if start else None, "end_date": end.isoformat() if end else None,
            "time_unit": unit, "periods": len(base) if base is not None else None,
            "option_count": len(tiers) or 1, "line_count": lines,
        },
        "defaults": {
            "requested_by": text("requested_by"), "salesperson_email": text("salesperson_email"), "salesperson_market": text("salesperson_market"),
            "request_type": "" if "renewal" in request_type.lower() else request_type,
            "agency_name": text("agency_name"), "agency_fee": fee, "client_website": text("client_website"),
        },
    }


def derive_proposal_id(email: str, source_id: str, request_id: str, basics: DuplicateBasics) -> str:
    """The new row's id: the same caller + source + request id + form body always gives the same id (a retried or double-clicked
    request finds its draft instead of making a second one), and a change to any of the four gives a different one (a corrected form
    is a new copy, never the first draft with stale values). 22 URL-safe characters like every other proposal id. The source id is a
    secret the caller must already hold, so the result is unguessable however weak the request id is."""
    body = json.dumps(dataclasses.asdict(basics), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(f"dup|{(email or '').strip().lower()}|{source_id}|{request_id}|{body}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest[:16]).rstrip(b"=").decode("ascii")
