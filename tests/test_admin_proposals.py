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


def _row(proposal_id: str, seller_email: str, total_count: int) -> dict:
    return {
        "proposal_id": proposal_id, "client_name": "Acme", "seller_email": seller_email,
        "requested_by": "Jane AE", "notion_id": "EVC-1", "proposal_title": "t", "filename": "f.xlsx",
        "generated_at": None, "requester_ip": "1.2.3.4", "requester_user_agent": "UA",
        "summary": {"total_net": 100.0}, "total_count": total_count,
    }


def test_mine_filter_matches_on_seller_email_case_insensitively(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return [_row("p1", "Planner@Entravision.com", 1)]

    monkeypatch.setattr(main, "fetch_all", fake_fetch_all)
    result = asyncio.run(main.admin_list_proposals(_fake_request("planner@entravision.com"), mine=True))

    assert "LOWER(seller_email) = LOWER(%s)" in captured["sql"]
    # email param, then LIMIT, OFFSET (page=1/page_size=25 defaults)
    assert captured["params"] == ("planner@entravision.com", 25, 0)
    assert result["count"] == 1
    assert result["total_count"] == 1
    assert result["proposals"][0]["proposal_id"] == "p1"


def test_all_scope_omits_the_mine_filter_entirely(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(main, "fetch_all", fake_fetch_all)
    asyncio.run(main.admin_list_proposals(_fake_request(), mine=False))

    assert "LOWER(seller_email)" not in captured["sql"]
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

    assert "LOWER(seller_email) = LOWER(%s)" in captured["sql"]
    assert captured["params"][0] == "planner@entravision.com"
    assert result["proposals"][0]["proposal_id"] == "p1"


def test_my_proposals_defaults_to_a_smaller_page_size_than_admin(monkeypatch):
    monkeypatch.setattr(main, "fetch_all", lambda sql, params: [_row("p1", "x", 1)])
    result = asyncio.run(main.my_proposals(_fake_request()))
    assert result["page_size"] == 10  # a compact in-wizard lookup, not a full admin table


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
