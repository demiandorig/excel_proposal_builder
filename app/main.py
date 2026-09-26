"""
FastAPI server for the Entravision Proposal Builder.

Endpoints:
  GET  /                    -> serves the single-page web UI
  GET  /api/catalog         -> returns the AdFlo product catalog (id, name, family, rate, min)
  POST /api/parse           -> body: {notion_text} -> parses to ProposalRequest dict
  POST /api/recommend       -> body: {request, monthly_budget} -> suggested line items
  POST /api/generate        -> body: {request, line_items, force_tabs?} -> downloadable .xlsx
  GET  /api/download/{id}   -> serves a generated .xlsx
  POST /api/drive/upload    -> body: {proposal_id, seller_email} -> uploads to Google Drive
                               (graceful no-op if Drive creds aren't configured)
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import re
import secrets
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

# Load .env (OPENAI_API_KEY, DRIVE_CLIENT_ID/SECRET/ROOT_FOLDER_ID, etc.) into
# the process environment before anything below reads os.environ — the repo
# has shipped a .env/.env.example convention for a while, but nothing ever
# actually loaded it, so values sitting in .env silently had no effect
# unless separately exported in the shell. python-dotenv never overwrites a
# variable that's already set in the real environment, so an explicit shell
# export still takes precedence over .env if both are present.
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Body, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from psycopg.types.json import Jsonb

from app import auth as auth_svc

from app.db import fetch_all, fetch_one, get_connection
from app.catalog import (
    CATALOG, by_name, families, by_family,
    effective_catalog, load_rate_overrides, save_rate_overrides, clear_rate_override,
    load_custom_products, add_custom_product, delete_custom_product, update_custom_product,
    _OVERRIDABLE_FIELDS,
    record_product_alias, resolve_product_alias, all_product_aliases,
    load_deleted_builtin_names, set_builtin_deleted,
)
from app.services.notion_parser import (
    ProposalRequest,
    ProductSpecifics,
    parse_notion,
    classify_output_tabs,
)
from app.services.proposal_generator import LineItem, AddonItem, generate_proposal
from app.services.recommender import recommend_line_items
from app.market_config import (
    load_market_config, set_market_entry, delete_market_entry, DEFAULT_KEY,
    BASE_CCS_KEY, get_base_ccs, set_base_ccs, get_all_ccs_for_market,
    T1_CCS_KEY, get_t1_ccs, set_t1_ccs, T1_SPEND_THRESHOLD,
)
from app.services import drive_uploader
from app.services import ai_enricher
from app.services import docx_builder
from app.services import pptx_builder
from app.services import strategy_brief as strategy_brief_svc
from app.services import ad_presence as ad_presence_svc
from app.services import roadblocks as roadblocks_svc
from app.services import monthly_allocation
from app.services import notion_client


# ---------------------------------------------------------------------------
# App + storage
# ---------------------------------------------------------------------------

app = FastAPI(title="Entravision Proposal Builder", version="0.1.0")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
# Persistent storage — NOT the OS temp dir, which Windows periodically clears.
# At ~10-30 proposals/day this is a trivial number of small files for a flat
# directory (tens of thousands/year); override via PROPOSALS_DIR env var if
# you'd rather point it at a shared/network location.
PROPOSALS_DIR = Path(os.environ.get("PROPOSALS_DIR") or (BASE_DIR.parent / "data" / "proposals"))
PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)


def _migrate_legacy_temp_proposals() -> None:
    """
    One-time migration: earlier versions of this app stored proposals in the
    OS temp dir, which Windows periodically clears. If that folder still has
    files and the new persistent folder is empty, copy them over so existing
    history isn't stranded behind the switch.
    """
    if any(PROPOSALS_DIR.iterdir()):
        return  # already has content — never overwrite
    legacy_dir = Path(tempfile.gettempdir()) / "entravision_proposals"
    if not legacy_dir.exists():
        return
    import shutil
    for f in legacy_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, PROPOSALS_DIR / f.name)


_migrate_legacy_temp_proposals()

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def _no_cache_static(request, call_next):
    """
    Force every /static/* response (app.js, styles.css, admin.js, ...) to
    skip browser caching entirely. Found the hard way: a planner (or a
    Claude verification session) reloading the page after a deploy can
    keep running yesterday's app.js indefinitely — StaticFiles' default
    Last-Modified/ETag caching lets the browser serve straight from its
    own disk cache without even a conditional request in some cases, so a
    real fix can silently appear to "not work" for anyone still on the
    stale cached copy. This is a small internal tool, not a high-traffic
    site — trading away asset caching entirely is a trivial cost next to
    "the browser might be running old code and nobody can tell."
    """
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Login — every route needs a valid session except this small allowlist
# (plus /static/* — just JS/CSS/images, no data) and everything under
# /api/admin/ (+ the /admin page) additionally needs is_admin. See
# app/auth.py for the session model itself.
# ---------------------------------------------------------------------------
SESSION_COOKIE_NAME = "session_token"
_PUBLIC_PATHS = {
    "/login",
    "/api/login",
    "/api/health",
    "/api/market-ccs",  # already a deliberately public, non-admin lookup (see its own route below)
}


@app.middleware("http")
async def _require_login(request: Request, call_next):
    """
    Rejects anything not on the public allowlist without a valid session
    cookie — an HTML page request redirects to /login (with `next` so it
    lands back where it was headed), an /api/* request gets a plain JSON
    401/403 instead (app/static/session-guard.js, loaded on every page,
    turns a 401 from ANY fetch call into a client-side redirect, so a
    session that expires mid-use is handled the same way as never having
    logged in). A valid, non-admin session hitting an admin-only path
    (the /admin page or /api/admin/*) gets 403 (API) / redirected to `/`
    (page) instead of 401 — they ARE logged in, they just can't be here.
    """
    path = request.url.path
    if path in _PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)

    is_api = path.startswith("/api/")
    token = request.cookies.get(SESSION_COOKIE_NAME)
    try:
        user = auth_svc.get_user_by_session(token) if token else None
    except RuntimeError:
        # DATABASE_URL not set/reachable — fail closed (no silent bypass of
        # login just because the session store itself is unavailable).
        user = None

    if user is None:
        if is_api:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return RedirectResponse(f"/login?next={quote(path, safe='')}")

    if (path == "/admin" or path.startswith("/api/admin/")) and not user["is_admin"]:
        if is_api:
            return JSONResponse({"detail": "Admin access required"}, status_code=403)
        return RedirectResponse("/")

    request.state.user = user
    response = await call_next(request)
    try:
        # Rolling expiry — every authenticated request pushes the session
        # another SESSION_LIFETIME_DAYS forward, so an active user is
        # effectively never logged out (see app/auth.py's own docstring).
        auth_svc.refresh_session(token)
    except Exception as e:
        _logger.warning("Session refresh failed (non-fatal): %s: %s", type(e).__name__, e)
    return response


@app.on_event("startup")
async def _on_startup() -> None:
    """
    Best-effort, never fatal to app startup — this app has always been
    able to START without a reachable DATABASE_URL (individual DB-backed
    requests fail on their own instead, per app/db.py's own RuntimeError),
    and login shouldn't change that: a startup DB hiccup should still let
    the process come up rather than crash-looping.
    """
    try:
        auth_svc.bootstrap_admin_from_env()
        auth_svc.purge_expired_sessions()
    except Exception as e:
        _logger.warning("Startup auth bootstrap/session-purge skipped: %s: %s", type(e).__name__, e)


def _static_asset_version() -> int:
    """
    One shared cache-busting version for every /static/* reference in a
    served HTML page — the newest mtime across app/static/, as a plain
    integer. Belt-and-suspenders alongside the no-cache middleware above:
    that middleware only helps once the browser actually asks the server
    again, and some caching layers skip that ask entirely for a URL
    they've already seen; appending ?v=<version> makes every deploy a
    genuinely new URL, which forces the request regardless.
    """
    try:
        return int(max(f.stat().st_mtime for f in STATIC_DIR.rglob("*") if f.is_file()))
    except ValueError:
        return 0  # empty directory — shouldn't happen, but don't crash the page over it


def _serve_html_with_cache_busted_static(path: Path) -> HTMLResponse:
    """Read an HTML template and append ?v=<version> to every /static/...
    reference (src="..." or href="...") so a deploy is never masked by a
    stale cached app.js/styles.css — see _static_asset_version() above."""
    html = path.read_text(encoding="utf-8")
    version = _static_asset_version()
    html = re.sub(
        r'((?:src|href)="/static/[^"?]+)"',
        rf'\1?v={version}"',
        html,
    )
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_next_short_id() -> str:
    """Return a sequential 4-digit proposal ID, persisted across restarts."""
    counter_file = PROPOSALS_DIR / "_counter.json"
    try:
        count = json.loads(counter_file.read_text()).get("count", 0) + 1 if counter_file.exists() else 1
        counter_file.write_text(json.dumps({"count": count}))
        return f"{count:04d}"
    except Exception:
        import random
        return str(random.randint(1000, 9999))


def _reconstruct_proposal_request(raw: dict) -> ProposalRequest:
    """Turn a `request` dict (as every endpoint receives `body.request`
    from the frontend) back into a real ProposalRequest — the exact same
    dict -> dataclass reconstruction every endpoint touching `request`
    needs (specifics sub-dict -> ProductSpecifics, unknown/extra keys
    dropped). Was copy-pasted at 6 separate call sites (one per endpoint);
    consolidated into one function so a future ProposalRequest field
    doesn't need updating in 6+ places to actually be reachable — same
    "one shared place, not N near-identical copies" fix already applied
    to normalize_newlines (see text_utils.py) and the JSON-parsing layer
    (see llm_utils.py)."""
    raw = dict(raw)
    if "specifics" in raw and isinstance(raw["specifics"], dict):
        raw["specifics"] = ProductSpecifics(**raw["specifics"])
    valid_fields = set(ProposalRequest.__dataclass_fields__.keys())
    raw = {k: v for k, v in raw.items() if k in valid_fields}
    return ProposalRequest(**raw)


def _build_reopen_state(
    *,
    request_dict: dict,
    line_items: list[LineItemModel],
    tiers: Optional[list[TierModel]],
    avails_data: Optional[dict[str, AvailsEntry]],
    strategy_brief: Optional[dict],
    roadblocks: Optional[dict],
    force_tabs: Optional[dict],
    addons: list[AddonItemModel],
    raw_notion_text: Optional[str],
    enrichment: Optional[dict],
    time_unit: str,
    monthly_distribution_mode: str,
    campaign_name_override: Optional[str],
    wizard_step: int,
) -> dict:
    """Everything a reopen needs to fully restore the wizard — shared by
    /api/generate (a completed proposal) and /api/proposal/draft (an
    in-progress one), so the two can't quietly drift apart on what
    "resuming" actually restores. Was previously built inline only inside
    /api/generate and was missing monthly_distribution_mode/
    campaign_name_override entirely — a real pre-existing gap (reopening
    ANY proposal silently reset Step 05's even/prorated choice back to
    "even") fixed here for both call sites at once."""
    return {
        "request": request_dict,
        "line_items": [li.model_dump() for li in (line_items or (tiers[0].line_items if tiers else []))],
        "avails_data": {
            k: v.model_dump() for k, v in
            (avails_data or (tiers[0].avails_data if tiers else {}) or {}).items()
        },
        "tiers": [t.model_dump() for t in tiers] if tiers else None,
        "strategy_brief": strategy_brief,
        "roadblocks": roadblocks,
        "force_tabs": force_tabs,
        "addons": [a.model_dump() for a in (addons or [])],
        "raw_notion_text": raw_notion_text,
        "enrichment": enrichment,
        # time_unit/monthly_distribution_mode are PROPOSAL-wide (no tier
        # home) — each tier's own period_merge_groups already rides along
        # inside "tiers" above (TierModel.model_dump() includes it).
        # Without time_unit specifically, reopening a non-month proposal
        # would default state.timeUnit back to "month" client-side while
        # monthly_allocations keys stay in the OLD format (e.g.
        # "W1-2026-09-01"), which _mbReconcileMonthsForDateChange would
        # then treat as entirely unrecognized — silently wiping the
        # reopened breakdown's apparent numbers.
        "time_unit": time_unit,
        "monthly_distribution_mode": monthly_distribution_mode,
        "campaign_name_override": campaign_name_override,
        # Which step this was saved/generated at — a draft resumes here;
        # a completed generate stamps 8, though the reopen flow for an
        # already-generated proposal ignores this and always lands on
        # Curate (Step 04) as it always has.
        "wizard_step": max(1, min(8, wizard_step or 1)),
    }


def _resolve_draftable_proposal_id(requested_proposal_id: Optional[str]) -> str:
    """Shared by /api/generate AND /api/proposal/draft (save_draft) —
    reuses `requested_proposal_id` (state.proposalId, from an earlier
    draft save or a reopened proposal) IN PLACE only when it still
    belongs to a draft or doesn't exist yet. A real, already-GENERATED
    proposal's row is NEVER reused, by either caller: /api/generate
    mints a fresh id for it (unchanged from before drafts existed, so
    its prior download history/files stay intact under their own row),
    and — critically — so does save_draft(), which otherwise would
    silently flip a real, already-sent proposal's status back to
    'draft' and null out its filename/generated_at/summary columns the
    moment autosave (or a stray manual Save Draft click) fires while the
    planner is just looking at a reopened, completed proposal. Extracted
    so this resolution logic is directly testable without invoking the
    whole generate()/save_draft() handlers."""
    existing = _get_proposal_metadata(requested_proposal_id) if requested_proposal_id else None
    if existing and existing.get("status") == "draft":
        return requested_proposal_id
    return secrets.token_urlsafe(16)


_PROPOSAL_COLUMNS = (
    "proposal_id, client_name, seller_email, created_by_email, requested_by, notion_id, "
    "proposal_title, filename, email_doc_filename, pptx_net_filename, "
    "pptx_gross_filename, generated_at, requester_ip, requester_user_agent, "
    "summary, reopen_state, status, updated_at"
)


def _proposal_metadata_from_row(row: dict | None) -> dict | None:
    """Restore the metadata shape used by the existing API from a DB row."""
    if row is None:
        return None
    meta = dict(row)
    generated_at = meta.get("generated_at")
    if hasattr(generated_at, "isoformat"):
        meta["generated_at"] = generated_at.isoformat()
    updated_at = meta.get("updated_at")
    if hasattr(updated_at, "isoformat"):
        meta["updated_at"] = updated_at.isoformat()
    meta["status"] = meta.get("status") or "generated"
    meta["summary"] = meta.get("summary") or {}
    meta["reopen_state"] = meta.get("reopen_state") or {}
    if meta.get("filename"):
        meta["path"] = str(PROPOSALS_DIR / meta["filename"])
    if meta.get("email_doc_filename"):
        meta["email_doc_path"] = str(PROPOSALS_DIR / meta["email_doc_filename"])
    return meta


def _get_proposal_metadata(proposal_id: str) -> dict | None:
    row = fetch_one(
        f"SELECT {_PROPOSAL_COLUMNS} FROM proposals WHERE proposal_id = %s",
        (proposal_id,),
    )
    return _proposal_metadata_from_row(row)


def _save_proposal_metadata(
    *,
    proposal_id: str,
    client_name: str,
    seller_email: str,
    created_by_email: str,
    requested_by: str,
    notion_id: str | None,
    proposal_title: str,
    reopen_state: dict,
    # Only known once a real Excel/email/deck has actually been built —
    # None for a draft save (see status="draft" below), always given by
    # the real /api/generate call site.
    filename: str | None = None,
    email_doc_filename: str | None = None,
    pptx_net_filename: str | None = None,
    pptx_gross_filename: str | None = None,
    generated_at: datetime | None = None,
    requester_ip: str | None = None,
    requester_user_agent: str | None = None,
    summary: dict | None = None,
    # 'generated' (the default, matching every row before this column
    # existed) or 'draft' — see schema.sql's migration comment. Callers
    # that pass neither `filename` nor a non-default `status` reproduce
    # this function's exact pre-draft-feature behavior.
    status: str = "generated",
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO proposals (
                proposal_id, client_name, seller_email, created_by_email, requested_by, notion_id,
                proposal_title, filename, email_doc_filename, pptx_net_filename,
                pptx_gross_filename, generated_at, requester_ip,
                requester_user_agent, summary, reopen_state, status, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (proposal_id) DO UPDATE SET
                client_name = EXCLUDED.client_name,
                seller_email = EXCLUDED.seller_email,
                created_by_email = EXCLUDED.created_by_email,
                requested_by = EXCLUDED.requested_by,
                notion_id = EXCLUDED.notion_id,
                proposal_title = EXCLUDED.proposal_title,
                filename = EXCLUDED.filename,
                email_doc_filename = EXCLUDED.email_doc_filename,
                pptx_net_filename = EXCLUDED.pptx_net_filename,
                pptx_gross_filename = EXCLUDED.pptx_gross_filename,
                generated_at = EXCLUDED.generated_at,
                requester_ip = EXCLUDED.requester_ip,
                requester_user_agent = EXCLUDED.requester_user_agent,
                summary = EXCLUDED.summary,
                reopen_state = EXCLUDED.reopen_state,
                status = EXCLUDED.status,
                updated_at = now()
            """,
            (
                proposal_id,
                client_name,
                seller_email,
                created_by_email,
                requested_by,
                notion_id,
                proposal_title,
                filename,
                email_doc_filename,
                pptx_net_filename,
                pptx_gross_filename,
                generated_at,
                requester_ip,
                requester_user_agent,
                Jsonb(summary or {}),
                Jsonb(reopen_state),
                status,
            ),
        )


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class ParseRequest(BaseModel):
    notion_text: str = Field(..., description="Raw paste from Notion")


class LineItemModel(BaseModel):
    # Stable per-line identity from the frontend — lets two lines share the
    # same product (e.g. same product, different targeting) without their
    # avails colliding. Optional only so older/manual API calls don't break.
    id: Optional[str] = None
    product_name: str
    monthly_budget: float
    months: int = 3
    rate_override: Optional[float] = None
    notes_override: Optional[str] = None
    target_override: Optional[str] = None
    target_secondary: Optional[str] = None  # secondary audience for added scale/avails
    estimated_cpm_override: Optional[float] = None  # Step 04 override of the catalog's estimated CPM (Fixed/impressions-estimate products)
    buying_model_override: Optional[str] = None  # Step 04 override of the catalog's buying model (CPM/CPP/Fixed)
    is_added_value: bool = False  # $0 budget is deliberate — exempt from below-minimum validation, sorts to the bottom of the export
    added_value_pct: Optional[float] = None  # AV lines only: this % of the tier's real (non-AV) budget is the line's estimated gift value, shown in the export
    # Step 04's per-line objective dropdown (Awareness / Website Conversion /
    # Click-To-Call / Conquesting / Lead Generation / a free-text "Other").
    # Defaults client-side from the Step 02-inferred campaign_goal but is
    # fully planner-editable per line — None only for older/manual API calls
    # that never sent one, in which case the export falls back to the
    # catalog's own short_label exactly as it always has.
    objective_override: Optional[str] = None
    # Optional Step 04.5 "Monthly Breakdown" — {"YYYY-MM": dollars, ...}.
    # None/empty means this line doesn't use it (the feature is entirely
    # opt-in per line, inferred from data presence — see
    # app/services/monthly_allocation.py's module docstring for why
    # there's deliberately no separate enabled/disabled flag). Dollars are
    # the source of truth; percentage is always derived from these on
    # both the client and server, never stored separately.
    # Key format tracks GenerateRequest.time_unit: "YYYY-MM" for month,
    # "W{n}-YYYY-MM-DD" for week, "YYYY-MM+YYYY-MM+YYYY-MM" for quarter
    # (see monthly_allocation.periods_between) — always whatever the
    # PROPOSAL-wide granularity toggle currently is, never mixed.
    monthly_allocations: Optional[dict[str, float]] = None


class AvailsEntry(BaseModel):
    max_imps: Optional[float] = None
    max_spend: Optional[float] = None
    est_uniques: Optional[float] = None
    # Avg. frequency (= max_imps / est_uniques) — a Step 06 calculator aid,
    # interchangeable with imps/uniques (entering any two derives the
    # third). Defaults to 5.5 in the UI for Fixed/estimated-CPM products.
    # Not written to its own Excel cell today — only feeds the imps/
    # uniques values that already have one — kept here purely so it
    # round-trips correctly through reopen instead of silently vanishing.
    frequency: Optional[float] = None
    # True when this value was derived from the other field (Fixed/estimated-CPM
    # products) rather than typed directly — rendered as "Est. …" text in Excel.
    max_imps_estimated: Optional[bool] = None
    max_spend_estimated: Optional[bool] = None
    # "imps" or "spend" — whichever field the planner directly typed most
    # recently; the other is always mechanically derived from it. Lets the
    # Excel export write the derived side as a live formula instead of a
    # static number. None for avails saved before this field existed.
    basis: Optional[str] = None
    # Free-form mode: same three columns as always, but each one takes
    # whatever text the planner typed ("50 to 100 estimated clicks")
    # instead of a parsed, calculated number — written verbatim, no
    # cross-field calculation, no SOV. The *_text fields are only
    # meaningful when `freeform` is true; ignored otherwise.
    freeform: Optional[bool] = None
    max_imps_text: Optional[str] = None
    max_spend_text: Optional[str] = None
    est_uniques_text: Optional[str] = None
    # Free-form's one non-free-text field: there's no real imps/spend to
    # compute SOV from in free-form mode, so the planner declares it
    # directly instead — still written as a real %, still gets the same
    # traffic-light conditional formatting as a computed SOV.
    sov_pct_freeform: Optional[float] = None


class TierModel(BaseModel):
    label: str  # "A".."J" — internal key; tabs_built/admin-history still key off this exact letter (see generate_proposal's sheet-naming, which sanitizes `name` separately for the visible tab title)
    # Planner-given display name (e.g. "Independent", "Democrat") shown
    # everywhere a seller/client actually reads this — proposal title,
    # Gamma outline, mailto context, AND (as of the multi-option sheet-tab
    # naming feature) the Excel tab title itself, sanitized/de-duped for
    # Excel's naming rules. None/blank falls back to "Option {label}". The
    # INTERNAL label keeps keying tabs_built/admin-proposal-history (see
    # TierModel.label's own comment) regardless of what the visible name is.
    name: Optional[str] = None
    # Per-tier geo override (e.g. two options targeting different DMAs) —
    # None/blank falls back to the campaign-level ProposalRequest.geo,
    # exactly like `name` falls back to "Option {label}".
    geo: Optional[str] = None
    # Per-tier flight-date override (e.g. two options running different
    # windows) — same fallback convention as `geo`: None/blank falls back
    # to the campaign-level ProposalRequest.start_date/end_date.
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    line_items: list[LineItemModel]
    avails_data: Optional[dict[str, AvailsEntry]] = None
    # Step 05's "combine adjacent periods into one bucket" control — e.g.
    # [["2026-09", "2026-10"]] merges those two period keys into one
    # "September–October" line so a too-small partial period (a flight
    # starting Sep 28 gives September ~3 active days) is checked against
    # the MINIMUM as one combined total instead of failing on its own.
    # TIER-wide (not per-line-item): every line in a tier already shares
    # the SAME resolved start/end dates (see start_date/end_date above),
    # so they share one period list and one merge decision — the Excel
    # export's Monthly Breakdown columns are one shared grid per tier,
    # which couldn't represent two lines disagreeing about what's merged
    # anyway. Each inner list is 2+ period keys that must be adjacent in
    # this tier's own period list — see monthly_allocation.apply_period_merges.
    period_merge_groups: Optional[list[list[str]]] = None

    @field_validator("label")
    @classmethod
    def _label_must_be_a_single_known_letter(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if v not in _TIER_LABELS:
            raise ValueError(f"tier label must be one of {', '.join(_TIER_LABELS)} (got {v!r})")
        return v


# The full set of valid tier labels — up to 10 simultaneous budget options
# ("A".."J"), matching the frontend's TIER_LABELS in app.js. Previously
# unenforced entirely (a comment said "up to 4" but nothing checked it);
# added real validation here at the same time the cap was actually raised,
# rather than leaving it open-ended indefinitely.
_TIER_LABELS = [chr(ord("A") + i) for i in range(10)]


class AddonItemModel(BaseModel):
    product_name: str
    amount: float
    notes_override: Optional[str] = None


class GenerateRequest(BaseModel):
    request: dict   # serialized ProposalRequest
    line_items: list[LineItemModel] = []   # legacy single-tier shape — used only when `tiers` is absent
    tiers: Optional[list[TierModel]] = None  # tiered-budget options (up to 10); preferred over `line_items` when present
    force_tabs: Optional[dict] = None

    @field_validator("tiers")
    @classmethod
    def _tiers_within_cap_and_unique(cls, v: Optional[list[TierModel]]) -> Optional[list[TierModel]]:
        if not v:
            return v
        if len(v) > len(_TIER_LABELS):
            raise ValueError(f"at most {len(_TIER_LABELS)} tiers are supported (got {len(v)})")
        labels = [t.label for t in v]
        if len(set(labels)) != len(labels):
            raise ValueError(f"tier labels must be unique (got {labels})")
        return v
    avails_data: Optional[dict[str, AvailsEntry]] = None  # product_name -> avails; ignored when `tiers` is present
    strategy_brief: Optional[dict] = None  # confirmed Step 03 brief, if not skipped
    roadblocks: Optional[dict] = None  # confirmed Step 05 roadblocks result, if not skipped — saved to reopen_state purely so a reopen can restore it; not otherwise used by generation itself
    # The exact Step 01 paste that produced `request` below — has no effect
    # on generation (parsing already happened), carried through purely so
    # reopen_state can save it and a reopen can refill the textarea (see
    # GET /api/proposal/{id}/reopen and app.js's maybeReopenProposal()).
    raw_notion_text: Optional[str] = None
    # Step 04's Add-Ons module picks — proposal-wide (not per-tier). []
    # (the default, and what an updated frontend always sends even with
    # nothing picked) means "planner picked none" and the export's ADD-ONS
    # section is omitted entirely — NOT the legacy hardcoded 4-item list.
    # That fallback only ever fires for generate_proposal()'s own default
    # (addons=None), which nothing reachable through this endpoint passes.
    addons: list[AddonItemModel] = []
    # Step 05's plan-wide default-split choice ("even" or "prorated") —
    # only affects the export's own fallback estimate for a line that was
    # never individually customized (see monthly_allocation.compute_default_allocation);
    # a line with its own monthly_allocations already carries real planner
    # numbers regardless of this setting.
    monthly_distribution_mode: str = "even"
    # The Step 04 toggle's granularity — "week" | "month" | "quarter".
    # Proposal-wide (not per-tier, matching where the toggle actually
    # lives in the UI), drives how Curate/Avails/Step 05 label totals and
    # which of monthly_allocation.py's period functions Step 05 and the
    # export use to build each line's breakdown. Defaults to "month" —
    # this app's original, only-ever behavior before the toggle existed.
    time_unit: str = "month"
    # Planner-set override for the proposal name bar's editable campaign-
    # name segment (see app.js's proposal-name-edit UI) — when present,
    # replaces whatever the AI enrichment call itself would have guessed,
    # so the real generated title/filename matches what the planner
    # explicitly chose rather than the AI's own invention.
    campaign_name_override: Optional[str] = None
    # Echoes back state.proposalId — set when this generate follows an
    # earlier draft save (or a reopened proposal) of the SAME work.
    # Reused as the row's real proposal_id ONLY when it still belongs to a
    # draft (nothing real generated under it yet — see the resolution
    # logic in generate()); an already-GENERATED proposal being reopened
    # and re-generated still gets a fresh id as before, so its prior
    # download history/files stay intact under their own row.
    proposal_id: Optional[str] = None


class DraftSaveRequest(BaseModel):
    """POST /api/proposal/draft's body — the same wizard-state shape
    GenerateRequest carries, but every field is optional/defaulted since a
    draft can be saved from as early as Step 02 with nothing curated yet,
    and deliberately carries NO validation beyond basic type-checking (an
    unbalanced/incomplete plan is exactly what a draft is allowed to be)."""
    proposal_id: Optional[str] = None  # None -> mint a new row; else upsert the existing one
    wizard_step: int = 2  # which step the planner was on — resumed to on reopen
    request: dict = {}
    line_items: list[LineItemModel] = []
    tiers: Optional[list[TierModel]] = None

    @field_validator("tiers")
    @classmethod
    def _tiers_within_cap_and_unique(cls, v: Optional[list[TierModel]]) -> Optional[list[TierModel]]:
        return GenerateRequest._tiers_within_cap_and_unique(v)

    avails_data: Optional[dict[str, AvailsEntry]] = None
    strategy_brief: Optional[dict] = None
    roadblocks: Optional[dict] = None
    raw_notion_text: Optional[str] = None
    force_tabs: Optional[dict] = None
    addons: list[AddonItemModel] = []
    monthly_distribution_mode: str = "even"
    time_unit: str = "month"
    campaign_name_override: Optional[str] = None
    enrichment: Optional[dict] = None  # Step 07 AI email content, if the planner got that far before saving
    # Client-computed preview title (buildProposalNamePreview()) or the
    # real one once known — shown in My Proposals so a draft row isn't
    # blank; falls back to client_name server-side if empty.
    proposal_title: str = ""


class StrategyRequest(BaseModel):
    request: dict
    reprompt: Optional[str] = None
    # "consistent" (default): recommend only from the families already in
    # request["products_selected"] (Step 02's parse). "new_mix": ignore
    # that and recommend freely across the whole catalog, same as this
    # app's original behavior — for when the planner explicitly wants a
    # from-scratch recommendation instead of a rationale for what's
    # already selected. See strategy_brief.generate_brief()'s own docstring.
    mode: str = "consistent"
    # A prior /api/ad-presence result to fold into the brief on regenerate.
    ad_presence: Optional[dict] = None


class AdPresenceRequest(BaseModel):
    client_name: str = ""
    client_website: str = ""


class RecommendRequest(BaseModel):
    request: dict
    monthly_budget: float
    strategy_brief: Optional[dict] = None
    # Step 04's toggle — without this, Suggest Mix would seed every line's
    # budget against the catalog's raw MONTHLY minimum_spend regardless of
    # what unit the planner is actually curating in (a ~4x overshoot in
    # Weekly mode). See recommender.granularity_scale.
    time_unit: str = "month"


class RoadblocksRequest(BaseModel):
    request: dict
    line_items: list[LineItemModel]
    strategy_brief: Optional[dict] = None


class DriveUploadRequest(BaseModel):
    proposal_id: str
    seller_email: str


class RepromptEmailsRequest(BaseModel):
    request: dict
    line_items: list[LineItemModel]
    campaign_name: Optional[str] = ""
    current_internal_subject: str = ""
    current_internal_body: str = ""
    current_client_subject: str = ""
    current_client_body: str = ""
    reprompt: str
    # "both" (default, original behavior): revise both emails. "internal"/
    # "client": revise only that one — see ai_enricher.reprompt_emails()'s
    # own docstring for the guardrail that enforces this server-side.
    scope: str = "both"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Serve the SPA."""
    return _serve_html_with_cache_busted_static(TEMPLATES_DIR / "index.html")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    """Serve the login page. Already-logged-in visitors are sent straight
    to `/` instead of seeing the form again."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token and auth_svc.get_user_by_session(token):
        return RedirectResponse("/")
    return _serve_html_with_cache_busted_static(TEMPLATES_DIR / "login.html")


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/login")
async def login(body: LoginRequest, response: Response) -> dict:
    """Checks email/password, and on success sets the session cookie the
    auth middleware (_require_login) looks for on every later request."""
    user = auth_svc.get_user_by_email(body.email)
    if user is None or user["disabled"] or not auth_svc.verify_password(
        body.password, user["password_hash"], user["password_salt"]
    ):
        # Deliberately the SAME message for "no such account" and "wrong
        # password" — telling them apart lets an attacker enumerate which
        # emails have accounts here.
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    token = auth_svc.create_session(user["id"])
    response.set_cookie(
        SESSION_COOKIE_NAME, token,
        max_age=auth_svc.SESSION_LIFETIME_DAYS * 86400,
        httponly=True, secure=True, samesite="lax", path="/",
    )
    return {"ok": True, "email": user["email"], "is_admin": user["is_admin"]}


@app.post("/api/logout")
async def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        auth_svc.delete_session(token)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request) -> dict:
    """Who's logged in — the masthead uses this to show an email + Log out
    link, and admin.html to decide whether to show the Users tab at all."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"email": user["email"], "is_admin": user["is_admin"]}


@app.get("/api/catalog")
async def get_catalog() -> dict:
    """Return the AdFlo product catalog (with any admin rate overrides applied), grouped by family."""
    grouped: dict[str, list] = {}
    for p in effective_catalog():
        grouped.setdefault(p.family, []).append({
            "name": p.name,
            "short_label": p.short_label,
            "family": p.family,
            "pricing_model": p.buying_model,
            "rate": p.base_rate,
            "minimum_spend": p.minimum_spend,
            "estimated_impressions": p.estimated_impressions,
            "estimated_cpm_for_imps": p.estimated_cpm_for_imps,
            "sizes": p.sizes,
            "description": p.proposal_description,
            "notes": p.notes,
            "wide_orbit_code": p.wide_orbit_code,
            "is_addon": p.is_addon,
        })
    return {
        "families": families(),
        "products_by_family": grouped,
    }


@app.post("/api/parse")
async def parse(body: ParseRequest) -> dict:
    """Parse Notion text into a structured ProposalRequest."""
    catalog_names = [p.name for p in effective_catalog()]
    req = parse_notion(body.notion_text, catalog_names, db_aliases=all_product_aliases())
    tabs = classify_output_tabs(
        req.request_type,
        req.products_selected,
        has_agency_fee=req.agency_fee is not None and req.agency_fee > 0,
    )
    return {
        "request": asdict(req),
        "suggested_tabs": tabs,
    }


@app.post("/api/strategy")
async def strategy(body: StrategyRequest) -> dict:
    """Generate (or regenerate) an AI strategy brief for the proposal. Also
    writes a downloadable .docx of the brief and returns a token to fetch it
    via /api/download-strategy/{token}, same pattern as the roadblocks step."""
    req = _reconstruct_proposal_request(body.request)
    brief = await strategy_brief_svc.generate_brief(req, reprompt=body.reprompt, mode=body.mode,
                                                    ad_presence=body.ad_presence)

    doc_token: Optional[str] = None
    if brief.get("strategy_summary") or brief.get("recommended_tactics"):
        doc_token = secrets.token_urlsafe(12)
        # Same naming convention every other export uses ({ID} | Campaign |
        # Entravision | MonYY | Doc Type) — this used to be just the raw
        # client name, missing the ID entirely. No AI-inferred campaign name
        # exists yet this early in the wizard (that only happens at Step 07
        # Generate), so this falls back to the client name the same way the
        # final proposal itself does when enrichment hasn't run.
        doc_title = ai_enricher.build_proposal_title(
            short_id=ai_enricher.normalize_notion_id(req.notion_id) or "DRAFT",
            campaign_name=ai_enricher._fallback_campaign_name(req),
            request_type=req.request_type,
            ref_date=req.start_date or "",
            doc_type_override="Strategy Brief",
            client_name=req.client_name or "",
        )
        doc_filename = ai_enricher.safe_filename(doc_title) + ".docx"
        doc_path = PROPOSALS_DIR / f"strategy_{doc_token}.docx"
        built = docx_builder.build_strategy_brief_docx(
            output_path=doc_path,
            title=req.client_name or "Proposal",
            client_summary=brief.get("client_summary", ""),
            market_context=brief.get("market_context", ""),
            objectives_analysis=brief.get("objectives_analysis", ""),
            strategy_summary=brief.get("strategy_summary", ""),
            recommended_tactics=brief.get("recommended_tactics", []),
            key_insights=brief.get("key_insights", []),
            monthly_budget=req.monthly_budget or 0.0,
            total_months=req.total_months or 0,
            ad_presence=brief.get("ad_presence"),
        )
        if built:
            (PROPOSALS_DIR / f"strategy_{doc_token}.json").write_text(json.dumps({
                "path": str(doc_path), "filename": doc_filename,
            }))
        else:
            doc_token = None

    brief["doc_token"] = doc_token
    return brief


@app.post("/api/ad-presence")
async def ad_presence_check(body: AdPresenceRequest) -> dict:
    """Optional, on-demand Meta/Google/TikTok ad-library check, run from Step 03 after the brief."""
    name, website = body.client_name.strip(), body.client_website.strip()
    if not (name or website):
        raise HTTPException(status_code=400, detail="Client name or website required.")
    # Backstop only: the check applies its own 60s per-platform budget and returns partial results.
    try:
        result = await asyncio.wait_for(ad_presence_svc.check_ad_presence(name, website), timeout=90)
    except Exception as exc:
        message = "timed out after 90s" if isinstance(exc, asyncio.TimeoutError) else (str(exc) or type(exc).__name__)
        result = {"error": message, "summary": "", "meta": {}, "google": {}, "tiktok": {}}
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    result["inputs"] = {"client_name": name, "client_website": website}
    return result


@app.get("/api/download-strategy/{doc_token}")
async def download_strategy(doc_token: str) -> FileResponse:
    """Download the Step 03 AI strategy brief Word doc."""
    meta_file = PROPOSALS_DIR / f"strategy_{doc_token}.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail="Strategy brief not found")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    path = Path(meta["path"])
    if not path.exists():
        raise HTTPException(status_code=410, detail="Strategy brief file expired")
    return FileResponse(
        path=str(path),
        filename=meta["filename"],
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


class StrategyDocRebuildRequest(BaseModel):
    request: dict
    client_summary: str = ""
    market_context: str = ""
    objectives_analysis: str = ""
    strategy_summary: str = ""
    recommended_tactics: list = []
    key_insights: list = []
    ad_presence: Optional[dict] = None


@app.post("/api/strategy/{doc_token}/rebuild")
async def rebuild_strategy_doc(doc_token: str, body: StrategyDocRebuildRequest) -> dict:
    """
    Rebuilds the downloadable Strategy Brief .docx IN PLACE (same
    doc_token/file path, so the existing download link on Step 03 keeps
    working without the planner needing a new one) — same "revise the
    already-generated doc without re-running the AI" pattern
    reprompt_emails() already uses for the client email doc.

    Exists specifically for the tactic-card checkboxes (see app.js's
    renderStrategyBrief): recommended_tactics here is whatever the
    PLANNER currently has selected, already filtered client-side —
    deliberately not the full, unfiltered brief. The AI itself never runs
    again; this only re-renders the same content into the .docx.
    """
    meta_file = PROPOSALS_DIR / f"strategy_{doc_token}.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail="Strategy brief not found")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    doc_path = Path(meta["path"])

    req = _reconstruct_proposal_request(body.request)

    built = docx_builder.build_strategy_brief_docx(
        output_path=doc_path,
        title=req.client_name or "Proposal",
        client_summary=body.client_summary,
        market_context=body.market_context,
        objectives_analysis=body.objectives_analysis,
        strategy_summary=body.strategy_summary,
        recommended_tactics=body.recommended_tactics,
        key_insights=body.key_insights,
        monthly_budget=req.monthly_budget or 0.0,
        total_months=req.total_months or 0,
        ad_presence=body.ad_presence,
    )
    if not built:
        raise HTTPException(status_code=500, detail="Could not rebuild strategy brief doc.")
    return {"rebuilt": True}


@app.post("/api/roadblocks")
async def roadblocks(body: RoadblocksRequest) -> dict:
    """
    Step 05 — AI-researched platform restrictions/roadblocks for the
    confirmed product mix, grounded in the Step 03 strategy brief + Notion
    context via live web search. Also writes a downloadable .docx report
    and returns a token to fetch it via /api/download-roadblocks/{token}.
    """
    req = _reconstruct_proposal_request(body.request)

    line_items = [
        LineItem(id=li.id, product_name=li.product_name, monthly_budget=li.monthly_budget, months=li.months)
        for li in body.line_items
    ]

    result = await asyncio.to_thread(
        roadblocks_svc.generate_roadblocks, req, line_items, strategy_brief=body.strategy_brief)

    doc_token: Optional[str] = None
    if result.get("product_roadblocks"):
        doc_token = secrets.token_urlsafe(12)
        # Same naming-convention fix as the Strategy Brief doc above.
        doc_title = ai_enricher.build_proposal_title(
            short_id=ai_enricher.normalize_notion_id(req.notion_id) or "DRAFT",
            campaign_name=ai_enricher._fallback_campaign_name(req),
            request_type=req.request_type,
            ref_date=req.start_date or "",
            client_name=req.client_name or "",
            doc_type_override="Roadblocks Report",
        )
        doc_filename = ai_enricher.safe_filename(doc_title) + ".docx"
        doc_path = PROPOSALS_DIR / f"roadblocks_{doc_token}.docx"
        built = docx_builder.build_roadblocks_docx(
            output_path=doc_path,
            title=req.client_name or "Proposal",
            overall_summary=result.get("overall_summary", ""),
            product_roadblocks=result["product_roadblocks"],
            used_web_search=result.get("used_web_search", False),
        )
        if built:
            (PROPOSALS_DIR / f"roadblocks_{doc_token}.json").write_text(json.dumps({
                "path": str(doc_path), "filename": doc_filename,
            }))
        else:
            doc_token = None

    result["doc_token"] = doc_token
    return result


@app.get("/api/download-roadblocks/{doc_token}")
async def download_roadblocks(doc_token: str) -> FileResponse:
    """Download the Step 05 roadblocks report Word doc."""
    meta_file = PROPOSALS_DIR / f"roadblocks_{doc_token}.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail="Roadblocks report not found")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    path = Path(meta["path"])
    if not path.exists():
        raise HTTPException(status_code=410, detail="Roadblocks report file expired")
    return FileResponse(
        path=str(path),
        filename=meta["filename"],
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.post("/api/recommend")
async def recommend(body: RecommendRequest) -> dict:
    """Given a parsed request + a monthly budget, suggest line items."""
    req = _reconstruct_proposal_request(body.request)

    items = recommend_line_items(req, body.monthly_budget, strategy_brief=body.strategy_brief, time_unit=body.time_unit)
    return {"line_items": [asdict(li) for li in items]}


def _validate_monthly_breakdown(
    tiers: list[dict], req: ProposalRequest, multi_tier: bool, granularity: str = "month",
) -> tuple[list[str], list[dict]]:
    """
    Extracted from /api/generate so it's independently testable. Returns
    (balance_errors, minimum_warnings) — NEITHER blocks generation; both are
    surfaced back to the planner as non-blocking notes (see the response's
    monthly_breakdown_balance_notes / monthly_breakdown_warnings):
      - balance_errors: a line item's monthly dollars don't sum to its own
        Curate-step total. In normal use this shouldn't happen often —
        app.js's _mbSyncBudgetToAllocation keeps the Curate-step total in
        sync with whatever was actually typed into Monthly Breakdown as
        soon as an edit is committed — but a line the planner left only
        partially filled in still reports here rather than being silently
        treated as complete.
      - minimum_warnings: below-rate-card-minimum months, for EVERY tier
        regardless of balance errors (the planner may have a deliberate
        reason to go under), surfaced back on the response instead of
        being silently swallowed.

    `granularity` is the proposal-wide Step 04 toggle ("week"/"month"/
    "quarter", from GenerateRequest.time_unit) — it decides which period
    list monthly_allocations keys are checked against, and how each
    product's minimum_spend is scaled (see
    monthly_allocation._effective_minimum_for_period). Each line item's
    own period_merge_groups (if any) are applied on top of that period
    list before either the balance or minimum check runs — see
    monthly_allocation.apply_period_merges.
    """
    balance_errors: list[str] = []
    minimum_warnings: list[dict] = []
    for t in tiers:
        tier_label = t.get("label") or "A"
        effective_start = (t.get("start_date") or req.start_date or "").strip()
        effective_end = (t.get("end_date") or req.end_date or "").strip()
        start_d = monthly_allocation.parse_flexible_date(effective_start)
        end_d = monthly_allocation.parse_flexible_date(effective_end)
        base_periods = monthly_allocation.periods_between(start_d, end_d, granularity) if (start_d and end_d) else []
        for li in t["line_items"]:
            if not li.monthly_allocations:
                continue
            months = monthly_allocation.apply_period_merges(base_periods, li.period_merge_groups)
            total = li.monthly_budget * li.months
            result = monthly_allocation.reconcile_allocation(total, li.monthly_allocations)
            if not result["balanced"]:
                direction = "over-allocated" if result["over_allocated"] else "under-allocated"
                balance_errors.append(
                    f"Option {tier_label} — {li.product_name}: monthly breakdown is {direction} by "
                    f"${abs(result['remaining']):,.2f} (allocated ${result['allocated']:,.2f} of ${total:,.2f})."
                )
            if months:
                product = by_name(li.product_name)
                min_spend = product.minimum_spend if product else None
                minimum_warnings.extend(monthly_allocation.check_minimum_violations(
                    line_item_label=f"Option {tier_label}" if multi_tier else "",
                    product_name=li.product_name,
                    minimum_spend=min_spend,
                    is_added_value=li.is_added_value,
                    months=months,
                    allocations=li.monthly_allocations,
                    granularity=granularity,
                ))
    return balance_errors, minimum_warnings


@app.post("/api/generate")
async def generate(body: GenerateRequest, request: Request) -> dict:
    """
    Generate an Excel proposal with AI enrichment.
    Returns proposal_id, filename, proposal_title, summary, and enrichment content.
    """
    req = _reconstruct_proposal_request(body.request)

    def _to_line_items(models: list[LineItemModel]) -> list[LineItem]:
        return [
            LineItem(
                id=li.id,
                product_name=li.product_name,
                monthly_budget=li.monthly_budget,
                months=li.months,
                rate_override=li.rate_override,
                notes_override=li.notes_override,
                target_override=li.target_override,
                target_secondary=li.target_secondary,
                estimated_cpm_override=li.estimated_cpm_override,
                buying_model_override=li.buying_model_override,
                is_added_value=li.is_added_value,
                added_value_pct=li.added_value_pct,
                objective_override=li.objective_override,
                monthly_allocations=li.monthly_allocations,
            )
            for li in models
        ]

    # Normalize into the tiered shape: a legacy single-tier call (no `tiers`
    # sent) becomes one implicit tier "A", so the rest of this handler and
    # generate_proposal() only ever deal with one code path.
    if body.tiers:
        tiers = [
            {
                "label": t.label,
                "name": t.name,
                "geo": t.geo,
                "start_date": t.start_date,
                "end_date": t.end_date,
                "line_items": _to_line_items(t.line_items),
                "avails_data": {name: entry.model_dump() for name, entry in (t.avails_data or {}).items()},
                "period_merge_groups": t.period_merge_groups,
            }
            for t in body.tiers
        ]
    else:
        tiers = [{
            "label": "A",
            "geo": None,
            "start_date": None,
            "end_date": None,
            "line_items": _to_line_items(body.line_items),
            "avails_data": {name: entry.model_dump() for name, entry in (body.avails_data or {}).items()},
            "period_merge_groups": None,
        }]
    multi_tier = len(tiers) > 1

    # Monthly Breakdown validation — no longer a hard gate (per explicit
    # planner feedback: a line's real monthly figures can legitimately
    # differ month to month, e.g. $25k/$10k/$30k across a quarter, and
    # generation must never block on that). app.js's Curate-side sync
    # (_mbSyncBudgetToAllocation) keeps monthly_budget in step with
    # whatever the planner actually typed into Monthly Breakdown as soon
    # as they commit an edit, so by the time Generate is clicked a
    # COMPLETE line is balanced by construction — an "unbalanced" result
    # here almost always just means a line's breakdown was left partially
    # filled in, which the planner may have every reason to do (e.g.
    # deliberately skipping the rest and letting the export estimate it).
    # Surfaced back as a non-blocking warning, same treatment as
    # minimum_warnings below, instead of ever refusing to generate.
    balance_errors, minimum_warnings = _validate_monthly_breakdown(tiers, req, multi_tier, granularity=body.time_unit)

    # Union of every tier's line items — product blurbs and the campaign
    # name don't vary by tier, so enrichment runs once against everything
    # that could appear in the workbook, deduplicated by product name.
    seen_products = set()
    union_line_items: list[LineItem] = []
    for t in tiers:
        for li in t["line_items"]:
            if li.product_name not in seen_products:
                seen_products.add(li.product_name)
                union_line_items.append(li)

    # 1. Resolve the proposal ID: planner's Notion ID wins over the internal counter
    notion_id = ai_enricher.normalize_notion_id(req.notion_id)
    short_id = notion_id or _get_next_short_id()

    # 2. AI enrichment (campaign name + blurbs + emails) — grounded in the
    #    confirmed Step 03 strategy brief when the planner didn't skip it.
    #    When there's more than one budget option, the emails are prompted
    #    to lay out each option explicitly rather than describing one plan.
    tier_context = [
        {"label": t["label"], "name": t.get("name"), "line_items": t["line_items"]} for t in tiers
    ] if multi_tier else None
    enrichment = await asyncio.to_thread(
        ai_enricher.enrich_proposal,
        req, union_line_items, short_id, strategy_brief=body.strategy_brief, tiers=tier_context,
    )
    # A planner-set name override wins over whatever the AI itself guessed
    # — used verbatim (not re-run through the AI) so it can't drift from
    # what was explicitly typed. Only the NAME segment changes; blurbs/
    # emails still come from the same real enrichment call above.
    if body.campaign_name_override and body.campaign_name_override.strip():
        enrichment.campaign_name = body.campaign_name_override.strip()

    # 3. Build the naming-convention title
    proposal_title = ai_enricher.build_proposal_title(
        short_id=short_id,
        campaign_name=enrichment.campaign_name or req.client_name or "Campaign",
        request_type=req.request_type,
        ref_date=req.start_date or "",
        client_name=req.client_name or "",
    )

    # 4. Derive filenames from the title.
    proposal_id = _resolve_draftable_proposal_id(body.proposal_id)
    safe_base = ai_enricher.safe_filename(proposal_title)
    filename = f"{safe_base}.xlsx"
    output_path = PROPOSALS_DIR / filename
    email_doc_filename: Optional[str] = None

    # 5. Generate Excel — one set of tabs (Net/wsections/Gross/Avails-Only)
    #    PER TIER when there's more than one budget option, lettered
    #    Proposal A/B/C/D; DOOH and Process FAQs stay single shared tabs.
    addons = [
        AddonItem(product_name=a.product_name, amount=a.amount, notes_override=a.notes_override)
        for a in body.addons
    ]
    try:
        summary = generate_proposal(
            req, tiers[0]["line_items"], output_path,
            force_tabs=body.force_tabs,
            enrichment=enrichment,
            proposal_title=proposal_title,
            avails_data=tiers[0]["avails_data"],
            # Always pass the resolved tiers list (never None) — it
            # already carries period_merge_groups per tier, and
            # generate_proposal computes its own multi_tier from
            # len(tiers), so a 1-element list behaves identically to the
            # old `None` (which made it rebuild an equivalent 1-element
            # list internally, just without period_merge_groups).
            tiers=tiers,
            addons=addons,
            monthly_distribution_mode=body.monthly_distribution_mode,
            time_unit=body.time_unit,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")

    # 7. Insert a copy-paste-ready proposal line near the top of the internal
    #    email (planner adds the Drive link after upload). The AI is prompted
    #    to leave a "{{PROPOSAL_LINE}}" placeholder right after its opening
    #    line — substitute it there; if it's missing, prepend after the
    #    first line instead so the link still lands near the top, not buried.
    internal_email_body = enrichment.internal_email_body
    if internal_email_body:
        proposal_line = f"Proposal: {proposal_title}\nGoogle Drive Link: [paste link here]"

        # Full Presentation requests also come with a presentation deck —
        # a separate deliverable this app doesn't generate, prepared by the
        # seller outside it — so the email needs its own placeholder line
        # for that Drive link too, right alongside the proposal's.
        if "full presentation" in (req.request_type or "").lower():
            deck_title = ai_enricher.build_proposal_title(
                short_id=short_id,
                campaign_name=enrichment.campaign_name or req.client_name or "Campaign",
                request_type=req.request_type,
                ref_date=req.start_date or "",
                client_name=req.client_name or "",
                doc_type_override="Digital Media Deck",
            )
            proposal_line += f"\n\nPresentation: {deck_title}\nGoogle Drive Link: [paste link here]"

        if "{{PROPOSAL_LINE}}" in internal_email_body:
            internal_email_body = internal_email_body.replace("{{PROPOSAL_LINE}}", proposal_line)
        else:
            lines = internal_email_body.split("\n", 1)
            first_line = lines[0]
            rest = lines[1] if len(lines) > 1 else ""
            internal_email_body = f"{first_line}\n\n{proposal_line}\n\n{rest.lstrip()}"

        # Sign the email as the ACTUAL logged-in planner (request.state.user,
        # set by _require_login) rather than trusting the model to invent or
        # reproduce a real name/email verbatim — same "AI leaves a literal
        # placeholder, code substitutes the real value" pattern as
        # {{PROPOSAL_LINE}} above. There's no display-name field stored
        # anywhere in this app (see schema.sql's users table), so the name
        # is derived from the email's own dot-separated local part (see
        # ai_enricher._display_name_from_email's own comment) — falls back
        # to the bare email if that yields nothing (an unusual address with
        # no dot AND an empty local part, practically never).
        planner_email = request.state.user["email"]
        planner_name = ai_enricher._display_name_from_email(planner_email)
        signature = f"{planner_name}, part of your digital strategy team\n{planner_email}" if planner_name else planner_email
        if "{{PLANNER_SIGNATURE}}" in internal_email_body:
            internal_email_body = internal_email_body.replace("{{PLANNER_SIGNATURE}}", signature)
        else:
            internal_email_body = f"{internal_email_body.rstrip()}\n\n{signature}"

    # 8. Build client email Word doc (if AI produced content)
    if enrichment.client_email_body:
        email_doc_filename = f"{safe_base}_Client_Email.docx"
        email_doc_path = PROPOSALS_DIR / email_doc_filename
        docx_builder.build_client_email_docx(
            output_path=email_doc_path,
            proposal_title=proposal_title,
            ae_name=req.requested_by or "",
            ae_email=req.salesperson_email or "",
            # The persistent, copyable proposal title IS the subject line —
            # not the AI's own subject guess — so the emailed subject always
            # matches what's shown at the top of the app and used for the
            # filename, rather than varying with each AI generation.
            subject=proposal_title,
            body=enrichment.client_email_body,
        )

    # 8b. Build the simple, signature-ready Net/Gross PowerPoint decks (Step
    #     07's PPTX export) — ALL tiers included (each becomes its own
    #     labeled section with its own subtotal; alternatives, so never
    #     summed into one combined total), one deck per tab type actually
    #     built (Net and/or Gross apply uniformly across every tier — there's
    #     no per-tier tab selection in this app — so checking tabs_built
    #     once for either suffix is enough to know whether to build it here).
    pptx_net_filename: Optional[str] = None
    pptx_gross_filename: Optional[str] = None
    tabs_built_list = summary.get("tabs_built", [])
    pptx_tiers = []
    for t in tiers:
        t_products = [p for li in t["line_items"] if (p := by_name(li.product_name)) is not None]
        t_line_items = [li for li in t["line_items"] if by_name(li.product_name) is not None]
        if t_products:
            pptx_tiers.append({
                "name": (t.get("name") or "").strip() or f"Option {t['label']}",
                "products": t_products, "line_items": t_line_items,
            })
    if pptx_tiers:
        if any(tb == f"Proposal {t['label']}" for t in tiers for tb in tabs_built_list):
            pptx_net_filename = f"{safe_base}_Net_Deck.pptx"
            try:
                pptx_builder.build_signature_deck(
                    req, pptx_tiers, PROPOSALS_DIR / pptx_net_filename,
                    gross=False, proposal_title=proposal_title,
                )
            except Exception:
                pptx_net_filename = None  # never let an optional export break the main Generate call
        if any(tb == f"Proposal {t['label']} (Gross)" for t in tiers for tb in tabs_built_list):
            pptx_gross_filename = f"{safe_base}_Gross_Deck.pptx"
            try:
                pptx_builder.build_signature_deck(
                    req, pptx_tiers, PROPOSALS_DIR / pptx_gross_filename,
                    gross=True, proposal_title=proposal_title,
                )
            except Exception:
                pptx_gross_filename = None

    # 9. Track requester device/IP for the admin view
    user_agent = request.headers.get("user-agent", "")
    client_ip = request.client.host if request.client else ""

    # 10. Build enrichment payload for the frontend (and for reopen_state
    # below — moved up from its old spot after _save_proposal_metadata so
    # the same dict can be persisted, not just returned for this one
    # response cycle. Previously this was pure ephemera: a reopen had no
    # way to see the campaign name / email copy / product blurbs a
    # proposal was actually generated with).
    enrichment_out = {
        "campaign_name": enrichment.campaign_name,
        "internal_email_subject": enrichment.internal_email_subject,
        "internal_email_body": internal_email_body,
        "client_email_subject": enrichment.client_email_subject,
        "client_email_body": enrichment.client_email_body,
        "product_blurbs": [
            {"product_name": pb.product_name, "blurb": pb.blurb}
            for pb in enrichment.product_blurbs
        ],
        "has_email_doc": email_doc_filename is not None,
        "used_web_search": enrichment.used_web_search,
        "error": enrichment.error,
    }

    # 11. Store metadata in PostgreSQL. The generated files remain on disk;
    # their filenames are persisted so the existing download routes can derive
    # their paths after a restart or redeploy.
    reopen_state = _build_reopen_state(
        request_dict=body.request,
        line_items=body.line_items,
        tiers=body.tiers,
        avails_data=body.avails_data,
        strategy_brief=body.strategy_brief,
        roadblocks=body.roadblocks,
        force_tabs=body.force_tabs,
        addons=body.addons,
        raw_notion_text=body.raw_notion_text,
        enrichment=enrichment_out,
        time_unit=body.time_unit,
        monthly_distribution_mode=body.monthly_distribution_mode,
        campaign_name_override=body.campaign_name_override,
        wizard_step=8,
    )
    _save_proposal_metadata(
        proposal_id=proposal_id,
        client_name=req.client_name,
        seller_email=req.salesperson_email,
        # The ACTUAL logged-in user (from the session, set by _require_login)
        # — NOT necessarily the same as req.salesperson_email above, which
        # is just whatever the pasted Notion text's own "Salesperson
        # email:" field said (the AE the deal belongs to, which someone
        # else — an ops coordinator, a covering planner — may be the one
        # actually generating). "My Proposal History" filters on THIS
        # field specifically so it reliably means "proposals I generated",
        # not "proposals where the pasted text happened to name me."
        created_by_email=request.state.user["email"],
        requested_by=req.requested_by,
        notion_id=notion_id,
        proposal_title=proposal_title,
        filename=filename,
        email_doc_filename=email_doc_filename,
        pptx_net_filename=pptx_net_filename,
        pptx_gross_filename=pptx_gross_filename,
        generated_at=datetime.now(timezone.utc),
        status="generated",
        requester_ip=client_ip,
        requester_user_agent=user_agent,
        summary=summary,
        reopen_state=reopen_state,
    )

    return {
        "proposal_id": proposal_id,
        "filename": filename,
        "proposal_title": proposal_title,
        "summary": summary,
        "enrichment": enrichment_out,
        "has_pptx_net": pptx_net_filename is not None,
        "has_pptx_gross": pptx_gross_filename is not None,
        # Below-minimum-monthly-spend flags — never blocks generation (the
        # planner may have a deliberate reason to go under), but must
        # never be silently swallowed either. See the balance-error gate
        # above for the one thing about Monthly Breakdown that IS a hard
        # failure.
        "monthly_breakdown_warnings": minimum_warnings,
        # Never blocks (see the comment above where these are computed) —
        # purely informational, e.g. "this line's breakdown is still only
        # 62.5% filled in."
        "monthly_breakdown_balance_notes": balance_errors,
    }


@app.get("/api/proposal/{proposal_id}/reopen")
async def reopen_proposal(proposal_id: str) -> dict:
    """
    Return the full saved state (request, line items, avails, strategy brief)
    for a previously generated proposal OR a saved draft, so the app can
    pre-fill the wizard for edits instead of starting from a blank paste.
    `status` tells the frontend which of the two this is — a draft resumes
    at `wizard_step`; a completed proposal always lands on Curate, as it
    always has (see app.js's maybeReopenProposal()).
    """
    meta = _get_proposal_metadata(proposal_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    reopen_state = meta.get("reopen_state")
    if not reopen_state:
        raise HTTPException(status_code=410, detail="This proposal was generated before reopening was supported.")
    return {
        "proposal_title": meta.get("proposal_title", ""),
        "status": meta.get("status") or "generated",
        **reopen_state,
    }


@app.post("/api/proposal/draft")
async def save_draft(body: DraftSaveRequest, request: Request) -> dict:
    """
    Save the wizard's CURRENT state as a resumable, incomplete "draft" —
    callable from any step once Step 01/02 parsing has produced a
    `request` (the frontend gates its Save Draft button the same way it
    gates the proposal-name bar: from Step 02 onward). Deliberately runs
    NO validation beyond Pydantic's own type-checking — a draft is
    explicitly allowed to be unbalanced/incomplete, unlike /api/generate's
    hard Monthly Breakdown gate — and never touches the AI services or the
    filesystem (no Excel/email/deck is built), so saving progress never
    burns an OpenAI call and is safe to click as often as the planner likes.

    Upserts the SAME `proposals` row on every call once one exists (via
    `body.proposal_id`, echoed back from the first save and then carried
    by the frontend on every later save) rather than piling up duplicate
    rows per session — reuses reopen_state's exact shape, so a saved
    draft resumes through the very same GET .../reopen code path a
    completed proposal already does.
    """
    req = _reconstruct_proposal_request(body.request)
    notion_id = ai_enricher.normalize_notion_id(req.notion_id)
    # Reuses body.proposal_id only when it doesn't exist yet or is still a
    # draft — NEVER when it already belongs to a real GENERATED proposal,
    # which matters a lot once autosave is in the picture: a planner
    # reopening a past, completed proposal just to look at it must never
    # have autosave silently flip it back to "draft" and blank its
    # filename/generated_at columns. See _resolve_draftable_proposal_id's
    # own docstring for the full reasoning (shared with /api/generate).
    proposal_id = _resolve_draftable_proposal_id((body.proposal_id or "").strip() or None)

    reopen_state = _build_reopen_state(
        request_dict=body.request,
        line_items=body.line_items,
        tiers=body.tiers,
        avails_data=body.avails_data,
        strategy_brief=body.strategy_brief,
        roadblocks=body.roadblocks,
        force_tabs=body.force_tabs,
        addons=body.addons,
        raw_notion_text=body.raw_notion_text,
        enrichment=body.enrichment,
        time_unit=body.time_unit,
        monthly_distribution_mode=body.monthly_distribution_mode,
        campaign_name_override=body.campaign_name_override,
        wizard_step=body.wizard_step,
    )
    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")
    _save_proposal_metadata(
        proposal_id=proposal_id,
        client_name=req.client_name,
        seller_email=req.salesperson_email,
        created_by_email=request.state.user["email"],
        requested_by=req.requested_by,
        notion_id=notion_id,
        proposal_title=body.proposal_title.strip() or req.client_name or "Untitled draft",
        requester_ip=client_ip,
        requester_user_agent=user_agent,
        summary={},
        reopen_state=reopen_state,
        status="draft",
    )
    return {"proposal_id": proposal_id, "saved_at": datetime.now(timezone.utc).isoformat()}


@app.delete("/api/proposal/{proposal_id}")
async def delete_draft(proposal_id: str, request: Request) -> dict:
    """
    Deletes a DRAFT only — there is no delete path for a real generated
    proposal (that row is a permanent record of what was actually sent,
    same reasoning "My Proposal History" itself is never destructive).
    Scoped to the caller's own drafts, so one planner can't delete
    another's in-progress work.
    """
    meta = _get_proposal_metadata(proposal_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if meta.get("status") != "draft":
        raise HTTPException(status_code=400, detail="Only drafts can be deleted.")
    if (meta.get("created_by_email") or "").lower() != (request.state.user["email"] or "").lower():
        raise HTTPException(status_code=403, detail="You can only delete your own drafts.")
    with get_connection() as conn:
        conn.execute("DELETE FROM proposals WHERE proposal_id = %s", (proposal_id,))
    return {"ok": True}


@app.post("/api/proposal/{proposal_id}/reprompt-emails")
async def reprompt_emails(proposal_id: str, body: RepromptEmailsRequest) -> dict:
    """
    Step 07 — revise the internal + client-facing emails based on the
    planner's final review feedback, without touching the already-generated
    Excel file, its naming-convention title, or the campaign name. If this
    proposal has a client-email Word doc on disk, it's rebuilt in place at
    the same path/filename, so the existing download link keeps working and
    now serves the revised content.
    """
    req = _reconstruct_proposal_request(body.request)

    line_items = [
        LineItem(id=li.id, product_name=li.product_name, monthly_budget=li.monthly_budget, months=li.months)
        for li in body.line_items
    ]

    result = await asyncio.to_thread(
        ai_enricher.reprompt_emails,
        req, line_items, body.campaign_name,
        body.current_internal_subject, body.current_internal_body,
        body.current_client_subject, body.current_client_body,
        body.reprompt, scope=body.scope,
    )

    # scope="internal" guarantees client_email_body is unchanged (see the
    # guardrail in ai_enricher.reprompt_emails()) — skip rebuilding the
    # client-facing .docx in that case, nothing in it actually changed.
    meta = _get_proposal_metadata(proposal_id)
    if meta is not None and body.scope != "internal" and not result.get("error") and result.get("client_email_body"):
        email_doc_path_str = meta.get("email_doc_path")
        if email_doc_path_str:
            docx_builder.build_client_email_docx(
                output_path=Path(email_doc_path_str),
                proposal_title=meta.get("proposal_title", ""),
                ae_name=req.requested_by or "",
                ae_email=req.salesperson_email or "",
                subject=meta.get("proposal_title", ""),  # same title-as-subject rule as the initial generate
                body=result["client_email_body"],
            )

    return result


class GammaOutlineRefineRequest(BaseModel):
    outline: str
    reprompt: str


@app.post("/api/refine-gamma-outline")
async def refine_gamma_outline_endpoint(body: GammaOutlineRefineRequest) -> dict:
    """
    Revise the Gamma/media-strategy-co-pilot outline text based on the
    planner's free-text feedback. This is the ONLY AI call anywhere in
    that feature — the outline itself is built entirely client-side (see
    app.js's buildGammaOutline, no fetch at all) and stays that way; this
    endpoint only runs when the planner explicitly clicks "Refine".
    """
    return await asyncio.to_thread(ai_enricher.refine_gamma_outline, body.outline, body.reprompt)


@app.get("/api/download/{proposal_id}")
async def download(proposal_id: str) -> FileResponse:
    """Download a previously generated proposal."""
    meta = _get_proposal_metadata(proposal_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    path = Path(meta["path"])
    if not path.exists():
        raise HTTPException(status_code=410, detail="Proposal file expired")
    return FileResponse(
        path=str(path),
        filename=meta["filename"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/download-email/{proposal_id}")
async def download_email(proposal_id: str) -> FileResponse:
    """Download the client-facing email Word document for a generated proposal."""
    meta = _get_proposal_metadata(proposal_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    doc_path_str = meta.get("email_doc_path")
    if not doc_path_str:
        raise HTTPException(status_code=404, detail="No email document for this proposal")
    doc_path = Path(doc_path_str)
    if not doc_path.exists():
        raise HTTPException(status_code=410, detail="Email document file expired")
    return FileResponse(
        path=str(doc_path),
        filename=meta.get("email_doc_filename", "client_email.docx"),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


_PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@app.get("/api/download-pptx-net/{proposal_id}")
async def download_pptx_net(proposal_id: str) -> FileResponse:
    """Download the simple, signature-ready Net PowerPoint deck for a generated proposal."""
    meta = _get_proposal_metadata(proposal_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    pptx_filename = meta.get("pptx_net_filename")
    if not pptx_filename:
        raise HTTPException(status_code=404, detail="No Net PowerPoint for this proposal")
    pptx_path = PROPOSALS_DIR / pptx_filename
    if not pptx_path.exists():
        raise HTTPException(status_code=410, detail="PowerPoint file expired")
    return FileResponse(path=str(pptx_path), filename=pptx_filename, media_type=_PPTX_MEDIA_TYPE)


@app.get("/api/download-pptx-gross/{proposal_id}")
async def download_pptx_gross(proposal_id: str) -> FileResponse:
    """Download the simple, signature-ready Gross PowerPoint deck for a generated proposal."""
    meta = _get_proposal_metadata(proposal_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    pptx_filename = meta.get("pptx_gross_filename")
    if not pptx_filename:
        raise HTTPException(status_code=404, detail="No Gross PowerPoint for this proposal")
    pptx_path = PROPOSALS_DIR / pptx_filename
    if not pptx_path.exists():
        raise HTTPException(status_code=410, detail="PowerPoint file expired")
    return FileResponse(path=str(pptx_path), filename=pptx_filename, media_type=_PPTX_MEDIA_TYPE)


@app.post("/api/drive/upload")
async def drive_upload(body: DriveUploadRequest, request: Request) -> dict:
    """
    Upload a generated proposal to Google Drive.
    Folder structure: <DRIVE_ROOT>/<seller_email>/<filename>
    Returns {needs_auth: true, auth_url: ...} when OAuth2 authorization is required.
    Returns a graceful no-op when Drive credentials aren't configured.
    """
    meta = _get_proposal_metadata(body.proposal_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    path = Path(meta["path"])

    redirect_uri = str(request.base_url).rstrip("/") + "/api/drive/callback"
    result = drive_uploader.upload_proposal(
        local_path=path,
        seller_email=body.seller_email or meta.get("seller_email", ""),
        as_google_sheet=True,
        redirect_uri=redirect_uri,
    )
    if result.get("needs_auth"):
        result["auth_url"] = drive_uploader.get_auth_url(redirect_uri)
    return result


@app.get("/api/drive/callback")
async def drive_callback(code: str, request: Request) -> HTMLResponse:
    """Handle the OAuth2 redirect from Google after the user authorizes Drive access."""
    redirect_uri = str(request.base_url).rstrip("/") + "/api/drive/callback"
    try:
        drive_uploader.exchange_code(code, redirect_uri)
        html = (
            "<!doctype html><html><head><title>Drive Authorized</title></head><body>"
            "<script>"
            "if(window.opener){"
            "window.opener.postMessage('drive_auth_success','*');"
            "window.close();"
            "} else {"
            "document.write('<p>Google Drive authorized. Close this tab and retry the upload.</p>');"
            "}"
            "</script>"
            "<p>Google Drive authorized. You can close this tab.</p>"
            "</body></html>"
        )
    except Exception as e:
        html = (
            f"<!doctype html><html><body>"
            f"<p>Authorization failed: {e}</p>"
            f"<p>Close this tab and try again.</p>"
            f"</body></html>"
        )
    return HTMLResponse(content=html)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "catalog_size": len(CATALOG)}


# ---------------------------------------------------------------------------
# Admin — proposal history + rate overrides + user management
#
# Behind login (see _require_login above) — every /api/admin/* route AND
# the /admin page itself additionally require is_admin, not just a valid
# session.
# ---------------------------------------------------------------------------

class RateOverrideRequest(BaseModel):
    product_name: str
    base_rate: Optional[float] = None
    minimum_spend: Optional[float] = None
    estimated_cpm_for_imps: Optional[float] = None


class NewProductRequest(BaseModel):
    family: str
    name: str
    short_label: Optional[str] = None
    buying_model: str  # "CPM" | "CPP" | "Fixed"
    base_rate: Optional[float] = None
    minimum_spend: Optional[float] = None
    proposal_description: Optional[str] = None
    sizes: Optional[str] = None
    notes: Optional[str] = None
    estimated_cpm_for_imps: Optional[float] = None
    tech_platform: Optional[str] = None
    is_addon: bool = False


class MarketConfigRequest(BaseModel):
    market_key: str  # matched against the parsed "Salesperson market" field; "__default__" is the fallback every market uses until it has its own entry
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    dsc_email: Optional[str] = None  # Digital Sales Coordinator (assistant) — CC'd on the internal seller email
    dsm_email: Optional[str] = None  # Digital Sales Manager — CC'd on the internal seller email
    ccs: Optional[list[str]] = None


@app.get("/admin", response_class=HTMLResponse)
async def admin_page() -> HTMLResponse:
    """Serve the admin SPA (proposal history + rate overrides)."""
    return _serve_html_with_cache_busted_static(TEMPLATES_DIR / "admin.html")


# --------------------------------------------------------------------------
# Users — admin-only account management. There's no self-service signup;
# an admin creates every account here (the very first one comes from the
# ADMIN_BOOTSTRAP_EMAIL/PASSWORD startup step in app/auth.py instead, since
# nobody can be logged in yet to use this endpoint).
# --------------------------------------------------------------------------

class NewUserRequest(BaseModel):
    email: str
    password: str
    is_admin: bool = False


class UserUpdateRequest(BaseModel):
    is_admin: Optional[bool] = None
    disabled: Optional[bool] = None
    new_password: Optional[str] = None


@app.get("/api/admin/users")
async def admin_list_users() -> dict:
    return {"users": auth_svc.list_users()}


@app.get("/api/admin/users/export")
async def admin_export_users() -> Response:
    """Download the user list as a CSV — same "at least see what's there
    at a glance, outside the app" audit use as the Rates tab's export.
    Never includes password hashes/salts — list_users() itself never
    returns those (see auth_svc._public_user)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["email", "is_admin", "disabled", "created_at"], extrasaction="ignore")
    writer.writeheader()
    for u in auth_svc.list_users():
        writer.writerow(u)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=proposal_builder_users_export.csv"},
    )


@app.post("/api/admin/users")
async def admin_create_user(body: NewUserRequest) -> dict:
    try:
        user = auth_svc.create_user(body.email, body.password, is_admin=body.is_admin)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"created": True, "user": user}


@app.patch("/api/admin/users/{user_id}")
async def admin_update_user(user_id: str, body: UserUpdateRequest, request: Request) -> dict:
    if auth_svc.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found.")
    acting_user = request.state.user  # set by _require_login — always present here, this route is admin-only
    if body.disabled and user_id == acting_user["id"]:
        raise HTTPException(status_code=400, detail="You can't disable your own account.")
    if body.is_admin is False and user_id == acting_user["id"]:
        raise HTTPException(status_code=400, detail="You can't remove your own admin access.")
    if body.new_password is not None:
        try:
            auth_svc.set_user_password(user_id, body.new_password)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    if body.is_admin is not None:
        auth_svc.set_user_admin(user_id, body.is_admin)
    if body.disabled is not None:
        auth_svc.set_user_disabled(user_id, body.disabled)
    return {"saved": True, "user": auth_svc.get_user_by_id(user_id)}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request) -> dict:
    acting_user = request.state.user
    if user_id == acting_user["id"]:
        raise HTTPException(status_code=400, detail="You can't delete your own account.")
    deleted = auth_svc.delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"deleted": True}


# Deliberately narrower than _PROPOSAL_COLUMNS (drops reopen_state,
# email_doc_filename, pptx_net_filename, pptx_gross_filename) — the list
# view below never reads any of those, but reopen_state in particular can
# be a genuinely large JSON blob (the full request/line_items/avails_data
# for every tier), and the old query pulled it across the wire for EVERY
# row just to immediately discard it on every single admin page load.
_PROPOSAL_LIST_COLUMNS = (
    "proposal_id, client_name, seller_email, requested_by, notion_id, "
    "proposal_title, filename, generated_at, requester_ip, "
    "requester_user_agent, summary, status, updated_at"
)


def _query_proposals(*, mine_email: Optional[str], search: str, page: int, page_size: int) -> dict:
    """
    Shared query behind both /api/admin/proposals (any admin, defaults to
    their own but can see everyone's) and /api/my-proposals (any logged-in
    user, ALWAYS their own — see that endpoint for why it's a separate,
    non-admin-namespaced route rather than a relaxed permission check on
    this one). Paginated and filtered at the DATABASE level (LIMIT/OFFSET
    + a WHERE clause), not fetch-everything-then-slice-in-Python — a
    growing proposals table was an unbounded, ever-slower query on every
    single page load otherwise.

    mine_email: None means no seller_email filter at all (admin "All
    proposals"); any other value scopes to that exact email
    (case-insensitive) — the CALLER decides which, this function just
    applies whatever it's given.
    """
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)  # hard cap — this is a page size, not an export
    offset = (page - 1) * page_size

    where_clauses = []
    params: list = []
    if mine_email:
        # created_by_email (the actual logged-in session that clicked
        # Generate) — NOT seller_email (whatever the pasted Notion text's
        # own "Salesperson email:" field said, which someone else may be
        # the one generating on behalf of). See schema.sql's migration
        # comment for the full reasoning; NULL for any proposal generated
        # before this column existed, so those correctly never match here.
        where_clauses.append("LOWER(created_by_email) = LOWER(%s)")
        params.append(mine_email)
    search = search.strip()
    if search:
        like = f"%{search}%"
        where_clauses.append(
            "(client_name ILIKE %s OR seller_email ILIKE %s OR requested_by ILIKE %s "
            "OR notion_id ILIKE %s OR proposal_title ILIKE %s)"
        )
        params.extend([like, like, like, like, like])
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    # COUNT(*) OVER() rides along with the page's own rows in one round
    # trip, instead of a second full COUNT query just for the "N total"
    # figure the pagination UI needs.
    rows = fetch_all(
        f"""
        SELECT {_PROPOSAL_LIST_COLUMNS}, COUNT(*) OVER() AS total_count
        FROM proposals
        {where_sql}
        ORDER BY updated_at DESC
        LIMIT %s OFFSET %s
        """,
        (*params, page_size, offset),
    )

    total_count = rows[0]["total_count"] if rows else 0
    proposals = []
    for row in rows:
        meta = _proposal_metadata_from_row(row)
        proposals.append({
            "proposal_id": meta["proposal_id"],
            "client_name": meta.get("client_name", ""),
            "seller_email": meta.get("seller_email", ""),
            "requested_by": meta.get("requested_by", ""),
            "notion_id": meta.get("notion_id", ""),
            "proposal_title": meta.get("proposal_title", ""),
            "filename": meta.get("filename", ""),
            "generated_at": meta.get("generated_at", ""),
            "requester_ip": meta.get("requester_ip", ""),
            "requester_user_agent": meta.get("requester_user_agent", ""),
            "total_net": (meta.get("summary") or {}).get("total_net"),
            "total_gross": (meta.get("summary") or {}).get("total_gross"),
            "tabs_built": (meta.get("summary") or {}).get("tabs_built", []),
            "status": meta.get("status") or "generated",
            "updated_at": meta.get("updated_at", ""),
        })
    return {
        "proposals": proposals,
        "count": len(proposals),
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, -(-total_count // page_size)),  # ceil division
    }


@app.get("/api/admin/proposals")
async def admin_list_proposals(
    request: Request,
    page: int = 1,
    page_size: int = 25,
    mine: bool = True,
    search: str = "",
) -> dict:
    """
    List generated proposals, newest first — client, seller, Notion ID,
    title, and who/what device generated it. Admin-only (see
    /api/my-proposals for the non-admin equivalent).

    mine: True (the default) scopes to the LOGGED-IN admin's own
    proposals (seller_email matches their login email, case-insensitive)
    — per explicit request, planners land on "my work" first rather than
    the whole company's history. False returns everyone's — an admin
    privilege /api/my-proposals deliberately doesn't have.

    search: optional free-text filter (client, seller/AE, Notion ID, or
    proposal title) — applied server-side now that the full list isn't
    sitting in the browser to filter client-side anymore; a search
    box keystroke is a real request now, not an instant in-memory filter.
    """
    return _query_proposals(
        mine_email=request.state.user["email"] if mine else None,
        search=search, page=page, page_size=page_size,
    )


@app.get("/api/my-proposals")
async def my_proposals(request: Request, page: int = 1, page_size: int = 10, search: str = "") -> dict:
    """
    A non-admin planner's OWN proposal history — same paginated/searched
    query /api/admin/proposals uses, but deliberately a separate route
    outside the /api/admin/* namespace (reachable by any logged-in user,
    not just admins — see _require_login's blanket "/api/admin/* needs
    is_admin" rule, which this is intentionally NOT under) rather than a
    carved-out exception inside that admin-only gate. Always scoped to
    the CALLER's own email server-side — there's no `mine` param to
    override, unlike the admin endpoint, so a non-admin can never see
    anyone else's history no matter what they pass.
    """
    return _query_proposals(
        mine_email=request.state.user["email"],
        search=search, page=page, page_size=page_size,
    )


# ---------------------------------------------------------------------------
# Notion Requests database search — Step 01
# ---------------------------------------------------------------------------
# Planner-facing (any logged-in user, not admin-only — every planner needs
# this at Step 01, same reasoning as /api/my-proposals above being outside
# the /api/admin/* namespace).

# The exact 3 statuses the planner picks from — matches the Requests
# database's own "Status" property values shown in its board view. Kept
# as a fixed, reviewed list (not fetched live from Notion on every page
# load) since which statuses make sense for a planner to search is a
# product decision, not something that should silently change if someone
# relabels a status option in Notion.
_NOTION_SEARCH_STATUSES = ["New", "Paused", "Progress"]


@app.get("/api/notion/search")
async def notion_search(status: str) -> dict:
    """
    Requests currently at `status` (one of _NOTION_SEARCH_STATUSES) — a
    lightweight result per page (whatever properties
    notion_client.page_to_flat_dict finds, generically extracted) for
    Step 01's picker UI. Returns {"configured": false} rather than a 4xx
    when the integration has no token/database set, so the frontend can
    just hide the search UI instead of showing an error for something
    that's an intentional not-set-up-yet state, not a fault.
    """
    if not notion_client.is_configured():
        return {"configured": False, "results": []}
    if status not in _NOTION_SEARCH_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {_NOTION_SEARCH_STATUSES}")
    try:
        pages = notion_client.query_by_status(status)
    except notion_client.NotionAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {
        "configured": True,
        "results": [
            {"page_id": p["id"], **notion_client.page_to_flat_dict(p)}
            for p in pages
        ],
    }


@app.get("/api/notion/page/{page_id}")
async def notion_get_page(page_id: str) -> dict:
    """
    What Step 01 reads once a planner picks a specific search result:
    both the page's PROPERTIES (for the reference panel) and its BODY
    TEXT (confirmed by the planner to be literally the same "Label:
    value" content a paste already contains) — see
    notion_client.get_page_body_text for why that means zero further
    parsing is needed; the frontend can feed body_text straight into the
    same textarea a manual paste fills.
    """
    if not notion_client.is_configured():
        raise HTTPException(status_code=503, detail="Notion integration isn't configured.")
    try:
        page = notion_client.get_page(page_id)
        body_text = notion_client.get_page_body_text(page_id)
    except notion_client.NotionAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"page_id": page_id, "properties": notion_client.page_to_flat_dict(page), "body_text": body_text}


# ---------------------------------------------------------------------------
# Admin analytics dashboard
# ---------------------------------------------------------------------------
# Aggregates come from summary/reopen_state (JSONB columns) rather than
# their own dedicated columns — nothing here is a new schema.sql change.
# Every value is pulled as JSON TEXT (Postgres's ->> operator), never CAST
# in SQL (::numeric/::int) — a single malformed/legacy row would abort the
# WHOLE query with a Postgres cast error, whereas text extraction never
# fails; each field is parsed defensively in Python instead, skipping only
# the one bad value rather than the whole row. jsonb_typeof(...) = 'array'
# (not "IS NOT NULL") is the correct way to detect a real JSON array vs.
# summary's own JSON `null` (Python's `None`, which IS a JSON value, not
# SQL NULL — `'null'::jsonb IS NOT NULL` is TRUE in Postgres, a real trap
# for exactly this check).
_ANALYTICS_WINDOWS = {"30d": 30, "90d": 90, "12m": 365, "all": None}


def _analytics_since(window: str) -> Optional[datetime]:
    days = _ANALYTICS_WINDOWS.get(window, None)
    if not days:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def _parse_number(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_int(raw: Optional[str]) -> Optional[int]:
    n = _parse_number(raw)
    return int(n) if n is not None else None


def _isoformat_or_none(value) -> Optional[str]:
    """Same explicit datetime->str conversion _proposal_metadata_from_row
    already applies before a dict response leaves this module — a plain
    `-> dict` return (no Pydantic model) needs it done by hand."""
    return value.isoformat() if hasattr(value, "isoformat") else value


def compute_admin_analytics(window: str = "all") -> dict:
    """
    THE aggregation logic behind GET /api/admin/analytics — extracted so
    it's independently testable against a mocked fetch_all() (same
    pattern as _validate_monthly_breakdown), since the real query needs a
    live Postgres this dev environment doesn't have.

    "Duplicated" proposals: rows sharing the same non-blank notion_id —
    confirmed as a REAL, already-happening pattern (reopening a past
    proposal and generating again creates a brand-new proposal_id/row
    carrying the SAME notion_id forward, and notion_id has no UNIQUE
    constraint) rather than a hypothetical. A blank/missing notion_id
    never counts toward this — grouping every no-ID proposal together as
    if they were the same request would be a false positive, not a
    genuine duplicate.
    """
    since = _analytics_since(window)
    # Drafts (status='draft') never had a real Excel/summary built and
    # must never count toward "who creates plans"/revenue analytics — a
    # NULL generated_at already excludes them whenever a time window is
    # active, but "all" has no window clause at all, so this needs to be
    # explicit rather than relying on that side effect.
    where_clauses = ["status = 'generated'"]
    params: list = []
    if since:
        where_clauses.append("generated_at >= %s")
        params.append(since)
    where_sql = f"WHERE {' AND '.join(where_clauses)}"
    params = tuple(params)
    rows = fetch_all(
        f"""
        SELECT
            proposal_id,
            client_name,
            notion_id,
            created_by_email,
            seller_email,
            generated_at,
            summary->>'total_net' AS total_net_raw,
            summary->>'total_gross' AS total_gross_raw,
            jsonb_typeof(summary->'tiers') AS tiers_type,
            reopen_state->'request'->>'total_months' AS total_months_raw,
            reopen_state->'request'->>'request_type' AS request_type_raw,
            reopen_state->>'time_unit' AS time_unit_raw
        FROM proposals
        {where_sql}
        ORDER BY generated_at DESC NULLS LAST
        """,
        params,
    )

    total_count = len(rows)

    # --- Who creates plans ---------------------------------------------
    planner_counts: Counter = Counter()
    planner_net_totals: dict[str, float] = {}
    for r in rows:
        email = (r.get("created_by_email") or "").strip().lower() or "(unknown)"
        planner_counts[email] += 1
        net = _parse_number(r.get("total_net_raw"))
        if net is not None:
            planner_net_totals[email] = planner_net_totals.get(email, 0.0) + net
    by_planner = [
        {
            "email": email,
            "count": count,
            "total_net": round(planner_net_totals.get(email, 0.0), 2),
            "avg_net": round(planner_net_totals.get(email, 0.0) / count, 2) if email in planner_net_totals else None,
        }
        for email, count in planner_counts.most_common()
    ]

    # --- Duplicated / reworked requests (shared notion_id) --------------
    notion_id_rows: dict[str, list[dict]] = {}
    for r in rows:
        nid = (r.get("notion_id") or "").strip()
        if not nid:
            continue
        notion_id_rows.setdefault(nid, []).append(r)
    unique_request_count = len(notion_id_rows)
    proposals_with_notion_id = sum(len(v) for v in notion_id_rows.values())
    duplicate_count = proposals_with_notion_id - unique_request_count
    most_regenerated = sorted(
        (
            {
                "notion_id": nid,
                "count": len(group),
                "client_name": group[0].get("client_name") or "",
                "latest_generated_at": _isoformat_or_none(
                    max((g.get("generated_at") for g in group if g.get("generated_at")), default=None)
                ),
            }
            for nid, group in notion_id_rows.items() if len(group) > 1
        ),
        key=lambda x: (-x["count"], x["notion_id"]),
    )[:20]

    # --- Proposal value / flight length ---------------------------------
    net_values = [v for v in (_parse_number(r.get("total_net_raw")) for r in rows) if v is not None]
    months_values = [v for v in (_parse_int(r.get("total_months_raw")) for r in rows) if v is not None]
    multi_tier_count = sum(1 for r in rows if r.get("tiers_type") == "array")

    # --- Volume over time (monthly buckets) ------------------------------
    month_counts: Counter = Counter()
    for r in rows:
        gen = r.get("generated_at")
        if gen:
            month_counts[gen.strftime("%Y-%m")] += 1
    by_month = [{"month": k, "count": v} for k, v in sorted(month_counts.items())]

    # --- Request type breakdown ------------------------------------------
    request_type_counts = Counter((r.get("request_type_raw") or "(unspecified)") for r in rows)
    by_request_type = [
        {"request_type": k, "count": v} for k, v in request_type_counts.most_common()
    ]

    # --- Week/Month/Quarter toggle adoption -------------------------------
    time_unit_counts = Counter((r.get("time_unit_raw") or "month") for r in rows)
    by_time_unit = [{"time_unit": k, "count": v} for k, v in time_unit_counts.most_common()]

    return {
        "window": window,
        "total_proposals": total_count,
        "unique_requests": unique_request_count + (total_count - proposals_with_notion_id),
        "duplicate_count": duplicate_count,
        "avg_total_net": round(sum(net_values) / len(net_values), 2) if net_values else None,
        "total_net_sum": round(sum(net_values), 2) if net_values else 0.0,
        "avg_months": round(sum(months_values) / len(months_values), 2) if months_values else None,
        "multi_tier_pct": round(multi_tier_count / total_count * 100, 1) if total_count else 0.0,
        "by_planner": by_planner,
        "most_regenerated": most_regenerated,
        "by_month": by_month,
        "by_request_type": by_request_type,
        "by_time_unit": by_time_unit,
    }


@app.get("/api/admin/analytics")
async def admin_analytics(window: str = "all") -> dict:
    """
    Admin-only (gated by the blanket /api/admin/* middleware rule — see
    _require_login) usage dashboard: who's generating proposals, how many
    are reworked/regenerated versions of the same Notion request, volume
    over time, average value/flight length, and adoption of the Week/
    Month/Quarter toggle. window: "30d" | "90d" | "12m" | "all" (default).
    """
    if window not in _ANALYTICS_WINDOWS:
        window = "all"
    return compute_admin_analytics(window)


@app.get("/api/admin/notion/schema")
async def admin_notion_schema() -> dict:
    """
    One-time setup helper, admin-only (gated by the blanket /api/admin/*
    rule) — NOT used by Step 01's own search at runtime. Dumps the
    Requests database's real property names/types/option-lists so the
    hardcoded property names in app/services/notion_client.py (and any
    future paste-text field mapping) can be confirmed against reality
    instead of guessed from a screenshot. Run this once after setting
    NOTION_API_TOKEN/NOTION_REQUESTS_DATABASE_ID, never on a hot path.
    """
    try:
        return {"properties": notion_client.get_database_schema()}
    except notion_client.NotionAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


def _resolve_admin_product_key(product_name: str) -> tuple[str, bool]:
    """
    Given a display name an admin action is targeting — the product's
    CURRENT name (possibly renamed), its ORIGINAL/never-renamed name, or an
    even older former name — resolve it to (stable_key, is_builtin).
    stable_key is the raw CATALOG literal name for a built-in (the actual
    rate_overrides.product_name / by_name() lookup key, which never
    changes regardless of renames), or the custom product's own current
    `name` column for a custom (customs have no separate stable/display
    split the way built-ins do — see update_custom_product). Raises
    ValueError (surfaced as a 404) if nothing matches.
    """
    builtin_raw_names = {p.name for p in CATALOG}
    custom_names = {p.name for p in load_custom_products()}
    # 1. Direct raw/current match — covers "never renamed", the common case
    # for both built-ins and customs.
    if product_name in builtin_raw_names:
        return product_name, True
    if product_name in custom_names:
        return product_name, False
    # 2. Current EFFECTIVE (renamed) display name for a built-in.
    overrides = load_rate_overrides()
    for p in CATALOG:
        if overrides.get(p.name, {}).get("name") == product_name:
            return p.name, True
    # 3. An older former name, from further back than the latest rename.
    current = resolve_product_alias(product_name)
    if current and current != product_name:
        return _resolve_admin_product_key(current)
    raise ValueError(f"Unknown product '{product_name}'")


@app.get("/api/admin/rates")
async def admin_get_rates() -> dict:
    """Return the full catalog (built-in + admin-added) with current overrides
    flagged, for both the inline rate editor and the "Edit product" panel —
    every _OVERRIDABLE_FIELDS field is override-aware here now, not just the
    original 3 numeric ones (family/buying_model/sizes/etc. used to always
    show the raw catalog value even when overridden — a real display bug,
    fixed alongside adding the fields the edit panel needs). Soft-deleted
    built-ins are included (not hidden like effective_catalog()'s default)
    so the admin can see and restore them."""
    overrides = load_rate_overrides()
    custom_names = {p.name for p in load_custom_products()}
    deleted_names = load_deleted_builtin_names()
    products = []
    for p in CATALOG + load_custom_products():
        override = overrides.get(p.name, {})
        products.append({
            "name": override.get("name", p.name),
            # The raw/original name — stays stable across renames, so
            # admin.js can target edit/delete/restore actions unambiguously
            # even after the display `name` above has changed.
            "stable_name": p.name,
            "family": override.get("family", p.family),
            "short_label": override.get("short_label", p.short_label),
            "buying_model": override.get("buying_model", p.buying_model),
            "base_rate": override.get("base_rate", p.base_rate),
            "minimum_spend": override.get("minimum_spend", p.minimum_spend),
            "estimated_cpm_for_imps": override.get("estimated_cpm_for_imps", p.estimated_cpm_for_imps),
            "sizes": override.get("sizes", p.sizes),
            "tech_platform": override.get("tech_platform", p.tech_platform),
            "proposal_description": override.get("proposal_description", p.proposal_description),
            "notes": override.get("notes", p.notes),
            "catalog_base_rate": p.base_rate,
            "catalog_minimum_spend": p.minimum_spend,
            "catalog_estimated_cpm_for_imps": p.estimated_cpm_for_imps,
            "has_override": p.name in overrides,
            "is_custom": p.name in custom_names,
            "is_deleted": p.name in deleted_names,
            "is_addon": override.get("is_addon", p.is_addon),
        })
    return {"products": products, "overridable_fields": list(_OVERRIDABLE_FIELDS)}


@app.post("/api/admin/rates")
async def admin_save_rate(body: RateOverrideRequest) -> dict:
    """Save (or update) a rate override for one product."""
    try:
        stable_key, _ = _resolve_admin_product_key(body.product_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    overrides = load_rate_overrides()
    fields_ = {
        k: v for k, v in {
            "base_rate": body.base_rate,
            "minimum_spend": body.minimum_spend,
            "estimated_cpm_for_imps": body.estimated_cpm_for_imps,
        }.items() if v is not None
    }
    # Merge onto (rather than replace) any existing override row so this
    # narrow 3-field editor can't silently drop OTHER overridden fields —
    # a rename (`name`) or an "Edit product" panel change, for instance.
    existing = overrides.get(stable_key, {})
    merged = {**existing, **fields_}
    if merged:
        overrides[stable_key] = merged
    else:
        overrides.pop(stable_key, None)
    save_rate_overrides(overrides)
    return {"saved": True, "product_name": stable_key, "override": merged}


@app.delete("/api/admin/rates/{product_name}")
async def admin_clear_rate(product_name: str) -> dict:
    """Revert one product's rate override back to the catalog default
    (also undoes any rename or soft-delete recorded on that row)."""
    try:
        stable_key, _ = _resolve_admin_product_key(product_name)
    except ValueError:
        stable_key = product_name  # nothing to resolve — clearing is a no-op either way
    clear_rate_override(stable_key)
    return {"cleared": True, "product_name": stable_key}


@app.post("/api/admin/products")
async def admin_add_product(body: NewProductRequest) -> dict:
    """Add a brand-new catalog product from the admin UI — persisted alongside
    (not mixed into) the built-in AdFlo catalog, available everywhere immediately."""
    try:
        product = add_custom_product({
            "family": body.family,
            "name": body.name,
            "short_label": body.short_label,
            "buying_model": body.buying_model,
            "base_rate": body.base_rate,
            "minimum_spend": body.minimum_spend,
            "proposal_description": body.proposal_description,
            "sizes": body.sizes,
            "notes": body.notes,
            "estimated_cpm_for_imps": body.estimated_cpm_for_imps,
            "tech_platform": body.tech_platform,
            "is_addon": body.is_addon,
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"added": True, "product": asdict(product)}


@app.delete("/api/admin/products/{product_name}")
async def admin_delete_product(product_name: str) -> dict:
    """
    Remove a product. A built-in is SOFT-deleted (hidden from new
    selection everywhere except the admin Rates table itself, which still
    shows it — with a Restore action — so this is reversible; the CATALOG
    Python list itself is untouched, since it's meant to stay a pure
    reflection of the rate card, per catalog.py's own module docstring). A
    custom (admin-added) product is hard-deleted, same as before.
    """
    try:
        stable_key, is_builtin = _resolve_admin_product_key(product_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if is_builtin:
        set_builtin_deleted(stable_key, True)
        return {"deleted": True, "product_name": stable_key, "mode": "soft_delete"}
    deleted = delete_custom_product(stable_key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Custom product '{stable_key}' not found.")
    return {"deleted": True, "product_name": stable_key, "mode": "hard_delete"}


@app.post("/api/admin/products/{product_name}/restore")
async def admin_restore_product(product_name: str) -> dict:
    """Undo a built-in product's soft-delete. Custom products are hard-deleted (see above) — nothing to restore."""
    try:
        stable_key, is_builtin = _resolve_admin_product_key(product_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not is_builtin:
        raise HTTPException(status_code=400, detail="Custom products aren't soft-deleted, so there's nothing to restore — it would need to be re-added instead.")
    set_builtin_deleted(stable_key, False)
    return {"restored": True, "product_name": stable_key}


class ProductEditRequest(BaseModel):
    """Full-field edit for an EXISTING product (built-in or custom) — the
    admin "Edit" panel's payload. Same field set as NewProductRequest, plus
    `new_name` (renaming IS supported here now — see catalog.py's
    _OVERRIDABLE_FIELDS/by_name/record_product_alias for how an old name
    keeps resolving afterward, for both the parser and already-saved
    proposals). `product_name` identifies which product to edit and may be
    its current name, its original/never-renamed name, or any former name
    — see _resolve_admin_product_key. Every other field is optional and
    None means "leave as shown" — the edit panel pre-fills from the
    product's current effective values and sends null for anything the
    admin cleared, same "empty = revert to catalog default" convention the
    single-field rate editor already uses."""
    product_name: str
    new_name: Optional[str] = None
    family: Optional[str] = None
    short_label: Optional[str] = None
    buying_model: Optional[str] = None
    base_rate: Optional[float] = None
    minimum_spend: Optional[float] = None
    estimated_cpm_for_imps: Optional[float] = None
    sizes: Optional[str] = None
    tech_platform: Optional[str] = None
    proposal_description: Optional[str] = None
    notes: Optional[str] = None
    is_addon: Optional[bool] = None


@app.post("/api/admin/products/edit")
async def admin_edit_product(body: ProductEditRequest) -> dict:
    """
    Edit any field of an EXISTING product, built-in or custom:
      - Built-in: stored as a rate override (like the single-field editor,
        just covering every field that form doesn't) — only fields that
        actually differ from the real catalog value are kept, so
        resubmitting the form unchanged doesn't stamp redundant overrides.
      - Custom: updated in place via update_custom_product (full replace
        under the hood, so every field — not just the overridable set —
        actually changes).
    A `new_name` that differs from the product's current effective name
    renames it and records an alias (see catalog.py) so the parser and any
    already-saved proposal keep resolving the name it used to have.
    """
    try:
        stable_key, is_builtin = _resolve_admin_product_key(body.product_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    overrides = load_rate_overrides()
    current_effective_name = overrides.get(stable_key, {}).get("name", stable_key) if is_builtin else stable_key

    new_name = (body.new_name or "").strip() or None
    if new_name and new_name == current_effective_name:
        new_name = None  # resubmitted unchanged — not actually a rename
    if new_name:
        taken = {p.name for p in effective_catalog(include_deleted=True)} - {current_effective_name}
        if new_name in taken:
            raise HTTPException(status_code=400, detail=f"A product named '{new_name}' already exists.")

    fields = {k: v for k, v in body.model_dump(exclude={"product_name", "new_name"}).items() if v is not None}
    if new_name:
        fields["name"] = new_name

    if is_builtin:
        catalog_product = next(p for p in CATALOG if p.name == stable_key)
        kept = {k: v for k, v in fields.items() if v != getattr(catalog_product, k, None)}
        if kept:
            overrides[stable_key] = kept
        else:
            overrides.pop(stable_key, None)
        save_rate_overrides(overrides)
        if new_name:
            record_product_alias(current_effective_name, new_name)
        return {"saved": True, "product_name": new_name or stable_key, "mode": "override", "override": overrides.get(stable_key, {})}
    else:
        try:
            updated = update_custom_product(stable_key, fields)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if new_name:
            record_product_alias(current_effective_name, new_name)
        return {"saved": True, "product_name": updated.name, "mode": "custom"}


# Shared column order for both the export and the bulk-upsert import, so a
# planner can export -> edit in Excel -> re-upload the same file to update
# records without reshaping anything. is_custom/has_override are exported
# for visibility but ignored on import — they're derived status, not real
# editable fields (a row can't "become" built-in, and has_override is just
# "does base_rate/minimum_spend/estimated_cpm_for_imps differ from the
# built-in catalog's own value," which importing the row already implies).
_PRODUCT_CSV_COLUMNS = [
    "name", "family", "short_label", "buying_model", "base_rate", "minimum_spend",
    "estimated_cpm_for_imps", "sizes", "tech_platform", "proposal_description",
    "notes", "is_addon", "is_custom", "has_override", "is_deleted",
]


@app.get("/api/admin/products/export")
async def admin_export_products() -> Response:
    """Download the full effective catalog (built-ins + overrides applied
    + custom products) as a CSV — for review/audit, and as a starting
    point for the bulk-upsert import below (same column order)."""
    overrides = load_rate_overrides()
    custom_names = {p.name for p in load_custom_products()}
    deleted_names = load_deleted_builtin_names()
    raw_products = list(CATALOG) + load_custom_products()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_PRODUCT_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    # include_deleted=True (and zipped against the equally-unfiltered
    # raw_products) so a soft-deleted built-in doesn't misalign this
    # pairing — has_override/is_deleted are checked by each product's
    # STABLE name (`raw.name`), not `p.name`, which is the CURRENT
    # (possibly renamed) display name once overrides are applied below.
    for p, raw in zip(effective_catalog(include_deleted=True), raw_products):
        writer.writerow({
            "name": p.name,
            "family": p.family,
            "short_label": p.short_label,
            "buying_model": p.buying_model,
            "base_rate": p.base_rate,
            "minimum_spend": p.minimum_spend,
            "estimated_cpm_for_imps": p.estimated_cpm_for_imps,
            "sizes": p.sizes,
            "tech_platform": p.tech_platform,
            "proposal_description": p.proposal_description,
            "notes": p.notes,
            "is_addon": p.is_addon,
            "is_custom": p.name in custom_names,
            "has_override": raw.name in overrides,
            "is_deleted": raw.name in deleted_names,
        })
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=adflo_catalog_export.csv"},
    )


def _parse_csv_bool(v: str) -> bool:
    return (v or "").strip().lower() in ("true", "1", "yes", "y")


def _parse_csv_float(v: str) -> Optional[float]:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


@app.post("/api/admin/products/bulk-upsert")
async def admin_bulk_upsert_products(file: UploadFile = File(...)) -> dict:
    """
    Add or update many products at once from an uploaded CSV (same columns
    as the export above). Matched by `name`:
      - Name matches a BUILT-IN catalog product -> saved as a rate override
        (only base_rate/minimum_spend/estimated_cpm_for_imps — a built-in's
        other fields are the canonical catalog and aren't editable this way,
        same restriction the single-row rate editor already has).
      - Name matches an EXISTING CUSTOM product -> replaced entirely with
        the row's values (delete + re-add, so every field — not just the
        3 overridable ones — actually updates).
      - Name matches nothing -> added as a new custom product.
    Never raises for a single bad row — collects per-row errors instead, so
    one typo in a 60-row sheet doesn't block the other 59.
    """
    raw = (await file.read()).decode("utf-8-sig")  # -sig: tolerate an Excel-saved CSV's BOM
    reader = csv.DictReader(io.StringIO(raw))
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail="Empty or unreadable CSV file.")
    missing_cols = {"name", "family", "buying_model"} - set(reader.fieldnames)
    if missing_cols:
        raise HTTPException(status_code=400, detail=f"CSV is missing required column(s): {', '.join(sorted(missing_cols))}")

    builtin_names = {p.name for p in CATALOG}
    custom_names = {p.name for p in load_custom_products()}
    created, updated_override, updated_custom, errors = [], [], [], []
    rows_processed = 0

    for i, row in enumerate(reader, start=2):  # row 1 is the header
        rows_processed += 1
        name = (row.get("name") or "").strip()
        if not name:
            errors.append(f"Row {i}: missing name — skipped.")
            continue
        try:
            if name in builtin_names:
                fields = {
                    k: _parse_csv_float(row.get(k))
                    for k in ("base_rate", "minimum_spend", "estimated_cpm_for_imps")
                }
                # Only actually store a field as an override when it differs
                # from the built-in catalog's OWN value — otherwise
                # re-uploading an unedited (or only-partially-edited) export
                # would silently stamp a redundant override onto every
                # single built-in product, cluttering the whole catalog
                # with "Override" badges for values nobody actually changed.
                catalog_product = next((p for p in CATALOG if p.name == name), None)
                kept = {
                    k: v for k, v in fields.items()
                    if v is not None and (catalog_product is None or v != getattr(catalog_product, k, None))
                }
                if kept:
                    overrides = load_rate_overrides()
                    overrides[name] = kept
                    save_rate_overrides(overrides)
                    updated_override.append(name)
                # else: every provided value matched the catalog default (or
                # the row left them all blank) — nothing to do, not an error.
            else:
                new_fields = {
                    "family": row.get("family"),
                    "name": name,
                    "short_label": row.get("short_label"),
                    "buying_model": row.get("buying_model"),
                    "base_rate": _parse_csv_float(row.get("base_rate")),
                    "minimum_spend": _parse_csv_float(row.get("minimum_spend")),
                    "estimated_cpm_for_imps": _parse_csv_float(row.get("estimated_cpm_for_imps")),
                    "sizes": row.get("sizes"),
                    "tech_platform": row.get("tech_platform"),
                    "proposal_description": row.get("proposal_description"),
                    "notes": row.get("notes"),
                    "is_addon": _parse_csv_bool(row.get("is_addon")),
                }
                if name in custom_names:
                    delete_custom_product(name)  # re-add below with the full new field set
                    add_custom_product(new_fields)
                    updated_custom.append(name)
                else:
                    add_custom_product(new_fields)
                    custom_names.add(name)
                    created.append(name)
        except ValueError as e:
            errors.append(f"Row {i} ('{name}'): {e}")

    return {
        "created": created,
        "updated_as_rate_override": updated_override,
        "updated_custom_product": updated_custom,
        "errors": errors,
        "total_rows_processed": rows_processed,
    }


# ---------------------------------------------------------------------------
# Market config — per-market office address (shown on every export's meta
# block) and CC list (for the Step 07 seller-email link). Both used to be a
# single hardcoded Burbank address with no CCs regardless of market; this
# makes both admin-editable, with a "__default__" entry every market falls
# back to until it has its own values.
# ---------------------------------------------------------------------------

@app.get("/api/admin/market-config")
async def admin_get_market_config() -> dict:
    """Every configured market's address + CC list ("__default__" first),
    plus the always-on base CC list."""
    config = load_market_config()
    markets = []
    for key, entry in config.items():
        if key in (BASE_CCS_KEY, T1_CCS_KEY):
            continue  # not a market — their own fields below, different shape (a bare list)
        markets.append({
            "market_key": key,
            "is_default": key == DEFAULT_KEY,
            "address_line1": entry.get("address_line1", ""),
            "address_line2": entry.get("address_line2", ""),
            "dsc_email": entry.get("dsc_email") or "",
            "dsm_email": entry.get("dsm_email") or "",
            "ccs": entry.get("ccs", []),
        })
    markets.sort(key=lambda m: (not m["is_default"], m["market_key"].lower()))
    return {"markets": markets, "base_ccs": config.get(BASE_CCS_KEY, []), "t1_ccs": config.get(T1_CCS_KEY, [])}


@app.get("/api/admin/market-config/export")
async def admin_export_market_config() -> Response:
    """Download the market config table as a CSV — same audit/at-a-glance
    use as the Rates and Users exports. Base/T1 CC lists aren't per-market
    rows, so they're included as two extra summary rows at the top rather
    than left out of the export entirely."""
    config = load_market_config()
    buf = io.StringIO()
    fieldnames = ["market_key", "is_default", "address_line1", "address_line2", "dsc_email", "dsm_email", "ccs"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerow({"market_key": "(base CCs — every proposal, every market)", "ccs": "; ".join(config.get(BASE_CCS_KEY, []))})
    writer.writerow({"market_key": "(T1 escalation CCs)", "ccs": "; ".join(config.get(T1_CCS_KEY, []))})
    for key, entry in config.items():
        if key in (BASE_CCS_KEY, T1_CCS_KEY):
            continue
        writer.writerow({
            "market_key": key,
            "is_default": key == DEFAULT_KEY,
            "address_line1": entry.get("address_line1", ""),
            "address_line2": entry.get("address_line2", ""),
            "dsc_email": entry.get("dsc_email") or "",
            "dsm_email": entry.get("dsm_email") or "",
            "ccs": "; ".join(entry.get("ccs", [])),
        })
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=proposal_builder_markets_export.csv"},
    )


class BaseCcsRequest(BaseModel):
    ccs: list[str] = []


@app.post("/api/admin/market-config/base-ccs")
async def admin_save_base_ccs(body: BaseCcsRequest) -> dict:
    """Set the CC list included on EVERY seller-email mailto regardless of market."""
    saved = set_base_ccs(body.ccs)
    return {"saved": True, "base_ccs": saved}


@app.post("/api/admin/market-config/t1-ccs")
async def admin_save_t1_ccs(body: BaseCcsRequest) -> dict:
    """Set the escalation CC list added when any tier spends $10k/mo+."""
    saved = set_t1_ccs(body.ccs)
    return {"saved": True, "t1_ccs": saved}


@app.get("/api/market-ccs")
async def get_market_ccs_for(market: str = "") -> dict:
    """Public (non-admin) lookup used by the Step 07 seller-email mailto
    link: base CCs + this market's own CCs, deduped (query param, not a
    path segment, because market names carry spaces/parens, e.g.
    "Los Angeles (Tier 1)") — plus the T1 escalation list and its spend
    threshold, returned separately since whether T1 actually applies
    depends on the proposal's tier spend, which only the client knows."""
    return {
        "ccs": get_all_ccs_for_market(market or None),
        "t1_ccs": get_t1_ccs(),
        "t1_spend_threshold": T1_SPEND_THRESHOLD,
    }


@app.post("/api/admin/market-config")
async def admin_save_market_config(body: MarketConfigRequest) -> dict:
    """Upsert one market's address/CCs (or "__default__"). Blank/omitted
    fields leave that part of the entry untouched — clear the address by
    posting an explicit empty string, not by omitting it."""
    market_key = body.market_key.strip()
    if not market_key:
        raise HTTPException(status_code=400, detail="market_key is required.")
    fields = {
        k: v for k, v in {
            "address_line1": body.address_line1,
            "address_line2": body.address_line2,
            "dsc_email": body.dsc_email,
            "dsm_email": body.dsm_email,
            "ccs": body.ccs,
        }.items() if v is not None
    }
    entry = set_market_entry(market_key, fields)
    return {"saved": True, "market_key": market_key, "entry": entry}


@app.delete("/api/admin/market-config/{market_key}")
async def admin_delete_market_config(market_key: str) -> dict:
    """Remove a market's own override, reverting it to "__default__". The
    "__default__" entry itself can't be deleted — clear its fields with a
    POST instead."""
    if market_key == DEFAULT_KEY:
        raise HTTPException(status_code=400, detail="Can't delete the default entry — edit it with POST instead.")
    deleted = delete_market_entry(market_key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Market '{market_key}' not found.")
    return {"deleted": True, "market_key": market_key}
