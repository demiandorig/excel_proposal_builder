"""
Admin -> Markets: a per-market list of research assignees, CC'd on the Step 07 seller email for that market along with
the DSC, the DSM and the market's own CC list.
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import pytest
from fastapi import HTTPException

from app import market_config as mc

ROOT = Path(__file__).resolve().parent.parent


def _config(**markets):
    base = {
        mc.BASE_CCS_KEY: ["salesplanning@entravision.com"],
        mc.T1_CCS_KEY: ["jwoods@entravision.com"],
        mc.DEFAULT_KEY: {"address_line1": "1 Estrella Way", "address_line2": "Burbank", "dsc_email": None, "dsm_email": None,
                         "research_emails": [], "ccs": []},
    }
    base.update(markets)
    return base


# --- who gets CC'd -------------------------------------------------------------------------------------------------------------

def test_a_markets_research_assignees_are_cc_d_with_the_base_ccs_market_ccs_dsc_and_dsm(monkeypatch):
    monkeypatch.setattr(mc, "load_market_config", lambda: _config(Denver={
        "address_line1": "x", "address_line2": "y", "dsc_email": "dsc@entravision.com", "dsm_email": "dsm@entravision.com",
        "research_emails": ["r1@entravision.com", "R2@entravision.com"], "ccs": ["cc1@entravision.com"]}))
    assert mc.get_all_ccs_for_market("Denver") == [
        "salesplanning@entravision.com", "cc1@entravision.com", "dsc@entravision.com", "dsm@entravision.com",
        "r1@entravision.com", "R2@entravision.com"]


def test_a_research_assignee_who_is_already_on_the_list_appears_once_whatever_the_case(monkeypatch):
    monkeypatch.setattr(mc, "load_market_config", lambda: _config(Denver={
        "dsc_email": "dsc@entravision.com", "research_emails": ["DSC@entravision.com", "Salesplanning@entravision.com", "r@entravision.com"],
        "ccs": []}))
    assert mc.get_all_ccs_for_market("Denver") == ["salesplanning@entravision.com", "dsc@entravision.com", "r@entravision.com"]


def test_a_market_without_its_own_research_assignees_uses_the_defaults_like_the_dsc_and_dsm_do(monkeypatch):
    config = _config(Denver={"dsc_email": "d@entravision.com", "research_emails": [], "ccs": []})
    config[mc.DEFAULT_KEY]["research_emails"] = ["default-research@entravision.com"]
    monkeypatch.setattr(mc, "load_market_config", lambda: config)
    assert mc.get_market_research_emails("Denver") == ["default-research@entravision.com"]          # blank: the default's
    assert mc.get_market_research_emails("Somewhere Unconfigured") == ["default-research@entravision.com"]
    config["Denver"]["research_emails"] = ["own@entravision.com"]
    assert mc.get_market_research_emails("Denver (Tier 1)") == ["own@entravision.com"]              # its own wins, and the match is by name
    assert "default-research@entravision.com" not in mc.get_all_ccs_for_market("Denver")


def test_the_public_cc_lookup_carries_them_to_the_seller_email(monkeypatch):
    import app.main as m
    monkeypatch.setattr(mc, "load_market_config", lambda: _config(Denver={"research_emails": ["r@entravision.com"], "ccs": []}))
    out = asyncio.run(m.get_market_ccs_for("Denver"))
    assert "r@entravision.com" in out["ccs"] and out["t1_ccs"] == ["jwoods@entravision.com"]


# --- what an admin types ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("a@x.com, b@x.com", ["a@x.com", "b@x.com"]),
    ("a@x.com; b@x.com\nc@x.com", ["a@x.com", "b@x.com", "c@x.com"]),
    (["a@x.com", " A@X.com ", "b@x.com,c@x.com"], ["a@x.com", "b@x.com", "c@x.com"]),
    ("", []), (None, []), ([], []), ("  ,  ;  ", []),
])
def test_emails_are_split_trimmed_and_deduplicated(raw, expected):
    assert mc.normalize_emails(raw) == expected


@pytest.mark.parametrize("bad", ["not an email", "a@b", "@x.com", "a@@x.com", "Name <a@x.com>", "a b@x.com", "a@x.com, oops"])
def test_something_that_cannot_be_an_address_is_refused_with_its_text(bad):
    with pytest.raises(ValueError, match="doesn't look like an email address"):
        mc.normalize_emails(bad)


# --- storage ---------------------------------------------------------------------------------------------------------------------

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, rows, modern=True):
        self.rows, self.modern, self.selects, self.inserts, self.deleted = rows, modern, [], [], False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        if "information_schema.columns" in q:
            return _Result([{"ok": 1}] if self.modern else [])
        if q.startswith("SELECT market_key"):
            self.selects.append(q)
            return _Result([{k: v for k, v in r.items() if self.modern or k != "research_emails"} for r in self.rows])
        if q.startswith("DELETE FROM market_config"):
            self.deleted = True
            return _Result([])
        if q.startswith("INSERT INTO market_config"):
            self.inserts.append((q, params))
            return _Result([])
        raise AssertionError(q[:80])


@pytest.fixture(autouse=True)
def _reset_column_probe():
    mc._research_column_known = False
    yield
    mc._research_column_known = False


def _row(key, **kw):
    base = {"market_key": key, "address_line1": "a", "address_line2": "b", "dsc_email": None, "dsm_email": None, "ccs": [],
            "research_emails": []}
    base.update(kw)
    return base


def test_the_loader_reads_research_assignees_when_the_column_exists(monkeypatch):
    conn = _Conn([_row("Denver", research_emails=["r@entravision.com"]), _row(mc.BASE_CCS_KEY, ccs=["base@entravision.com"])])
    monkeypatch.setattr(mc, "get_connection", lambda: conn)
    config = mc.load_market_config()
    assert config["Denver"]["research_emails"] == ["r@entravision.com"]
    assert config[mc.BASE_CCS_KEY] == ["base@entravision.com"]
    assert config[mc.DEFAULT_KEY]["research_emails"] == []                  # the built-in fallback has the key too
    assert "research_emails" in conn.selects[0]


def test_before_the_column_exists_everything_loads_and_saves_as_it_always_did(monkeypatch):
    conn = _Conn([_row("Denver", ccs=["c@entravision.com"])], modern=False)
    monkeypatch.setattr(mc, "get_connection", lambda: conn)
    config = mc.load_market_config()
    assert config["Denver"]["research_emails"] == [] and config["Denver"]["ccs"] == ["c@entravision.com"]
    assert "research_emails" not in conn.selects[0]
    mc.save_market_config(config)
    assert conn.deleted and all("research_emails" not in sql for sql, _ in conn.inserts)
    assert mc.research_column_available() is False


def test_saving_writes_the_assignees_when_the_column_exists(monkeypatch):
    conn = _Conn([_row("Denver")])
    monkeypatch.setattr(mc, "get_connection", lambda: conn)
    mc.save_market_config({"Denver": {"address_line1": "x", "research_emails": ["r@entravision.com"], "ccs": []},
                           mc.BASE_CCS_KEY: ["base@entravision.com"]})
    denver = next(params for sql, params in conn.inserts if params[0] == "Denver")
    assert denver[-1] == ["r@entravision.com"]
    base = next(params for sql, params in conn.inserts if params[0] == mc.BASE_CCS_KEY)
    assert base[-1] == []                                                   # the reserved CC rows never carry assignees


def test_saving_assignees_without_the_column_says_how_to_fix_it_instead_of_dropping_them(monkeypatch):
    conn = _Conn([_row("Denver")], modern=False)
    monkeypatch.setattr(mc, "get_connection", lambda: conn)
    with pytest.raises(ValueError, match="restart the app"):
        mc.set_market_entry("Denver", {"research_emails": ["r@entravision.com"]})
    assert not conn.inserts
    mc.set_market_entry("Denver", {"ccs": ["c@entravision.com"]})          # a save that doesn't touch them still works
    assert conn.inserts


def test_a_partial_save_keeps_the_assignees_it_does_not_mention(monkeypatch):
    conn = _Conn([_row("Denver", research_emails=["r@entravision.com"], dsc_email="d@entravision.com")])
    monkeypatch.setattr(mc, "get_connection", lambda: conn)
    entry = mc.set_market_entry("Denver", {"dsm_email": "m@entravision.com"})
    assert entry["research_emails"] == ["r@entravision.com"] and entry["dsc_email"] == "d@entravision.com"
    saved = next(params for sql, params in conn.inserts if params[0] == "Denver")
    assert saved[-1] == ["r@entravision.com"]


# --- the admin API --------------------------------------------------------------------------------------------------------------

def test_the_markets_endpoints_return_save_and_export_research_assignees(monkeypatch):
    import app.main as m
    store = _config(Denver={"address_line1": "x", "address_line2": "y", "dsc_email": "", "dsm_email": "",
                            "research_emails": ["r1@entravision.com", "r2@entravision.com"], "ccs": ["c@entravision.com"]})
    monkeypatch.setattr(m, "load_market_config", lambda: store)
    out = asyncio.run(m.admin_get_market_config())
    denver = next(x for x in out["markets"] if x["market_key"] == "Denver")
    assert denver["research_emails"] == ["r1@entravision.com", "r2@entravision.com"]
    assert next(x for x in out["markets"] if x["is_default"])["research_emails"] == []

    rows = list(csv.DictReader(io.StringIO(asyncio.run(m.admin_export_market_config()).body.decode("utf-8"))))
    assert "research_emails" in rows[0]
    assert next(r for r in rows if r["market_key"] == "Denver")["research_emails"] == "r1@entravision.com; r2@entravision.com"

    seen = {}
    monkeypatch.setattr(m, "set_market_entry", lambda key, fields: seen.update(key=key, fields=fields) or fields)
    asyncio.run(m.admin_save_market_config(m.MarketConfigRequest(market_key="Denver", research_emails=[" R1@entravision.com", "r1@entravision.com", "r3@entravision.com"])))
    assert seen["fields"] == {"research_emails": ["R1@entravision.com", "r3@entravision.com"]}       # cleaned, de-duplicated; nothing else touched
    seen.clear()
    asyncio.run(m.admin_save_market_config(m.MarketConfigRequest(market_key="Denver", dsc_email="d@entravision.com")))
    assert "research_emails" not in seen["fields"]                                                   # absent = left alone
    seen.clear()
    asyncio.run(m.admin_save_market_config(m.MarketConfigRequest(market_key="Denver", research_emails=[])))
    assert seen["fields"] == {"research_emails": []}                                                  # an explicit empty list clears them


def test_a_bad_address_or_a_missing_column_is_a_400_not_a_500(monkeypatch):
    import app.main as m
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.admin_save_market_config(m.MarketConfigRequest(market_key="Denver", research_emails=["not an email"])))
    assert exc.value.status_code == 400 and "doesn't look like an email address" in exc.value.detail

    def no_column(key, fields):
        raise ValueError("The database doesn't have the research-assignees column yet - restart the app")
    monkeypatch.setattr(m, "set_market_entry", no_column)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.admin_save_market_config(m.MarketConfigRequest(market_key="Denver", research_emails=["r@entravision.com"])))
    assert exc.value.status_code == 400 and "restart the app" in exc.value.detail


# --- the page and the schema ----------------------------------------------------------------------------------------------------

def test_the_markets_tab_has_the_research_assignees_column_and_saves_it_as_a_list():
    html = (ROOT / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
    js = (ROOT / "app" / "static" / "admin.js").read_text(encoding="utf-8")
    assert "Research Assignees" in html and html.count('colspan="8"') >= 1
    assert 'data-field="research_emails"' in js
    assert 'inp.dataset.field === "research_emails"' in js
    assert "research assignees" in html.lower() and "CC'd" in html


def test_schema_sql_adds_the_column_idempotently_so_the_startup_bootstrap_creates_it():
    from app.services import schema_bootstrap as sb
    targets = {p["target"] for p in sb.idempotent_statements((ROOT / "schema.sql").read_text(encoding="utf-8"))}
    assert "market_config.research_emails" in targets
