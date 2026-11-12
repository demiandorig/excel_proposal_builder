"""Shared helpers for the proposal-history list tests (test_proposal_list_query.py, test_proposal_list_db.py, test_admin_proposals.py).

`FakeProposalsDb` stands in for app.db.fetch_all: it tells the list's three statements apart by a marker in their SQL, answers each from
what the test configured, records every call (kind, SQL, parameters, thread) and refuses any statement it does not know, so a fourth,
unplanned query shows up as a failure instead of as an empty answer.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional

# What GET /api/my-proposals and /api/admin/proposals returned before the list grew: every one of these keys still has to come back, built the
# same way. (The golden test in test_proposal_list_query.py rebuilds them from the old expressions.)
OLD_ROW_KEYS = (
    "proposal_id", "client_name", "seller_email", "requested_by", "notion_id", "proposal_title", "filename", "generated_at",
    "requester_ip", "requester_user_agent", "total_net", "total_gross", "tabs_built", "status", "updated_at",
)
# What it gained (created_by_email is the admin route's only).
NEW_ROW_KEYS = (
    "flight_start", "flight_end", "flight_text", "line_count", "option_count", "time_unit", "wizard_step", "duplicated_from", "can_reopen",
)
OLD_ENVELOPE_KEYS = ("proposals", "count", "total_count", "page", "page_size", "total_pages")
NEW_ENVELOPE_KEYS = ("facets", "applied")


def fake_request(email: str = "planner@entravision.com", *, is_admin: bool = True) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user={"email": email, "is_admin": is_admin}))


def call(route, request, **params) -> dict:
    """Run one of the async list routes the way the existing tests do."""
    return asyncio.run(route(request, **params))


def page_row(proposal_id: str = "p1", **overrides: Any) -> dict:
    """A row of the PAGE statement as the database returns it (`total_count` is added by the fake)."""
    row = {
        "proposal_id": proposal_id, "client_name": "Acme", "seller_email": "ae@entravision.com", "created_by_email": "planner@entravision.com",
        "requested_by": "Jane AE", "notion_id": "EVC-1", "proposal_title": "EVC-1_Acme_Proposal", "filename": "f.xlsx",
        "generated_at": datetime(2026, 11, 2, 15, 30, tzinfo=timezone.utc), "requester_ip": "1.2.3.4", "requester_user_agent": "UA",
        "summary": {"total_net": 100.0, "total_gross": 117.0, "tabs_built": ["net"]}, "status": "generated",
        "updated_at": datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def facts_row(proposal_id: str = "p1", **overrides: Any) -> dict:
    """A row of the FACTS statement (what `jsonb_to_record` gave for one proposal)."""
    row = {
        "proposal_id": proposal_id, "start_raw": "2027-01-05", "end_raw": "03/31/2027", "line_count": 3, "tier_count": None,
        "time_unit_raw": "week", "step_raw": "4", "dup_raw": None, "has_state": True,
    }
    row.update(overrides)
    return row


@dataclass(frozen=True)
class Call:
    kind: str                 # "counts" | "page" | "facts"
    sql: str
    params: tuple
    thread: int               # threading.get_ident() of the caller


class FakeProposalsDb:
    """fetch_all for the history list. `rows` are PAGE rows (without total_count); `total_count` defaults to len(rows).

    counts: the (all, draft, generated) the counts statement answers (default: len(rows), then split by status).
    facts: the FACTS rows to answer (default: one `facts_row` per page row).
    fail: statement kinds that raise `error` instead of answering.
    """

    def __init__(self, rows: Optional[list[dict]] = None, *, total_count: Optional[int] = None, counts: Optional[tuple] = None,
                 facts: Optional[list[dict]] = None, fail: tuple = (), error: Optional[Exception] = None):
        self.rows = list(rows or [])
        self.total_count = len(self.rows) if total_count is None else total_count
        self.counts = counts
        self.facts = facts
        self.fail = tuple(fail)
        self.error = error or RuntimeError("the database said no")
        self.calls: list[Call] = []

    # --- fetch_all -------------------------------------------------------------------------------------------------
    @staticmethod
    def kind_of(sql: str) -> str:
        if "jsonb_to_record" in sql:
            return "facts"
        if "FILTER (WHERE" in sql:
            return "counts"
        if "COUNT(*) OVER()" in sql:
            return "page"
        raise AssertionError(f"a statement the history list should not run:\n{sql}")

    def __call__(self, sql: str, params=()):
        kind = self.kind_of(sql)
        params = tuple(params)
        # Every %s of the SQL has exactly one parameter (the facts' ANY(%s) takes the whole list as one): a mismatch is a placeholder bug.
        assert sql.count("%s") == len(params), f"{kind}: {sql.count('%s')} placeholders for {len(params)} parameters"
        self.calls.append(Call(kind, sql, params, threading.get_ident()))
        if kind in self.fail:
            raise self.error
        if kind == "page":
            return [{**row, "total_count": self.total_count} for row in self.rows]
        if kind == "counts":
            all_n, draft_n, generated_n = self.counts if self.counts is not None else self._split()
            return [{"all_n": all_n, "draft_n": draft_n, "generated_n": generated_n}]
        if self.facts is not None:
            return list(self.facts)
        return [facts_row(row["proposal_id"]) for row in self.rows]

    def _split(self) -> tuple[int, int, int]:
        drafts = sum(1 for row in self.rows if row.get("status") == "draft")
        return len(self.rows), drafts, len(self.rows) - drafts

    # --- for the tests ---------------------------------------------------------------------------------------------
    def install(self, monkeypatch) -> "FakeProposalsDb":
        from app import main
        monkeypatch.setattr(main, "fetch_all", self)
        return self

    @property
    def kinds(self) -> list[str]:
        return [c.kind for c in self.calls]

    def of(self, kind: str) -> list[Call]:
        return [c for c in self.calls if c.kind == kind]

    def one(self, kind: str) -> Call:
        found = self.of(kind)
        assert len(found) == 1, f"expected exactly one {kind} statement, saw {self.kinds}"
        return found[0]

    @property
    def page(self) -> Call:
        return self.one("page")

    @property
    def counts_call(self) -> Call:
        return self.one("counts")

    @property
    def facts_call(self) -> Call:
        return self.one("facts")
