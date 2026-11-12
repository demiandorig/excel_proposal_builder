"""POST /api/proposal/{id}/duplicate and GET /api/proposal/{id}/duplicate-defaults, and the request model behind them, offline.

Same pattern as tests/test_draft_save.py and tests/test_constanza_snapshot_compat.py: the route functions are called directly with a fake request
and the two database helpers they use (`_get_proposal_metadata`, `_insert_draft_if_absent`) replaced by a small in-memory store that behaves like the
table (a draft inserted once, an insert of a taken id refused, JSON handed back with its keys re-ordered the way Postgres does). The same routes are
also driven over HTTP through the real app for the login rule, the route table and the 422 shape. The SQL itself is tests/test_duplicate_db.py.

  who may copy        owner 200 / other planner 403 / admin 200 (the copy is the admin's) / missing 404 / saved before reopening existed 410 / no request 409
  what is stored      a DRAFT: the form's client, Notion ID and seller, the title built by the app's own naming function, the lineage and the notes
  the same request    twice: the first draft again (`created: false`), one insert; racing: the winner's draft; someone else's row at that id: 409
  a changed form      is a new copy, never the first draft with stale values
  failures            an unreachable database is a 503 in words, a bug in the transform a 500 that wrote nothing and logged no client name
  no AI               nothing on this path calls an AI or Gamma service
  the model           every invalid input and its exact message, and what is normalised
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import main
from app.services import proposal_duplicate as pd
from tests.fixtures import duplicate_sources as fx

PLANNER = "planner@entravision.com"
ADMIN = "admin@entravision.com"
SOURCE_ID = "SourceProposalId0001"
REQUEST_ID = "dialog-request-0123456789"


# ====================================================================================================================== harness

def jsonb(value):
    """What Postgres's JSONB column hands back: every object's keys re-ordered (shorter keys first, then by bytes). Nothing may depend on key order."""
    def order(pairs):
        return dict(sorted(pairs, key=lambda kv: (len(kv[0].encode("utf-8")), kv[0].encode("utf-8"))))
    return json.loads(json.dumps(value), object_pairs_hook=order)


def fake_request(email: str = PLANNER, *, admin: bool = False, agent: str = "pytest") -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user={"email": email, "is_admin": admin}), client=SimpleNamespace(host="10.0.0.7"), headers={"user-agent": agent})


def source_row(state=None, *, owner=PLANNER, status="draft", **extra) -> dict:
    row = {
        "proposal_id": SOURCE_ID, "client_name": "Acme Motors", "seller_email": "ana@entravision.com", "created_by_email": owner, "requested_by": "Ana Torres",
        "notion_id": "EVC-48213", "proposal_title": "EVC-48213 | Acme Motors - Fall Push | Entravision | Sep26 | Digital Media Proposal",
        "filename": None, "email_doc_filename": None, "pptx_net_filename": None, "pptx_gross_filename": None, "generated_at": None, "requester_ip": "1.2.3.4",
        "requester_user_agent": "UA", "summary": {}, "reopen_state": jsonb(fx.source_monthly() if state is None else state), "status": status,
        "updated_at": datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc),
    }
    row.update(extra)
    return row


class Store:
    """The proposals table as the routes see it: `_get_proposal_metadata` (a fresh copy of a row, as a real read gives) and `_insert_draft_if_absent`."""

    def __init__(self, *rows: dict):
        self.rows = {row["proposal_id"]: row for row in rows}
        self.inserts: list[dict] = []
        self.reads: list[str] = []

    def get(self, proposal_id):
        self.reads.append(proposal_id)
        row = self.rows.get(proposal_id)
        return main._proposal_metadata_from_row(copy.deepcopy(row)) if row else None

    def insert(self, **kw) -> bool:
        self.inserts.append(copy.deepcopy(kw))
        if kw["proposal_id"] in self.rows:
            return False
        self.rows[kw["proposal_id"]] = {**kw, "reopen_state": jsonb(kw["reopen_state"]), "status": "draft", "summary": {}, "filename": None,
                                        "updated_at": datetime.now(timezone.utc)}
        return True


@pytest.fixture
def store(monkeypatch):
    s = Store(source_row())
    monkeypatch.setattr(main, "_get_proposal_metadata", s.get)
    monkeypatch.setattr(main, "_insert_draft_if_absent", s.insert)
    return s


@pytest.fixture
def no_ai(monkeypatch):
    """Anything that would reach an AI model, Gamma, or the web raises: the duplicate path must never get there."""
    def boom(*a, **k):
        raise AssertionError("an AI / Gamma / web service was called while duplicating a proposal")
    for module, names in ((main.ai_enricher, ("enrich_proposal", "reprompt_emails", "refine_gamma_outline")), (main.strategy_brief_svc, ("generate_brief",)),
                          (main.roadblocks_svc, ("generate_roadblocks",)), (main.ad_presence_svc, ("check_meta", "check_google", "check_tiktok")),
                          (main.gamma_client, ("start_generation", "get_generation")), (main.llm_utils, ("responses_text", "chat_text"))):
        for name in names:
            monkeypatch.setattr(module, name, boom)


def form(**over) -> main.DuplicateProposalRequest:
    fields = dict(request_id=REQUEST_ID, client_name="Beta Burgers", start_date="2027-02-01", end_date="2027-04-30", notion_id="50001", campaign_name="Spring Push")
    fields.update(over)
    return main.DuplicateProposalRequest(**fields)


def duplicate(body=None, request=None, source_id=SOURCE_ID):
    return asyncio.run(main.duplicate_proposal(source_id, body or form(), request or fake_request()))


def defaults(request=None, source_id=SOURCE_ID):
    return asyncio.run(main.duplicate_defaults(source_id, request or fake_request()))


def refused(call, *args, **kwargs) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        call(*args, **kwargs)
    return caught.value


# ====================================================================================================================== who may copy what

def test_the_owner_gets_a_copy_with_the_documented_answer(store, no_ai):
    answer = duplicate()
    new_id = answer["proposal_id"]
    assert answer["created"] is True and len(new_id) == 22 and new_id != SOURCE_ID and new_id in store.rows
    assert answer["open_url"] == f"/?reopen={new_id}" and answer["wizard_step"] == 2
    assert answer["proposal_title"] == "EVC-50001 | Beta Burgers - Spring Push | Entravision | Feb27 | Digital Media Proposal"
    assert answer["duplicated_from"] == {"proposal_id": SOURCE_ID, "title": store.rows[SOURCE_ID]["proposal_title"], "client_name": "Acme Motors"}
    assert {n["code"] for n in answer["notes"]} >= {"dates_moved", "total_changed", "avails_cleared", "ai_dropped"}
    assert answer["stats"] == {"totals_before": {"A": 69000.0, "B": 32000.0}, "totals_after": {"A": 69000.0, "B": 24000.0}, "periods": 3, "time_unit": "month"}
    assert set(answer) == {"proposal_id", "created", "proposal_title", "open_url", "wizard_step", "duplicated_from", "notes", "stats"}


def test_another_planner_may_not_copy_it_and_nothing_is_written(store):
    error = refused(duplicate, request=fake_request("someone.else@entravision.com"))
    assert (error.status_code, error.detail) == (403, "You can only duplicate your own proposals.")
    assert store.inserts == []


def test_the_owner_check_ignores_the_case_of_the_email(store):
    assert duplicate(request=fake_request("Planner@Entravision.COM"))["created"] is True


def test_an_admin_may_copy_a_planners_proposal_and_the_copy_is_the_admins(store):
    answer = duplicate(request=fake_request(ADMIN, admin=True))
    copy_row = store.rows[answer["proposal_id"]]
    assert copy_row["created_by_email"] == ADMIN
    assert store.rows[SOURCE_ID]["created_by_email"] == PLANNER                   # the planner's proposal is untouched
    assert copy_row["reopen_state"]["duplicated_from"]["proposal_id"] == SOURCE_ID


def test_a_row_with_no_owner_is_nobodys_but_an_admins(store):
    store.rows[SOURCE_ID]["created_by_email"] = None
    assert refused(duplicate).status_code == 403
    assert duplicate(request=fake_request(ADMIN, admin=True))["created"] is True
    # an empty email never matches an empty owner
    assert refused(duplicate, request=fake_request("")).status_code == 403


def test_a_missing_proposal_is_a_404_in_plain_words(store):
    error = refused(duplicate, source_id="NoSuchProposal")
    assert (error.status_code, error.detail) == (404, "We couldn't find that proposal. It may have been deleted.")


def test_a_proposal_saved_before_reopening_existed_is_a_410(store):
    store.rows[SOURCE_ID]["reopen_state"] = None
    error = refused(duplicate)
    assert (error.status_code, error.detail) == (410, "This proposal was saved before copying was supported, so it can't be duplicated.")
    assert refused(defaults).status_code == 410
    store.rows[SOURCE_ID]["reopen_state"] = {}
    assert refused(duplicate).status_code == 410


@pytest.mark.parametrize("state", [{"line_items": []}, {"request": {}, "line_items": []}, {"request": "x"}, ["x"]])
def test_a_saved_state_with_no_request_details_is_a_409(store, state):
    store.rows[SOURCE_ID]["reopen_state"] = state
    error = refused(duplicate)
    assert (error.status_code, error.detail) == (409, "There's nothing to copy yet: this draft has no request details.")
    assert refused(defaults).status_code == 409


def test_the_checks_come_in_the_documented_order(store):
    store.rows[SOURCE_ID]["reopen_state"] = None
    assert refused(duplicate, request=fake_request("other@entravision.com")).status_code == 403            # not yours: 403 before 410
    assert refused(duplicate, source_id="Nope", request=fake_request("other@entravision.com")).status_code == 404


def test_a_generated_proposal_can_be_copied_too_and_its_files_are_not(store):
    store.rows[SOURCE_ID].update(status="generated", filename="x.xlsx", email_doc_filename="x.docx", pptx_net_filename="n.pptx", generated_at=datetime.now(timezone.utc))
    answer = duplicate()
    row = store.rows[answer["proposal_id"]]
    assert row["status"] == "draft" and row.get("filename") is None and "filename" not in store.inserts[0]
    assert store.rows[SOURCE_ID]["filename"] == "x.xlsx"


# ====================================================================================================================== what is stored

def test_the_stored_row_is_a_draft_with_the_forms_basics_and_the_lineage(store):
    answer = duplicate(form(salesperson_email="luis@entravision.com", requested_by="Luis Ortega", agency_name="Beta Media", agency_fee=0.1))
    assert len(store.inserts) == 1
    kw = store.inserts[0]
    assert set(kw) == {"proposal_id", "client_name", "seller_email", "created_by_email", "requested_by", "notion_id", "proposal_title", "reopen_state",
                       "requester_ip", "requester_user_agent"}                       # no filenames, no summary, no status: the insert makes a bare draft
    assert (kw["client_name"], kw["seller_email"], kw["created_by_email"], kw["requested_by"], kw["notion_id"]) == (
        "Beta Burgers", "luis@entravision.com", PLANNER, "Luis Ortega", "EVC-50001")
    assert (kw["requester_ip"], kw["requester_user_agent"]) == ("10.0.0.7", "pytest")
    assert kw["proposal_title"] == answer["proposal_title"]
    state = kw["reopen_state"]
    assert state["wizard_step"] == 2 and state["request"]["agency_name"] == "Beta Media" and state["request"]["agency_fee"] == 0.1
    lineage = state["duplicated_from"]
    assert set(lineage) == {"proposal_id", "title", "client_name", "at"} and lineage["proposal_id"] == SOURCE_ID and lineage["client_name"] == "Acme Motors"
    assert datetime.fromisoformat(lineage["at"]).tzinfo is not None
    assert [n["code"] for n in state["duplicate_notes"]] == [n["code"] for n in answer["notes"]]


def test_a_blank_notion_id_is_stored_as_null_and_the_title_says_so(store):
    answer = duplicate(form(notion_id="", campaign_name=""))
    assert store.inserts[0]["notion_id"] is None
    assert answer["proposal_title"] == "---- | Beta Burgers | Entravision | Feb27 | Digital Media Proposal"


def test_the_title_is_the_naming_functions_with_the_copys_request_type_and_start(store):
    from app.services import ai_enricher
    answer = duplicate(form(request_type="Quick Question", start_date="2027-06-20", end_date="2027-08-31"))
    assert answer["proposal_title"] == ai_enricher.build_proposal_title("EVC-50001", "Spring Push", "Quick Question", ref_date="2027-06-20", client_name="Beta Burgers")
    assert "Jun27" in answer["proposal_title"] and answer["proposal_title"].endswith("| Question")


def test_the_state_is_what_a_saved_draft_looks_like_plus_the_lineage_and_the_notes(store):
    duplicate()
    state = store.inserts[0]["reopen_state"]
    plain = {k: v for k, v in state.items() if k not in ("duplicated_from", "duplicate_notes")}
    draft = main.DraftSaveRequest(**plain)
    rebuilt = main._build_reopen_state(
        request_dict=draft.request, line_items=draft.line_items, tiers=draft.tiers, avails_data=draft.avails_data, strategy_brief=draft.strategy_brief,
        roadblocks=draft.roadblocks, force_tabs=draft.force_tabs, addons=draft.addons, raw_notion_text=draft.raw_notion_text, enrichment=draft.enrichment,
        time_unit=draft.time_unit, monthly_distribution_mode=draft.monthly_distribution_mode, campaign_name_override=draft.campaign_name_override,
        wizard_step=draft.wizard_step)
    assert rebuilt == plain
    assert state["strategy_brief"] is None and state["enrichment"] is None and state["raw_notion_text"] is None


def test_products_renamed_since_the_source_was_saved_come_across_under_their_current_name(store):
    source = fx.source_monthly()
    source["tiers"][0]["line_items"][2]["product_name"] = "YouTube Ads"
    source["request"]["products_selected"].append("YouTube Ads")
    store.rows[SOURCE_ID]["reopen_state"] = jsonb(source)
    duplicate()
    names = [li["product_name"] for t in store.inserts[0]["reopen_state"]["tiers"] for li in t["line_items"]]
    assert "YouTube Ads - Skippable" in names and "YouTube Ads" not in names
    assert "YouTube Ads - Skippable" in store.inserts[0]["reopen_state"]["request"]["products_selected"]


def test_a_product_that_left_the_catalog_is_listed_in_a_note_and_stays(store):
    source = fx.source_monthly()
    source["tiers"][0]["line_items"][2]["product_name"] = "A Product Nobody Sells Any More"
    store.rows[SOURCE_ID]["reopen_state"] = jsonb(source)
    answer = duplicate()
    assert any(n["code"] == "products_missing" and "A Product Nobody Sells Any More" in n["text"] for n in answer["notes"])
    assert "A Product Nobody Sells Any More" in json.dumps(store.inserts[0]["reopen_state"])


def test_a_row_handed_back_by_reference_is_not_changed_by_the_copy(monkeypatch):
    """_with_current_product_names renames products IN PLACE; the route must work on its own private copy of the state."""
    source = fx.source_monthly()
    source["tiers"][0]["line_items"][2]["product_name"] = "YouTube Ads"
    row = main._proposal_metadata_from_row(source_row(source))
    before = copy.deepcopy(row)
    monkeypatch.setattr(main, "_get_proposal_metadata", lambda pid: row if pid == SOURCE_ID else None)
    inserted = []
    monkeypatch.setattr(main, "_insert_draft_if_absent", lambda **kw: inserted.append(kw) or True)
    duplicate()
    assert row == before and row["reopen_state"]["tiers"][0]["line_items"][2]["product_name"] == "YouTube Ads"
    assert inserted[0]["reopen_state"]["tiers"][0]["line_items"][2]["product_name"] == "YouTube Ads - Skippable"


def test_the_source_row_is_only_read_never_written_or_changed(store):
    before = copy.deepcopy(store.rows[SOURCE_ID])
    duplicate()
    duplicate(form(client_name="Gamma Grill"))
    assert store.rows[SOURCE_ID] == before and {i["proposal_id"] for i in store.inserts}.isdisjoint({SOURCE_ID})


def test_the_log_line_has_counters_and_ids_but_no_names_or_emails(store, caplog):
    with caplog.at_level(logging.INFO, logger="app.main"):
        duplicate(form(client_name="Zanzibar Zeppelins", salesperson_email="zed@entravision.com"))
    lines = [r.getMessage() for r in caplog.records if "duplicate proposal" in r.getMessage()]
    assert len(lines) == 1 and lines[0].startswith(f"duplicate proposal source={SOURCE_ID} new=")
    assert "lines_reset=" in lines[0] and "texts_cleared=" in lines[0]
    everything = " ".join(r.getMessage() for r in caplog.records)
    for secret in ("Zanzibar", "zed@", "Acme", "planner@", "Beta"):
        assert secret not in everything


# ====================================================================================================================== the same request twice, racing requests, a changed form

def test_the_same_request_twice_returns_the_first_draft_and_inserts_once(store):
    first = duplicate()
    second = duplicate()
    assert first["created"] is True and second["created"] is False
    assert second["proposal_id"] == first["proposal_id"] and len(store.inserts) == 1
    assert {k: v for k, v in second.items() if k != "created"} == {k: v for k, v in first.items() if k != "created"}


def test_a_retry_by_a_planner_whose_email_has_capitals_still_finds_the_copy(store):
    """The copy stores the email as the login gave it; the replay check lowers both sides, so a retry is the same planner, not a stranger (a 409)."""
    mixed = "Ana.Torres@Entravision.COM"
    store.rows[SOURCE_ID]["created_by_email"] = mixed
    first = duplicate(request=fake_request(mixed))
    again = duplicate(request=fake_request(mixed))
    assert first["created"] is True and again["created"] is False and again["proposal_id"] == first["proposal_id"] and len(store.inserts) == 1
    lowered = duplicate(request=fake_request(mixed.lower()))                          # the id is derived from the lower-cased email: the same copy, found again
    assert lowered["created"] is False and lowered["proposal_id"] == first["proposal_id"] and len(store.inserts) == 1


def test_a_retry_after_the_copy_was_autosaved_still_finds_it_but_the_one_shot_notes_are_gone(store):
    first = duplicate()
    row = store.rows[first["proposal_id"]]
    saved = {k: v for k, v in row["reopen_state"].items() if k != "duplicate_notes"}            # the first save rebuilds the state and drops the notes
    row["reopen_state"] = saved
    again = duplicate()
    assert again["created"] is False and again["proposal_id"] == first["proposal_id"] and again["notes"] == []
    assert again["duplicated_from"]["client_name"] == "Acme Motors" and again["stats"]["totals_before"] == {"A": 69000.0, "B": 32000.0}


def test_a_changed_form_with_the_same_request_id_is_a_new_copy_not_the_first_with_stale_values(store):
    first = duplicate(form(client_name="Beta Burgers"))
    second = duplicate(form(client_name="Gamma Grill"))
    third = duplicate(form(client_name="Beta Burgers", end_date="2027-05-31"))
    assert len({first["proposal_id"], second["proposal_id"], third["proposal_id"]}) == 3 and len(store.inserts) == 3
    assert store.rows[second["proposal_id"]]["client_name"] == "Gamma Grill"


def test_a_new_request_id_is_a_new_copy(store):
    assert duplicate(form(request_id="another-dialog-0123456789"))["proposal_id"] != duplicate()["proposal_id"]


def test_a_different_caller_copying_the_same_source_gets_their_own_draft(store):
    store.rows[SOURCE_ID]["created_by_email"] = None
    mine = duplicate(request=fake_request(ADMIN, admin=True))
    other_admin = duplicate(request=fake_request("admin2@entravision.com", admin=True))
    assert mine["proposal_id"] != other_admin["proposal_id"]


def test_two_identical_requests_racing_each_other_answer_with_the_winners_draft(store, monkeypatch):
    """Both pass the "does the copy exist yet" read; the database lets only one insert win, and the loser answers with the winner's row."""
    real_insert = store.insert
    new_id = pd.derive_proposal_id(PLANNER, SOURCE_ID, REQUEST_ID, pd.DuplicateBasics(**form().model_dump(exclude={"request_id"})))
    winner = duplicate()                                         # the request that got there first (its row is now in the store)
    del store.rows[new_id]
    reads = {"n": 0}
    original_get = store.get

    def get_after_the_winner_lands(pid):
        if pid == new_id:
            reads["n"] += 1
            if reads["n"] == 1:
                return None                                       # this request looked before the winner inserted...
        return original_get(pid)

    def insert_loses_the_race(**kw):
        store.rows[new_id] = {**kw, "reopen_state": jsonb(kw["reopen_state"]), "status": "draft", "summary": {}, "filename": None, "updated_at": None}
        return False                                              # ...and its own insert found the winner's row already there

    monkeypatch.setattr(main, "_get_proposal_metadata", get_after_the_winner_lands)
    monkeypatch.setattr(main, "_insert_draft_if_absent", insert_loses_the_race)
    answer = duplicate()
    assert answer["created"] is False and answer["proposal_id"] == winner["proposal_id"] == new_id


def test_a_lost_race_whose_row_has_vanished_is_a_409_not_a_crash(store, monkeypatch):
    monkeypatch.setattr(main, "_insert_draft_if_absent", lambda **kw: False)
    error = refused(duplicate)
    assert (error.status_code, error.detail) == (409, "We couldn't create the copy. Please try again.")


def derived_id(email=PLANNER, **over) -> str:
    return pd.derive_proposal_id(email, SOURCE_ID, REQUEST_ID, pd.DuplicateBasics(**form(**over).model_dump(exclude={"request_id"})))


def test_someone_elses_row_at_the_derived_id_is_a_409_and_is_never_returned(store):
    store.rows[derived_id()] = source_row(owner="intruder@entravision.com", proposal_id=derived_id())
    store.rows[derived_id()]["reopen_state"]["duplicated_from"] = {"proposal_id": SOURCE_ID}
    error = refused(duplicate)
    assert (error.status_code, error.detail) == (409, "We couldn't create the copy. Please try again.") and store.inserts == []


def test_a_row_at_the_derived_id_that_is_not_a_copy_of_this_source_is_a_409(store):
    store.rows[derived_id()] = source_row(owner=PLANNER, proposal_id=derived_id())                                       # no lineage at all
    assert refused(duplicate).status_code == 409
    store.rows[derived_id()]["reopen_state"]["duplicated_from"] = {"proposal_id": "SomeOtherSource"}
    assert refused(duplicate).status_code == 409
    store.rows[derived_id()]["created_by_email"] = None
    store.rows[derived_id()]["reopen_state"]["duplicated_from"] = {"proposal_id": SOURCE_ID}
    assert refused(duplicate).status_code == 409                                                                         # an ownerless row is nobody's


# ====================================================================================================================== failures

@pytest.mark.parametrize("error", [RuntimeError("DATABASE_URL is required for persistent application data."), psycopg.OperationalError("connection refused")])
def test_an_unreachable_database_is_a_503_in_plain_words(store, monkeypatch, error):
    def down(*a, **k):
        raise error
    for name in ("_get_proposal_metadata",):
        monkeypatch.setattr(main, name, down)
    for call in (duplicate, defaults):
        caught = refused(call)
        assert (caught.status_code, caught.detail) == (503, "The database isn't reachable right now. Nothing was created. Try again in a moment.")


def test_a_database_that_goes_away_during_the_insert_is_a_503_and_nothing_is_claimed(store, monkeypatch):
    def down(**kw):
        raise psycopg.OperationalError("server closed the connection")
    monkeypatch.setattr(main, "_insert_draft_if_absent", down)
    assert refused(duplicate).status_code == 503


def test_a_database_that_goes_away_while_looking_for_the_copy_is_a_503(store, monkeypatch):
    calls = {"n": 0}

    def flaky(pid):
        calls["n"] += 1
        if calls["n"] == 2:                                       # the first read (the source) works, the second (does the copy exist?) does not
            raise psycopg.OperationalError("gone")
        return store.get(pid)
    monkeypatch.setattr(main, "_get_proposal_metadata", flaky)
    assert refused(duplicate).status_code == 503 and store.inserts == []


def test_a_missing_table_still_reaches_the_apps_own_503_handler_not_ours(store, monkeypatch):
    def no_table(**kw):
        raise psycopg.errors.UndefinedTable("relation proposals does not exist")
    monkeypatch.setattr(main, "_insert_draft_if_absent", no_table)
    with pytest.raises(psycopg.errors.UndefinedTable):
        duplicate()


def test_a_bug_in_the_transform_is_a_500_that_wrote_nothing_and_logged_no_client_data(store, monkeypatch, caplog):
    def broken(state, basics, **kw):
        raise KeyError("Zanzibar Zeppelins")
    monkeypatch.setattr(pd, "transform_reopen_state", broken)
    with caplog.at_level(logging.INFO, logger="app.main"):
        error = refused(duplicate, form(client_name="Zanzibar Zeppelins"))
    assert (error.status_code, error.detail) == (500, "We couldn't create the copy. Nothing was saved.") and store.inserts == []
    assert "Zanzibar" not in " ".join(r.getMessage() for r in caplog.records) and "KeyError" in " ".join(r.getMessage() for r in caplog.records)


def test_an_output_the_draft_models_refuse_is_a_500_that_wrote_nothing_and_logs_where_not_what(store, monkeypatch, caplog):
    real = pd.transform_reopen_state

    def bad_option_letter(state, basics, **kw):
        result = real(state, basics, **kw)
        result.state["tiers"][0]["label"] = "Zanzibar"             # TierModel allows A to J
        return result
    monkeypatch.setattr(pd, "transform_reopen_state", bad_option_letter)
    with caplog.at_level(logging.INFO, logger="app.main"):
        error = refused(duplicate)
    assert error.status_code == 500 and store.inserts == []
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "ValidationError" in logged and "label" in logged and "Zanzibar" not in logged


# ====================================================================================================================== no AI

def test_no_ai_or_gamma_service_is_called_on_either_route(store, no_ai):
    defaults()
    duplicate()
    duplicate()


# ====================================================================================================================== the copy as the page reopens it

def test_the_reopen_route_gives_the_copy_back_with_its_lineage_and_notes_and_current_names(store, no_ai):
    answer = duplicate()
    reopened = asyncio.run(main.reopen_proposal(answer["proposal_id"]))               # reads the row Postgres would hand back (keys re-ordered)
    assert reopened["status"] == "draft" and reopened["wizard_step"] == 2 and reopened["proposal_title"] == answer["proposal_title"]
    assert reopened["duplicated_from"]["proposal_id"] == SOURCE_ID and reopened["duplicated_from"]["client_name"] == "Acme Motors"
    assert [n["code"] for n in reopened["duplicate_notes"]] == [n["code"] for n in answer["notes"]]
    assert reopened["request"]["client_name"] == "Beta Burgers" and reopened["request"]["start_date"] == "2027-02-01"
    assert reopened["strategy_brief"] is None and reopened["enrichment"] is None and reopened["force_tabs"] == {}
    assert reopened["tiers"][0]["line_items"][0]["monthly_allocations"] == {"2027-02": 5000.0, "2027-03": 15000.0, "2027-04": 10000.0}
    assert reopened["time_unit"] == "month"


# ====================================================================================================================== duplicate-defaults

def test_the_defaults_describe_the_source_and_prefill_the_form(store):
    answer = defaults()
    assert answer["source"] == {
        "proposal_id": SOURCE_ID, "title": store.rows[SOURCE_ID]["proposal_title"], "client_name": "Acme Motors", "status": "draft",
        "start_date": "2026-09-01", "end_date": "2026-11-30", "time_unit": "month", "periods": 3, "option_count": 2, "line_count": 5,
    }
    assert answer["defaults"] == {
        "requested_by": "Ana Torres", "salesperson_email": "ana@entravision.com", "salesperson_market": "Phoenix",
        "request_type": "Full Presentation (Deck + Avails + Proposal Included)", "agency_name": "Initiative Media", "agency_fee": 0.15,
        "client_website": "https://www.acmemotors.com",
    }


def test_the_defaults_are_read_only_and_answer_who_may_copy(store):
    before = copy.deepcopy(store.rows)
    defaults()
    assert store.rows == before and store.inserts == []
    assert refused(defaults, request=fake_request("other@entravision.com")).status_code == 403
    assert refused(defaults, source_id="Nope").status_code == 404
    assert defaults(request=fake_request(ADMIN, admin=True))["source"]["proposal_id"] == SOURCE_ID


def test_the_defaults_name_the_row_as_history_shows_it_and_report_a_generated_source_as_generated(store):
    """The facts are the history row's (its client name and status), not the state's: a planner picks the row they see."""
    store.rows[SOURCE_ID].update(client_name="Acme Motors (Phoenix)", status="generated")
    source = defaults()["source"]
    assert source["client_name"] == "Acme Motors (Phoenix)" and source["status"] == "generated"
    store.rows[SOURCE_ID]["status"] = None                                    # a row older than the status column counts as generated, as everywhere else
    assert defaults()["source"]["status"] == "generated"


def test_the_defaults_fall_back_to_the_requests_client_name_and_blank_a_renewal_type(store):
    store.rows[SOURCE_ID]["client_name"] = None
    store.rows[SOURCE_ID]["reopen_state"]["request"]["request_type"] = "Digital Renewal Plan"
    answer = defaults()
    assert answer["source"]["client_name"] == "Acme Motors" and answer["defaults"]["request_type"] == ""


# ====================================================================================================================== over HTTP

USERS = {"planner": {"id": 1, "email": PLANNER, "is_admin": False}, "admin": {"id": 2, "email": ADMIN, "is_admin": True},
         "other": {"id": 3, "email": "other@entravision.com", "is_admin": False}}


@pytest.fixture
def client(monkeypatch, store):
    """A factory for HTTP clients of the real app, signed in as one of USERS (or nobody)."""
    try:
        from fastapi.testclient import TestClient
    except (ImportError, RuntimeError) as exc:
        pytest.skip(f"no HTTP test client here: {exc}")
    monkeypatch.setattr(main.auth_svc, "get_user_by_session", lambda token: USERS.get(token))
    monkeypatch.setattr(main.auth_svc, "refresh_session", lambda token: None)
    return lambda who="planner": TestClient(main.app, cookies={main.SESSION_COOKIE_NAME: who} if who else {})


@pytest.fixture
def http(client):
    return client("planner")


def body_json(**over) -> dict:
    return {"request_id": REQUEST_ID, "client_name": "Beta Burgers", "start_date": "2027-02-01", "end_date": "2027-04-30", **over}


def test_over_http_the_copy_is_made_and_the_answer_is_json(http, store):
    answer = http.post(f"/api/proposal/{SOURCE_ID}/duplicate", json=body_json(notion_id="evc 50001"))
    assert answer.status_code == 200, answer.text
    payload = answer.json()
    assert payload["created"] is True and payload["open_url"] == f"/?reopen={payload['proposal_id']}"
    assert store.rows[payload["proposal_id"]]["notion_id"] == "EVC-50001"
    again = http.post(f"/api/proposal/{SOURCE_ID}/duplicate", json=body_json(notion_id="evc 50001"))
    assert again.json()["created"] is False and again.json()["proposal_id"] == payload["proposal_id"]


def test_over_http_you_must_be_signed_in(client):
    nobody = client(None)
    assert nobody.post(f"/api/proposal/{SOURCE_ID}/duplicate", json=body_json()).status_code == 401
    assert nobody.get(f"/api/proposal/{SOURCE_ID}/duplicate-defaults").status_code == 401


def test_over_http_errors_carry_the_plain_message_as_detail_and_an_admin_gets_through(http, client, store):
    assert http.get("/api/proposal/Nope/duplicate-defaults").json() == {"detail": "We couldn't find that proposal. It may have been deleted."}
    other = client("other")
    refusal = other.post(f"/api/proposal/{SOURCE_ID}/duplicate", json=body_json())
    assert (refusal.status_code, refusal.json()) == (403, {"detail": "You can only duplicate your own proposals."})
    assert other.get(f"/api/proposal/{SOURCE_ID}/duplicate-defaults").status_code == 403
    assert client("admin").get(f"/api/proposal/{SOURCE_ID}/duplicate-defaults").status_code == 200
    made = client("admin").post(f"/api/proposal/{SOURCE_ID}/duplicate", json=body_json())
    assert made.status_code == 200 and store.rows[made.json()["proposal_id"]]["created_by_email"] == ADMIN


def test_over_http_an_invalid_form_is_a_422_in_the_apps_own_shape_without_echoing_the_input(http, store):
    answer = http.post(f"/api/proposal/{SOURCE_ID}/duplicate", json=body_json(start_date="2027-03-01", end_date="2027-02-01", client_name="Echo Me <b>"))
    assert answer.status_code == 422
    assert answer.json() == {"detail": [{"type": "value_error", "loc": ["body", "end_date"], "msg": "Value error, The end date can't be before the start date."}]}
    assert "Echo Me" not in answer.text and store.inserts == []
    several = http.post(f"/api/proposal/{SOURCE_ID}/duplicate", json={"request_id": "x"}).json()["detail"]
    assert [e["loc"][-1] for e in several] == ["request_id", "client_name", "start_date", "end_date"]
    assert several[0]["msg"] == "Value error, The page didn't send a valid request id. Reload the page and try again."
    assert several[1]["type"] == "missing"


def test_over_http_the_validation_comes_before_the_ownership_check(client, store):
    other = client("other")
    assert other.post(f"/api/proposal/{SOURCE_ID}/duplicate", json=body_json(client_name="")).status_code == 422
    assert other.post(f"/api/proposal/{SOURCE_ID}/duplicate", json=body_json()).status_code == 403


def test_over_http_the_other_proposal_routes_are_not_shadowed(http, monkeypatch):
    saved = {}
    monkeypatch.setattr(main, "_save_proposal_metadata", lambda **kw: saved.update(kw))
    answer = http.post("/api/proposal/draft", json={"request": {"client_name": "Acme"}, "proposal_title": "t"})
    assert answer.status_code == 200 and saved["status"] == "draft"


# ====================================================================================================================== the request model

def errors_of(**over) -> list[tuple[str, str]]:
    with pytest.raises(ValidationError) as caught:
        form(**over)
    return [(e["loc"][-1], e["msg"].removeprefix("Value error, ")) for e in caught.value.errors()]


@pytest.mark.parametrize("over, field, message", [
    (dict(client_name=""), "client_name", "Enter the new client's name."),
    (dict(client_name="   \t\n"), "client_name", "Enter the new client's name."),
    (dict(client_name="​​"), "client_name", "Enter the new client's name."),
    (dict(client_name=None), "client_name", "Enter the new client's name."),
    (dict(client_name="x" * 201), "client_name", "The client name can be at most 200 characters."),
    (dict(client_name=5), "client_name", "The client name must be text."),
    (dict(start_date=""), "start_date", "Enter the start date as YYYY-MM-DD."),
    (dict(start_date=None), "start_date", "Enter the start date as YYYY-MM-DD."),
    (dict(start_date="2027-2-1"), "start_date", "Enter the start date as YYYY-MM-DD."),
    (dict(start_date="02/01/2027"), "start_date", "Enter the start date as YYYY-MM-DD."),
    (dict(start_date="2027-02-01T00:00"), "start_date", "Enter the start date as YYYY-MM-DD."),
    (dict(start_date="٢٠٢٧-٠٢-٠١"), "start_date", "Enter the start date as YYYY-MM-DD."),     # Arabic-Indic digits
    (dict(start_date=20270201), "start_date", "Enter the start date as YYYY-MM-DD."),
    (dict(start_date="2027-02-30"), "start_date", "The start date isn't a real calendar date."),
    (dict(start_date="2027-13-01"), "start_date", "The start date isn't a real calendar date."),
    (dict(start_date="2026-02-29"), "start_date", "The start date isn't a real calendar date."),
    (dict(start_date="1999-12-31"), "start_date", "The start date must be in a year from 2000 to 2100."),
    (dict(start_date="2207-02-01", end_date="2207-04-30"), "start_date", "The start date must be in a year from 2000 to 2100."),
    (dict(end_date=""), "end_date", "Enter the end date as YYYY-MM-DD."),
    (dict(end_date="2027-04-31"), "end_date", "The end date isn't a real calendar date."),
    (dict(end_date="2027-01-31"), "end_date", "The end date can't be before the start date."),
    (dict(start_date="2027-01-01", end_date="2037-01-10"), "end_date", "The flight can't be longer than 10 years."),
    (dict(start_date="2027-01-01", end_date="2101-01-01"), "end_date", "The end date must be in a year from 2000 to 2100."),
    (dict(notion_id="12"), "notion_id", "The Notion ID is a 4 or 5 digit number, for example EVC-48213."),
    (dict(notion_id="123456"), "notion_id", "The Notion ID is a 4 or 5 digit number, for example EVC-48213."),
    (dict(notion_id="EVC-12ab"), "notion_id", "The Notion ID is a 4 or 5 digit number, for example EVC-48213."),
    (dict(notion_id="ABC-12345"), "notion_id", "The Notion ID is a 4 or 5 digit number, for example EVC-48213."),
    (dict(notion_id="EVC-123 45"), "notion_id", "The Notion ID is a 4 or 5 digit number, for example EVC-48213."),
    (dict(notion_id=48213), "notion_id", "The Notion ID must be text."),
    (dict(salesperson_email="nope"), "salesperson_email", "That doesn't look like an email address."),
    (dict(salesperson_email="a@b"), "salesperson_email", "That doesn't look like an email address."),
    (dict(salesperson_email="a b@c.com"), "salesperson_email", "That doesn't look like an email address."),
    (dict(agency_fee=15), "agency_fee", "The agency fee must be between 0% and 99%."),
    (dict(agency_fee=1), "agency_fee", "The agency fee must be between 0% and 99%."),
    (dict(agency_fee=-0.1), "agency_fee", "The agency fee must be between 0% and 99%."),
    (dict(agency_fee=float("nan")), "agency_fee", "The agency fee must be between 0% and 99%."),
    (dict(agency_fee="abc"), "agency_fee", "The agency fee must be a number between 0% and 99%."),
    (dict(agency_fee=True), "agency_fee", "The agency fee must be a number between 0% and 99%."),
    (dict(agency_fee=[0.1]), "agency_fee", "The agency fee must be a number between 0% and 99%."),
    (dict(request_id="short"), "request_id", "The page didn't send a valid request id. Reload the page and try again."),
    (dict(request_id="x" * 65), "request_id", "The page didn't send a valid request id. Reload the page and try again."),
    (dict(request_id="has spaces in it 0123456"), "request_id", "The page didn't send a valid request id. Reload the page and try again."),
    (dict(request_id="valid-id-0123456789\n"), "request_id", "The page didn't send a valid request id. Reload the page and try again."),
    (dict(request_id=None), "request_id", "The page didn't send a valid request id. Reload the page and try again."),
    (dict(agency_name="x" * 201), "agency_name", "The agency name can be at most 200 characters."),
    (dict(campaign_name="x" * 201), "campaign_name", "The campaign name can be at most 200 characters."),
    (dict(client_website="x" * 301), "client_website", "The website can be at most 300 characters."),
    (dict(requested_by="x" * 201), "requested_by", "Requested by can be at most 200 characters."),
    (dict(salesperson_market="x" * 201), "salesperson_market", "The market can be at most 200 characters."),
    (dict(request_type="x" * 201), "request_type", "The request type can be at most 200 characters."),
    (dict(agency_name=["a"]), "agency_name", "The agency name must be text."),
])
def test_model_refuses_each_invalid_input_with_its_plain_message(over, field, message):
    assert (field, message) in errors_of(**over)


def test_model_reports_every_problem_at_once_so_the_dialog_can_show_them_all():
    found = dict(errors_of(client_name="", start_date="nope", notion_id="1", salesperson_email="x"))
    assert set(found) == {"client_name", "start_date", "notion_id", "salesperson_email"}      # (end_date waits for a good start)


def test_model_normalises_what_it_accepts():
    body = form(client_name="  Beta \t Burgers\n ", notion_id="evc 50001", start_date=" 2027-02-01 ", end_date="2027-04-30", agency_name=" Beta  Media ",
                client_website="https://beta.example", campaign_name="​Spring‮  Push", salesperson_email=" luis@entravision.com ",
                requested_by="Luis\x00 Ortega", agency_fee="0.15")
    assert (body.client_name, body.notion_id, body.start_date, body.agency_name, body.campaign_name, body.salesperson_email, body.requested_by, body.agency_fee) == (
        "Beta Burgers", "EVC-50001", "2027-02-01", "Beta Media", "Spring Push", "luis@entravision.com", "Luis Ortega", 0.15)


@pytest.mark.parametrize("raw, expected", [("evc 50001", "EVC-50001"), ("EVC-50001", "EVC-50001"), ("EVC50001", "EVC-50001"), ("50001", "EVC-50001"), ("evc-4821", "EVC-4821"),
                                           ("4821", "EVC-4821"), ("  EVC  4821 ", "EVC-4821"), ("", ""), ("   ", ""), (None, "")])
def test_model_normalises_a_notion_id(raw, expected):
    assert form(notion_id=raw).notion_id == expected


@pytest.mark.parametrize("raw, expected", [(None, None), ("", None), ("  ", None), (0, None), (0.0, None), ("0", None), (0.15, 0.15), ("0.15", 0.15), (0.99, 0.99), (0.005, 0.005)])
def test_model_reads_an_agency_fee_as_a_fraction_and_zero_as_none(raw, expected):
    assert form(agency_fee=raw).agency_fee == expected


def test_model_keeps_the_originals_for_omitted_fields_and_blank_for_empty_ones():
    omitted = form()
    assert (omitted.requested_by, omitted.salesperson_email, omitted.salesperson_market, omitted.request_type) == (None, None, None, None)
    nulls = form(requested_by=None, salesperson_email=None, salesperson_market=None, request_type=None)
    assert (nulls.requested_by, nulls.salesperson_email, nulls.salesperson_market, nulls.request_type) == (None, None, None, None)
    blank = form(requested_by="", salesperson_email="", salesperson_market="", request_type="")
    assert (blank.requested_by, blank.salesperson_email, blank.salesperson_market, blank.request_type) == ("", "", "", "")


def test_model_accepts_the_edges_of_the_date_rules():
    assert form(start_date="2000-01-01", end_date="2000-01-01").end_date == "2000-01-01"
    assert form(start_date="2027-01-01", end_date="2036-12-31").end_date == "2036-12-31"        # 3,653 days
    assert form(start_date="2027-01-01", end_date="2037-01-07").end_date == "2037-01-07"        # exactly 3,660 days, the longest allowed
    assert ("end_date", "The flight can't be longer than 10 years.") in errors_of(start_date="2027-01-01", end_date="2037-01-08")      # 3,661
    assert form(start_date="2100-12-01", end_date="2100-12-31").start_date == "2100-12-01"
    assert form(start_date="2028-02-29", end_date="2028-03-01").start_date == "2028-02-29"      # a leap day


def test_model_ignores_unknown_fields_and_reads_keep_avails_leniently():
    body = form(something_new="x", keep_avails=None)
    assert not hasattr(body, "something_new") and body.keep_avails is False
    assert form(keep_avails=True).keep_avails is True


def test_the_model_and_the_transforms_basics_have_the_same_fields():
    """The route builds DuplicateBasics from the model by name: a field added to one and not the other must fail here, not in production."""
    import dataclasses
    assert set(main.DuplicateProposalRequest.model_fields) - {"request_id"} == {f.name for f in dataclasses.fields(pd.DuplicateBasics)}
    assert fx.source_monthly()                                                               # (the fixtures import cleanly with the real models)
