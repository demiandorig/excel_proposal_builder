"""
The history list (GET /api/my-proposals and GET /api/admin/proposals): what it asks the database and how a row is shaped.

Pure on purpose (no database, no clock, no network, nothing from app.main), so every rule here is a table-driven test and the
two routes stay thin. app.main runs the statements this module builds and hands the rows back to `shape_row`.

THREE small statements per request, because of what the proposals table holds. `reopen_state` is the whole saved wizard
(requests, lines, avails with forecast snapshots, emails): about 73 kB per row on average, up to 200 kB, compressed in TOAST. The
list needs a handful of facts from it (flight dates, how many lines, which step a draft reached), and the cheap-looking way to get
them, extra `reopen_state->...` columns in the page query, was measured at 457 ms against 7 ms for a planner with 666 proposals:
`COUNT(*) OVER()` forces every select-list expression to run for EVERY matching row before LIMIT applies, and every `->` reads
the stored document again. So:

  1. COUNTS  light columns only. The All / Drafts / Generated figures for the filter buttons (scope and search, NOT the status).
  2. PAGE    light columns only, the whitelisted ORDER BY, LIMIT / OFFSET, `COUNT(*) OVER()` for the total.
  3. FACTS   `jsonb_to_record` over `reopen_state` for THIS page's ids only (`proposal_id = ANY(...)`), the document read once per
             row. A separate statement, not a LATERAL join inside the page query: written as a join the planner turned it into a
             hash join over the whole table at page_size 100 (544 ms, measured).

`reopen_state` therefore never appears in statements 1 and 2 (`plan_list` refuses a column list that names it, and a test
walks every parameter combination). Statements 1 and 3 are fail-soft in app.main: they only decorate the list.

Nothing the caller sends is ever placed in the SQL text. `sort`, `dir` and `status` are looked up in the dictionaries below (the
routes also type them as Literals, so an unknown value is a 422 before any of this runs), and search words only ever travel as
bound parameters with `\\`, `%` and `_` escaped, so a typed "100%" or "a_b" means those characters.

Everything read out of `reopen_state` is read as TEXT or as raw JSON and parsed here, never cast in SQL: one malformed row
would otherwise abort the whole statement (the analytics code's rule). The facts are shaped defensively: whatever a row holds
(NULL, `{}`, an array, a number, a string where an object belongs), the answer is "unknown" for that fact, never an error.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Optional

from app.services import monthly_allocation

# --- the query parameters --------------------------------------------------------------------------------------------------
# Literals so FastAPI answers 422 for anything else; the tuples are what the helpers look values up in (a test pins that the two
# agree).
StatusParam = Literal["all", "draft", "generated"]
SortParam = Literal["updated", "client", "value"]
DirParam = Literal["asc", "desc"]

LIST_STATUSES = ("all", "draft", "generated")
LIST_SORTS = ("updated", "client", "value")
LIST_DIRS = ("asc", "desc")

# Every call to the list is at most this many rows: a page size, not an export.
MAX_PAGE_SIZE = 100
# An absurd page number would make OFFSET overflow a bigint (a 500); nobody has this many pages.
MAX_PAGE = 100_000

# --- search ----------------------------------------------------------------------------------------------------------------
SEARCH_COLUMNS = ("client_name", "seller_email", "requested_by", "notion_id", "proposal_title")
SEARCH_MAX_WORDS = 6
SEARCH_MAX_WORD_LEN = 80
SEARCH_MAX_LEN = 200

# --- ordering --------------------------------------------------------------------------------------------------------------
# The Step 04 total of a generated proposal. A draft has no summary, and the guard keeps the one cast in these statements from
# ever meeting a string (a cast error would fail the whole page).
_NET = "CASE WHEN jsonb_typeof(summary->'total_net') = 'number' THEN (summary->>'total_net')::numeric END"
# Case-insensitive, and a blank or whitespace-only name sorts like "no name" (last in BOTH directions below).
_CLIENT = "LOWER(NULLIF(BTRIM(client_name), ''))"

# sort -> direction -> ORDER BY terms. A WHITELIST: nothing the caller sends is placed in the SQL, only looked up here.
SORTS: dict[str, dict[str, str]] = {
    "updated": {"desc": "updated_at DESC", "asc": "updated_at ASC"},
    "client": {
        "asc": f"{_CLIENT} ASC NULLS LAST, updated_at DESC",
        "desc": f"{_CLIENT} DESC NULLS LAST, updated_at DESC",
    },
    "value": {
        "desc": f"{_NET} DESC NULLS LAST, updated_at DESC",
        "asc": f"{_NET} ASC NULLS LAST, updated_at DESC",
    },
}
# The direction a sort starts in when the caller names none: newest first, A to Z, biggest first.
DEFAULT_DIR = {"updated": "desc", "client": "asc", "value": "desc"}
# Appended to every ORDER BY so the order is TOTAL: two rows that tie on value or on updated_at still have one fixed order, so
# walking page after page never repeats or skips a row.
_TIE_BREAK = "proposal_id DESC"

# The one column the counts and page statements must never name (see the module docstring).
_HEAVY_COLUMN = "reopen_state"

# --- the facts statement (3) -----------------------------------------------------------------------------------------------
# `jsonb_to_record` reads the document ONCE per row (each `->` would read the stored document again: 7 of them cost 1.8 s over
# 3,000 rows against 0.27 s for this). It raises on anything that is not an object, hence the CASE (a legacy row, `[]` or `5`
# gives an all-NULL record). `request` is read as JSON and only its two date strings are taken out, as strings: a number or an
# array in that place is "no date", not text for a table cell. `x.request IS NOT NULL OR x.line_items IS NOT NULL` is "this row
# saved a plan", i.e. what /reopen can restore (the rows it answers 410 for have neither).
FACTS_SQL = """
SELECT p.proposal_id,
       CASE WHEN jsonb_typeof(x.request->'start_date') = 'string' THEN x.request->>'start_date' END AS start_raw,
       CASE WHEN jsonb_typeof(x.request->'end_date') = 'string' THEN x.request->>'end_date' END AS end_raw,
       CASE WHEN jsonb_typeof(x.line_items) = 'array' THEN jsonb_array_length(x.line_items) END AS line_count,
       CASE WHEN jsonb_typeof(x.tiers) = 'array' THEN jsonb_array_length(x.tiers) END AS tier_count,
       x.time_unit AS time_unit_raw,
       x.wizard_step AS step_raw,
       x.duplicated_from AS dup_raw,
       (x.request IS NOT NULL OR x.line_items IS NOT NULL) AS has_state
FROM proposals p
CROSS JOIN LATERAL jsonb_to_record(CASE WHEN jsonb_typeof(p.reopen_state) = 'object' THEN p.reopen_state END)
     AS x(request jsonb, line_items jsonb, tiers jsonb, time_unit text, wizard_step text, duplicated_from jsonb)
WHERE p.proposal_id = ANY(%s)
"""

# One word of free text out of a flight date or a lineage field: controls, line separators, zero-width and bidi marks (and lone
# surrogates, which cannot be encoded) become a space, like app/services/line_modifier.py.
_UNWANTED = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2064\ufeff\ud800-\udfff]+")

FLIGHT_TEXT_MAX_LEN = 60
LINEAGE_FIELD_MAX_LEN = 200
LINEAGE_FIELDS = ("proposal_id", "client_name", "notion_id", "title")


@dataclass(frozen=True)
class ListPlan:
    """What one list request will ask: the normalised parameters and the two statements that do not depend on the page's rows."""

    page: int
    page_size: int
    offset: int
    status: str
    sort: str
    direction: str
    counts_sql: str
    counts_params: tuple
    page_sql: str
    page_params: tuple

    @property
    def applied(self) -> dict:
        """The echo the response carries: the values after normalisation, so a client can see what it actually got."""
        return {"status": self.status, "sort": self.sort, "dir": self.direction}


# ---------------------------------------------------------------------------------------------------------------------------
# parameters -> SQL pieces
# ---------------------------------------------------------------------------------------------------------------------------

def order_by(sort: Any, direction: Any = None) -> tuple[str, str, str]:
    """(ORDER BY terms, sort, direction) for the caller's choice, whitelisted. An unknown sort is the default one, an unknown or
    missing direction is that sort's natural one. `proposal_id DESC` is always appended, so the order is total."""
    key = sort if isinstance(sort, str) and sort in SORTS else "updated"
    way = direction if isinstance(direction, str) and direction in LIST_DIRS else DEFAULT_DIR[key]
    return f"{SORTS[key][way]}, {_TIE_BREAK}", key, way


def like_pattern(word: str) -> str:
    """`%word%` for ILIKE with the LIKE wildcards of the word itself escaped (Postgres' default escape is the backslash), so a
    typed `%` or `_` is that character and `100%` does not match every proposal."""
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search_words(search: Any) -> list[str]:
    """The words of a search box: at most SEARCH_MAX_LEN characters looked at, split on whitespace, at most SEARCH_MAX_WORDS
    words of at most SEARCH_MAX_WORD_LEN characters each. NUL characters are dropped (Postgres refuses them in text, which would
    be a 500 for a stray %00). The caps cut, they never refuse: an older page that sends a long search still gets an answer."""
    if not isinstance(search, str):
        return []
    text = search.replace("\x00", "")[:SEARCH_MAX_LEN]
    return [word[:SEARCH_MAX_WORD_LEN] for word in text.split()[:SEARCH_MAX_WORDS]]


def scope_where(mine_email: Optional[str], search: Any) -> tuple[list[str], list]:
    """The WHERE terms (and their parameters, in order) that scope the list to one planner and to the search words.

    `mine_email=None` is "everyone" (the admin's All proposals); any string, even an empty one, scopes to exactly that email,
    case-insensitively, so a caller that somehow had no email gets nothing rather than everyone's. created_by_email (the logged-in
    session that clicked Generate or saved the draft) is the owner, NOT seller_email (whatever the pasted request said), and it is
    NULL for proposals generated before the column existed, so those never match a planner.

    Every word must match one of SEARCH_COLUMNS (case-insensitive substring): "evc 48213" and "acme hvac" find what a person
    means however the words are spread over client, seller, Notion ID and title."""
    clauses: list[str] = []
    params: list = []
    if mine_email is not None:
        clauses.append("LOWER(created_by_email) = LOWER(%s)")
        params.append(mine_email)
    for word in search_words(search):
        clauses.append("(" + " OR ".join(f"{column} ILIKE %s" for column in SEARCH_COLUMNS) + ")")
        params.extend([like_pattern(word)] * len(SEARCH_COLUMNS))
    return clauses, params


def status_clause(status: Any) -> Optional[str]:
    """The WHERE term for the status button, or None for All. 'generated' is "not a draft": the published rows."""
    if status == "draft":
        return "status = 'draft'"
    if status == "generated":
        return "status <> 'draft'"
    return None


def plan_list(
    *,
    columns: str,
    mine_email: Optional[str],
    search: Any = "",
    page: int = 1,
    page_size: int = 10,
    status: Any = "all",
    sort: Any = "updated",
    direction: Any = None,
) -> ListPlan:
    """Normalise the request and build the counts and page statements. `columns` is the light column list of the page query;
    one that names `reopen_state` is refused (the 457 ms shape of the module docstring must stay impossible to write)."""
    if _HEAVY_COLUMN in columns:
        raise ValueError("the list's columns must not include reopen_state: it is read for the page's ids only, in the facts statement")
    page = min(max(int(page), 1), MAX_PAGE)
    page_size = min(max(int(page_size), 1), MAX_PAGE_SIZE)
    offset = (page - 1) * page_size
    status = status if isinstance(status, str) and status in LIST_STATUSES else "all"
    order_sql, sort, direction = order_by(sort, direction)

    scope, params = scope_where(mine_email, search)
    where = list(scope)
    status_term = status_clause(status)
    if status_term:
        where.append(status_term)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    scope_sql = f"WHERE {' AND '.join(scope)}" if scope else ""

    # The counts follow the scope and the search but NOT the status, so "Drafts 0" shows before the person presses it.
    counts_sql = (
        "SELECT COUNT(*) AS all_n, COUNT(*) FILTER (WHERE status = 'draft') AS draft_n, "
        f"COUNT(*) FILTER (WHERE status <> 'draft') AS generated_n FROM proposals {scope_sql}"
    )
    # COUNT(*) OVER() rides along with the page's rows (no second full COUNT for the total); that is also why nothing heavy may
    # be selected here: it would be evaluated for every matching row.
    page_sql = f"""
        SELECT {columns}, COUNT(*) OVER() AS total_count
        FROM proposals
        {where_sql}
        ORDER BY {order_sql}
        LIMIT %s OFFSET %s
        """
    return ListPlan(
        page=page, page_size=page_size, offset=offset, status=status, sort=sort, direction=direction,
        counts_sql=counts_sql, counts_params=tuple(params),
        page_sql=page_sql, page_params=(*params, page_size, offset),
    )


def facts_params(proposal_ids: Iterable[str]) -> tuple:
    """The facts statement's parameters: one array of the page's ids."""
    return (list(proposal_ids),)


# ---------------------------------------------------------------------------------------------------------------------------
# database rows -> response
# ---------------------------------------------------------------------------------------------------------------------------

def facets_from_rows(rows: Any) -> Optional[dict]:
    """{"all", "draft", "generated"} from the counts statement's one row, or None when it did not come back in that shape (the
    statement failed or answered something else): the page then hides the counts instead of showing wrong ones."""
    if not isinstance(rows, (list, tuple)) or not rows or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    try:
        return {"all": int(row["all_n"]), "draft": int(row["draft_n"]), "generated": int(row["generated_n"])}
    except (KeyError, TypeError, ValueError):
        return None


def facts_by_id(rows: Any) -> dict[str, dict]:
    """The facts statement's rows keyed by proposal_id."""
    return {row["proposal_id"]: row for row in rows or [] if isinstance(row, dict) and row.get("proposal_id") is not None}


def clean_text(value: Any, limit: int) -> str:
    """Text for one line of the page: "" for anything that is not a string, control and invisible characters turned into spaces,
    runs of whitespace collapsed, the ends trimmed, at most `limit` characters. Markup is NOT stripped (the page puts it in text
    nodes, never in HTML)."""
    if not isinstance(value, str):
        return ""
    return " ".join(_UNWANTED.sub(" ", value).split())[:limit]


def flight_date(raw: Any) -> Optional[str]:
    """ISO date of a flight date the way planners type them (2027-01-05, 1/5/2027, 1/5/27, 2027/01/05), else None. Never raises."""
    parsed = monthly_allocation.parse_flexible_date(raw if isinstance(raw, str) else None)
    return parsed.isoformat() if parsed else None


def small_int(raw: Any, lo: int, hi: int) -> Optional[int]:
    """A whole number from lo to hi out of whatever the document held (an int, a whole float, digits as text), else None. A
    boolean is not a number here: it goes through str() like everything else that is not a float, and int("True") is an error."""
    if isinstance(raw, float):
        number = int(raw) if raw.is_integer() else None
    else:
        try:
            number = int(str(raw).strip())
        except ValueError:
            number = None
    return number if number is not None and lo <= number <= hi else None


def lineage(raw: Any) -> Optional[dict]:
    """Where a copy came from, as the history chip needs it: {"proposal_id", "client_name", "notion_id", "title"}, each at most
    LINEAGE_FIELD_MAX_LEN characters of plain text, or None.

    `reopen_state.duplicated_from` is written by the duplicate feature as an object (`{proposal_id, title, client_name, at}`);
    a bare id string is accepted too, and anything else (NULL, a list, a number, `{}`) is "not a copy". Unknown keys are dropped
    (`at` and whatever a later version adds), and a value that is not a string is not shown."""
    if isinstance(raw, str):
        raw = {"proposal_id": raw}
    if not isinstance(raw, dict):
        return None
    out = {key: clean_text(raw.get(key), LINEAGE_FIELD_MAX_LEN) for key in LINEAGE_FIELDS}
    return out if any(out.values()) else None


def option_count(tier_count: Optional[int], line_count: Optional[int]) -> Optional[int]:
    """How many options (tiers) the saved plan has: the tiers array's length; a plan with lines but no tiers array is one option;
    None when nothing is built yet."""
    if isinstance(tier_count, int) and not isinstance(tier_count, bool) and tier_count > 0:
        return tier_count
    if isinstance(line_count, int) and not isinstance(line_count, bool) and line_count > 0:
        return 1
    return None


def shape_row(meta: dict, facts: Optional[dict], *, admin: bool = False) -> dict:
    """One proposal of the response.

    `meta` is the page row as app.main._proposal_metadata_from_row built it (timestamps already ISO text). The first block of
    keys is what the list has always returned, built the same way, unchanged. `facts` is that row's entry of the facts statement,
    or None when it is not known (the statement was not run or failed): every fact is then null and `can_reopen` stays true, because
    the server's 410 on a legacy row is the authority and a failed decoration must not make every proposal look unopenable.
    `created_by_email` (who generated it) is for the admin route only."""
    summary = meta.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    row = {
        "proposal_id": meta["proposal_id"],
        "client_name": meta.get("client_name", ""),
        "seller_email": meta.get("seller_email", ""),
        "requested_by": meta.get("requested_by", ""),
        "notion_id": meta.get("notion_id", ""),
        "proposal_title": meta.get("proposal_title", ""),
        "filename": meta.get("filename", ""),
        "generated_at": meta.get("generated_at", ""),
        "requester_ip": meta.get("requester_ip", ""),
        "requester_user_agent": meta.get("requester_user_agent", ""),
        "total_net": summary.get("total_net"),
        "total_gross": summary.get("total_gross"),
        "tabs_built": summary.get("tabs_built", []),
        "status": meta.get("status") or "generated",
        "updated_at": meta.get("updated_at", ""),
    }
    if admin:
        row["created_by_email"] = meta.get("created_by_email") or ""

    known = facts is not None
    facts = facts or {}
    start, end = flight_date(facts.get("start_raw")), flight_date(facts.get("end_raw"))
    typed = [clean_text(facts.get(key), FLIGHT_TEXT_MAX_LEN) for key in ("start_raw", "end_raw")]
    typed = [text for text in typed if text]
    has_state = bool(facts.get("has_state"))
    line_count = small_int(facts.get("line_count"), 0, 100_000)
    row.update({
        "flight_start": start,
        "flight_end": end,
        # What the person typed, only when the two dates did not both read as dates: the page shows it instead of "no dates".
        "flight_text": "" if (start and end) or not typed else " – ".join(typed)[:FLIGHT_TEXT_MAX_LEN],
        "line_count": line_count,
        "option_count": option_count(small_int(facts.get("tier_count"), 0, 1000), line_count),
        "time_unit": monthly_allocation.normalize_time_unit(facts.get("time_unit_raw")) if has_state else None,
        "wizard_step": small_int(facts.get("step_raw"), 1, 8),
        "duplicated_from": lineage(facts.get("dup_raw")),
        "can_reopen": has_state if known else True,
    })
    return row
