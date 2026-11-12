"""The SQL behind "Duplicate for a new client", against a real PostgreSQL (tests marked real_db): the insert that makes a copy exactly once, and the
CASE in the save upsert that keeps a copy's lineage across autosaves and across the generate that promotes it.

What is pinned:

  * `_insert_draft_if_absent` makes a bare draft (status draft, no files, no summary, no generated_at), returns True once, and False for an id that is
    taken, leaving that row exactly as it was; eight threads inserting one id at the same moment make one row and one True
  * `_save_proposal_metadata` keeps `reopen_state.duplicated_from` across draft saves and across the promotion to "generated", drops the one-shot
    `duplicate_notes` at the first save, and leaves every other row's state exactly as the page sent it (no lineage, a JSON null, a NULL state)
  * the whole flow through the real routes on the real table: copy, the same request again, eight racing requests, reopen, autosave, generate
  * no schema change: the table has no lineage column, and the test database is built from schema.sql alone

The database comes from DATABASE_URL. When that database has no `proposals` table (a test database built from the migrations only), a scratch database is
created on the same server from schema.sql and dropped afterwards; when it has one, the tests use it with unique ids and delete what they made.
Without a reachable database every test skips, like the other real_db tests.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import conninfo

from app import db, main
from app.services import schema_bootstrap
from tests.fixtures import duplicate_sources as fx

pytestmark = pytest.mark.real_db


# ====================================================================================================================== the database

@pytest.fixture(scope="module")
def database():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("no DATABASE_URL")
    try:
        with psycopg.connect(url, connect_timeout=2) as conn:
            has_table = conn.execute("SELECT to_regclass('public.proposals') IS NOT NULL").fetchone()[0]
    except Exception:  # noqa: BLE001 - any failure to connect is "no database here"
        pytest.skip("no reachable database")
    if has_table:
        yield url
        return
    parts = conninfo.conninfo_to_dict(url)
    name = f"epb_dup_{uuid.uuid4().hex[:12]}"
    try:
        with psycopg.connect(conninfo.make_conninfo(**{**parts, "dbname": "postgres"}), autocommit=True, connect_timeout=2) as admin:
            admin.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"this database has no proposals table and a scratch one cannot be created: {type(exc).__name__}")
    scratch = conninfo.make_conninfo(**{**parts, "dbname": name})
    patch = pytest.MonkeyPatch()
    try:
        with psycopg.connect(scratch, autocommit=True) as conn:                      # schema.sql alone: no startup migrations, no bootstrap
            for statement in schema_bootstrap.split_statements(schema_bootstrap.SCHEMA_PATH.read_text(encoding="utf-8")):
                conn.execute(statement)
        patch.setenv("DATABASE_URL", scratch)
        db.reset_for_tests()
        yield scratch
    finally:
        db.close_pool()
        patch.undo()
        db.reset_for_tests()
        with psycopg.connect(conninfo.make_conninfo(**{**parts, "dbname": "postgres"}), autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


class Rows:
    """Unique ids and a unique owner for one test, and the cleanup of everything made under them."""

    def __init__(self):
        self.tag = f"__pytest_dup_{uuid.uuid4().hex[:10]}"
        self.email = f"{self.tag}@example.test"
        self.admin_email = f"{self.tag}_admin@example.test"
        self.ids: list[str] = []

    def new_id(self, suffix: str = "") -> str:
        pid = f"{self.tag}_{len(self.ids)}{suffix}"
        self.ids.append(pid)
        return pid

    def read(self, proposal_id: str) -> dict | None:
        return db.fetch_one("SELECT * FROM proposals WHERE proposal_id = %s", (proposal_id,))

    def count(self) -> int:
        return db.fetch_one("SELECT COUNT(*) AS n FROM proposals WHERE proposal_id = ANY(%s) OR created_by_email LIKE %s", (self.ids, f"{self.tag}%"))["n"]


@pytest.fixture
def rows(database):
    r = Rows()
    try:
        yield r
    finally:
        with db.get_connection() as conn:
            conn.execute("DELETE FROM proposals WHERE proposal_id = ANY(%s) OR created_by_email LIKE %s", (r.ids, f"{r.tag}%"))


def insert(rows: Rows, pid: str, *, reopen_state=None, **over) -> bool:
    fields = dict(proposal_id=pid, client_name="Beta Burgers", seller_email="luis@entravision.com", created_by_email=rows.email, requested_by="Luis Ortega",
                  notion_id="EVC-50001", proposal_title="EVC-50001 | Beta Burgers | Entravision | Feb27 | Digital Media Proposal",
                  reopen_state={"request": {"client_name": "Beta Burgers"}, "wizard_step": 2} if reopen_state is None else reopen_state,
                  requester_ip="10.0.0.7", requester_user_agent="pytest")
    fields.update(over)
    return main._insert_draft_if_absent(**fields)


def save(rows: Rows, pid: str, reopen_state: dict, **over) -> None:
    fields = dict(proposal_id=pid, client_name="Beta Burgers", seller_email="luis@entravision.com", created_by_email=rows.email, requested_by="Luis Ortega",
                  notion_id="EVC-50001", proposal_title="a title", reopen_state=reopen_state, summary={}, status="draft")
    fields.update(over)
    main._save_proposal_metadata(**fields)


LINEAGE = {"proposal_id": "SourceProposalId0001", "title": "EVC-48213 | Acme Motors", "client_name": "Acme Motors", "at": "2026-11-11T12:00:00+00:00"}
NOTES = [{"code": "dates_moved", "level": "info", "text": "Flight moved."}]


# ====================================================================================================================== _insert_draft_if_absent

def test_the_insert_makes_a_bare_draft_and_says_so(rows):
    pid = rows.new_id()
    assert insert(rows, pid, reopen_state={"request": {"client_name": "Beta Burgers"}, "wizard_step": 2, "duplicated_from": LINEAGE}) is True
    row = rows.read(pid)
    assert row["status"] == "draft" and row["summary"] == {} and row["created_by_email"] == rows.email
    assert (row["filename"], row["email_doc_filename"], row["pptx_net_filename"], row["pptx_gross_filename"], row["generated_at"]) == (None, None, None, None, None)
    assert (row["client_name"], row["seller_email"], row["requested_by"], row["notion_id"], row["requester_ip"], row["requester_user_agent"]) == (
        "Beta Burgers", "luis@entravision.com", "Luis Ortega", "EVC-50001", "10.0.0.7", "pytest")
    assert row["reopen_state"]["duplicated_from"] == LINEAGE and row["updated_at"] is not None and row["created_at"] is not None
    assert abs((datetime.now(timezone.utc) - row["updated_at"]).total_seconds()) < 60            # a copy sorts to the top of the history


def test_a_missing_notion_id_is_stored_as_null(rows):
    pid = rows.new_id()
    insert(rows, pid, notion_id=None)
    assert rows.read(pid)["notion_id"] is None


def test_the_insert_of_a_taken_id_returns_false_and_leaves_the_row_exactly_as_it_was(rows):
    pid = rows.new_id()
    assert insert(rows, pid, reopen_state={"request": {"client_name": "first"}}) is True
    before = rows.read(pid)
    assert insert(rows, pid, client_name="second", created_by_email="someone.else@example.test", reopen_state={"request": {"client_name": "second"}}) is False
    assert rows.read(pid) == before


def test_the_insert_never_overwrites_a_generated_proposal(rows):
    pid = rows.new_id()
    save(rows, pid, {"request": {"client_name": "real"}}, status="generated", filename="real.xlsx", generated_at=datetime.now(timezone.utc))
    before = rows.read(pid)
    assert insert(rows, pid) is False
    assert rows.read(pid) == before and before["status"] == "generated" and before["filename"] == "real.xlsx"


def test_eight_threads_inserting_one_id_at_the_same_moment_make_one_row_and_one_true(rows):
    pid = rows.new_id()
    start = threading.Barrier(8)
    results: list = []

    def worker(n: int) -> None:
        start.wait()
        try:
            results.append(insert(rows, pid, client_name=f"thread {n}", reopen_state={"request": {"n": n}}))
        except Exception as exc:  # noqa: BLE001 - reported below
            results.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert sorted(map(str, results)) == ["False"] * 7 + ["True"]
    assert rows.count() == 1
    winner = rows.read(pid)
    assert winner["client_name"] == f"thread {winner['reopen_state']['request']['n']}"             # one thread's whole row, never a mixture


# ====================================================================================================================== the CASE in the upsert

def test_the_lineage_survives_an_autosave_and_the_one_shot_notes_do_not(rows):
    pid = rows.new_id()
    insert(rows, pid, reopen_state={"request": {"client_name": "Beta Burgers"}, "wizard_step": 2, "duplicated_from": LINEAGE, "duplicate_notes": NOTES})
    assert rows.read(pid)["reopen_state"]["duplicate_notes"] == NOTES
    save(rows, pid, {"request": {"client_name": "Beta Burgers", "geo": "Dallas"}, "wizard_step": 3, "line_items": []})          # what the page sends: it knows no lineage
    state = rows.read(pid)["reopen_state"]
    assert state["duplicated_from"] == LINEAGE and "duplicate_notes" not in state
    assert state["wizard_step"] == 3 and state["request"]["geo"] == "Dallas" and state["line_items"] == []                    # everything else is what the page sent
    save(rows, pid, {"request": {"client_name": "Beta Burgers"}, "wizard_step": 4})
    assert rows.read(pid)["reopen_state"] == {"request": {"client_name": "Beta Burgers"}, "wizard_step": 4, "duplicated_from": LINEAGE}


def test_the_lineage_survives_the_generate_that_promotes_the_draft(rows):
    pid = rows.new_id()
    insert(rows, pid, reopen_state={"request": {"client_name": "Beta Burgers"}, "duplicated_from": LINEAGE, "duplicate_notes": NOTES})
    save(rows, pid, {"request": {"client_name": "Beta Burgers"}, "wizard_step": 8}, status="generated", filename="beta.xlsx", generated_at=datetime.now(timezone.utc),
         summary={"total_net": 69000.0})
    row = rows.read(pid)
    assert row["status"] == "generated" and row["filename"] == "beta.xlsx" and row["summary"] == {"total_net": 69000.0}
    assert row["reopen_state"]["duplicated_from"] == LINEAGE and "duplicate_notes" not in row["reopen_state"]


def test_a_lineage_in_the_incoming_state_never_replaces_the_stored_one(rows):
    pid = rows.new_id()
    insert(rows, pid, reopen_state={"request": {}, "duplicated_from": LINEAGE})
    save(rows, pid, {"request": {}, "duplicated_from": {"proposal_id": "someone-elses", "client_name": "Hijack"}})
    assert rows.read(pid)["reopen_state"]["duplicated_from"] == LINEAGE


def test_a_row_without_lineage_is_saved_exactly_as_the_page_sent_it(rows):
    pid = rows.new_id()
    first = {"request": {"client_name": "Plain"}, "wizard_step": 2, "line_items": [], "strategy_brief": None}
    save(rows, pid, first)
    assert rows.read(pid)["reopen_state"] == first
    second = {"request": {"client_name": "Plain again"}, "wizard_step": 5, "tiers": [{"label": "A", "line_items": []}]}
    save(rows, pid, second)
    state = rows.read(pid)["reopen_state"]
    assert state == second and "duplicated_from" not in state and "duplicate_notes" not in state


def test_a_stored_state_that_was_never_set_or_is_not_an_object_is_replaced_normally(rows):
    pid = rows.new_id()
    with db.get_connection() as conn:
        conn.execute("INSERT INTO proposals (proposal_id, created_by_email, status) VALUES (%s, %s, 'draft')", (pid, rows.email))            # reopen_state NULL
    save(rows, pid, {"request": {"client_name": "Late state"}})
    assert rows.read(pid)["reopen_state"] == {"request": {"client_name": "Late state"}}
    with db.get_connection() as conn:
        conn.execute("UPDATE proposals SET reopen_state = '[1, 2]'::jsonb WHERE proposal_id = %s", (pid,))                                      # an array
    save(rows, pid, {"request": {"client_name": "From an array"}})
    assert rows.read(pid)["reopen_state"] == {"request": {"client_name": "From an array"}}
    with db.get_connection() as conn:
        conn.execute("UPDATE proposals SET reopen_state = '\"text\"'::jsonb WHERE proposal_id = %s", (pid,))                                   # a scalar
    save(rows, pid, {"request": {"client_name": "From a scalar"}})
    assert rows.read(pid)["reopen_state"] == {"request": {"client_name": "From a scalar"}}


def test_a_json_null_lineage_is_kept_as_it_is_and_breaks_nothing(rows):
    pid = rows.new_id()
    with db.get_connection() as conn:
        conn.execute("INSERT INTO proposals (proposal_id, created_by_email, status, reopen_state) VALUES (%s, %s, 'draft', %s::jsonb)",
                     (pid, rows.email, json.dumps({"request": {}, "duplicated_from": None})))
    save(rows, pid, {"request": {"client_name": "x"}})
    assert rows.read(pid)["reopen_state"] == {"request": {"client_name": "x"}, "duplicated_from": None}


def test_the_save_still_updates_every_other_column(rows):
    pid = rows.new_id()
    insert(rows, pid, reopen_state={"request": {}, "duplicated_from": LINEAGE})
    save(rows, pid, {"request": {}}, client_name="Renamed", seller_email="new@entravision.com", requested_by="Someone", notion_id="EVC-60000", proposal_title="new title")
    row = rows.read(pid)
    assert (row["client_name"], row["seller_email"], row["requested_by"], row["notion_id"], row["proposal_title"]) == (
        "Renamed", "new@entravision.com", "Someone", "EVC-60000", "new title")


# ====================================================================================================================== the whole flow through the real routes

def request_of(email: str, *, admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user={"email": email, "is_admin": admin}), client=SimpleNamespace(host="10.0.0.7"), headers={"user-agent": "pytest"})


def form(**over) -> main.DuplicateProposalRequest:
    fields = dict(request_id="dialog-request-0123456789", client_name="Beta Burgers", start_date="2027-02-01", end_date="2027-04-30", notion_id="50001",
                  campaign_name="Spring Push")
    fields.update(over)
    return main.DuplicateProposalRequest(**fields)


def a_source(rows: Rows, state=None, **over) -> str:
    pid = rows.new_id("_src")
    save(rows, pid, fx.source_monthly() if state is None else state, client_name="Acme Motors", notion_id="EVC-48213", **over)
    return pid


def test_a_copy_is_a_stored_draft_in_the_callers_history_and_the_source_is_untouched(rows):
    source = a_source(rows)
    source_before = rows.read(source)
    answer = asyncio.run(main.duplicate_proposal(source, form(), request_of(rows.email)))
    rows.ids.append(answer["proposal_id"])
    assert answer["created"] is True
    row = rows.read(answer["proposal_id"])
    assert row["status"] == "draft" and row["summary"] == {} and row["filename"] is None and row["created_by_email"] == rows.email
    assert (row["client_name"], row["notion_id"], row["seller_email"], row["proposal_title"]) == ("Beta Burgers", "EVC-50001", "ana@entravision.com", answer["proposal_title"])
    assert row["reopen_state"]["duplicated_from"]["proposal_id"] == source and row["reopen_state"]["wizard_step"] == 2
    assert {n["code"] for n in row["reopen_state"]["duplicate_notes"]} == {n["code"] for n in answer["notes"]}
    assert rows.read(source) == source_before                                                    # no write, no updated_at bump
    assert asyncio.run(main.reopen_proposal(source))["request"]["client_name"] == "Acme Motors"


def test_an_admins_copy_of_a_planners_proposal_belongs_to_the_admin(rows):
    source = a_source(rows)
    answer = asyncio.run(main.duplicate_proposal(source, form(), request_of(rows.admin_email, admin=True)))
    rows.ids.append(answer["proposal_id"])
    assert rows.read(answer["proposal_id"])["created_by_email"] == rows.admin_email and rows.read(source)["created_by_email"] == rows.email


def test_the_same_request_again_finds_the_same_draft_and_eight_racing_requests_make_one(rows):
    source = a_source(rows)
    first = asyncio.run(main.duplicate_proposal(source, form(), request_of(rows.email)))
    rows.ids.append(first["proposal_id"])
    again = asyncio.run(main.duplicate_proposal(source, form(), request_of(rows.email)))
    assert again["created"] is False and again["proposal_id"] == first["proposal_id"] and rows.count() == 2          # the source and one copy

    start = threading.Barrier(8)
    answers: list = []

    def worker() -> None:
        start.wait()
        try:
            answers.append(asyncio.run(main.duplicate_proposal(source, form(request_id="racing-request-0123456789"), request_of(rows.email))))
        except Exception as exc:  # noqa: BLE001 - reported below
            answers.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert all(isinstance(a, dict) for a in answers), answers
    assert sorted(a["created"] for a in answers) == [False] * 7 + [True]
    assert len({a["proposal_id"] for a in answers}) == 1 and answers[0]["proposal_id"] != first["proposal_id"]
    rows.ids.append(answers[0]["proposal_id"])
    assert rows.count() == 3


def test_open_edit_generate_the_copy_keeps_its_lineage_the_whole_way(rows):
    source = a_source(rows)
    answer = asyncio.run(main.duplicate_proposal(source, form(), request_of(rows.email)))
    new_id = answer["proposal_id"]
    rows.ids.append(new_id)

    opened = asyncio.run(main.reopen_proposal(new_id))                                          # what the new tab loads (Postgres re-orders the JSON keys)
    assert opened["duplicated_from"]["proposal_id"] == source and opened["duplicated_from"]["client_name"] == "Acme Motors"
    assert [n["code"] for n in opened["duplicate_notes"]] == [n["code"] for n in answer["notes"]]
    assert opened["wizard_step"] == 2 and opened["status"] == "draft" and opened["request"]["client_name"] == "Beta Burgers"
    assert opened["tiers"][0]["line_items"][0]["monthly_allocations"] == {"2027-02": 5000.0, "2027-03": 15000.0, "2027-04": 10000.0}

    # the page's first autosave: the whole wizard state, with the same proposal id, from Step 03
    saved = asyncio.run(main.save_draft(main.DraftSaveRequest(
        proposal_id=new_id, wizard_step=3, request=opened["request"], tiers=opened["tiers"], line_items=opened["line_items"], avails_data=opened["avails_data"],
        addons=opened["addons"], time_unit=opened["time_unit"], monthly_distribution_mode=opened["monthly_distribution_mode"], proposal_title="a renamed title"),
        request_of(rows.email)))
    assert saved["proposal_id"] == new_id and rows.count() == 2                                 # the same row, not a new one
    again = asyncio.run(main.reopen_proposal(new_id))
    assert again["duplicated_from"]["proposal_id"] == source and "duplicate_notes" not in again and again["wizard_step"] == 3

    # a retry of the dialog's request after the first autosave still finds the copy, now without its one-shot notes
    retry = asyncio.run(main.duplicate_proposal(source, form(), request_of(rows.email)))
    assert retry["created"] is False and retry["proposal_id"] == new_id and retry["notes"] == []

    # the generate that promotes the draft (the route writes through the same function)
    save(rows, new_id, {"request": opened["request"], "wizard_step": 8}, status="generated", filename="beta.xlsx", generated_at=datetime.now(timezone.utc),
         client_name="Beta Burgers", notion_id="EVC-50001")
    final = asyncio.run(main.reopen_proposal(new_id))
    assert final["status"] == "generated" and final["duplicated_from"]["proposal_id"] == source


def test_the_defaults_read_the_stored_state(rows):
    source = a_source(rows)
    answer = asyncio.run(main.duplicate_defaults(source, request_of(rows.email)))
    assert answer["source"]["periods"] == 3 and answer["source"]["option_count"] == 2 and answer["defaults"]["agency_fee"] == 0.15
    assert rows.count() == 1


# ====================================================================================================================== no schema change

def test_the_table_has_no_lineage_column_and_the_feature_needs_none(database):
    columns = {r["column_name"] for r in db.fetch_all("SELECT column_name FROM information_schema.columns WHERE table_name = 'proposals'")}
    assert {"proposal_id", "created_by_email", "status", "updated_at", "reopen_state"} <= columns
    assert not [c for c in columns if "duplicat" in c or "lineage" in c or "copied" in c]
