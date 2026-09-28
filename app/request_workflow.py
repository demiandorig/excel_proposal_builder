"""Feature-flagged intake and planner queue for the request library.

Fillout's published form is not changed here. The adapter accepts a small,
versioned webhook envelope keyed by stable Fillout question IDs; the original
answers and payload are retained for later mapping fixes and audit.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from app.db import get_connection
from app.catalog import CATALOG
from app.services.notion_parser import ProposalRequest, classify_output_tabs

router = APIRouter()

REQUEST_TYPES = frozenset({
    "Question Only", "Renewal Proposal Request",
    "Proposal Page Only (No Avails Needed)",
    "Avails / Estimates Only (I don't need a proposal right now)",
    "Proposal Page With Avails", "Full Presentation", "Audit / Research",
})
STATUSES = ("New", "Progress", "Paused", "Reviewing", "Done", "Canceled")
PRIORITIES = ("Untriaged", "Low", "Medium", "High", "Critical")
ALLOWED_TRANSITIONS = {
    "New": {"Progress", "Paused", "Canceled"},
    "Progress": {"Paused", "Reviewing", "Canceled"},
    "Paused": {"Progress", "Canceled"},
    "Reviewing": {"Progress", "Done", "Canceled"},
    "Done": set(),
    "Canceled": set(),
}

# Latest unpublished 2026 export. Page/widget keys are retained in the
# contract so labels and choice order may change without breaking intake.
QUESTION_IDS = {
    "first_name": "bWXCG9rN4vgJy3NW8PGXqi",
    "last_name": "x1hGWHZCvQNBN1M4Mp3CH5",
    "email": "dNY5mW453Jki7XaasbZjeq",
    "market": "dmv47zNJ7ZuPHCoohkNYn6",
    "request_type": "q29Yauj9AFxZxg2nWGBEJh",
    "ccs": "2iNp",
    "client_name": "mtLKYCYxL7ZhAGp4eg2G3d",
    "monthly_budget": "mGb32w7djAxC61VPY73kaR",
    "proposal_due": "cy8qB3HbCHVBDH1BtCbjK1",
    "avails_due": "i1gtgdCtuvAgEDxTzvN4ne",
    "presentation_due": "7NMgmT5cedthgmqx4rnXUS",
    "audit_due": "3bSut7cWuupRo7sicQmZEa",
    "renewal_due": "oUYh",
    "question": "v8TzcExsPpezRzuo7p4Bm2",
    "client_website": "pV7z7uUgYe8NXdu2qPtRie",
    "agency_name": "oER6g78ritsEwXK3KuPp3V",
    "agency_fee": "kn6vCEaZjnkDa4zZG7JHiN",
    "start_date": "onR2euL1mHyaMBNGtJXjar",
    "end_date": "87bWNYfH6K28Hv4dYkjRw8",
    "total_months": "aPs4Dsz2FdnnZPwXjYXqPF",
    "campaign_goal": "e8Xx4r5PySpkSjKVZckMP4",
    "language": "c6B3BrAB7vXrmmpunZq73U",
    "other_languages": "rDjQAJ1FzshQs7zJRGkNnb",
    "geo": "6HCUyAhHxJ7JmgcVKhiw86",
    "demo": "tBwetcazcw8Mi6kXZyv2bX",
    "behavioral": "orX9nEQKtB5Q14W2FWGm8c",
    "contextual": "gSTah9NhAbrNyh5z9ezr3B",
    "products": "aeZEdQ6BnWCUPN51LyaAhZ",
    "comments": "22gP1C8VzpuuTtap44mCZf",
    "renewal_client": "aseU",
    "renewal_changes": "2Emq",
    "renewal_budget": "c8Dp",
    "research_client": "vUF4XmKSa1k2Woh9RS6QXR",
    "research_details": "5Ndpk6ePKJU7YJdBLrXkSA",
}


def enabled() -> bool:
    return os.getenv("REQUEST_WORKFLOW_ENABLED", "").lower() in {"1", "true", "yes"}


def require_enabled() -> None:
    if not enabled():
        raise HTTPException(status_code=404, detail="Request workflow is disabled")


def _plain(value: Any) -> str:
    if isinstance(value, dict) and set(value) == {"value"}:
        value = value["value"]
    if isinstance(value, (list, dict)):
        return ", ".join(str(v) for v in value) if isinstance(value, list) else json.dumps(value)
    return str(value or "").strip()


def _answer(answers: dict, name: str) -> str:
    key = QUESTION_IDS[name]
    # Advanced Fillout webhooks may qualify a widget ID with its page ID.
    return _plain(answers.get(key, answers.get(next((k for k in answers if k.endswith("." + key)), ""), "")))


def _date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def normalize_submission(answers: dict) -> tuple[dict | None, str | None]:
    if not isinstance(answers, dict):
        return None, "answers must be an object keyed by Fillout question ID"
    first = _answer(answers, "first_name")
    last = _answer(answers, "last_name")
    email = _answer(answers, "email").lower()
    market = _answer(answers, "market")
    request_type = _answer(answers, "request_type")
    if not first or not last or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        return None, "Seller name and valid seller email are required"
    if not market:
        return None, "Seller market is required"
    if request_type not in REQUEST_TYPES:
        return None, "Unknown or missing request type"
    due_key = {
        "Question Only": None,
        "Renewal Proposal Request": "renewal_due",
        "Proposal Page Only (No Avails Needed)": "proposal_due",
        "Avails / Estimates Only (I don't need a proposal right now)": "avails_due",
        "Proposal Page With Avails": "proposal_due",
        "Full Presentation": "presentation_due",
        "Audit / Research": "audit_due",
    }[request_type]
    due_raw = _answer(answers, due_key) if due_key else ""
    due = _date(due_raw)
    if due_raw and not due:
        return None, "Invalid due date"
    budget_raw = _answer(answers, "monthly_budget")
    budget = None
    if budget_raw:
        try:
            budget = Decimal(budget_raw.replace(",", ""))
            if not budget.is_finite() or budget < 0 or budget > Decimal("999999999999.99"):
                raise InvalidOperation
        except InvalidOperation:
            return None, "Invalid monthly budget"
    client_name = _answer(answers, "client_name")
    if not client_name and request_type == "Renewal Proposal Request":
        client_name = _answer(answers, "renewal_client")
    if not client_name and request_type == "Audit / Research":
        client_name = _answer(answers, "research_client")
    return {
        "seller_name": f"{first} {last}", "seller_email": email,
        "market": market, "request_type": request_type,
        "client_name": client_name,
        "due_date": due, "monthly_budget": budget,
        "details": {"answers": answers, "mapping_version": 1,
                    "needs_budget_review": budget is not None and budget < 100,
                    "seller_ccs": _answer(answers, "ccs")},
    }, None


def builder_seed(row: dict) -> dict:
    """Prefill Step 02 from immutable intake; planner confirms before use."""
    answers = row["details"]["answers"]
    req = ProposalRequest(
        request_id=str(row["id"]), request_code=row["code"],
        requested_by=row["seller_name"], salesperson_email=row["seller_email"],
        salesperson_market=row["market"], ccs=_answer(answers, "ccs"),
        request_type=row["request_type"], client_name=row["client_name"],
        client_website=_answer(answers, "client_website"),
        agency_name=_answer(answers, "agency_name"),
        start_date=_answer(answers, "start_date")[:10],
        end_date=_answer(answers, "end_date")[:10],
        monthly_budget=float(row["monthly_budget"]) if row["monthly_budget"] is not None else None,
        campaign_goal=_answer(answers, "campaign_goal"),
        language=_answer(answers, "language"),
        other_languages=_answer(answers, "other_languages"),
        geo=_answer(answers, "geo"), demo=_answer(answers, "demo"),
        behavioral=_answer(answers, "behavioral"),
        contextual=_answer(answers, "contextual"),
        salesperson_comments=_answer(answers, "comments"),
        question_details=_answer(answers, "question"),
        renewal_client=_answer(answers, "renewal_client"),
        renewal_changes_description=_answer(answers, "renewal_changes"),
        renewal_budget=_answer(answers, "renewal_budget"),
        renewal_due_date=_answer(answers, "renewal_due"),
    )
    months = _answer(answers, "total_months")
    if months.isdigit():
        req.total_months = int(months)
    selected = answers.get(QUESTION_IDS["products"], [])
    source_products = ([str(v) for v in selected if v] if isinstance(selected, list)
                       else [v.strip() for v in str(selected).split(",") if v.strip()] if selected else [])
    req.products_selected_raw = ", ".join(source_products)
    catalog_names = {p.name.casefold(): p.name for p in CATALOG}
    req.products_selected = [catalog_names[v.casefold()] for v in source_products if v.casefold() in catalog_names]
    unmatched = [v for v in source_products if v.casefold() not in catalog_names]
    if unmatched:
        req.warnings.append("Choose catalog products for these Fillout selections: " + ", ".join(unmatched))
    fee = _answer(answers, "agency_fee")
    if fee:
        req.warnings.append("Agency fee needs planner review; the source answer is preserved in request details.")
    if row["details"].get("needs_budget_review"):
        req.warnings.append("Monthly budget is under $100; confirm the source amount before generating.")
    return {"request": req.to_dict(),
            "suggested_tabs": classify_output_tabs(req.request_type, source_products, False)}


def _snapshot(row: dict) -> dict:
    return {k: str(v) if isinstance(v, (date, Decimal, uuid.UUID)) else v
            for k, v in row.items() if k != "details"}


def _activity(conn, row: dict, actor: str, action: str, before: dict | None = None, note: str | None = None) -> None:
    conn.execute(
        "INSERT INTO planning_request_activity (id, request_id, actor_email, action, before_state, after_state, note) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (uuid.uuid4(), row["id"], actor, action,
         Jsonb(_snapshot(before)) if before else None, Jsonb(_snapshot(row)), note),
    )


@router.post("/api/intake/fillout")
async def fillout_intake(request: Request) -> dict:
    require_enabled()
    secret = os.getenv("FILLOUT_WEBHOOK_SECRET", "")
    expected_form = os.getenv("FILLOUT_FORM_ID", "")
    supplied = request.headers.get("x-fillout-webhook-secret", "")
    if not secret or not expected_form:
        raise HTTPException(status_code=503, detail="Intake is not configured")
    if not hmac.compare_digest(supplied, secret):
        raise HTTPException(status_code=401, detail="Invalid intake credential")
    content_length = request.headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > 256_000:
        raise HTTPException(status_code=413, detail="Payload too large")
    raw = await request.body()
    if len(raw) > 256_000:
        raise HTTPException(status_code=413, detail="Payload too large")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(body, dict) or body.get("schema_version") != 1:
        raise HTTPException(status_code=400, detail="Expected version 1 intake envelope")
    form_id = body.get("form_id")
    submission_id = body.get("submission_id")
    if form_id != expected_form or not isinstance(submission_id, str) or not submission_id.strip():
        raise HTTPException(status_code=400, detail="Form or submission ID mismatch")
    answers = body.get("answers")
    normalized, issue = normalize_submission(answers)
    payload_hash = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    with get_connection() as conn:
        # Serialize deliveries for one source submission, including edits
        # delivered with a different payload hash on another worker.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                     (form_id + ":" + submission_id,))
        exact = conn.execute(
            "SELECT id, state, request_id FROM planning_intake_events "
            "WHERE form_id = %s AND submission_id = %s AND payload_sha256 = %s",
            (form_id, submission_id, payload_hash),
        ).fetchone()
        if exact:
            return {"state": exact["state"], "request_id": str(exact["request_id"]) if exact["request_id"] else None,
                    "duplicate": True}
        prior = conn.execute(
            "SELECT id FROM planning_intake_events WHERE form_id = %s AND submission_id = %s LIMIT 1",
            (form_id, submission_id),
        ).fetchone()
        if prior:
            issue = "Changed submission requires review; original request was preserved"
        event_id = uuid.uuid4()
        state = "quarantined" if issue else "accepted"
        conn.execute(
            "INSERT INTO planning_intake_events (id, form_id, submission_id, payload_sha256, payload, state, issue) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (event_id, form_id, submission_id, payload_hash, Jsonb(body), state, issue),
        )
        if issue:
            return {"state": state, "issue": issue, "request_id": None}
        request_id = uuid.uuid4()
        number = conn.execute("SELECT nextval('planning_request_number_seq') AS n").fetchone()["n"]
        code = f"REQ-{number:06d}"
        row = conn.execute(
            "INSERT INTO planning_requests (id, code, intake_event_id, request_type, seller_name, "
            "seller_email, market, client_name, due_date, monthly_budget, details) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (request_id, code, event_id, normalized["request_type"], normalized["seller_name"],
             normalized["seller_email"], normalized["market"], normalized["client_name"],
             normalized["due_date"], normalized["monthly_budget"], Jsonb(normalized["details"])),
        ).fetchone()
        conn.execute("UPDATE planning_intake_events SET request_id = %s WHERE id = %s",
                     (request_id, event_id))
        _activity(conn, row, "fillout", "created")
    return {"state": "accepted", "request_id": str(request_id), "code": code, "duplicate": False}


@router.get("/api/requests")
async def list_requests(request: Request, status: str = "Active", search: str = "", page: int = 1) -> dict:
    require_enabled()
    if status not in (*STATUSES, "Active", "All") or page < 1:
        raise HTTPException(status_code=400, detail="Invalid filter")
    search = search.strip()[:120]
    where = []
    params: list[Any] = []
    if status == "Active":
        where.append("r.status NOT IN ('Done', 'Canceled')")
    elif status != "All":
        where.append("r.status = %s")
        params.append(status)
    if search:
        where.append("(r.code ILIKE %s OR r.client_name ILIKE %s OR r.seller_name ILIKE %s OR r.seller_email ILIKE %s)")
        params.extend([f"%{search}%"] * 4)
    clause = " WHERE " + " AND ".join(where) if where else ""
    with get_connection() as conn:
        total = conn.execute("SELECT count(*) AS n FROM planning_requests r" + clause, params).fetchone()["n"]
        rows = conn.execute(
            "SELECT r.id, r.code, r.request_type, r.seller_name, r.seller_email, r.market, "
            "r.client_name, r.due_date, r.monthly_budget, r.priority, r.status, r.owner_id, "
            "u.email AS owner_email, r.version, r.created_at "
            "FROM planning_requests r LEFT JOIN users u ON u.id = r.owner_id" + clause +
            " ORDER BY r.due_date ASC NULLS LAST, r.created_at DESC LIMIT 50 OFFSET %s",
            (*params, (page - 1) * 50),
        ).fetchall()
    return {"requests": [_snapshot(r) for r in rows], "total": total, "page": page,
            "is_lead": bool(request.state.user["is_admin"]),
            "viewer_id": request.state.user["id"]}


@router.get("/api/requests/{request_id}")
async def get_request(request_id: uuid.UUID) -> dict:
    require_enabled()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT r.*, u.email AS owner_email, e.submission_id, e.form_id "
            "FROM planning_requests r LEFT JOIN users u ON u.id = r.owner_id "
            "JOIN planning_intake_events e ON e.id = r.intake_event_id "
            "WHERE r.id = %s", (request_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Request not found")
        activity = conn.execute(
            "SELECT actor_email, action, note, created_at FROM planning_request_activity "
            "WHERE request_id = %s ORDER BY created_at DESC LIMIT 100", (request_id,),
        ).fetchall()
        proposals = conn.execute(
            "SELECT p.proposal_id, p.status, p.proposal_title, p.updated_at "
            "FROM planning_request_proposals l JOIN proposals p ON p.proposal_id = l.proposal_id "
            "WHERE l.request_id = %s ORDER BY p.updated_at DESC", (request_id,),
        ).fetchall()
    result = _snapshot(row)
    result["details"] = row["details"]
    result["activity"] = [_snapshot(a) for a in activity]
    result["proposals"] = [_snapshot(p) for p in proposals]
    return result


@router.get("/api/requests/{request_id}/builder")
async def get_builder_seed(request_id: uuid.UUID, request: Request) -> dict:
    require_enabled()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM planning_requests WHERE id = %s", (request_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    if row["status"] != "Progress":
        raise HTTPException(status_code=409, detail="Move the request to Progress before starting a proposal")
    if not request.state.user["is_admin"] and row["owner_id"] != request.state.user["id"]:
        raise HTTPException(status_code=403, detail="Only the owner or lead can start this proposal")
    return builder_seed(row)


class RequestChange(BaseModel):
    version: int = Field(ge=1)
    status: str | None = None
    priority: str | None = None
    owner_id: str | None = None
    note: str = Field(default="", max_length=2000)
    review_link: str | None = None


@router.patch("/api/requests/{request_id}")
async def update_request(request_id: uuid.UUID, body: RequestChange, request: Request) -> dict:
    require_enabled()
    user = request.state.user
    is_lead = bool(user["is_admin"])
    changed = [k for k in ("status", "priority", "owner_id") if getattr(body, k) is not None]
    if len(changed) != 1:
        raise HTTPException(status_code=400, detail="Change one operational field at a time")
    field = changed[0]
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM planning_requests WHERE id = %s FOR UPDATE", (request_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Request not found")
        if row["version"] != body.version:
            raise HTTPException(status_code=409, detail="Request changed; reload before updating")
        if field in ("priority", "owner_id") and not is_lead:
            raise HTTPException(status_code=403, detail="Lead access required")
        if field == "status":
            if not is_lead and row["owner_id"] != user["id"]:
                raise HTTPException(status_code=403, detail="Only the owner or lead can change status")
            if body.status not in ALLOWED_TRANSITIONS[row["status"]]:
                raise HTTPException(status_code=409, detail="Invalid status transition")
            if body.status in ("Done", "Canceled") and not is_lead:
                raise HTTPException(status_code=403, detail="Lead access required for completion or cancellation")
            if body.status == "Canceled" and not body.note.strip():
                raise HTTPException(status_code=400, detail="Cancellation reason is required")
            if body.status == "Reviewing":
                parsed = urlparse(body.review_link or "")
                valid_link = parsed.scheme in ("http", "https") and bool(parsed.netloc)
                if body.review_link and not valid_link:
                    raise HTTPException(status_code=400, detail="Invalid seller delivery link")
                if not valid_link and not body.note.strip():
                    raise HTTPException(status_code=400, detail="Seller delivery link or delivery note is required for Reviewing")
                conn.execute(
                    "UPDATE planning_requests SET status = %s, reviewing_at = now(), "
                    "review_link = %s, version = version + 1, updated_at = now() WHERE id = %s",
                    (body.status, body.review_link, request_id),
                )
            else:
                timestamp = {"Done": "done_at", "Canceled": "canceled_at"}.get(body.status)
                extra = f", {timestamp} = now()" if timestamp else ""
                conn.execute(
                    "UPDATE planning_requests SET status = %s, version = version + 1, updated_at = now()" + extra +
                    " WHERE id = %s", (body.status, request_id),
                )
        elif field == "priority":
            if body.priority not in PRIORITIES:
                raise HTTPException(status_code=400, detail="Invalid priority")
            conn.execute("UPDATE planning_requests SET priority = %s, version = version + 1, "
                         "updated_at = now() WHERE id = %s", (body.priority, request_id))
        else:
            owner = conn.execute("SELECT id FROM users WHERE id = %s AND disabled = FALSE",
                                 (body.owner_id,)).fetchone()
            if not owner:
                raise HTTPException(status_code=400, detail="Owner must be an active user")
            conn.execute("UPDATE planning_requests SET owner_id = %s, version = version + 1, "
                         "updated_at = now() WHERE id = %s", (body.owner_id, request_id))
        updated = conn.execute("SELECT * FROM planning_requests WHERE id = %s", (request_id,)).fetchone()
        _activity(conn, updated, user["email"], field + "_changed", row, body.note.strip() or None)
    return _snapshot(updated)


@router.get("/api/requests/users/assignable")
async def assignable_users(request: Request) -> dict:
    require_enabled()
    if not request.state.user["is_admin"]:
        raise HTTPException(status_code=403, detail="Lead access required")
    with get_connection() as conn:
        rows = conn.execute("SELECT id, email FROM users WHERE disabled = FALSE ORDER BY email").fetchall()
    return {"users": rows}


@router.get("/api/admin/request-intake")
async def intake_exceptions() -> dict:
    """Admin-only exception list; the existing auth middleware gates /api/admin/."""
    require_enabled()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, form_id, submission_id, received_at, issue FROM planning_intake_events "
            "WHERE state = 'quarantined' ORDER BY received_at DESC LIMIT 100"
        ).fetchall()
    return {"exceptions": [_snapshot(row) for row in rows]}
