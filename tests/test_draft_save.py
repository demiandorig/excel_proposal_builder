"""
Covers the resumable-draft feature: POST /api/proposal/draft (save_draft),
DELETE /api/proposal/{id} (delete_draft), and /api/generate's proposal_id
resolution (reuses an existing DRAFT's id in place; mints a fresh one for
an already-GENERATED proposal, unchanged from before drafts existed).
Same pattern as test_admin_proposals.py — calls the handler functions
directly with a fake Request and monkeypatched DB calls, since a real
Postgres connection isn't available in this dev environment.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import main


def _fake_request(email: str = "planner@entravision.com") -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(user={"email": email, "is_admin": False}),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"user-agent": "pytest"},
    )


class _CapturingConn:
    """Fakes `with get_connection() as conn: conn.execute(sql, params)` —
    records every call instead of touching a real database."""

    def __init__(self, calls: list):
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self._calls.append((sql, params))
        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])


def _draft_body(**overrides) -> main.DraftSaveRequest:
    fields = {
        "request": {"client_name": "Acme", "salesperson_email": "ae@entravision.com"},
        "wizard_step": 4,
        "line_items": [],
        "proposal_title": "Acme draft",
    }
    fields.update(overrides)
    return main.DraftSaveRequest(**fields)


# --- save_draft --------------------------------------------------------


def test_save_draft_mints_a_fresh_id_when_none_given(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "get_connection", lambda: _CapturingConn(calls))

    result = asyncio.run(main.save_draft(_draft_body(), _fake_request()))

    assert result["proposal_id"]  # non-empty, server-minted
    assert len(calls) == 1
    sql, params = calls[0]
    assert params[0] == result["proposal_id"]
    assert params[-1] == "draft"  # status, appended last


def test_save_draft_reuses_a_given_proposal_id_when_it_is_still_a_draft(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "get_connection", lambda: _CapturingConn(calls))
    monkeypatch.setattr(main, "_get_proposal_metadata", lambda pid: {"status": "draft"})

    result = asyncio.run(main.save_draft(_draft_body(proposal_id="existing-123"), _fake_request()))

    assert result["proposal_id"] == "existing-123"
    assert calls[0][1][0] == "existing-123"


def test_save_draft_never_downgrades_an_already_generated_proposal(monkeypatch):
    """The critical guard for autosave: a planner just looking at (or
    idly editing) a reopened, already-GENERATED proposal must never have
    a background save silently flip its status back to 'draft' and null
    out its filename/generated_at/summary — that would corrupt a real,
    already-sent proposal's history row. A brand-new draft row is
    created instead, leaving the generated one completely untouched."""
    calls = []
    monkeypatch.setattr(main, "get_connection", lambda: _CapturingConn(calls))
    monkeypatch.setattr(main, "_get_proposal_metadata", lambda pid: {"status": "generated"})
    monkeypatch.setattr(main.secrets, "token_urlsafe", lambda n: "fresh-draft-id")

    result = asyncio.run(main.save_draft(_draft_body(proposal_id="real-generated-1"), _fake_request()))

    assert result["proposal_id"] == "fresh-draft-id"
    assert result["proposal_id"] != "real-generated-1"
    assert calls[0][1][0] == "fresh-draft-id"  # the real proposal's row was never touched


def test_save_draft_never_validates_an_unbalanced_or_empty_plan(monkeypatch):
    # No line_items, no strategy_brief, no avails_data — exactly what a
    # Step-02 draft looks like. Must not raise.
    calls = []
    monkeypatch.setattr(main, "get_connection", lambda: _CapturingConn(calls))
    result = asyncio.run(main.save_draft(main.DraftSaveRequest(request={"client_name": "Acme"}), _fake_request()))
    assert result["proposal_id"]


def test_save_draft_falls_back_to_client_name_when_title_blank(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "get_connection", lambda: _CapturingConn(calls))
    asyncio.run(main.save_draft(_draft_body(proposal_title=""), _fake_request()))
    sql, params = calls[0]
    # proposal_title is param index 6 — see _save_proposal_metadata's
    # column order.
    assert params[6] == "Acme"


def test_save_draft_reopen_state_carries_wizard_step_and_distribution_mode(monkeypatch):
    captured_reopen_state = {}

    def fake_save(*, reopen_state, **kwargs):
        captured_reopen_state.update(reopen_state)

    monkeypatch.setattr(main, "_save_proposal_metadata", fake_save)
    asyncio.run(main.save_draft(
        _draft_body(wizard_step=3, monthly_distribution_mode="prorated", campaign_name_override="Override"),
        _fake_request(),
    ))
    assert captured_reopen_state["wizard_step"] == 3
    assert captured_reopen_state["monthly_distribution_mode"] == "prorated"
    assert captured_reopen_state["campaign_name_override"] == "Override"


# --- delete_draft --------------------------------------------------------


def test_delete_draft_removes_own_draft(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_get_proposal_metadata",
                        lambda pid: {"status": "draft", "created_by_email": "planner@entravision.com"})
    monkeypatch.setattr(main, "get_connection", lambda: _CapturingConn(calls))

    result = asyncio.run(main.delete_draft("d1", _fake_request()))
    assert result == {"ok": True}
    assert calls[0][1] == ("d1",)


def test_delete_draft_404s_when_missing(monkeypatch):
    monkeypatch.setattr(main, "_get_proposal_metadata", lambda pid: None)
    with pytest.raises(main.HTTPException) as exc:
        asyncio.run(main.delete_draft("nope", _fake_request()))
    assert exc.value.status_code == 404


def test_delete_draft_refuses_a_real_generated_proposal(monkeypatch):
    monkeypatch.setattr(main, "_get_proposal_metadata",
                        lambda pid: {"status": "generated", "created_by_email": "planner@entravision.com"})
    with pytest.raises(main.HTTPException) as exc:
        asyncio.run(main.delete_draft("p1", _fake_request()))
    assert exc.value.status_code == 400


def test_delete_draft_refuses_another_planners_draft(monkeypatch):
    monkeypatch.setattr(main, "_get_proposal_metadata",
                        lambda pid: {"status": "draft", "created_by_email": "someone-else@entravision.com"})
    with pytest.raises(main.HTTPException) as exc:
        asyncio.run(main.delete_draft("d1", _fake_request("planner@entravision.com")))
    assert exc.value.status_code == 403


# --- _reconstruct_proposal_request ----------------------------------------


def test_reconstruct_proposal_request_converts_specifics_and_drops_unknown_keys():
    req = main._reconstruct_proposal_request({
        "client_name": "Acme",
        "specifics": {"sem": {"picked": True}},
        "not_a_real_field": "ignored",
    })
    assert req.client_name == "Acme"
    assert req.specifics.sem == {"picked": True}


# --- _build_reopen_state ---------------------------------------------------


def test_build_reopen_state_includes_the_previously_missing_fields():
    # monthly_distribution_mode/campaign_name_override were a real
    # pre-existing gap (reopening ANY proposal silently reset the
    # even/prorated choice) — this locks in the fix.
    state = main._build_reopen_state(
        request_dict={"client_name": "Acme"}, line_items=[], tiers=None, avails_data=None,
        strategy_brief=None, roadblocks=None, force_tabs=None, addons=[], raw_notion_text=None,
        enrichment=None, time_unit="week", monthly_distribution_mode="prorated",
        campaign_name_override="Q4 Push", wizard_step=5,
    )
    assert state["monthly_distribution_mode"] == "prorated"
    assert state["campaign_name_override"] == "Q4 Push"
    assert state["wizard_step"] == 5
    assert state["time_unit"] == "week"


# --- _resolve_draftable_proposal_id (shared by /api/generate and save_draft) --


def test_resolve_draftable_proposal_id_reuses_an_existing_drafts_id_in_place(monkeypatch):
    """A draft (status='draft') being finalized/re-saved promotes the SAME
    row — no orphaned Draft row left behind."""
    monkeypatch.setattr(main, "_get_proposal_metadata",
                        lambda pid: {"status": "draft"} if pid == "draft-1" else None)
    minted = []
    monkeypatch.setattr(main.secrets, "token_urlsafe", lambda n: minted.append(1) or "fresh-id")

    assert main._resolve_draftable_proposal_id("draft-1") == "draft-1"
    assert not minted  # never had to mint a fresh one


def test_resolve_draftable_proposal_id_mints_fresh_for_an_already_generated_proposal(monkeypatch):
    """Never reuses a row that's already status='generated' — whether the
    caller is /api/generate (regenerating creates a new history row, same
    as always) or save_draft (autosave must never downgrade a real, sent
    proposal back to 'draft')."""
    monkeypatch.setattr(main, "_get_proposal_metadata",
                        lambda pid: {"status": "generated"} if pid == "old-real-1" else None)
    monkeypatch.setattr(main.secrets, "token_urlsafe", lambda n: "fresh-id")

    assert main._resolve_draftable_proposal_id("old-real-1") == "fresh-id"


def test_resolve_draftable_proposal_id_mints_fresh_when_none_given(monkeypatch):
    monkeypatch.setattr(main.secrets, "token_urlsafe", lambda n: "fresh-id")
    assert main._resolve_draftable_proposal_id(None) == "fresh-id"


def test_resolve_draftable_proposal_id_mints_fresh_when_the_id_is_unknown(monkeypatch):
    # A stale/garbage id (e.g. state.proposalId from a deleted draft) must
    # never silently write into some OTHER row — fall back to a fresh id.
    monkeypatch.setattr(main, "_get_proposal_metadata", lambda pid: None)
    monkeypatch.setattr(main.secrets, "token_urlsafe", lambda n: "fresh-id")
    assert main._resolve_draftable_proposal_id("deleted-draft") == "fresh-id"
