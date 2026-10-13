"""
Admin -> Restrictions: categories and extra rules an admin adds directly in the app. Roadblocks' AI reads them like a
sheet tab, Step 07 offers them as choices, and a sheet sync never deletes them.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import pytest
from fastapi import HTTPException

from app.services import restrictions as rs

APP = Path(__file__).resolve().parent.parent / "app"


# --- validation -------------------------------------------------------------------------------------------------------------

def test_a_guidance_category_needs_a_name_and_details_and_gets_sensible_defaults():
    f = rs.clean_manual_fields(name="  Alcohol  ", kind="guidance", body="Alcohol ads need a TTB label.\nNo minors.")
    assert f["name"] == "Alcohol" and f["kind"] == "guidance" and f["keywords"] == ["alcohol"]
    assert f["summary"] == "Alcohol ads need a TTB label." and f["items"] == []
    with pytest.raises(ValueError, match="name"):
        rs.clean_manual_fields(name=" ", kind="guidance", body="x")
    with pytest.raises(ValueError, match="details"):
        rs.clean_manual_fields(name="X", kind="guidance")


def test_a_not_accepted_category_needs_a_reason_and_an_allowlist_needs_products():
    f = rs.clean_manual_fields(name="Firearms", kind="blanket_hold", summary="Entravision does not accept firearms ads.")
    assert f["kind"] == "blanket_hold" and f["body"] == f["summary"]
    with pytest.raises(ValueError, match="why"):
        rs.clean_manual_fields(name="Firearms", kind="blanket_hold")
    f = rs.clean_manual_fields(name="Loans", kind="allowlist", items="eDigital Display\n\n  Email Marketing Campaigns \neDigital Display")
    assert f["items"] == ["eDigital Display", "Email Marketing Campaigns"]                    # blanks and repeats dropped
    assert "only accepted for the products listed below" in f["summary"] and "eDigital Display" in f["body"]
    with pytest.raises(ValueError, match="approved product"):
        rs.clean_manual_fields(name="Loans", kind="allowlist", items="")


def test_the_type_keywords_and_lengths_are_checked_and_control_characters_removed():
    with pytest.raises(ValueError, match="Type"):
        rs.clean_manual_fields(name="X", kind="whatever", body="x")
    with pytest.raises(ValueError, match="too long"):
        rs.clean_manual_fields(name="N" * 200, kind="guidance", body="x")
    f = rs.clean_manual_fields(name="Wine\x00", kind="guidance", body="Rule\x07 one", keywords="Wine, BEER ,  wine")
    assert f["name"] == "Wine" and f["body"] == "Rule one" and f["keywords"] == ["wine", "beer"]
    assert rs.clean_notes("  a\x00b\r\nc ") == "ab\nc"
    with pytest.raises(ValueError, match="too long"):
        rs.clean_notes("x" * (rs.MAX_NOTES_CHARS + 1))


# --- what the AI reads ------------------------------------------------------------------------------------------------------------

def _matrix(cats):
    return {"categories": cats, "verdicts": {"Search - SEM": {"checks": [
        {"category": c.name, "status": "guidance", "note": c.summary} for c in cats]}}, "using_builtin": False, "synced_at": None}


def test_a_hand_added_category_and_extra_notes_are_in_the_roadblocks_prompt_marked_as_such():
    alcohol = rs.Category(name="Alcohol", kind="guidance", keywords=["alcohol"], summary="TTB rules apply.",
                          body="Ads need the TTB responsible-drinking line.", is_manual=True)
    political = rs.builtin_categories()[0]
    political.extra_notes = "Always get the committee's disclaimer text in writing."
    block = rs.matrix_prompt_block(_matrix([alcohol, political]))
    assert "plus rules an Entravision admin added in the app" in block
    assert "### Alcohol (added in the app by an Entravision admin)\nAds need the TTB responsible-drinking line." in block
    assert "### Political\n" in block and "(added in the app" not in block.split("### Political")[1].split("Additional rules")[0]
    assert "Additional rules an Entravision admin added for Political" in block and "committee's disclaimer text" in block


def test_a_plain_sheet_matrix_prompt_is_unchanged():
    block = rs.matrix_prompt_block(_matrix(rs.builtin_categories()))
    assert "added in the app" not in block and "Additional rules" not in block and "admin added" not in block


def test_a_hand_added_not_accepted_category_reads_not_accepted_for_every_product():
    cat = rs.Category(name="Firearms", kind="blanket_hold", summary="Entravision does not accept firearms ads.", is_manual=True)
    verdict = rs.evaluate_product("Search - SEM", "Search", [cat])
    assert verdict["verdict"] == "not_allowed" and "firearms" in verdict["checks"][0]["note"]


def test_a_hand_added_approved_products_list_is_judged_like_a_sheet_allowlist():
    cat = rs.Category(name="Loans", kind="allowlist", items=["Search listing"], summary="s", is_manual=True,
                      product_map={"Search listing": ["Search - SEM"]})
    assert rs.evaluate_product("Search - SEM", "Search", [cat])["verdict"] == "allowed"
    assert rs.evaluate_product("eDigital Display", "Display", [cat])["verdict"] == "not_allowed"


def test_keywords_of_a_hand_added_category_suggest_it():
    cat = rs.Category(name="Alcohol", kind="guidance", keywords=["alcohol", "wine"], is_manual=True)
    assert rs.suggest_categories("Winery launching a new wine club", [cat]) == ["Alcohol"]
    assert rs.suggest_categories("A bakery", [cat]) == []


# --- reading the stored rows ----------------------------------------------------------------------------------------------------

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _row(name, kind="guidance", manual=False, notes="", **kw):
    base = {"name": name, "kind": kind, "keywords": [name.lower()], "items": [], "summary": "s", "body": "b", "product_map": {}}
    base.update(kw)
    base["is_manual"], base["extra_notes"] = manual, notes
    return base


class _Conn:
    def __init__(self, rows, modern=True, sync=None):
        self.rows, self.modern, self.sync, self.selects = rows, modern, sync, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        if "information_schema.columns" in q:
            return _Result([{"ok": 1}] if self.modern else [])
        if q.startswith("SELECT name, kind"):
            self.selects.append(q)
            if not self.modern:
                rows = [{k: v for k, v in r.items() if k not in ("is_manual", "extra_notes")} for r in self.rows]
                return _Result(rows)
            return _Result(self.rows)
        if "FROM restriction_sync" in q:
            return _Result([self.sync] if self.sync else [])
        raise AssertionError(q[:80])


@pytest.fixture(autouse=True)
def _reset_column_probe():
    rs._manual_columns_known = False
    yield
    rs._manual_columns_known = False


def test_only_hand_added_rows_still_leave_the_built_in_snapshot_in_place(monkeypatch):
    conn = _Conn([_row("Alcohol", manual=True), _row("Political", manual=True)])       # a clash with a built-in name is dropped
    monkeypatch.setattr(rs, "get_connection", lambda: conn)
    state = rs.load_state()
    names = [c.name for c in state["categories"]]
    assert names[:2] == ["Political", "Cannabis"] and "Alcohol" in names and names.count("Political") == 1
    assert state["using_builtin"] is True and state["sync"] is None and state["error"] is False
    assert next(c for c in state["categories"] if c.name == "Alcohol").is_manual


def test_sheet_rows_plus_hand_added_rows_are_all_returned(monkeypatch):
    sync = {"sheet_url": "u", "source_title": "t", "synced_at": datetime.now(timezone.utc), "synced_by": "a"}
    conn = _Conn([_row("Alcohol", manual=True), _row("Casino"), _row("Political", "allowlist", notes="note")], sync=sync)
    monkeypatch.setattr(rs, "get_connection", lambda: conn)
    state = rs.load_state()
    assert [c.name for c in state["categories"]] == ["Alcohol", "Casino", "Political"] and state["using_builtin"] is False
    assert {c.name: c.extra_notes for c in state["categories"]}["Political"] == "note"
    assert state["sync"]["source_title"] == "t"


def test_before_the_new_columns_exist_everything_reads_as_it_always_did(monkeypatch):
    conn = _Conn([_row("Casino"), _row("Political", "allowlist")], modern=False)
    monkeypatch.setattr(rs, "get_connection", lambda: conn)
    state = rs.load_state()
    assert [c.name for c in state["categories"]] == ["Casino", "Political"] and not any(c.is_manual for c in state["categories"])
    assert "is_manual" not in conn.selects[0]                      # the old column list was used


def test_a_sync_carries_extra_notes_and_keywords_to_a_tab_of_the_same_name():
    previous = [rs.Category(name="Alcohol", kind="guidance", keywords=["booze"], extra_notes="Mind the TTB.", is_manual=True)]
    new = [rs.Category(name="alcohol", kind="guidance", keywords=["alcohol"], body="sheet text")]
    rs.carry_over_admin_edits(new, previous)
    assert new[0].extra_notes == "Mind the TTB." and new[0].keywords == ["booze"] and not new[0].is_manual


def test_creating_a_category_checks_names_against_the_sheet_the_built_ins_and_other_hand_added_ones(monkeypatch):
    monkeypatch.setattr(rs, "load_state", lambda: {"categories": rs.builtin_categories(), "using_builtin": True, "sync": None, "error": False})
    with pytest.raises(ValueError, match="already a category called"):
        rs.create_manual_category(name="political", kind="guidance", body="x")
    monkeypatch.setattr(rs, "load_state", lambda: {"categories": [], "using_builtin": True, "sync": None, "error": True})
    with pytest.raises(RuntimeError, match="Couldn't read"):
        rs.create_manual_category(name="Alcohol", kind="guidance", body="x")


def test_without_the_new_columns_a_write_says_how_to_fix_it(monkeypatch):
    monkeypatch.setattr(rs, "load_state", lambda: {"categories": [], "using_builtin": True, "sync": None, "error": False})
    monkeypatch.setattr(rs, "get_connection", lambda: _Conn([], modern=False))
    with pytest.raises(ValueError, match="restart the app"):
        rs.create_manual_category(name="Alcohol", kind="guidance", body="x")
    with pytest.raises(ValueError, match="restart the app"):
        rs.delete_manual_category("Alcohol")
    with pytest.raises(ValueError, match="restart the app"):
        rs.update_category("Political", keywords="political", product_map={}, extra_notes="x")


# --- the admin endpoints ------------------------------------------------------------------------------------------------------------

def test_the_create_and_delete_endpoints_map_errors_to_http_statuses(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m, "_restriction_admin_payload", lambda warnings=None: {"warnings": warnings or [], "categories": []})
    cat = rs.Category(name="Alcohol", kind="guidance", is_manual=True)
    monkeypatch.setattr(m.restrictions_svc, "create_manual_category", lambda **kw: (cat, ["map me"]))
    out = asyncio.run(m.admin_create_restriction(m.RestrictionCreateRequest(name="Alcohol", body="x")))
    assert out["created"] == "Alcohol" and out["warnings"] == ["map me"]

    def refuse(**kw):
        raise ValueError("There is already a category called \"Alcohol\".")
    monkeypatch.setattr(m.restrictions_svc, "create_manual_category", refuse)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.admin_create_restriction(m.RestrictionCreateRequest(name="Alcohol", body="x")))
    assert exc.value.status_code == 400

    def down(**kw):
        raise RuntimeError("Couldn't read the restrictions table")
    monkeypatch.setattr(m.restrictions_svc, "create_manual_category", down)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.admin_create_restriction(m.RestrictionCreateRequest(name="Alcohol", body="x")))
    assert exc.value.status_code == 503

    monkeypatch.setattr(m.restrictions_svc, "delete_manual_category", lambda name: True)
    assert asyncio.run(m.admin_delete_restriction("Alcohol"))["deleted"] is True
    monkeypatch.setattr(m.restrictions_svc, "delete_manual_category", lambda name: False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.admin_delete_restriction("Nope"))
    assert exc.value.status_code == 404

    def sheet_tab(name):
        raise ValueError("That category comes from the sheet.")
    monkeypatch.setattr(m.restrictions_svc, "delete_manual_category", sheet_tab)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.admin_delete_restriction("Political"))
    assert exc.value.status_code == 400


def test_a_sync_reports_a_hand_added_category_a_sheet_tab_took_over(monkeypatch):
    import app.main as m
    sheet = [rs.Category(name="Alcohol", kind="guidance", body="from the sheet")]
    monkeypatch.setattr(m.restrictions_svc, "parse_workbook_ex", lambda data: (sheet, ["a warning"]))
    monkeypatch.setattr(m.restrictions_svc, "load_state", lambda: {
        "categories": [rs.Category(name="Alcohol", kind="guidance", is_manual=True, extra_notes="mine")], "using_builtin": True,
        "sync": None, "error": False})
    stored = {}
    monkeypatch.setattr(m.restrictions_svc, "replace_all", lambda cats, **kw: stored.update(cats=cats) or ["Alcohol"])
    warnings = m._ingest_restrictions(b"x", "https://sheet", "Title", "admin@x")
    assert warnings[0] == "a warning" and "replaced the category of that name you had added by hand" in warnings[1]
    assert stored["cats"][0].extra_notes == "mine"                 # its notes came along even though the built-in snapshot was in use


def test_the_chooser_list_says_which_categories_were_added_by_hand(monkeypatch):
    import app.main as m
    from app.services.notion_parser import ProposalRequest
    cats = [rs.Category(name="Political", kind="allowlist", summary="s"), rs.Category(name="Alcohol", kind="guidance", summary="s", is_manual=True)]
    monkeypatch.setattr(m.restrictions_svc, "load_state", lambda: {"categories": cats, "using_builtin": True, "sync": None, "error": False})
    monkeypatch.setattr(m, "_reconstruct_proposal_request", lambda d: ProposalRequest())
    monkeypatch.setattr(m, "_refresh_restrictions_if_stale", lambda: False)

    class _Body:
        request = {}
        product_names = []
    out = asyncio.run(m.restriction_categories(_Body()))
    assert [(c["name"], c["manual"]) for c in out["categories"]] == [("Political", False), ("Alcohol", True)]


# --- the admin page ------------------------------------------------------------------------------------------------------------------

def test_the_restrictions_tab_has_the_add_form_extra_notes_and_hand_added_editing():
    html = (APP / "templates" / "admin.html").read_text(encoding="utf-8")
    js = (APP / "static" / "admin.js").read_text(encoding="utf-8")
    for needle in ('id="rs-add-form"', 'id="rs-add-name"', 'id="rs-add-kind"', 'id="rs-add-keywords"', 'id="rs-add-body"', 'id="rs-add-items"'):
        assert needle in html
    for option in ('value="guidance"', 'value="blanket_hold"', 'value="allowlist"'):
        assert option in html
    for needle in ("data-rs-notes", "data-rs-delete", "data-rs-manual", "wireAddRestriction", 'method: "POST"', "/api/admin/restrictions"):
        assert needle in js


def test_editing_a_built_in_category_persists_the_snapshot_first_but_editing_a_hand_added_one_does_not(monkeypatch):
    import app.main as m

    class _Req:
        class state:
            user = {"email": "admin@entravision.com"}
    cats = rs.builtin_categories() + [rs.Category(name="Alcohol", kind="guidance", is_manual=True)]
    monkeypatch.setattr(m.restrictions_svc, "load_state", lambda: {"categories": cats, "using_builtin": True, "sync": None, "error": False})
    seeded = []
    monkeypatch.setattr(m.restrictions_svc, "seed_builtin_into_db", lambda by: seeded.append(by))
    monkeypatch.setattr(m.restrictions_svc, "update_category", lambda name, **kw: rs.Category(name=name, kind="guidance", is_manual=name == "Alcohol"))
    monkeypatch.setattr(m, "_restriction_admin_payload", lambda warnings=None: {"warnings": warnings or []})
    body = m.RestrictionUpdateRequest(keywords="x", extra_notes="Ask Legal.")
    asyncio.run(m.admin_update_restriction("Political", body, _Req()))
    assert seeded == ["admin@entravision.com"]
    asyncio.run(m.admin_update_restriction("Alcohol", body, _Req()))
    assert seeded == ["admin@entravision.com"]                     # unchanged: a stored hand-added row needs no seeding
