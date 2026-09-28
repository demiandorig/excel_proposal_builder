"""Checks the 2026 form adapter and guarded operational transitions."""
import asyncio
import hashlib
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import request_workflow as workflow


def answers(**overrides):
    data = {
        workflow.QUESTION_IDS["first_name"]: "Yadira",
        workflow.QUESTION_IDS["last_name"]: "Rodriguez",
        workflow.QUESTION_IDS["email"]: "YRodriguez@entravision.com",
        workflow.QUESTION_IDS["market"]: "El Centro-Yuma (Tier 2)",
        workflow.QUESTION_IDS["request_type"]: "Proposal Page With Avails",
        workflow.QUESTION_IDS["client_name"]: "El Centro Motors Ford",
        workflow.QUESTION_IDS["monthly_budget"]: 7,
        workflow.QUESTION_IDS["proposal_due"]: "2026-09-28",
        workflow.QUESTION_IDS["products"]: ["Connected TV (OTT)", "SEM"],
    }
    data.update(overrides)
    return data


def test_seller_details_are_the_intake_entry_and_low_budget_is_flagged():
    normalized, issue = workflow.normalize_submission(answers())
    assert issue is None
    assert normalized["seller_name"] == "Yadira Rodriguez"
    assert normalized["seller_email"] == "yrodriguez@entravision.com"
    assert normalized["due_date"].isoformat() == "2026-09-28"
    assert normalized["monthly_budget"] == Decimal("7")
    assert normalized["details"]["needs_budget_review"] is True
    assert normalized["details"]["answers"][workflow.QUESTION_IDS["client_name"]] == "El Centro Motors Ford"


def test_bad_seller_or_request_type_is_quarantined():
    data = answers(**{workflow.QUESTION_IDS["email"]: "not-an-email"})
    assert workflow.normalize_submission(data)[0] is None


def test_renewal_and_research_keep_their_branch_client_names():
    renewal = answers(**{
        workflow.QUESTION_IDS["request_type"]: "Renewal Proposal Request",
        workflow.QUESTION_IDS["client_name"]: "",
        workflow.QUESTION_IDS["renewal_client"]: "Renewal Client",
        workflow.QUESTION_IDS["renewal_due"]: "2026-10-15",
    })
    normalized, issue = workflow.normalize_submission(renewal)
    assert issue is None
    assert normalized["client_name"] == "Renewal Client"
    assert normalized["due_date"].isoformat() == "2026-10-15"
    research = answers(**{
        workflow.QUESTION_IDS["request_type"]: "Audit / Research",
        workflow.QUESTION_IDS["client_name"]: "",
        workflow.QUESTION_IDS["research_client"]: "Research Client",
    })
    normalized, issue = workflow.normalize_submission(research)
    assert issue is None
    assert normalized["client_name"] == "Research Client"
    data = answers(**{workflow.QUESTION_IDS["request_type"]: "Unrecognized"})
    assert workflow.normalize_submission(data)[0] is None


def test_builder_seed_uses_request_code_and_preserves_source_answers():
    row = {
        "id": uuid.uuid4(), "code": "REQ-000031", "status": "Progress",
        "seller_name": "Yadira Rodriguez", "seller_email": "yrodriguez@entravision.com",
        "market": "El Centro-Yuma (Tier 2)", "request_type": "Proposal Page With Avails",
        "client_name": "El Centro Motors Ford", "monthly_budget": Decimal("7"),
        "details": {"answers": answers(), "needs_budget_review": True},
    }
    seeded = workflow.builder_seed(row)
    assert seeded["request"]["request_code"] == "REQ-000031"
    assert seeded["request"]["request_id"] == str(row["id"])
    assert seeded["request"]["products_selected"] == []
    assert any("Choose catalog products" in w for w in seeded["request"]["warnings"])
    assert any("under $100" in w for w in seeded["request"]["warnings"])
    assert seeded["suggested_tabs"]["net"] is True


def test_builder_seed_endpoint_requires_progress_and_owner_or_lead(monkeypatch):
    request_id = uuid.uuid4()
    row = {
        "id": request_id, "code": "REQ-000031", "status": "New", "owner_id": "planner-id",
        "seller_name": "Yadira Rodriguez", "seller_email": "yrodriguez@entravision.com",
        "market": "El Centro-Yuma (Tier 2)", "request_type": "Proposal Page With Avails",
        "client_name": "El Centro Motors Ford", "monthly_budget": Decimal("2000"),
        "details": {"answers": answers(), "needs_budget_review": False},
    }
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, sql, params=()): return SimpleNamespace(fetchone=lambda: row)
    monkeypatch.setenv("REQUEST_WORKFLOW_ENABLED", "1")
    monkeypatch.setattr(workflow, "get_connection", Conn)
    other = SimpleNamespace(state=SimpleNamespace(user={"id": "other-id", "is_admin": False}))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(workflow.get_builder_seed(request_id, other))
    assert exc.value.status_code == 409
    row["status"] = "Progress"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(workflow.get_builder_seed(request_id, other))
    assert exc.value.status_code == 403
    owner = SimpleNamespace(state=SimpleNamespace(user={"id": "planner-id", "is_admin": False}))
    assert asyncio.run(workflow.get_builder_seed(request_id, owner))["request"]["request_code"] == "REQ-000031"


class FakeConn:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if sql.startswith("UPDATE planning_requests SET status"):
            self.row["status"] = params[0]
            self.row["version"] += 1
        return SimpleNamespace(fetchone=lambda: dict(self.row))


def test_reviewing_requires_delivery_link_and_only_lead_can_complete(monkeypatch):
    request_id = uuid.uuid4()
    row = {
        "id": request_id, "code": "REQ-000031", "status": "Progress",
        "version": 1, "owner_id": "planner-id", "details": {},
    }
    conn = FakeConn(row)
    monkeypatch.setenv("REQUEST_WORKFLOW_ENABLED", "1")
    monkeypatch.setattr(workflow, "get_connection", lambda: conn)
    planner = SimpleNamespace(state=SimpleNamespace(user={"id": "planner-id", "email": "planner@entravision.com", "is_admin": False}))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(workflow.update_request(request_id, workflow.RequestChange(version=1, status="Reviewing"), planner))
    assert exc.value.status_code == 400
    result = asyncio.run(workflow.update_request(request_id, workflow.RequestChange(version=1, status="Reviewing", review_link="https://example.com/plan"), planner))
    assert result["status"] == "Reviewing"
    assert result["version"] == 2
    with pytest.raises(HTTPException) as exc:
        asyncio.run(workflow.update_request(request_id, workflow.RequestChange(version=2, status="Done"), planner))
    assert exc.value.status_code == 403
    lead = SimpleNamespace(state=SimpleNamespace(user={"id": "lead-id", "email": "lead@entravision.com", "is_admin": True}))
    assert asyncio.run(workflow.update_request(request_id, workflow.RequestChange(version=2, status="Done"), lead))["status"] == "Done"


def test_replayed_quarantined_submission_is_idempotent(monkeypatch):
    payload = {"schema_version": 1, "form_id": "form-test", "submission_id": "sub-1", "answers": answers()}
    encoded = json.dumps(payload).encode()
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    class IntakeConn:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, sql, params=()):
            if "payload_sha256 = %s" in sql:
                assert params == ("form-test", "sub-1", digest)
                return SimpleNamespace(fetchone=lambda: {"state": "quarantined", "request_id": None})
            if "pg_advisory_xact_lock" in sql:
                return SimpleNamespace(fetchone=lambda: None)
            raise AssertionError("An exact replay must not write another intake event")

    async def body(): return encoded
    request = SimpleNamespace(headers={"x-fillout-webhook-secret": "test-secret"}, body=body)
    monkeypatch.setenv("REQUEST_WORKFLOW_ENABLED", "1")
    monkeypatch.setenv("FILLOUT_FORM_ID", "form-test")
    monkeypatch.setenv("FILLOUT_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(workflow, "get_connection", lambda: IntakeConn())
    assert asyncio.run(workflow.fillout_intake(request)) == {"state": "quarantined", "request_id": None, "duplicate": True}
