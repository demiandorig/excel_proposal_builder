"""
Covers /api/admin/proposals' new server-side pagination/filter/search —
the previous version pulled every row (including the big reopen_state
JSON blob) into Python and paginated/filtered nowhere at all. Calls
main.admin_list_proposals() directly with a fake `request.state.user`
and a monkeypatched fetch_all() (asserting on the SQL/params it receives)
rather than going through the full FastAPI app + a real session cookie +
a real Postgres connection — none of that middleware/DB plumbing is what
changed here, only the SQL this function builds and the shape it returns.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import main


def _fake_request(email: str = "planner@entravision.com") -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user={"email": email, "is_admin": True}))


def _row(proposal_id: str, seller_email: str, total_count: int, status: str = "generated") -> dict:
    return {
        "proposal_id": proposal_id, "client_name": "Acme", "seller_email": seller_email,
        "requested_by": "Jane AE", "notion_id": "EVC-1", "proposal_title": "t", "filename": "f.xlsx",
        "generated_at": None, "requester_ip": "1.2.3.4", "requester_user_agent": "UA",
        "summary": {"total_net": 100.0}, "total_count": total_count,
        "status": status, "updated_at": None,
    }


def test_mine_filter_matches_on_created_by_email_case_insensitively(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return [_row("p1", "Planner@Entravision.com", 1)]

    monkeypatch.setattr(main, "fetch_all", fake_fetch_all)
    result = asyncio.run(main.admin_list_proposals(_fake_request("planner@entravision.com"), mine=True))

    # created_by_email (the actual logged-in session), NOT seller_email
    # (whatever the pasted Notion text's own field said) — see
    # schema.sql's migration comment for why these are deliberately
    # different fields.
    assert "LOWER(created_by_email) = LOWER(%s)" in captured["sql"]
    # email param, then LIMIT, OFFSET (page=1/page_size=25 defaults)
    assert captured["params"] == ("planner@entravision.com", 25, 0)
    assert result["count"] == 1
    assert result["total_count"] == 1
    assert result["proposals"][0]["proposal_id"] == "p1"
    assert result["proposals"][0]["status"] == "generated"


def test_draft_rows_flow_through_the_list_with_their_status(monkeypatch):
    monkeypatch.setattr(main, "fetch_all", lambda sql, params: [_row("p2", "ae@x.com", 1, status="draft")])
    result = asyncio.run(main.my_proposals(_fake_request()))
    assert result["proposals"][0]["status"] == "draft"


def test_list_orders_by_updated_at_not_generated_at(monkeypatch):
    # A draft (generated_at NULL) must still be able to sort ahead of an
    # older generated proposal — updated_at, not generated_at, drives the
    # order now, so a draft sorts by when it was last saved, not always
    # pushed to the bottom.
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        return [_row("p1", "x", 1)]

    monkeypatch.setattr(main, "fetch_all", fake_fetch_all)
    asyncio.run(main.my_proposals(_fake_request()))
    assert "ORDER BY updated_at DESC" in captured["sql"]
    assert "generated_at DESC" not in captured["sql"]


def test_all_scope_omits_the_mine_filter_entirely(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(main, "fetch_all", fake_fetch_all)
    asyncio.run(main.admin_list_proposals(_fake_request(), mine=False))

    assert "LOWER(created_by_email)" not in captured["sql"]
    assert "WHERE" not in captured["sql"]
    assert captured["params"] == (25, 0)


def test_search_and_mine_combine_with_and(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(main, "fetch_all", fake_fetch_all)
    asyncio.run(main.admin_list_proposals(_fake_request("a@b.com"), mine=True, search="acme"))

    assert " AND " in captured["sql"]
    assert captured["params"] == ("a@b.com", "%acme%", "%acme%", "%acme%", "%acme%", "%acme%", 25, 0)


def test_pagination_math_and_page_size_cap(monkeypatch):
    def fake_fetch_all(sql, params):
        return [_row("p1", "x", 240)]

    monkeypatch.setattr(main, "fetch_all", fake_fetch_all)
    result = asyncio.run(main.admin_list_proposals(_fake_request(), page=3, page_size=500, mine=False))

    # page_size hard-capped at 100, so 240 rows -> 3 pages (100+100+40)
    assert result["page_size"] == 100
    assert result["total_pages"] == 3
    assert result["page"] == 3


def test_empty_result_has_sane_defaults(monkeypatch):
    monkeypatch.setattr(main, "fetch_all", lambda sql, params: [])
    result = asyncio.run(main.admin_list_proposals(_fake_request(), mine=True))

    assert result["proposals"] == []
    assert result["total_count"] == 0
    assert result["total_pages"] == 1  # never zero — an empty state is still "page 1 of 1"


def test_my_proposals_always_scopes_to_the_callers_own_email(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return [_row("p1", "planner@entravision.com", 1)]

    monkeypatch.setattr(main, "fetch_all", fake_fetch_all)
    # No `mine` param exists on this endpoint at all — it's always scoped,
    # unlike /api/admin/proposals which can see everyone's.
    result = asyncio.run(main.my_proposals(_fake_request("planner@entravision.com")))

    assert "LOWER(created_by_email) = LOWER(%s)" in captured["sql"]
    assert captured["params"][0] == "planner@entravision.com"
    assert result["proposals"][0]["proposal_id"] == "p1"


def test_my_proposals_defaults_to_a_smaller_page_size_than_admin(monkeypatch):
    monkeypatch.setattr(main, "fetch_all", lambda sql, params: [_row("p1", "x", 1)])
    result = asyncio.run(main.my_proposals(_fake_request()))
    assert result["page_size"] == 10  # a compact in-wizard lookup, not a full admin table


class _CapturingConn:
    def __init__(self, calls: list):
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self._calls.append((sql, params))
        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])


def test_save_proposal_metadata_persists_created_by_email_separately_from_seller_email(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "get_connection", lambda: _CapturingConn(calls))

    # seller_email deliberately DIFFERENT from created_by_email here — the
    # exact real-world case this fixes: the pasted Notion text names one
    # AE, but a different logged-in person is the one who actually clicked
    # Generate.
    main._save_proposal_metadata(
        proposal_id="p1", client_name="Acme", seller_email="ae@entravision.com",
        created_by_email="planner@entravision.com", requested_by="Jane AE",
        notion_id="EVC-1", proposal_title="t", filename="f.xlsx",
        email_doc_filename=None, pptx_net_filename=None, pptx_gross_filename=None,
        generated_at=None, requester_ip="1.2.3.4", requester_user_agent="UA",
        summary={}, reopen_state={},
    )

    assert len(calls) == 1
    sql, params = calls[0]
    assert "created_by_email" in sql
    # seller_email is params[2], created_by_email is params[3] — matches
    # the column order in the INSERT column list.
    assert params[2] == "ae@entravision.com"
    assert params[3] == "planner@entravision.com"
    # status defaults to "generated" (matching every row before drafts
    # existed) and is appended at the END of the param list, so it can't
    # shift the two positional indices asserted above.
    assert params[-1] == "generated"
    assert "status" in sql and "updated_at" in sql


def test_save_proposal_metadata_status_defaults_and_draft_override(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "get_connection", lambda: _CapturingConn(calls))

    # A draft omits every generate-only field entirely — must not raise
    # (they're all optional now) and must persist status="draft".
    main._save_proposal_metadata(
        proposal_id="d1", client_name="Acme", seller_email="", created_by_email="planner@entravision.com",
        requested_by="", notion_id=None, proposal_title="Untitled draft",
        reopen_state={"request": {}}, status="draft",
    )
    assert calls[0][1][-1] == "draft"
    assert calls[0][1][7] is None  # filename — never known yet for a draft


def test_users_export_csv_never_includes_password_fields(monkeypatch):
    monkeypatch.setattr(
        main.auth_svc, "list_users",
        lambda: [{"email": "a@b.com", "is_admin": True, "disabled": False, "created_at": "2026-01-01T00:00:00"}],
    )
    response = asyncio.run(main.admin_export_users())
    body = response.body.decode()
    assert "a@b.com" in body
    assert "password" not in body.lower()
    assert response.headers["content-disposition"].startswith("attachment;")


def test_market_config_export_includes_base_and_t1_ccs_rows(monkeypatch):
    monkeypatch.setattr(main, "load_market_config", lambda: {
        main.BASE_CCS_KEY: ["base@entravision.com"],
        main.T1_CCS_KEY: ["t1@entravision.com"],
        main.DEFAULT_KEY: {"address_line1": "123 Main St", "ccs": ["default@entravision.com"]},
        "Los Angeles": {"address_line1": "456 Sunset Blvd", "dsc_email": "dsc@x.com", "ccs": ["a@x.com", "b@x.com"]},
    })
    response = asyncio.run(main.admin_export_market_config())
    body = response.body.decode()
    assert "base@entravision.com" in body
    assert "t1@entravision.com" in body
    assert "Los Angeles" in body
    assert "a@x.com; b@x.com" in body  # multiple CCs joined with "; ", not a bare comma (would corrupt the CSV column)
