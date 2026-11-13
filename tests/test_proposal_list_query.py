"""The proposal-history list's query and its response (GET /api/my-proposals, GET /api/admin/proposals), offline.

app.main._query_proposals runs three statements per request (the page, the counts behind the status buttons, the saved plan's facts for
the page's own ids) and app/services/proposal_list.py decides what they say and how a row is shaped. Here the database is
tests/proposal_list_support.FakeProposalsDb, which tells the statements apart, records them, and fails on any statement it does not know;
tests/test_proposal_list_db.py runs the same statements against a real Postgres.

What these pin, in the order of the sections below:
  1. the statements (and that `reopen_state` can never be in the page or counts one: reading it for every matching row was 457 ms against 7);
  2. scope: a planner can only ever see their own rows, whatever else is sent;
  3. the status filter and that the counts ignore it;
  4. the sort whitelist, and that nothing a caller sends is ever in the SQL text;
  5. search: words, caps, escapes;
  6. the normalisers that turn whatever a saved document holds into the row's few facts;
  7. fail-soft: the counts and the facts only decorate the list;
  8. every key the list returned before still comes back, built the same way.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import threading
from datetime import date, datetime, timezone

import pytest

from app import main
from app.services import proposal_list as pl
from tests.proposal_list_support import (
    NEW_ENVELOPE_KEYS, NEW_ROW_KEYS, OLD_ENVELOPE_KEYS, OLD_ROW_KEYS, FakeProposalsDb, call, facts_row, fake_request, page_row,
)

PLANNER = "planner@entravision.com"
SORT_DIRS = [(sort, way) for sort in pl.LIST_SORTS for way in pl.LIST_DIRS]

# What each (sort, dir) must put after ORDER BY. Written out, not read from pl.SORTS: changing an ordering is a deliberate act.
_NET = "CASE WHEN jsonb_typeof(summary->'total_net') = 'number' THEN (summary->>'total_net')::numeric END"
_CLIENT = "LOWER(NULLIF(BTRIM(client_name), ''))"
EXPECTED_ORDER = {
    ("updated", "desc"): "updated_at DESC, proposal_id DESC",
    ("updated", "asc"): "updated_at ASC, proposal_id DESC",
    ("client", "asc"): f"{_CLIENT} ASC NULLS LAST, updated_at DESC, proposal_id DESC",
    ("client", "desc"): f"{_CLIENT} DESC NULLS LAST, updated_at DESC, proposal_id DESC",
    ("value", "desc"): f"{_NET} DESC NULLS LAST, updated_at DESC, proposal_id DESC",
    ("value", "asc"): f"{_NET} ASC NULLS LAST, updated_at DESC, proposal_id DESC",
}
FIVE_COLUMNS = "(client_name ILIKE %s OR seller_email ILIKE %s OR requested_by ILIKE %s OR notion_id ILIKE %s OR proposal_title ILIKE %s)"
HOSTILE = ["client; DROP TABLE proposals", "asc; DROP", "x' OR '1'='1", "all; DELETE FROM proposals", "UPDATED", "", "'", "0", "-1"]


@pytest.fixture
def http(monkeypatch):
    """The real app over HTTP (routing, the 422 for a bad parameter, the login and admin rules) with a stubbed session: http(admin=True)."""
    try:
        from fastapi.testclient import TestClient
    except (ImportError, RuntimeError) as exc:                      # Starlette's test client needs httpx installed
        pytest.skip(f"no HTTP test client here: {exc}")

    def make(admin: bool):
        monkeypatch.setattr(main.auth_svc, "get_user_by_session", lambda token: {"id": 1, "email": "planner@example.com", "is_admin": admin})
        monkeypatch.setattr(main.auth_svc, "refresh_session", lambda token: None)
        return TestClient(main.app, cookies={main.SESSION_COOKIE_NAME: "test-session"})
    return make


# ======================================================================================================== 1. the statements

def test_a_default_call_runs_the_page_then_the_counts_then_the_facts_of_the_pages_own_ids(monkeypatch):
    db = FakeProposalsDb([page_row("p1"), page_row("p2")]).install(monkeypatch)
    call(main.my_proposals, fake_request(PLANNER))

    assert db.kinds == ["page", "counts", "facts"]
    page, counts, facts = db.page, db.counts_call, db.facts_call
    assert "ORDER BY updated_at DESC, proposal_id DESC" in page.sql and "LIMIT %s OFFSET %s" in page.sql
    assert "COUNT(*) OVER() AS total_count" in page.sql
    assert page.params == (PLANNER, 10, 0)
    assert counts.params == (PLANNER,)
    assert "jsonb_to_record" in facts.sql and "p.proposal_id = ANY(%s)" in facts.sql
    assert facts.params == (["p1", "p2"],)                                      # one array parameter: exactly this page's ids


def test_the_facts_statement_is_not_run_for_an_empty_page_but_the_counts_still_are(monkeypatch):
    db = FakeProposalsDb([], counts=(7, 2, 5)).install(monkeypatch)
    result = call(main.my_proposals, fake_request(PLANNER), page=9)
    assert db.kinds == ["page", "counts"]
    assert result["proposals"] == [] and result["total_count"] == 0 and result["total_pages"] == 1
    assert result["facets"] == {"all": 7, "draft": 2, "generated": 5}           # a page past the end still reports what exists


def test_a_page_of_n_rows_asks_for_the_facts_of_exactly_those_n_ids(monkeypatch):
    rows = [page_row(f"id-{i:03d}") for i in range(37)]
    db = FakeProposalsDb(rows).install(monkeypatch)
    call(main.admin_list_proposals, fake_request(), mine=False, page_size=50)
    assert db.facts_call.params == ([f"id-{i:03d}" for i in range(37)],)


def _every_combination():
    for admin in (False, True):
        for mine in ((True,) if not admin else (True, False)):
            for search in ("", "acme hvac", "%", "100% a_b \\", "x" * 500):
                for status in pl.LIST_STATUSES:
                    for sort, way in SORT_DIRS:
                        yield admin, mine, search, status, sort, way


def test_reopen_state_is_in_neither_the_page_statement_nor_the_counts_statement_for_any_parameters(monkeypatch):
    """The structural guard: with the saved plan's JSON in the PAGE query, COUNT(*) OVER() made it run for every matching row (a planner with
    666 proposals: 457 ms against 7). The one statement that may read it is the facts statement, for the page's own ids."""
    seen = 0
    for admin, mine, search, status, sort, way in _every_combination():
        db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
        route = main.admin_list_proposals if admin else main.my_proposals
        extra = {"mine": mine} if admin else {}
        call(route, fake_request(PLANNER), search=search, status=status, sort=sort, dir=way, **extra)
        for c in (db.page, db.counts_call):
            assert "reopen_state" not in c.sql.lower(), (c.kind, search, status, sort, way)
            assert "->" not in c.sql.replace("summary->", ""), c.kind                  # no other JSON operator in the light statements
        assert "reopen_state" in db.facts_call.sql
        seen += 1
    assert seen == 3 * 5 * 3 * 6                                                         # (the matrix really ran: 3 scopes x 5 searches x 3 statuses x 6 sorts)


def test_the_light_column_list_never_names_reopen_state_and_the_planner_refuses_one_that_does():
    assert "reopen_state" not in main._PROPOSAL_LIST_COLUMNS
    assert "created_by_email" in main._PROPOSAL_LIST_COLUMNS                    # the admin view's planner column
    with pytest.raises(ValueError, match="reopen_state"):
        pl.plan_list(columns=main._PROPOSAL_LIST_COLUMNS + ", reopen_state->'duplicated_from' AS duplicated_from", mine_email=PLANNER)
    with pytest.raises(ValueError):
        pl.plan_list(columns="proposal_id, reopen_state", mine_email=None)


def test_the_facts_statement_reads_the_document_once_per_row_through_jsonb_to_record_never_with_arrows():
    """Each `reopen_state->...` re-reads the stored document (7 of them: 1.8 s over 3,000 rows against 0.27 s for one jsonb_to_record)."""
    sql = pl.FACTS_SQL
    assert not re.search(r"reopen_state\s*(->|#>)", sql)
    assert sql.count("jsonb_to_record(") == 1 and "p.proposal_id = ANY(%s)" in sql and sql.count("%s") == 1


def test_the_list_needs_no_schema_change_every_column_it_names_is_in_schema_sql():
    """A published deployment applies schema.sql's diff by hand: a column the list names that schema.sql does not define would be a 500 there."""
    from pathlib import Path
    schema = (Path(main.__file__).resolve().parents[1] / "schema.sql").read_text(encoding="utf-8")
    table = re.search(r"CREATE TABLE proposals \((.*?)\n\);", schema, re.S).group(1)
    defined = set(re.findall(r"^\s*(\w+)\s+[A-Z]", table, re.M)) | set(re.findall(r"ALTER TABLE proposals ADD COLUMN (?:IF NOT EXISTS )?(\w+)", schema))
    named = {c.strip() for c in main._PROPOSAL_LIST_COLUMNS.split(",")} | set(pl.SEARCH_COLUMNS) | {"updated_at", "status", "created_by_email", "reopen_state", "summary", "proposal_id"}
    assert named <= defined, named - defined


def test_the_page_statement_selects_exactly_the_light_columns_and_the_window_total(monkeypatch):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    call(main.my_proposals, fake_request(PLANNER))
    select = re.search(r"SELECT (.*?)\s+FROM proposals", db.page.sql, re.S).group(1)
    assert select == f"{main._PROPOSAL_LIST_COLUMNS}, COUNT(*) OVER() AS total_count"


def test_old_call_sites_of_query_proposals_with_only_the_four_original_arguments_still_work(monkeypatch):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    result = main._query_proposals(mine_email=PLANNER, search="", page=1, page_size=10)
    assert result["applied"] == {"status": "all", "sort": "updated", "dir": "desc"}
    assert "created_by_email" not in result["proposals"][0]                     # admin=False by default
    assert db.kinds == ["page", "counts", "facts"]


@pytest.mark.parametrize("route", [main.my_proposals, main.admin_list_proposals])
def test_the_routes_stay_async_and_the_blocking_statements_run_off_the_event_loop(monkeypatch, route):
    assert inspect.iscoroutinefunction(route)
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)

    async def go():
        loop_thread = threading.get_ident()
        await route(fake_request(PLANNER))
        return loop_thread

    loop_thread = asyncio.run(go())
    assert len(db.calls) == 3 and all(c.thread != loop_thread for c in db.calls)


@pytest.mark.parametrize("page, size, expect_page, expect_size, expect_offset", [
    (0, 0, 1, 1, 0), (-5, -5, 1, 1, 0), (1, 500, 1, 100, 0), (3, 100, 3, 100, 200), (2, 10, 2, 10, 10),
    (10 ** 30, 25, pl.MAX_PAGE, 25, (pl.MAX_PAGE - 1) * 25),                    # an absurd page number must not overflow OFFSET (a 500)
])
def test_page_and_page_size_are_clamped_before_they_become_limit_and_offset(monkeypatch, page, size, expect_page, expect_size, expect_offset):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    result = call(main.admin_list_proposals, fake_request(), mine=False, page=page, page_size=size)
    assert (result["page"], result["page_size"]) == (expect_page, expect_size)
    assert db.page.params == (expect_size, expect_offset) and expect_offset < 2 ** 63


# ======================================================================================================== 2. scope

@pytest.mark.parametrize("search", ["", "acme", "acme hvac evc 48213"])
@pytest.mark.parametrize("status", pl.LIST_STATUSES)
@pytest.mark.parametrize("sort, way", SORT_DIRS)
def test_my_proposals_is_always_scoped_to_the_callers_email_whatever_else_is_asked(monkeypatch, search, status, sort, way):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    call(main.my_proposals, fake_request("Planner@Entravision.com"), search=search, status=status, sort=sort, dir=way)
    for c in (db.page, db.counts_call):
        assert "LOWER(created_by_email) = LOWER(%s)" in c.sql and c.params[0] == "Planner@Entravision.com"


def test_the_scope_term_comes_before_every_search_term_so_it_is_the_first_parameter(monkeypatch):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    call(main.my_proposals, fake_request(PLANNER), search="acme")
    assert db.page.params == (PLANNER, *["%acme%"] * 5, 10, 0)
    assert db.counts_call.params == (PLANNER, *["%acme%"] * 5)
    assert db.page.sql.index("LOWER(created_by_email)") < db.page.sql.index("client_name ILIKE")


def test_the_all_scope_of_the_admin_has_no_scope_term_in_the_page_or_the_counts(monkeypatch):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    call(main.admin_list_proposals, fake_request(PLANNER), mine=False)
    assert "LOWER(created_by_email)" not in db.page.sql and "WHERE" not in db.page.sql
    assert db.counts_call.sql.split("FROM proposals", 1)[1].strip() == "" and db.counts_call.params == ()      # (its FILTER (WHERE ..) terms are not a scope)
    assert db.page.params == (25, 0)


def test_the_admin_mine_scope_is_the_logged_in_admins_own_email(monkeypatch):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    call(main.admin_list_proposals, fake_request("Boss@Entravision.com"), mine=True)
    assert db.page.params[0] == "Boss@Entravision.com" and db.counts_call.params == ("Boss@Entravision.com",)


def test_an_empty_email_scopes_to_nobodys_rows_instead_of_everyones(monkeypatch):
    db = FakeProposalsDb([]).install(monkeypatch)
    main._query_proposals(mine_email="", search="", page=1, page_size=10)
    assert "LOWER(created_by_email) = LOWER(%s)" in db.page.sql and db.page.params[0] == ""
    assert "LOWER(created_by_email) = LOWER(%s)" in db.counts_call.sql


def test_the_planner_route_has_no_way_to_ask_for_everyones_history(monkeypatch):
    assert "mine" not in inspect.signature(main.my_proposals).parameters
    assert "mine" in inspect.signature(main.admin_list_proposals).parameters


def test_a_planner_who_sends_mine_false_to_the_planner_route_is_still_scoped_to_themselves(http, monkeypatch):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    answer = http(admin=False).get("/api/my-proposals?mine=false&search=")
    assert answer.status_code == 200, answer.text
    assert db.page.params[0] == "planner@example.com" and db.counts_call.params == ("planner@example.com",)


def test_created_by_email_is_in_the_admin_routes_rows_only(monkeypatch):
    FakeProposalsDb([page_row("p1", created_by_email="someone@entravision.com"), page_row("p2", created_by_email=None)]).install(monkeypatch)
    admin_rows = call(main.admin_list_proposals, fake_request(), mine=False)["proposals"]
    assert [r["created_by_email"] for r in admin_rows] == ["someone@entravision.com", ""]
    planner_rows = call(main.my_proposals, fake_request(PLANNER))["proposals"]
    assert all("created_by_email" not in r for r in planner_rows)


# ======================================================================================================== 3. status

@pytest.mark.parametrize("status, term", [("draft", "status = 'draft'"), ("generated", "status <> 'draft'"), ("all", None)])
def test_the_status_filter_is_in_the_page_but_never_in_the_counts(monkeypatch, status, term):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    call(main.my_proposals, fake_request(PLANNER), search="acme", status=status)
    page_where = db.page.sql.split("FROM proposals", 1)[1].split("ORDER BY", 1)[0]
    counts_where = db.counts_call.sql.split("FROM proposals", 1)[1]
    assert "status" not in counts_where                                         # the counts follow scope and search, not the status button
    if term:
        assert term in page_where
        assert page_where.count("status") == 1
    else:
        assert "status" not in page_where
    assert db.page.params == (PLANNER, *["%acme%"] * 5, 10, 0)                  # the status clause is a literal: no parameter


def test_the_counts_statement_answers_all_three_figures_at_once(monkeypatch):
    db = FakeProposalsDb([page_row("p1")], counts=(140, 31, 109)).install(monkeypatch)
    result = call(main.my_proposals, fake_request(PLANNER))
    assert result["facets"] == {"all": 140, "draft": 31, "generated": 109}
    assert "COUNT(*) FILTER (WHERE status = 'draft')" in db.counts_call.sql and "COUNT(*) FILTER (WHERE status <> 'draft')" in db.counts_call.sql


def test_applied_echoes_what_was_understood(monkeypatch):
    FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    for sort, way in SORT_DIRS:
        for status in pl.LIST_STATUSES:
            applied = call(main.my_proposals, fake_request(PLANNER), status=status, sort=sort, dir=way)["applied"]
            assert applied == {"status": status, "sort": sort, "dir": way}
    natural = {sort: call(main.my_proposals, fake_request(PLANNER), sort=sort)["applied"]["dir"] for sort in pl.LIST_SORTS}
    assert natural == {"updated": "desc", "client": "asc", "value": "desc"}      # newest, A to Z, biggest first


# ======================================================================================================== 4. sort whitelist

def test_the_expected_order_table_covers_every_sort_and_direction():
    assert set(EXPECTED_ORDER) == set(SORT_DIRS)
    assert set(pl.SORTS) == set(pl.LIST_SORTS) and all(set(v) == set(pl.LIST_DIRS) for v in pl.SORTS.values())
    assert set(pl.DEFAULT_DIR) == set(pl.LIST_SORTS)


def test_the_literal_types_of_the_routes_are_the_same_values_the_helpers_whitelist():
    from typing import get_args
    assert get_args(pl.StatusParam) == pl.LIST_STATUSES and get_args(pl.SortParam) == pl.LIST_SORTS and get_args(pl.DirParam) == pl.LIST_DIRS


@pytest.mark.parametrize("sort, way", SORT_DIRS)
def test_every_sort_and_direction_puts_exactly_its_terms_after_order_by_with_the_tie_break_last(monkeypatch, sort, way):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    call(main.my_proposals, fake_request(PLANNER), sort=sort, dir=way)
    order = re.search(r"ORDER BY (.*?)\s+LIMIT %s OFFSET %s", db.page.sql, re.S).group(1)
    assert order == EXPECTED_ORDER[(sort, way)]
    assert order.endswith(", proposal_id DESC")                                 # a total order: pages never overlap or skip


def test_an_unknown_or_missing_direction_is_the_sorts_natural_one():
    assert pl.order_by("client", None)[1:] == ("client", "asc")
    assert pl.order_by("value", None)[1:] == ("value", "desc")
    assert pl.order_by("updated", None)[1:] == ("updated", "desc")
    assert pl.order_by("client", "sideways")[1:] == ("client", "asc")
    assert pl.order_by("nonsense", "asc")[1:] == ("updated", "asc")


@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_hostile_sort_dir_or_status_handed_to_the_helpers_is_normalised_to_the_defaults(monkeypatch, hostile):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    result = main._query_proposals(mine_email=PLANNER, search="", page=1, page_size=10, status=hostile, sort=hostile, direction=hostile)
    assert result["applied"] == {"status": "all", "sort": "updated", "dir": "desc"}
    assert "ORDER BY updated_at DESC, proposal_id DESC" in db.page.sql and "status" not in db.page.sql.split("FROM proposals", 1)[1]
    assert pl.order_by(hostile, hostile)[0] == "updated_at DESC, proposal_id DESC"
    assert pl.status_clause(hostile) is None


@pytest.mark.parametrize("hostile", ["client; DROP TABLE proposals", "asc; DROP", "x' OR '1'='1", "all; DELETE FROM proposals"])
def test_a_hostile_sort_dir_or_status_appears_in_no_statements_text_or_parameters_at_all(monkeypatch, hostile):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    main._query_proposals(mine_email=None, search="", page=1, page_size=10, status=hostile, sort=hostile, direction=hostile, admin=True)
    assert len(db.calls) == 3
    for c in db.calls:
        assert hostile not in c.sql
        assert all(hostile not in str(p) for p in c.params), (c.kind, c.params)


@pytest.mark.parametrize("hostile", ["client; DROP TABLE proposals", "asc; DROP", "x' OR '1'='1", "all; DELETE FROM proposals"])
def test_no_string_the_caller_sends_is_ever_in_any_statements_text_only_in_bound_parameters(monkeypatch, hostile):
    """Sort, dir and status only select entries of a dictionary; the search words only ever travel as bound parameters."""
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    main._query_proposals(mine_email=None, search=hostile, page=1, page_size=10, status=hostile, sort=hostile, direction=hostile, admin=True)
    assert len(db.calls) == 3
    for c in db.calls:
        for fragment in ("DROP", "DELETE", "client;", "asc;", "all;", "x'", "'1'='1"):
            assert fragment not in c.sql, (c.kind, fragment)
    for word in hostile.split()[:pl.SEARCH_MAX_WORDS]:
        assert pl.like_pattern(word) in db.page.params and pl.like_pattern(word) in db.counts_call.params


@pytest.mark.parametrize("path", ["/api/my-proposals", "/api/admin/proposals"])
@pytest.mark.parametrize("query", [
    "sort=client%3B%20DROP%20TABLE%20proposals", "dir=asc%3B%20DROP", "status=x%27%20OR%20%271%27%3D%271", "sort=UPDATED", "dir=up", "status=",
    "sort=flight_start", "sort=client_name", "status=all%3B%20DELETE%20FROM%20proposals", "dir=ASC",
])
def test_an_unknown_sort_dir_or_status_over_http_is_a_422_before_any_sql_exists(http, monkeypatch, path, query):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    answer = http(admin=True).get(f"{path}?{query}")
    assert answer.status_code == 422, answer.text
    assert db.calls == []
    detail = answer.json()["detail"]
    assert detail[0]["loc"][0] == "query" and detail[0]["loc"][1] in ("sort", "dir", "status")
    assert "DROP" not in answer.text and "DELETE" not in answer.text                # a refused value is not echoed


@pytest.mark.parametrize("path", ["/api/my-proposals", "/api/admin/proposals"])
def test_valid_sort_dir_status_and_search_over_http_reach_the_query_and_are_echoed(http, monkeypatch, path):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    answer = http(admin=True).get(f"{path}?status=draft&sort=client&dir=desc&search=acme%20hvac&page=2&page_size=5")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["applied"] == {"status": "draft", "sort": "client", "dir": "desc"} and body["page"] == 2 and body["page_size"] == 5
    assert EXPECTED_ORDER[("client", "desc")] in db.page.sql and "status = 'draft'" in db.page.sql
    assert db.page.params[-2:] == (5, 5) and db.page.params.count("%acme%") == 5 and db.page.params.count("%hvac%") == 5


def test_the_old_request_with_none_of_the_new_parameters_is_unchanged_over_http(http, monkeypatch):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    answer = http(admin=False).get("/api/my-proposals?page=1&page_size=10&search=")
    assert answer.status_code == 200 and "ORDER BY updated_at DESC, proposal_id DESC" in db.page.sql
    assert answer.json()["applied"] == {"status": "all", "sort": "updated", "dir": "desc"}


# ======================================================================================================== 5. search

def test_every_word_must_match_one_of_the_five_columns_so_the_groups_are_anded(monkeypatch):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    call(main.my_proposals, fake_request(PLANNER), search="acme hvac")
    where = db.page.sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0].strip()
    assert where == f"LOWER(created_by_email) = LOWER(%s) AND {FIVE_COLUMNS} AND {FIVE_COLUMNS}"
    assert db.page.params == (PLANNER, *["%acme%"] * 5, *["%hvac%"] * 5, 10, 0)
    assert db.counts_call.params == (PLANNER, *["%acme%"] * 5, *["%hvac%"] * 5)     # the counts see the same words


@pytest.mark.parametrize("search, words", [
    ("acme", ["acme"]), ("  acme   hvac  ", ["acme", "hvac"]), ("acme\thvac\nevc", ["acme", "hvac", "evc"]), ("EVC-48213", ["EVC-48213"]),
    ("evc 48213", ["evc", "48213"]), ("", []), ("   ", []), ("\t\n", []), (None, []), (5, []), (["acme"], []),
    ("a b c d e f g h", ["a", "b", "c", "d", "e", "f"]),                                # at most 6 words
    ("x" * 100, ["x" * 80]),                                                            # at most 80 characters of a word
    ("ac\x00me", ["acme"]),                                                             # Postgres refuses NUL in text: it would be a 500
    ("\x00", []),
])
def test_search_words_split_on_whitespace_and_are_capped(search, words):
    assert pl.search_words(search) == words


def test_only_the_first_200_characters_of_a_search_are_looked_at():
    long_words = " ".join(["w" * 70] * 4)                                               # 4 x 70 + 3 spaces = 283 characters
    words = pl.search_words(long_words)
    assert [len(w) for w in words] == [70, 70, 58]                                      # the third is cut by the 200 character cap, the fourth is gone
    assert sum(len(w) for w in words) + len(words) - 1 == pl.SEARCH_MAX_LEN


def test_a_blank_search_adds_no_clause_and_no_parameters(monkeypatch):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    call(main.admin_list_proposals, fake_request(), mine=False, search="   ")
    assert "ILIKE" not in db.page.sql and db.page.params == (25, 0) and db.counts_call.params == ()


@pytest.mark.parametrize("word, pattern", [
    ("acme", "%acme%"), ("100%", "%100\\%%"), ("a_b", "%a\\_b%"), ("c\\d", "%c\\\\d%"), ("%", "%\\%%"), ("_", "%\\_%"),
    ("%_\\", "%\\%\\_\\\\%"), ("Ünï", "%Ünï%"),
])
def test_like_wildcards_typed_by_a_person_are_literal_characters(word, pattern):
    assert pl.like_pattern(word) == pattern


def test_the_escaped_pattern_is_what_the_statement_receives(monkeypatch):
    db = FakeProposalsDb([page_row("p1")]).install(monkeypatch)
    call(main.my_proposals, fake_request(PLANNER), search="100%")
    assert db.page.params[1:6] == ("%100\\%%",) * 5


def test_scope_where_counts_placeholders_and_parameters_alike():
    for mine in (None, PLANNER):
        for search in ("", "a", "a b c d e f g"):
            clauses, params = pl.scope_where(mine, search)
            assert " ".join(clauses).count("%s") == len(params)


# ======================================================================================================== 6. normalisers

@pytest.mark.parametrize("raw, iso", [
    ("2027-01-05", "2027-01-05"), ("1/5/2027", "2027-01-05"), ("01/05/2027", "2027-01-05"), ("1/5/27", "2027-01-05"), ("2027/01/05", "2027-01-05"),
    (" 2027-01-05 ", "2027-01-05"), ("12/31/2026", "2026-12-31"), ("2/29/2028", "2028-02-29"),
    ("TBD", None), ("", None), ("   ", None), (None, None), (5, None), (5.5, None), ([], None), ({}, None), (True, None), (b"2027-01-05", None),
    (date(2027, 1, 5), None),                                  # only text is read: a date OBJECT is not what the database returns, and str() of one would parse
    ("2027-13-45", None), ("2/30/2027", None), ("2/29/2027", None), ("Jan 5, 2027", None), ("2027-01-05T00:00:00", None),
])
def test_flight_date_reads_the_dates_planners_type_and_nothing_else(raw, iso):
    assert pl.flight_date(raw) == iso


@pytest.mark.parametrize("raw, lo, hi, expected", [
    ("4", 1, 8, 4), (4, 1, 8, 4), (4.0, 1, 8, 4), (" 7 ", 1, 8, 7), ("1", 1, 8, 1), ("8", 1, 8, 8),
    ("0", 1, 8, None), (0, 1, 8, None), ("9", 1, 8, None), (-3, 1, 8, None), (4.5, 1, 8, None), ("4.0", 1, 8, None), ("abc", 1, 8, None),
    ("", 1, 8, None), (None, 1, 8, None), (True, 1, 8, None), (False, 0, 8, None), ([1], 1, 8, None), ({"a": 1}, 1, 8, None),
    (float("nan"), 1, 8, None), (float("inf"), 1, 8, None), ("9" * 5000, 1, 8, None), (10 ** 30, 1, 8, None),
])
def test_small_int_takes_whole_numbers_in_range_and_nothing_else(raw, lo, hi, expected):
    assert pl.small_int(raw, lo, hi) == expected


_EMPTY_LINEAGE_FIELDS = {"proposal_id": "", "client_name": "", "notion_id": "", "title": ""}


@pytest.mark.parametrize("raw, expected", [
    (None, None), ("", None), ("   ", None), ({}, None), ({"proposal_id": ""}, None), ({"at": "2026-11-11"}, None), ([], None), (["abc"], None),
    (5, None), (True, None), (1.5, None), ({"proposal_id": 5, "client_name": None, "title": ["x"]}, None),
    ("src-123", {**_EMPTY_LINEAGE_FIELDS, "proposal_id": "src-123"}),                    # a bare id string
    ({"proposal_id": "src-1", "title": "EVC-9 | Acme Air | Entravision", "client_name": "Acme Air", "at": "2026-11-11T10:00:00Z", "evil": "<b>x</b>"},
     {"proposal_id": "src-1", "client_name": "Acme Air", "notion_id": "", "title": "EVC-9 | Acme Air | Entravision"}),   # what the duplicate feature stores
    ({"proposal_id": "src-1", "client_name": "Acme Air", "notion_id": "EVC-9"},
     {"proposal_id": "src-1", "client_name": "Acme Air", "notion_id": "EVC-9", "title": ""}),
    ({"client_name": "<img src=x onerror=alert(1)>"}, {**_EMPTY_LINEAGE_FIELDS, "client_name": "<img src=x onerror=alert(1)>"}),   # kept as text; the page never uses innerHTML
    ({"client_name": "Acme\nAir\t​Inc   Ltd"}, {**_EMPTY_LINEAGE_FIELDS, "client_name": "Acme Air Inc Ltd"}),
    ({"proposal_id": "z" * 500, "client_name": "q" * 500, "title": "t" * 500, "notion_id": "n" * 500},
     {"proposal_id": "z" * 200, "client_name": "q" * 200, "notion_id": "n" * 200, "title": "t" * 200}),
    ("z" * 500, {**_EMPTY_LINEAGE_FIELDS, "proposal_id": "z" * 200}),
])
def test_lineage_accepts_what_the_duplicate_feature_stores_and_shows_text_only(raw, expected):
    assert pl.lineage(raw) == expected
    if expected:
        assert list(pl.lineage(raw)) == ["proposal_id", "client_name", "notion_id", "title"]


@pytest.mark.parametrize("tiers, lines, expected", [
    (None, None, None), (2, 3, 2), (3, None, 3), (1, 5, 1), (None, 3, 1), (0, 3, 1), (None, 0, None), (0, 0, None), (1, 0, 1),
    (True, None, None), (None, True, None), ("2", 1, 1),
])
def test_option_count_is_the_tiers_length_else_one_when_there_are_lines(tiers, lines, expected):
    assert pl.option_count(tiers, lines) == expected


@pytest.mark.parametrize("value, limit, expected", [
    ("Acme", 10, "Acme"), ("  a   b  ", 10, "a b"), ("a\nb\tc\x00d", 10, "a b c d"), ("a​b c﻿d", 10, "a b c d"), ("x" * 100, 5, "xxxxx"),
    (None, 10, ""), (5, 10, ""), (["a"], 10, ""), ("\ud800 lone", 10, "lone"),
])
def test_clean_text_makes_one_line_of_plain_text(value, limit, expected):
    assert pl.clean_text(value, limit) == expected


def test_facets_from_rows_is_none_unless_the_counts_row_has_the_three_figures():
    assert pl.facets_from_rows([{"all_n": 3, "draft_n": 1, "generated_n": 2}]) == {"all": 3, "draft": 1, "generated": 2}
    assert pl.facets_from_rows([{"all_n": "3", "draft_n": 1, "generated_n": 2}]) == {"all": 3, "draft": 1, "generated": 2}
    for bad in (None, [], (), "x", [None], [{}], [{"all_n": 3}], [{"all_n": None, "draft_n": 0, "generated_n": 0}], [{"proposal_id": "p1"}]):
        assert pl.facets_from_rows(bad) is None, bad


def test_facts_by_id_keeps_only_rows_that_name_a_proposal():
    rows = [facts_row("p1"), {}, {"proposal_id": None}, "junk", None, facts_row("p2")]
    assert list(pl.facts_by_id(rows)) == ["p1", "p2"]
    assert pl.facts_by_id(None) == {} and pl.facts_by_id([]) == {}


def _shape(facts, admin=False, **meta):
    base = {**page_row("p1"), "generated_at": "2026-11-02T15:30:00+00:00", "updated_at": "2026-11-03T09:00:00+00:00", "summary": {}}
    return pl.shape_row({**base, **meta}, facts, admin=admin)


def test_a_row_with_a_saved_plan_says_what_the_plan_holds():
    row = _shape(facts_row("p1", start_raw="1/5/2027", end_raw="2027-03-31", line_count=6, tier_count=2, time_unit_raw="quarter", step_raw="4",
                           dup_raw={"proposal_id": "s1", "client_name": "Acme Air", "title": "T", "at": "x"}))
    assert (row["flight_start"], row["flight_end"], row["flight_text"]) == ("2027-01-05", "2027-03-31", "")
    assert (row["line_count"], row["option_count"], row["time_unit"], row["wizard_step"], row["can_reopen"]) == (6, 2, "quarter", 4, True)
    assert row["duplicated_from"] == {"proposal_id": "s1", "client_name": "Acme Air", "notion_id": "", "title": "T"}


def test_a_legacy_row_with_no_saved_plan_cannot_be_reopened_and_claims_no_facts():
    legacy = facts_row("p1", start_raw=None, end_raw=None, line_count=None, tier_count=None, time_unit_raw=None, step_raw=None, has_state=False)
    row = _shape(legacy)
    assert row["can_reopen"] is False and row["time_unit"] is None                      # null, not the "month" a normalised None would invent
    assert all(row[k] is None for k in ("flight_start", "flight_end", "line_count", "option_count", "wizard_step", "duplicated_from"))
    assert row["flight_text"] == ""


@pytest.mark.parametrize("raw, unit", [("week", "week"), ("month", "month"), ("quarter", "quarter"), ("full_flight", "full_flight"),
                                       (None, "month"), ("", "month"), ('{"x": 1}', "month"), ("decade", "month"), ("WEEK", "week")])
def test_time_unit_is_normalised_when_there_is_a_saved_plan(raw, unit):
    assert _shape(facts_row("p1", time_unit_raw=raw))["time_unit"] == unit


@pytest.mark.parametrize("start, end, flight_text", [
    ("2027-01-05", "2027-03-31", ""),                          # both read: the dates speak
    ("TBD soon", None, "TBD soon"), ("TBD", "ASAP", "TBD – ASAP"), (None, "next spring", "next spring"),
    ("1/5/2027", "someday", "1/5/2027 – someday"),             # one reads: the raw text is still given for the page to choose
    (None, None, ""), ("", "  ", ""), ("x" * 100, None, "x" * 60), ("a\nb", "c​d", "a b – c d"),
])
def test_flight_text_is_what_was_typed_only_when_the_two_dates_did_not_both_read(start, end, flight_text):
    assert _shape(facts_row("p1", start_raw=start, end_raw=end))["flight_text"] == flight_text


def test_a_row_whose_facts_are_unknown_is_decorated_with_nothing_and_stays_openable():
    row = _shape(None)
    assert row["can_reopen"] is True                                                     # the server's 410 stays the authority
    assert all(row[k] is None for k in ("flight_start", "flight_end", "line_count", "option_count", "time_unit", "wizard_step", "duplicated_from"))


def test_a_summary_that_is_not_an_object_does_not_break_the_row():
    for summary in (None, [], ["x"], "text", 5, True):
        row = _shape(facts_row("p1"), summary=summary)
        assert row["total_net"] is None and row["total_gross"] is None and row["tabs_built"] == []


# ======================================================================================================== 7. fail-soft

def test_each_row_gets_its_own_facts_by_proposal_id_whatever_order_the_facts_come_back_in(monkeypatch):
    rows = [page_row("p1"), page_row("p2"), page_row("p3")]
    facts = [facts_row("p3", line_count=9, step_raw="8", has_state=False), facts_row("p1", line_count=1, step_raw="2"),
             facts_row("p2", line_count=5, step_raw="5", start_raw="2028-02-01", end_raw="2028-02-28")]
    FakeProposalsDb(rows, facts=facts).install(monkeypatch)
    got = {p["proposal_id"]: p for p in call(main.my_proposals, fake_request(PLANNER))["proposals"]}
    assert [(got[k]["line_count"], got[k]["wizard_step"], got[k]["can_reopen"]) for k in ("p1", "p2", "p3")] == [(1, 2, True), (5, 5, True), (9, 8, False)]
    assert (got["p2"]["flight_start"], got["p2"]["flight_end"]) == ("2028-02-01", "2028-02-28") and got["p1"]["flight_start"] == "2027-01-05"


def test_a_row_the_facts_statement_did_not_return_is_shaped_without_facts_and_stays_openable(monkeypatch):
    """(A proposal deleted between the page statement and the facts statement, or an id the facts statement skipped.)"""
    FakeProposalsDb([page_row("p1"), page_row("p2")], facts=[facts_row("p1", line_count=4)]).install(monkeypatch)
    got = {p["proposal_id"]: p for p in call(main.my_proposals, fake_request(PLANNER))["proposals"]}
    assert got["p1"]["line_count"] == 4 and got["p2"]["line_count"] is None and got["p2"]["can_reopen"] is True


def test_a_failing_counts_statement_still_returns_the_list_without_facets(monkeypatch):
    db = FakeProposalsDb([page_row("p1"), page_row("p2")], fail=("counts",)).install(monkeypatch)
    result = call(main.my_proposals, fake_request(PLANNER))
    assert result["facets"] is None and [p["proposal_id"] for p in result["proposals"]] == ["p1", "p2"]
    assert result["proposals"][0]["flight_start"] == "2027-01-05"                       # the facts still came
    assert db.kinds == ["page", "counts", "facts"]


def test_a_failing_facts_statement_still_returns_the_list_and_keeps_every_row_openable(monkeypatch):
    FakeProposalsDb([page_row("p1"), page_row("p2")], counts=(2, 0, 2), fail=("facts",)).install(monkeypatch)
    result = call(main.my_proposals, fake_request(PLANNER))
    assert result["facets"] == {"all": 2, "draft": 0, "generated": 2}
    for row in result["proposals"]:
        assert row["can_reopen"] is True and row["flight_start"] is None and row["line_count"] is None and row["duplicated_from"] is None
        assert row["time_unit"] is None and row["wizard_step"] is None and row["option_count"] is None


def test_both_decorating_statements_failing_still_returns_the_rows(monkeypatch):
    FakeProposalsDb([page_row(f"p{i}") for i in range(5)], fail=("counts", "facts")).install(monkeypatch)
    result = call(main.admin_list_proposals, fake_request(), mine=False)
    assert result["count"] == 5 and result["total_count"] == 5 and result["facets"] is None
    assert [p["proposal_id"] for p in result["proposals"]] == [f"p{i}" for i in range(5)]
    assert all(set(p) == set(OLD_ROW_KEYS) | set(NEW_ROW_KEYS) | {"created_by_email"} for p in result["proposals"])


def test_a_failing_page_statement_is_not_swallowed_and_nothing_else_runs(monkeypatch):
    db = FakeProposalsDb([page_row("p1")], fail=("page",), error=RuntimeError("no database")).install(monkeypatch)
    with pytest.raises(RuntimeError, match="no database"):
        call(main.my_proposals, fake_request(PLANNER))
    assert db.kinds == ["page"]                                                          # an outage fails after ONE wait, not three


def test_a_counts_statement_that_answers_the_wrong_shape_hides_the_counts_instead_of_inventing_them(monkeypatch):
    class Odd(FakeProposalsDb):
        def __call__(self, sql, params=()):
            if self.kind_of(sql) == "counts":
                super().__call__(sql, params)
                return [{"something": "else"}]
            return super().__call__(sql, params)
    Odd([page_row("p1")]).install(monkeypatch)
    assert call(main.my_proposals, fake_request(PLANNER))["facets"] is None


def test_the_log_of_a_failure_names_the_exception_type_and_never_the_search_text_or_the_parameters(monkeypatch, caplog):
    secret = "needle-9f3a Acme"
    error = RuntimeError(f"syntax error near {secret} for {PLANNER}")                    # a driver's message may well include the parameters
    FakeProposalsDb([page_row("p1")], fail=("counts", "facts"), error=error).install(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        call(main.my_proposals, fake_request(PLANNER), search=secret)
    ours = [r for r in caplog.records if r.name == "app.main"]
    assert len(ours) == 2 and all("RuntimeError" in r.getMessage() for r in ours)
    assert "counts" in ours[0].getMessage() and "facts" in ours[1].getMessage()
    for needle in ("needle-9f3a", "Acme", PLANNER, "syntax error", "ILIKE"):
        assert needle not in caplog.text, needle
    assert all(r.exc_info is None for r in ours)                                         # no traceback either: it carries the message


# ======================================================================================================== 8. the old contract

def _old_shape(meta: dict) -> dict:
    """The row exactly as _query_proposals built it before the list grew (the expressions, verbatim)."""
    return {
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
        "total_net": (meta.get("summary") or {}).get("total_net"),
        "total_gross": (meta.get("summary") or {}).get("total_gross"),
        "tabs_built": (meta.get("summary") or {}).get("tabs_built", []),
        "status": meta.get("status") or "generated",
        "updated_at": meta.get("updated_at", ""),
    }


GOLDEN_ROWS = [
    page_row("full"),
    page_row("draft", status="draft", summary={}, filename=None, generated_at=None, client_name="Draft Co"),
    page_row("nulls", client_name=None, seller_email=None, requested_by=None, notion_id=None, proposal_title=None, filename=None, generated_at=None,
             requester_ip=None, requester_user_agent=None, summary=None, status=None, updated_at=None, created_by_email=None),
    page_row("no-totals", summary={"tabs_built": ["net", "gross"]}),
    page_row("zero", summary={"total_net": 0, "total_gross": 0.0, "tabs_built": []}),
    page_row("odd-net", summary={"total_net": "not a number", "total_gross": None}),
    page_row("blank", client_name="", status=""),
    page_row("tz", updated_at=datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc), generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
]


@pytest.mark.parametrize("route, admin", [(main.my_proposals, False), (main.admin_list_proposals, True)])
def test_every_key_the_list_returned_before_is_still_there_with_the_same_value(monkeypatch, route, admin):
    FakeProposalsDb(GOLDEN_ROWS).install(monkeypatch)
    kwargs = {"mine": False} if admin else {}
    result = call(route, fake_request(), **kwargs)
    assert len(result["proposals"]) == len(GOLDEN_ROWS)
    for source, shaped in zip(GOLDEN_ROWS, result["proposals"]):
        expected = _old_shape(main._proposal_metadata_from_row(source))
        assert {k: shaped[k] for k in OLD_ROW_KEYS} == expected, source["proposal_id"]
        assert list(shaped)[: len(OLD_ROW_KEYS)] == list(OLD_ROW_KEYS)                   # and in the same order
        keys = set(OLD_ROW_KEYS) | set(NEW_ROW_KEYS) | ({"created_by_email"} if admin else set())
        assert set(shaped) == keys


def test_the_envelope_keeps_its_old_keys_and_meanings_and_gains_facets_and_applied(monkeypatch):
    FakeProposalsDb([page_row("p1")], total_count=240).install(monkeypatch)
    result = call(main.admin_list_proposals, fake_request(), mine=False, page=3, page_size=500)
    assert list(result)[: len(OLD_ENVELOPE_KEYS)] == list(OLD_ENVELOPE_KEYS) and set(result) == set(OLD_ENVELOPE_KEYS) | set(NEW_ENVELOPE_KEYS)
    assert (result["count"], result["total_count"], result["page"], result["page_size"], result["total_pages"]) == (1, 240, 3, 100, 3)


def test_total_pages_is_never_zero_and_rounds_up(monkeypatch):
    for total, size, pages in ((0, 10, 1), (1, 10, 1), (10, 10, 1), (11, 10, 2), (100, 100, 1), (101, 100, 2), (240, 100, 3)):
        FakeProposalsDb([page_row("p1")] if total else [], total_count=total).install(monkeypatch)
        assert call(main.my_proposals, fake_request(PLANNER), page_size=size)["total_pages"] == pages


def test_the_plan_is_a_pure_function_of_the_request():
    args = dict(columns="proposal_id, status", mine_email=PLANNER, search="acme", page=2, page_size=5, status="draft", sort="value", direction="asc")
    first, second = pl.plan_list(**args), pl.plan_list(**args)
    assert first == second and isinstance(first.page_params, tuple) and isinstance(first.counts_params, tuple)
    assert first.offset == 5 and first.applied == {"status": "draft", "sort": "value", "dir": "asc"}
