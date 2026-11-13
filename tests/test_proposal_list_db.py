"""The proposal-history list against a real PostgreSQL (skipped without a reachable DATABASE_URL that has the proposals table).

tests/test_proposal_list_query.py pins what the three statements SAY with a fake; here they RUN: the whitelisted ORDER BY, the escaped ILIKE
words, `jsonb_to_record` over every odd shape a `reopen_state` can hold, and that walking the pages of any sort returns every row exactly once
in the order one big page has (two rows that tie on value or on updated_at included). Nothing here asserts a time: the structural test in
test_proposal_list_query.py keeps the slow query shape from coming back.

The proposals table is shared (on Replit it is the real one), so every row these tests insert belongs to ONE planner who exists only here
(`list-<token>@example.test`), carries the same `<token>` in requested_by, and is deleted afterwards; the admin "All proposals" calls search for
that token so they only ever see these rows.
"""
from __future__ import annotations

import math
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import psycopg
import pytest

from app import main
from app.db import execute, fetch_all
from app.services import proposal_list as pl
from tests.proposal_list_support import NEW_ROW_KEYS, OLD_ROW_KEYS, call, fake_request

pytestmark = pytest.mark.real_db

BASE = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
SQL_NULL = object()                       # reopen_state IS NULL: a row saved before reopening existed
JSON_NULL = object()                      # reopen_state = 'null'::jsonb: the JSON null, which is not the same thing
SORT_DIRS = [(sort, way) for sort in pl.LIST_SORTS for way in pl.LIST_DIRS]


@pytest.fixture(scope="module")
def database():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("no DATABASE_URL")
    try:
        with psycopg.connect(url, connect_timeout=2) as conn:
            columns = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'proposals'").fetchall()}
    except Exception:
        pytest.skip("no reachable database")
    if not {"proposal_id", "created_by_email", "status", "updated_at", "reopen_state"} <= columns:
        pytest.skip("the proposals table (with created_by_email / status / updated_at) is not in this database")
    return url


@dataclass
class Planner:
    token: str
    email: str
    prefix: str

    def id(self, key: str) -> str:
        return f"{self.prefix}{key}"


@pytest.fixture
def planner(database):
    token = uuid.uuid4().hex[:12]
    who = Planner(token=token, email=f"list-{token}@example.test", prefix=f"__pytest_list_{token}_")
    yield who
    execute("DELETE FROM proposals WHERE left(proposal_id, %s) = %s", (len(who.prefix), who.prefix))


def put(who: Planner, key: str, *, client: Any = "Acme", status: str = "generated", summary: Optional[dict] = None, state: Any = None,
        updated: Optional[datetime] = None, owner: Any = "me", notion: Optional[str] = None, title: Optional[str] = None,
        requested_by: Optional[str] = None) -> str:
    """Insert one proposal through the application's own writer. `owner="me"` is the planner, None is a legacy row with no created_by_email."""
    proposal_id = who.id(key)
    main._save_proposal_metadata(
        proposal_id=proposal_id, client_name=client, seller_email="ae@entravision.com",
        created_by_email=who.email if owner == "me" else owner, requested_by=who.token if requested_by is None else requested_by,
        notion_id=notion, proposal_title=title if title is not None else f"{notion or 'EVC-0'} {key} Proposal",
        reopen_state={} if (state is SQL_NULL or state is JSON_NULL) else (state if state is not None else {"request": {}, "line_items": []}),
        summary=summary, status=status,
        filename=None if status == "draft" else f"{key}.xlsx", generated_at=None if status == "draft" else BASE,
    )
    if state is SQL_NULL:
        execute("UPDATE proposals SET reopen_state = NULL WHERE proposal_id = %s", (proposal_id,))
    elif state is JSON_NULL:
        execute("UPDATE proposals SET reopen_state = 'null'::jsonb WHERE proposal_id = %s", (proposal_id,))
    if updated is not None:                                             # the writer stamps now(); the tests need a known order
        execute("UPDATE proposals SET updated_at = %s WHERE proposal_id = %s", (updated, proposal_id))
    return proposal_id


def mine(who: Planner, **params) -> dict:
    return call(main.my_proposals, fake_request(params.pop("email", who.email), is_admin=False), page_size=params.pop("page_size", 100), **params)


def everyone(who: Planner, **params) -> dict:
    """The admin's All proposals, narrowed to this test's rows by the token every one of them carries."""
    return call(main.admin_list_proposals, fake_request("admin@example.test"), mine=False, page_size=params.pop("page_size", 100),
                search=params.pop("search", who.token), **params)


def ids(result: dict, who: Planner) -> list[str]:
    return [p["proposal_id"].removeprefix(who.prefix) for p in result["proposals"]]


# ================================================================================================================= owner isolation

def test_a_planner_sees_only_their_own_proposals_whatever_the_parameters(planner):
    put(planner, "a1", client="Alpha")
    put(planner, "a2", client="Bravo", status="draft", summary={})
    put(planner, "a3", client="Charlie")
    put(planner, "b1", client="Alpha", owner=f"other-{planner.token}@example.test")
    put(planner, "b2", client="Zulu", owner=f"OTHER-{planner.token}@example.test", status="draft", summary={})
    put(planner, "legacy", client="Old", owner=None)                      # generated before created_by_email existed: nobody's but an admin's

    for search in ("", planner.token, "alpha", "alpha bravo", "%", "_"):
        for status in pl.LIST_STATUSES:
            for sort, way in SORT_DIRS:
                got = ids(mine(planner, search=search, status=status, sort=sort, dir=way), planner)
                assert set(got) <= {"a1", "a2", "a3"}, (search, status, sort, way, got)
    assert sorted(ids(mine(planner), planner)) == ["a1", "a2", "a3"]
    assert mine(planner)["facets"] == {"all": 3, "draft": 1, "generated": 2}


def test_the_owner_match_ignores_case_and_the_admin_all_scope_sees_every_owner(planner):
    put(planner, "a1", client="Alpha")
    put(planner, "b1", client="Beta", owner=f"other-{planner.token}@example.test")
    put(planner, "legacy", client="Old", owner=None)

    assert ids(mine(planner, email=planner.email.upper()), planner) == ["a1"]
    everything = everyone(planner)
    assert sorted(ids(everything, planner)) == ["a1", "b1", "legacy"] and everything["facets"] == {"all": 3, "draft": 0, "generated": 3}
    by_id = {p["proposal_id"].removeprefix(planner.prefix): p for p in everything["proposals"]}
    assert by_id["a1"]["created_by_email"] == planner.email and by_id["legacy"]["created_by_email"] == ""
    admin_mine = call(main.admin_list_proposals, fake_request(planner.email), mine=True, page_size=100)
    assert ids(admin_mine, planner) == ["a1"] and "created_by_email" in admin_mine["proposals"][0]
    assert all("created_by_email" not in p for p in mine(planner)["proposals"])


# ================================================================================================================= status and counts

def test_the_counts_follow_the_search_but_not_the_status_and_the_page_follows_both(planner):
    for key, client, status in (("a1", "Alpha One", "generated"), ("a2", "Alpha Two", "draft"), ("a3", "Alpha Three", "draft"),
                                ("b1", "Beta", "generated"), ("b2", "Beta Two", "draft")):
        put(planner, key, client=client, status=status, summary={} if status == "draft" else {"total_net": 1})

    assert mine(planner)["facets"] == {"all": 5, "draft": 3, "generated": 2}
    alpha = mine(planner, search="alpha")
    assert alpha["facets"] == {"all": 3, "draft": 2, "generated": 1} and alpha["total_count"] == 3
    for status, expected_ids, expected_total in (("all", {"a1", "a2", "a3"}, 3), ("draft", {"a2", "a3"}, 2), ("generated", {"a1"}, 1)):
        got = mine(planner, search="alpha", status=status)
        assert set(ids(got, planner)) == expected_ids and got["total_count"] == expected_total
        assert got["facets"] == {"all": 3, "draft": 2, "generated": 1}         # the buttons keep showing what the OTHER buttons would give
        assert got["applied"]["status"] == status
    assert mine(planner, status="draft")["total_count"] == 3 and mine(planner, status="generated")["total_count"] == 2
    nothing = mine(planner, search="zzz-no-such-client")
    assert nothing["proposals"] == [] and nothing["facets"] == {"all": 0, "draft": 0, "generated": 0}


# ================================================================================================================= every sort, every page

# (key, client, status, total_net, minutes before BASE). Ties on purpose: value 500 three times, "echo"/"Echo" (and the same minute), blank,
# whitespace-only and NULL clients, drafts and a bad total.
SORT_ROWS = [
    ("r01", "alpha", "generated", 500, 10), ("r02", "Bravo", "generated", 500, 20), ("r03", "charlie", "generated", 9000, 30),
    ("r04", "Delta", "draft", None, 40), ("r05", "echo", "generated", 0, 50), ("r06", "Echo", "draft", None, 50),
    ("r07", "", "generated", 700, 60), ("r08", "   ", "draft", None, 70), ("r09", None, "generated", "abc", 80),
    ("r10", "foxtrot", "generated", 1000000.5, 90), ("r11", "golf", "generated", -50, 100), ("r12", "hotel", "generated", 500, 110),
    ("r13", "india", "draft", None, 120), ("r14", "juliet", "generated", None, 130),
]


@dataclass(frozen=True)
class SortRow:
    key: str
    client: Optional[str]
    status: str
    net: Any
    updated: datetime

    @property
    def number(self) -> Optional[float]:
        return float(self.net) if isinstance(self.net, (int, float)) and not isinstance(self.net, bool) else None

    @property
    def name(self) -> Optional[str]:
        return (self.client or "").strip().lower() or None


def _sort_rows() -> list[SortRow]:
    return [SortRow(k, c, s, n, BASE - timedelta(minutes=m)) for k, c, s, n, m in SORT_ROWS]


def expected_order(rows: list[SortRow], sort: str, way: str) -> list[str]:
    """The order the page must have, worked out in Python from the rules: the tie-break is proposal_id DESC, then the sort's own
    key; a missing key (blank client, no total) is LAST in both directions."""
    base = sorted(rows, key=lambda r: r.key, reverse=True)                  # proposal_id DESC
    if sort == "updated":
        return [r.key for r in sorted(base, key=lambda r: r.updated, reverse=(way == "desc"))]
    by_updated = sorted(base, key=lambda r: r.updated, reverse=True)       # then updated_at DESC (the sort's own secondary key)
    pick = (lambda r: r.name) if sort == "client" else (lambda r: r.number)
    present = sorted((r for r in by_updated if pick(r) is not None), key=pick, reverse=(way == "desc"))
    return [r.key for r in present + [r for r in by_updated if pick(r) is None]]


@pytest.fixture
def sorted_table(planner):
    rows = _sort_rows()
    for row in rows:
        put(planner, row.key, client=row.client, status=row.status, updated=row.updated,
            summary={} if row.status == "draft" else ({"total_net": row.net} if row.net is not None else {}))
    return rows


def _walk(planner: Planner, size: int, **params) -> tuple[list[str], list[dict]]:
    walked, pages, page = [], [], 1
    while True:
        result = mine(planner, page=page, page_size=size, **params)
        pages.append(result)
        got = ids(result, planner)
        if not got:
            return walked, pages
        walked += got
        page += 1
        assert page < 100, "the walk never ended"


@pytest.mark.parametrize("sort, way", SORT_DIRS)
def test_every_sort_gives_the_documented_order_and_walking_the_pages_gives_every_row_once_in_that_order(planner, sorted_table, sort, way):
    for status in pl.LIST_STATUSES:
        rows = [r for r in sorted_table if status == "all" or (r.status == "draft") == (status == "draft")]
        expected = expected_order(rows, sort, way)
        full = mine(planner, sort=sort, dir=way, status=status)
        assert ids(full, planner) == expected, (sort, way, status)
        assert full["applied"] == {"status": status, "sort": sort, "dir": way} and full["total_count"] == len(rows)
        for size in (1, 3, 4, 5, 100):
            walked, pages = _walk(planner, size, sort=sort, dir=way, status=status)
            assert walked == expected, (sort, way, status, size)            # no row repeated, none skipped, the same order
            assert len(set(walked)) == len(walked)
            assert {p["total_count"] for p in pages[:-1]} <= {len(rows)} and pages[0]["total_pages"] == max(1, math.ceil(len(rows) / size))
            assert pages[-1]["total_count"] == 0 and pages[-1]["facets"]["all"] == len(sorted_table)       # past the end: empty, the counts still there


def test_the_default_direction_of_each_sort_is_newest_first_a_to_z_and_biggest_first(planner, sorted_table):
    for sort, way in (("updated", "desc"), ("client", "asc"), ("value", "desc")):
        omitted = mine(planner, sort=sort)
        assert ids(omitted, planner) == expected_order(sorted_table, sort, way) and omitted["applied"]["dir"] == way
    assert ids(mine(planner), planner) == expected_order(sorted_table, "updated", "desc")


def test_blank_clients_and_drafts_without_a_total_sort_last_in_both_directions(planner, sorted_table):
    for way in pl.LIST_DIRS:
        by_client = ids(mine(planner, sort="client", dir=way), planner)
        assert set(by_client[-3:]) == {"r07", "r08", "r09"}                 # "", "   " and NULL
        by_value = ids(mine(planner, sort="value", dir=way), planner)
        no_total = {"r04", "r06", "r08", "r09", "r13", "r14"}               # drafts, a string total, no total at all
        assert set(by_value[-len(no_total):]) == no_total


def test_a_case_difference_does_not_split_a_client_sort_and_a_full_tie_is_broken_by_proposal_id(planner, sorted_table):
    by_client = ids(mine(planner, sort="client", dir="asc"), planner)
    assert by_client[:6] == ["r01", "r02", "r03", "r04", "r06", "r05"]      # alpha, Bravo, charlie, Delta: case-insensitive; echo / Echo tie on name AND minute: id DESC
    by_client_desc = ids(mine(planner, sort="client", dir="desc"), planner)
    assert by_client_desc.index("r06") + 1 == by_client_desc.index("r05")  # the tie-break does not flip with the direction


# ================================================================================================================= odd saved documents

FULL_STATE = {
    "request": {"client_name": "Acme", "start_date": "1/5/2027", "end_date": "2027-03-31"},
    "line_items": [{"id": "a"}, {"id": "b"}, {"id": "c"}], "tiers": [{"label": "A"}, {"label": "B"}], "time_unit": "quarter", "wizard_step": 4,
    "avails_data": {}, "wizard_unknown_key": {"anything": True},
}


def _facts(row: dict) -> tuple:
    return tuple(row[k] for k in ("can_reopen", "flight_start", "flight_end", "flight_text", "line_count", "option_count", "time_unit", "wizard_step"))


ODD_STATES = [
    # label, reopen_state, expected (can_reopen, start, end, flight_text, line_count, option_count, time_unit, wizard_step)
    ("sql-null", SQL_NULL, (False, None, None, "", None, None, None, None)),
    ("json-null", JSON_NULL, (False, None, None, "", None, None, None, None)),
    ("empty-object", {}, (False, None, None, "", None, None, None, None)),
    ("array", [], (False, None, None, "", None, None, None, None)),
    ("non-empty-array", [1, 2, 3], (False, None, None, "", None, None, None, None)),
    ("number", 5, (False, None, None, "", None, None, None, None)),
    ("string", "text", (False, None, None, "", None, None, None, None)),
    ("true", True, (False, None, None, "", None, None, None, None)),
    ("full", FULL_STATE, (True, "2027-01-05", "2027-03-31", "", 3, 2, "quarter", 4)),
    ("request-is-a-string", {"request": "oops", "line_items": {"a": 1}, "time_unit": {"x": 1}, "wizard_step": "abc"},
     (True, None, None, "", None, None, "month", None)),
    ("dates-are-not-strings", {"request": {"start_date": 5, "end_date": [1]}, "line_items": []}, (True, None, None, "", 0, None, "month", None)),
    ("dates-are-free-text", {"request": {"start_date": "TBD soon", "end_date": ""}, "line_items": [{}]}, (True, None, None, "TBD soon", 1, 1, "month", None)),
    ("lines-without-tiers", {"request": {}, "line_items": [{}, {}], "tiers": None, "wizard_step": 2}, (True, None, None, "", 2, 1, "month", 2)),
    ("empty-tiers-array", {"request": {}, "line_items": [{}], "tiers": []}, (True, None, None, "", 1, 1, "month", None)),
    ("tiers-is-an-object", {"request": {}, "line_items": [{}], "tiers": {"a": 1}}, (True, None, None, "", 1, 1, "month", None)),
    ("step-out-of-range", {"request": {}, "wizard_step": 99}, (True, None, None, "", None, None, "month", None)),
    ("step-as-float", {"request": {}, "wizard_step": 4.0}, (True, None, None, "", None, None, "month", None)),    # the app only ever writes 1..8 as an int
    ("step-as-array", {"request": {}, "wizard_step": [1]}, (True, None, None, "", None, None, "month", None)),
    ("unknown-unit", {"request": {}, "time_unit": "fortnight"}, (True, None, None, "", None, None, "month", None)),
    ("only-line-items", {"line_items": [{}, {}, {}]}, (True, None, None, "", 3, 1, "month", None)),
    ("explicit-nulls", {"request": None, "line_items": None, "tiers": None, "time_unit": None, "wizard_step": None}, (False, None, None, "", None, None, None, None)),
    ("one-bad-date", {"request": {"start_date": "2027-02-01", "end_date": "mañana"}}, (True, "2027-02-01", None, "2027-02-01 – mañana", None, None, "month", None)),
]


def test_every_odd_shape_of_reopen_state_gives_unknowns_never_an_error(planner):
    expected = {}
    for index, (label, state, outcome) in enumerate(ODD_STATES):
        expected[put(planner, f"odd{index:02d}", state=state)] = (label, outcome)
    result = mine(planner, page_size=100)
    assert result["total_count"] == len(ODD_STATES)
    for row in result["proposals"]:
        label, outcome = expected[row["proposal_id"]]
        assert _facts(row) == outcome, label
        assert set(row) == set(OLD_ROW_KEYS) | set(NEW_ROW_KEYS), label


def test_odd_documents_also_survive_the_admin_all_scope_every_sort_and_a_search(planner):
    for index, (_, state, _outcome) in enumerate(ODD_STATES):
        put(planner, f"odd{index:02d}", state=state, client=f"Odd {index}")
    for sort, way in SORT_DIRS:
        assert len(everyone(planner, sort=sort, dir=way)["proposals"]) == len(ODD_STATES)
    assert len(everyone(planner, search=f"{planner.token} odd")["proposals"]) == len(ODD_STATES)


def test_a_total_that_is_not_a_number_is_returned_as_it_is_and_does_not_break_the_value_sort(planner):
    put(planner, "ok", summary={"total_net": 1200.5, "total_gross": 1400, "tabs_built": ["net"]}, client="Ok")
    put(planner, "str", summary={"total_net": "twelve"}, client="Str")
    put(planner, "list", summary={"total_net": [1, 2]}, client="List")
    put(planner, "none", summary={"total_net": None}, client="None")
    result = mine(planner, sort="value")
    assert ids(result, planner)[0] == "ok"
    by_id = {p["proposal_id"].removeprefix(planner.prefix): p for p in result["proposals"]}
    assert by_id["ok"]["total_net"] == 1200.5 and by_id["ok"]["total_gross"] == 1400 and by_id["ok"]["tabs_built"] == ["net"]
    assert by_id["str"]["total_net"] == "twelve" and by_id["list"]["total_net"] == [1, 2] and by_id["none"]["total_net"] is None


# ================================================================================================================= lineage

def test_a_copys_lineage_comes_back_whether_it_is_an_object_or_a_bare_id(planner):
    base = {"request": {}, "line_items": [{}]}
    put(planner, "obj", state={**base, "duplicated_from": {"proposal_id": "src-1", "title": "EVC-9 | Acme Air | Entravision", "client_name": "Acme Air",
                                                           "at": "2026-11-11T10:00:00+00:00", "evil": "<b>x</b>"}})
    put(planner, "str", state={**base, "duplicated_from": "src-only-id"})
    put(planner, "long", state={**base, "duplicated_from": {"proposal_id": "z" * 500, "client_name": "q" * 500}})
    put(planner, "html", state={**base, "duplicated_from": {"client_name": "<img src=x onerror=alert(1)>"}})
    put(planner, "list", state={**base, "duplicated_from": ["a"]})
    put(planner, "empty", state={**base, "duplicated_from": {}})
    put(planner, "num", state={**base, "duplicated_from": 7})
    put(planner, "null", state={**base, "duplicated_from": None})
    put(planner, "none", state=base)
    got = {p["proposal_id"].removeprefix(planner.prefix): p["duplicated_from"] for p in mine(planner)["proposals"]}
    assert got["obj"] == {"proposal_id": "src-1", "client_name": "Acme Air", "notion_id": "", "title": "EVC-9 | Acme Air | Entravision"}
    assert got["str"] == {"proposal_id": "src-only-id", "client_name": "", "notion_id": "", "title": ""}
    assert got["long"] == {"proposal_id": "z" * 200, "client_name": "q" * 200, "notion_id": "", "title": ""}
    assert got["html"]["client_name"] == "<img src=x onerror=alert(1)>"       # text, for the page to put in a text node
    assert all(got[k] is None for k in ("list", "empty", "num", "null", "none"))


# ================================================================================================================= search

def test_every_typed_word_must_match_and_the_words_may_be_spread_over_the_columns(planner):
    put(planner, "a", client="Acme  HVAC", notion="EVC-48213", title="EVC-48213_AcmeHVAC_Proposal_20261105", requested_by=f"{planner.token} Jane Rivera")
    put(planner, "b", client="Acme Dental", notion="EVC-48214", title="EVC-48214_AcmeDental_Proposal_20261105", requested_by=f"{planner.token} Jane Rivera")
    put(planner, "c", client="Zeta Foods", notion="EVC-50001", title="EVC-50001_ZetaFoods_Proposal_20261105", requested_by=f"{planner.token} Sam Lee")

    def found(search):
        return sorted(ids(mine(planner, search=search), planner))

    assert found("acme") == ["a", "b"] and found("ACME hvac") == ["a"] and found("hvac acme") == ["a"]       # words, any order, any case
    assert found("acme  hvac") == ["a"]                                                                         # two spaces in the client, two in the box
    assert found("evc 48213") == ["a"] and found("48213") == ["a"] and found("EVC-48213") == ["a"]             # "EVC" and the number may be in different forms
    assert found("jane acme") == ["a", "b"] and found("sam acme") == []                                       # one word in requested_by, one in the client
    assert found("ae@entravision") == ["a", "b", "c"]                                                           # seller_email is searched too
    assert found("  ") == ["a", "b", "c"] and found("") == ["a", "b", "c"]
    assert found("acme dental evc 48214 jane proposal") == ["b"]                                               # six words
    assert found("acme dental evc 48214 jane nomatch") == []
    assert found("acme dental evc 48214 jane proposal nomatch-seventh") == ["b"]                              # a seventh word is not looked at
    assert found("w" * 300) == []


def test_percent_underscore_and_backslash_typed_in_the_box_are_ordinary_characters(planner):
    put(planner, "pct", client="Zeta 100% Foods")
    put(planner, "plain", client="Zeta 1000 Foods")
    put(planner, "under", client="Beta_Co")
    put(planner, "other", client="BetaXCo")
    put(planner, "slash", client="Gamma c\\d Inc")
    put(planner, "slash2", client="Gamma cXd Inc")

    def found(search):
        return sorted(ids(mine(planner, search=search), planner))

    assert found("100%") == ["pct"] and found("%") == ["pct"]                       # "%" is not "match everything"
    assert found("_") == ["under"] and found("beta_co") == ["under"]                 # "_" is not "any one character"
    assert found("c\\d") == ["slash"] and found("\\") == ["slash"]
    assert found("%%") == [] and found("__") == [] and found("100%%") == []


def test_a_nul_character_a_quote_or_an_sql_fragment_in_the_box_is_text_and_never_an_error(planner):
    put(planner, "a", client="Acme")
    assert ids(mine(planner, search="ac\x00me"), planner) == ["a"]                  # Postgres refuses NUL in text: it is dropped, not a 500
    for search in ("'; DROP TABLE proposals; --", "\" OR 1=1 --", "x' OR '1'='1", "%'; SELECT pg_sleep(5); --"):
        assert mine(planner, search=search)["proposals"] == []
    assert mine(planner, search=f"{planner.token}")["total_count"] == 1             # the table is still there and the row is intact


# ================================================================================================================= a big table

def test_a_table_of_120_rows_at_the_page_size_cap_is_two_pages_with_nothing_lost(planner):
    for index in range(120):
        put(planner, f"bulk{index:03d}", client=f"Client {index:03d}", status="draft" if index % 3 == 0 else "generated",
            summary={} if index % 3 == 0 else {"total_net": index * 10}, updated=BASE - timedelta(minutes=index),
            state=FULL_STATE if index % 2 else {"request": {}, "line_items": [{}]})
    first = mine(planner, page_size=100)
    second = mine(planner, page_size=100, page=2)
    assert (first["count"], second["count"]) == (100, 20) and first["total_count"] == second["total_count"] == 120
    assert (first["page_size"], first["total_pages"], second["page"]) == (100, 2, 2)
    assert first["facets"] == {"all": 120, "draft": 40, "generated": 80} == second["facets"]
    assert ids(first, planner) + ids(second, planner) == [f"bulk{i:03d}" for i in range(120)]
    assert all(p["can_reopen"] for p in first["proposals"] + second["proposals"])
    capped = mine(planner, page_size=5000)
    assert capped["page_size"] == 100 and capped["count"] == 100
    odd_rows = [p for p in first["proposals"] if p["proposal_id"].removeprefix(planner.prefix) in ("bulk001", "bulk003")]
    assert all(_facts(p)[1:] == ("2027-01-05", "2027-03-31", "", 3, 2, "quarter", 4) for p in odd_rows)


def test_a_page_of_ids_that_include_one_deleted_between_the_statements_is_still_shaped(planner):
    """The facts statement is `= ANY(ids)`: an id the page saw but the table no longer holds just has no facts."""
    keep = put(planner, "keep", state=FULL_STATE)
    gone = put(planner, "gone", state=FULL_STATE)
    execute("DELETE FROM proposals WHERE proposal_id = %s", (gone,))
    rows = fetch_all(pl.FACTS_SQL, pl.facts_params([keep, gone, "no-such-id"]))
    assert [r["proposal_id"] for r in rows] == [keep]
    assert pl.facts_by_id(rows)[keep]["line_count"] == 3
    assert fetch_all(pl.FACTS_SQL, pl.facts_params([])) == []
