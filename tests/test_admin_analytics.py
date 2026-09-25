"""
Tests for GET /api/admin/analytics's aggregation logic (compute_admin_analytics
in app/main.py). Mocks fetch_all() and asserts on the SQL/params it receives
plus the computed aggregates — same pattern as test_admin_proposals.py,
since a real query needs a live Postgres this dev environment doesn't have.

Row shape mocked here matches exactly what the real SQL SELECTs (see
compute_admin_analytics's own query): every summary/reopen_state field
comes back as already-extracted JSON TEXT (Postgres's ->> operator), not
a nested dict — the function parses each one defensively itself.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import app.main as m


def _row(
    proposal_id="p1", client_name="Acme", notion_id="EVC-100",
    created_by_email="planner@entravision.com", seller_email="ae@entravision.com",
    generated_at=None, total_net_raw="5000.0", total_gross_raw=None,
    tiers_type=None, total_months_raw="3", request_type_raw="New Business Request",
    time_unit_raw=None,
) -> dict:
    return {
        "proposal_id": proposal_id, "client_name": client_name, "notion_id": notion_id,
        "created_by_email": created_by_email, "seller_email": seller_email,
        "generated_at": generated_at or datetime(2026, 9, 15, tzinfo=timezone.utc),
        "total_net_raw": total_net_raw, "total_gross_raw": total_gross_raw,
        "tiers_type": tiers_type, "total_months_raw": total_months_raw,
        "request_type_raw": request_type_raw, "time_unit_raw": time_unit_raw,
    }


def test_empty_result_set_has_sane_defaults(monkeypatch):
    monkeypatch.setattr(m, "fetch_all", lambda sql, params: [])
    result = m.compute_admin_analytics("all")

    assert result["total_proposals"] == 0
    assert result["unique_requests"] == 0
    assert result["duplicate_count"] == 0
    assert result["avg_total_net"] is None
    assert result["total_net_sum"] == 0.0
    assert result["avg_months"] is None
    assert result["multi_tier_pct"] == 0.0
    assert result["by_planner"] == []
    assert result["most_regenerated"] == []
    assert result["by_month"] == []


def test_basic_averages(monkeypatch):
    rows = [
        _row(proposal_id="p1", total_net_raw="4000", total_months_raw="2"),
        _row(proposal_id="p2", total_net_raw="6000", total_months_raw="4"),
    ]
    monkeypatch.setattr(m, "fetch_all", lambda sql, params: rows)
    result = m.compute_admin_analytics("all")

    assert result["total_proposals"] == 2
    assert result["avg_total_net"] == 5000.0
    assert result["total_net_sum"] == 10000.0
    assert result["avg_months"] == 3.0


def test_malformed_numeric_value_is_excluded_not_crashing(monkeypatch):
    rows = [
        _row(proposal_id="p1", total_net_raw="not a number", total_months_raw="garbage"),
        _row(proposal_id="p2", total_net_raw="6000", total_months_raw="4"),
    ]
    monkeypatch.setattr(m, "fetch_all", lambda sql, params: rows)
    result = m.compute_admin_analytics("all")

    assert result["total_proposals"] == 2  # both rows still counted
    assert result["avg_total_net"] == 6000.0  # only the parseable one averaged
    assert result["avg_months"] == 4.0


def test_shared_notion_id_counted_as_duplicates(monkeypatch):
    rows = [
        _row(proposal_id="p1", notion_id="EVC-100"),
        _row(proposal_id="p2", notion_id="EVC-100"),  # regenerated
        _row(proposal_id="p3", notion_id="EVC-100"),  # regenerated again
        _row(proposal_id="p4", notion_id="EVC-200"),
    ]
    monkeypatch.setattr(m, "fetch_all", lambda sql, params: rows)
    result = m.compute_admin_analytics("all")

    assert result["total_proposals"] == 4
    assert result["unique_requests"] == 2  # EVC-100 and EVC-200
    assert result["duplicate_count"] == 2  # 2 extra rows beyond one-per-ID
    assert len(result["most_regenerated"]) == 1
    assert result["most_regenerated"][0]["notion_id"] == "EVC-100"
    assert result["most_regenerated"][0]["count"] == 3


def test_blank_notion_id_never_grouped_as_a_duplicate_of_itself(monkeypatch):
    # Multiple proposals with NO notion_id must NOT be treated as
    # duplicates of each other — that would be a false positive.
    rows = [
        _row(proposal_id="p1", notion_id=""),
        _row(proposal_id="p2", notion_id=None),
        _row(proposal_id="p3", notion_id="   "),
    ]
    monkeypatch.setattr(m, "fetch_all", lambda sql, params: rows)
    result = m.compute_admin_analytics("all")

    assert result["total_proposals"] == 3
    assert result["unique_requests"] == 3  # each counts as its own unique request
    assert result["duplicate_count"] == 0
    assert result["most_regenerated"] == []


def test_planner_leaderboard_sorted_by_count_desc(monkeypatch):
    rows = [
        _row(proposal_id="p1", created_by_email="alice@entravision.com", total_net_raw="1000"),
        _row(proposal_id="p2", created_by_email="alice@entravision.com", total_net_raw="3000"),
        _row(proposal_id="p3", created_by_email="bob@entravision.com", total_net_raw="5000"),
    ]
    monkeypatch.setattr(m, "fetch_all", lambda sql, params: rows)
    result = m.compute_admin_analytics("all")

    assert result["by_planner"][0]["email"] == "alice@entravision.com"
    assert result["by_planner"][0]["count"] == 2
    assert result["by_planner"][0]["total_net"] == 4000.0
    assert result["by_planner"][0]["avg_net"] == 2000.0
    assert result["by_planner"][1]["email"] == "bob@entravision.com"
    assert result["by_planner"][1]["count"] == 1


def test_missing_created_by_email_groups_under_unknown(monkeypatch):
    rows = [_row(proposal_id="p1", created_by_email=None), _row(proposal_id="p2", created_by_email="")]
    monkeypatch.setattr(m, "fetch_all", lambda sql, params: rows)
    result = m.compute_admin_analytics("all")

    assert result["by_planner"] == [{"email": "(unknown)", "count": 2, "total_net": 10000.0, "avg_net": 5000.0}]


def test_multi_tier_detected_via_jsonb_typeof_array_not_null_check(monkeypatch):
    # jsonb_typeof(summary->'tiers') = 'array' for a real multi-tier
    # proposal; 'null' for summary["tiers"]=None (single-tier, but a JSON
    # null value, which is NOT SQL NULL — the exact Postgres trap this
    # function's own comment warns about); None (SQL NULL) if summary
    # itself is missing/has no 'tiers' key at all.
    rows = [
        _row(proposal_id="p1", tiers_type="array"),
        _row(proposal_id="p2", tiers_type="null"),
        _row(proposal_id="p3", tiers_type=None),
        _row(proposal_id="p4", tiers_type="array"),
    ]
    monkeypatch.setattr(m, "fetch_all", lambda sql, params: rows)
    result = m.compute_admin_analytics("all")

    assert result["multi_tier_pct"] == 50.0  # 2 of 4


def test_volume_by_month_bucketed_correctly(monkeypatch):
    rows = [
        _row(proposal_id="p1", generated_at=datetime(2026, 8, 5, tzinfo=timezone.utc)),
        _row(proposal_id="p2", generated_at=datetime(2026, 8, 20, tzinfo=timezone.utc)),
        _row(proposal_id="p3", generated_at=datetime(2026, 9, 1, tzinfo=timezone.utc)),
    ]
    monkeypatch.setattr(m, "fetch_all", lambda sql, params: rows)
    result = m.compute_admin_analytics("all")

    assert result["by_month"] == [{"month": "2026-08", "count": 2}, {"month": "2026-09", "count": 1}]


def test_request_type_breakdown(monkeypatch):
    rows = [
        _row(proposal_id="p1", request_type_raw="New Business Request"),
        _row(proposal_id="p2", request_type_raw="New Business Request"),
        _row(proposal_id="p3", request_type_raw="Renewal Proposal Request"),
        _row(proposal_id="p4", request_type_raw=None),
    ]
    monkeypatch.setattr(m, "fetch_all", lambda sql, params: rows)
    result = m.compute_admin_analytics("all")

    by_type = {d["request_type"]: d["count"] for d in result["by_request_type"]}
    assert by_type["New Business Request"] == 2
    assert by_type["Renewal Proposal Request"] == 1
    assert by_type["(unspecified)"] == 1


def test_time_unit_adoption_defaults_missing_to_month(monkeypatch):
    # time_unit only exists in reopen_state for proposals generated after
    # the Week/Month/Quarter toggle shipped — every older row has no
    # time_unit_raw at all and correctly counts as "month" (this app's
    # only-ever behavior before the toggle existed).
    rows = [
        _row(proposal_id="p1", time_unit_raw="week"),
        _row(proposal_id="p2", time_unit_raw=None),
        _row(proposal_id="p3", time_unit_raw="month"),
    ]
    monkeypatch.setattr(m, "fetch_all", lambda sql, params: rows)
    result = m.compute_admin_analytics("all")

    by_unit = {d["time_unit"]: d["count"] for d in result["by_time_unit"]}
    assert by_unit["week"] == 1
    assert by_unit["month"] == 2


def test_window_all_time_has_no_time_filter_but_still_excludes_drafts(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(m, "fetch_all", fake_fetch_all)
    m.compute_admin_analytics("all")

    # No time-window clause for "all" — but a draft (status='draft', no
    # real Excel/summary ever built) must never count toward revenue/
    # "who creates plans" analytics regardless of window, so the
    # status filter is unconditional, not just something the old
    # generated_at-based WHERE happened to imply.
    assert "generated_at >=" not in captured["sql"]
    assert "status = 'generated'" in captured["sql"]
    assert captured["params"] == ()


def test_window_30d_adds_a_since_filter_alongside_the_status_filter(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(m, "fetch_all", fake_fetch_all)
    m.compute_admin_analytics("30d")

    assert "status = 'generated'" in captured["sql"]
    assert "generated_at >= %s" in captured["sql"]
    assert len(captured["params"]) == 1
    assert isinstance(captured["params"][0], datetime)


def test_unknown_window_falls_back_to_all_via_the_endpoint(monkeypatch):
    monkeypatch.setattr(m, "fetch_all", lambda sql, params: [])
    result = asyncio.run(m.admin_analytics(window="bogus"))
    assert result["window"] == "all"
