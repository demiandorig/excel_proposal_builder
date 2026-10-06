"""
Keyword-triggered banner disclaimers for the Excel and PowerPoint exports.

An admin maintains a list of rules (Admin -> Disclaimers); each has a set of
keywords, a banner text, a color, and optionally a seasonal window. When a
proposal is generated, every ACTIVE rule whose keywords appear in that
option's own plan text (and whose season, if set, overlaps the option's
flight) is rendered as a colored banner below the totals and above the legal
footer in the Excel sheet for that option, and on the closing slide of the
PPT. This replaces the single hard-coded "Live Sports" callout the Excel
footer used to carry (gated on a product NAME containing "sports"/
"tentpole") — that rule now lives here as a seeded, editable default.

Matching is deliberately simple and predictable: case-insensitive, whole
word/phrase ("live sports" matches "NBC Live Sports Stream" but "sport"
doesn't match "sports"), against structured plan fields only — never the raw
pasted Notion text, which also contains the form's own labels and would
cause false positives.

DB access is fail-safe by design: /api/generate must never 500 because this
table is missing or the DB hiccupped. On any load error the built-in
DEFAULT_DISCLAIMERS (the old Live Sports rule) apply instead, so exports
behave exactly as they did before this feature existed.
"""
from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass, asdict
from datetime import date
from typing import Any, Iterable, Optional

from app.db import get_connection

log = logging.getLogger(__name__)

# Banner color presets. `excel_*` are ARGB for openpyxl, `ppt_*` plain RGB hex
# for python-pptx. Same pale-fill/dark-text pairing the Excel footer's
# original Live Sports banner used (red: F8D7DA / 58151C).
COLOR_PRESETS: dict[str, dict[str, str]] = {
    "red":   {"label": "Red — warning",      "fill": "F8D7DA", "text": "58151C"},
    "amber": {"label": "Amber — heads-up",   "fill": "FFF3CD", "text": "664D03"},
    "green": {"label": "Green — tip",        "fill": "D4EDDA", "text": "155724"},
    "blue":  {"label": "Blue — information", "fill": "D1ECF1", "text": "0C5460"},
    "gray":  {"label": "Gray — neutral",     "fill": "E9ECEF", "text": "343A40"},
}
APPLIES_TO = ("both", "excel", "ppt")

MAX_BANNER_CHARS = 600
MAX_KEYWORDS = 50
MAX_KEYWORD_CHARS = 80

LIVE_SPORTS_TEXT = (
    "\U0001F4CA\U0001F449 Live Sports Inventory: The numbers provided in this proposal are "
    "geo-based estimates. Because publishers hold all cards and can adjust pricing or "
    "inventory without notice, we'll need a mandatory 3–5 business day pit stop for a "
    "full re-avail before any campaign goes live. Keep this buffer in your SLAs and "
    "potential secondary avenues for inventory delivery (i.e. audience-based buys)."
)
SEED_ID = "seed-live-sports"


@dataclass
class Disclaimer:
    id: str
    name: str
    keywords: list[str]
    banner_text: str
    color: str = "red"
    applies_to: str = "both"
    season_start: Optional[str] = None   # "MM-DD", recurs every year
    season_end: Optional[str] = None
    is_active: bool = True
    sort_order: int = 100

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# What applies when the table can't be read — and what gets seeded into it.
# Keywords cover the live-sports phrase plus the two sponsorship products the
# old name-based check actually caught (a bare "sports" is too broad: it would
# fire on a client called "Sports Authority" or a "sports" contextual target).
DEFAULT_DISCLAIMERS: list[Disclaimer] = [
    Disclaimer(
        id=SEED_ID, name="Live Sports Inventory",
        keywords=["live sports", "tentpole", "nbc sports", "fox sports"],
        banner_text=LIVE_SPORTS_TEXT, color="red", applies_to="both", sort_order=10,
    ),
]


# ---------------------------------------------------------------------------
# Validation / normalization (shared by the admin endpoints and the tests)
# ---------------------------------------------------------------------------

def normalize_keywords(raw: Iterable[str] | str | None) -> list[str]:
    """Comma/newline separated text (or a list) -> clean, lowercase,
    de-duplicated keyword list. Raises ValueError on anything unusable."""
    if raw is None:
        return []
    parts = re.split(r"[,\n;]", raw) if isinstance(raw, str) else list(raw)
    seen: list[str] = []
    for part in parts:
        kw = re.sub(r"\s+", " ", str(part or "")).strip().lower()
        if not kw or kw in seen:
            continue
        if len(kw) > MAX_KEYWORD_CHARS:
            raise ValueError(f"Keyword too long (max {MAX_KEYWORD_CHARS} characters): {kw[:30]}…")
        seen.append(kw)
    if len(seen) > MAX_KEYWORDS:
        raise ValueError(f"Too many keywords (max {MAX_KEYWORDS}).")
    return seen


def normalize_mmdd(raw: Optional[str]) -> Optional[str]:
    """"MM-DD" (or "M/D") -> canonical "MM-DD"; blank -> None. Validated
    against a leap year so "02-29" is allowed."""
    raw = (raw or "").strip()
    if not raw:
        return None
    m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})", raw)
    if not m:
        raise ValueError(f"Season dates must look like MM-DD (got {raw!r}).")
    month, day = int(m.group(1)), int(m.group(2))
    try:
        date(2000, month, day)
    except ValueError:
        raise ValueError(f"{raw!r} is not a real calendar date.")
    return f"{month:02d}-{day:02d}"


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def validate_fields(*, name: str, keywords: Any, banner_text: str, color: str, applies_to: str,
                    season_start: Optional[str], season_end: Optional[str]) -> dict[str, Any]:
    """Normalizes and validates one rule's editable fields. Returns the clean
    dict, raises ValueError with a planner-readable message."""
    # Pasted-in Word/PDF text often carries control characters, which openpyxl refuses to write.
    name = _CONTROL_CHARS.sub("", name or "").strip()
    if not name:
        raise ValueError("Give this disclaimer a name.")
    if len(name) > 120:
        raise ValueError("The name is too long (max 120 characters).")
    banner_text = _CONTROL_CHARS.sub("", banner_text or "").strip()
    if not banner_text:
        raise ValueError("Banner text can't be empty.")
    if len(banner_text) > MAX_BANNER_CHARS:
        raise ValueError(f"Banner text is too long (max {MAX_BANNER_CHARS} characters).")
    if color not in COLOR_PRESETS:
        raise ValueError(f"Unknown color {color!r}.")
    if applies_to not in APPLIES_TO:
        raise ValueError(f"'Applies to' must be one of {', '.join(APPLIES_TO)}.")
    kws = normalize_keywords(keywords)
    start, end = normalize_mmdd(season_start), normalize_mmdd(season_end)
    if bool(start) != bool(end):
        raise ValueError("Set both season dates, or neither.")
    if not kws and not start:
        raise ValueError("Add at least one keyword or a seasonal window — otherwise it would never trigger.")
    return {
        "name": name, "keywords": kws, "banner_text": banner_text, "color": color,
        "applies_to": applies_to, "season_start": start, "season_end": end,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_COLUMNS = "id, name, keywords, banner_text, color, applies_to, season_start, season_end, is_active, sort_order"


def _row_to_disclaimer(row: dict) -> Disclaimer:
    return Disclaimer(
        id=row["id"], name=row["name"], keywords=list(row["keywords"] or []),
        banner_text=row["banner_text"], color=row["color"], applies_to=row["applies_to"],
        season_start=row["season_start"], season_end=row["season_end"],
        is_active=bool(row["is_active"]), sort_order=int(row["sort_order"]),
    )


def _ensure_seed(conn) -> None:
    """Makes sure the default Live Sports rule exists. schema.sql seeds it
    too, but a deploy step that creates tables without running INSERTs would
    otherwise leave the table empty — silently dropping a banner exports
    have always carried. The seeded row can't be deleted (only deactivated,
    see delete()), so re-inserting it here can never undo an admin's choice."""
    d = DEFAULT_DISCLAIMERS[0]
    conn.execute(
        """INSERT INTO disclaimers (id, name, keywords, banner_text, color, applies_to, sort_order)
           VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING""",
        (d.id, d.name, d.keywords, d.banner_text, d.color, d.applies_to, d.sort_order),
    )


def list_all() -> list[Disclaimer]:
    """Every rule (active or not) for the admin page. Raises on DB errors —
    the admin UI should show those, unlike the generate path."""
    with get_connection() as conn:
        _ensure_seed(conn)
        rows = conn.execute(f"SELECT {_COLUMNS} FROM disclaimers ORDER BY sort_order, name").fetchall()
    return [_row_to_disclaimer(r) for r in rows]


def load_active() -> list[Disclaimer]:
    """Active rules for export. Never raises: a missing table or DB error
    falls back to DEFAULT_DISCLAIMERS (the pre-existing Live Sports rule)."""
    try:
        return [d for d in list_all() if d.is_active]
    except Exception as exc:  # noqa: BLE001 — deliberately broad, see module docstring
        log.warning("disclaimers table unavailable, using built-in defaults: %s", exc)
        return list(DEFAULT_DISCLAIMERS)


def create(fields: dict[str, Any]) -> Disclaimer:
    new_id = secrets.token_urlsafe(12)
    with get_connection() as conn:
        row = conn.execute(
            f"""INSERT INTO disclaimers (id, name, keywords, banner_text, color, applies_to,
                                         season_start, season_end, is_active, sort_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE,
                        COALESCE((SELECT MAX(sort_order) + 10 FROM disclaimers), 100))
                RETURNING {_COLUMNS}""",
            (new_id, fields["name"], fields["keywords"], fields["banner_text"], fields["color"],
             fields["applies_to"], fields["season_start"], fields["season_end"]),
        ).fetchone()
    return _row_to_disclaimer(row)


def update(disclaimer_id: str, fields: dict[str, Any]) -> Optional[Disclaimer]:
    """Full replace of the editable fields. None if the id doesn't exist."""
    with get_connection() as conn:
        row = conn.execute(
            f"""UPDATE disclaimers SET name=%s, keywords=%s, banner_text=%s, color=%s, applies_to=%s,
                       season_start=%s, season_end=%s, updated_at=now()
                WHERE id=%s RETURNING {_COLUMNS}""",
            (fields["name"], fields["keywords"], fields["banner_text"], fields["color"],
             fields["applies_to"], fields["season_start"], fields["season_end"], disclaimer_id),
        ).fetchone()
    return _row_to_disclaimer(row) if row else None


def set_active(disclaimer_id: str, is_active: bool) -> Optional[Disclaimer]:
    with get_connection() as conn:
        row = conn.execute(
            f"UPDATE disclaimers SET is_active=%s, updated_at=now() WHERE id=%s RETURNING {_COLUMNS}",
            (is_active, disclaimer_id),
        ).fetchone()
    return _row_to_disclaimer(row) if row else None


def delete(disclaimer_id: str) -> bool:
    if disclaimer_id == SEED_ID:
        raise ValueError("The built-in Live Sports rule can't be deleted — switch it off instead.")
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM disclaimers WHERE id=%s", (disclaimer_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _keyword_regex(keyword: str) -> re.Pattern:
    # Whole word/phrase: not preceded or followed by another letter/digit.
    return re.compile(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])", re.IGNORECASE)


def text_matches(keywords: list[str], text: str) -> list[str]:
    """Which of `keywords` appear (whole-word, case-insensitive) in `text`."""
    return [kw for kw in keywords if _keyword_regex(kw).search(text or "")]


def season_overlaps(season_start: str, season_end: str, flight_start: Optional[date],
                    flight_end: Optional[date]) -> bool:
    """Whether the flight overlaps the recurring MM-DD..MM-DD window in ANY
    year it touches. A window with start > end wraps the new year (e.g.
    11-15..01-05). Unknown flight dates -> False (never guess)."""
    if not flight_start or not flight_end:
        return False
    if flight_end < flight_start:
        flight_start, flight_end = flight_end, flight_start
    sm, sd = (int(x) for x in season_start.split("-"))
    em, ed = (int(x) for x in season_end.split("-"))

    def safe_date(year: int, month: int, day: int) -> date:
        try:
            return date(year, month, day)
        except ValueError:  # Feb 29 in a non-leap year
            return date(year, month, 28)

    wraps = (sm, sd) > (em, ed)
    for year in range(flight_start.year - 1, flight_end.year + 1):
        window_start = safe_date(year, sm, sd)
        window_end = safe_date(year + 1 if wraps else year, em, ed)
        if window_start <= flight_end and window_end >= flight_start:
            return True
    return False


_REQUEST_TEXT_FIELDS = (
    "client_name", "agency_name", "client_website", "campaign_goal", "language", "other_languages",
    "geo", "demo", "behavioral", "contextual", "salesperson_comments",
    "question_details", "renewal_changes_description",
)
_LINE_TEXT_FIELDS = ("product_name", "target_override", "target_secondary", "notes_override", "objective_override")


def request_text(req: Any, include_products: bool = False) -> str:
    """The request-level text a rule can match. The product list the planner pasted is OFF by default:
    it never follows Step 04 (a product dropped there is still in it, and it is shared by every
    option), so matching it would put a "Live Sports" banner on options that no longer hold the
    product. Each option's own line items are matched through tier_text() instead. Only the
    restricted-verticals SUGGESTION (a hint the planner confirms) opts in."""
    parts = [str(getattr(req, f, "") or "") for f in _REQUEST_TEXT_FIELDS]
    if include_products:
        parts.append(str(getattr(req, "products_selected_raw", "") or ""))
        parts.extend(str(p) for p in (getattr(req, "products_selected", None) or []))
    return "\n".join(p for p in parts if p)


def tier_text(tier: dict) -> str:
    """Per-option plan text: its own name/geo, and every line item's product
    name and planner-typed targeting/notes. Works for both the dataclass
    LineItem and the Pydantic LineItemModel (plain attribute access)."""
    parts = [str(tier.get("name") or ""), str(tier.get("geo") or "")]
    for li in tier.get("line_items") or []:
        parts.extend(str(getattr(li, f, "") or "") for f in _LINE_TEXT_FIELDS)
    return "\n".join(p for p in parts if p)


def _parse_date(raw: Optional[str]) -> Optional[date]:
    from app.services import monthly_allocation
    return monthly_allocation.parse_flexible_date(raw or "")


def matches(rule: Disclaimer, text: str, flight_start: Optional[date], flight_end: Optional[date]) -> bool:
    """A rule fires when ANY keyword is present AND (if it has a season) the
    flight overlaps it. A season-only rule (no keywords) fires on dates
    alone; a keyword-only rule ignores dates entirely."""
    if rule.keywords and not text_matches(rule.keywords, text):
        return False
    if rule.season_start and rule.season_end:
        return season_overlaps(rule.season_start, rule.season_end, flight_start, flight_end)
    return bool(rule.keywords)


def resolve_for_tiers(req: Any, tiers: list[dict], rules: Optional[list[Disclaimer]] = None) -> dict[str, list[dict]]:
    """{tier_label: [banner dict, ...]} for every option. `tiers` items need
    "label", optionally "name"/"geo"/"start_date"/"end_date", and
    "line_items". Banner dicts: {id, name, text, color, applies_to}, ordered
    by the rules' sort_order. `rules=None` loads the active rules from the DB
    (fail-safe)."""
    rules = sorted(rules if rules is not None else load_active(), key=lambda r: (r.sort_order, r.name))
    base_text = request_text(req)
    out: dict[str, list[dict]] = {}
    for tier in tiers:
        label = tier.get("label") or "A"
        text = base_text + "\n" + tier_text(tier)
        start = _parse_date(tier.get("start_date") or getattr(req, "start_date", None))
        end = _parse_date(tier.get("end_date") or getattr(req, "end_date", None))
        out[label] = [
            {
                "id": r.id, "name": r.name, "text": r.banner_text, "color": r.color,
                "applies_to": r.applies_to,
                # Resolved here so excel_template.py / pptx_builder.py can just
                # render a banner dict without importing this (DB-backed) module.
                "fill": preset(r.color)["fill"], "font_color": preset(r.color)["text"],
            }
            for r in rules if matches(r, text, start, end)
        ]
    return out


def for_target(banners: list[dict], target: str) -> list[dict]:
    """Filter resolved banners to those that apply to "excel" or "ppt"."""
    return [b for b in banners if b.get("applies_to", "both") in ("both", target)]


def union_for_ppt(by_tier: dict[str, list[dict]]) -> list[dict]:
    """The PPT's closing slide is shared across options, so it carries the
    de-duplicated union of every option's PPT-applicable banners."""
    seen: set[str] = set()
    merged: list[dict] = []
    for banners in by_tier.values():
        for b in for_target(banners, "ppt"):
            if b["id"] not in seen:
                seen.add(b["id"])
                merged.append(b)
    return merged


def preset(color: str) -> dict[str, str]:
    return COLOR_PRESETS.get(color) or COLOR_PRESETS["red"]
