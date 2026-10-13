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
import math
import os
import re
import secrets
import tempfile
import threading
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Optional
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
import psycopg
from psycopg.types.json import Jsonb

from app import auth as auth_svc
from app import disclaimers as disclaimers_svc

from app.db import fetch_all, fetch_one, get_connection
from app.catalog import (
    by_name, families, by_family, effective_catalog, all_product_aliases,
    list_products as catalog_list_products, create_product as catalog_create_product,
    update_product as catalog_update_product, delete_product as catalog_delete_product,
    bulk_upsert_products as catalog_bulk_upsert_products,
    EDITABLE_FIELDS as PRODUCT_EDITABLE_FIELDS,
)
from app.services.notion_parser import (
    ProposalRequest,
    ProductSpecifics,
    parse_notion,
    classify_output_tabs,
    normalize_tab_keys,
)
from app.services.proposal_generator import LineItem, AddonItem, generate_proposal, normalize_full_flight_lines
from app.services.recommender import recommend_line_items
from app.market_config import (
    load_market_config, set_market_entry, delete_market_entry, normalize_emails, DEFAULT_KEY,
    BASE_CCS_KEY, get_base_ccs, set_base_ccs, get_all_ccs_for_market,
    T1_CCS_KEY, get_t1_ccs, set_t1_ccs, T1_SPEND_THRESHOLD,
)
from app.services import drive_uploader
from app.services import proposal_files as proposal_files_svc
from app.services import ai_enricher
from app.services import email_totals as email_totals_svc
from app.services import docx_builder
from app.services import pptx_builder
from app.services import strategy_brief as strategy_brief_svc
from app.services import ad_presence as ad_presence_svc
from app.services import roadblocks as roadblocks_svc
from app.services import restrictions as restrictions_svc
from app.services import faq_entries as faq_svc
from app.services import migrations as schema_migrations
from app.services import schema_bootstrap
from app.services import gamma_client
from app.services import monthly_allocation
from app.services import notion_client
from app.services import constanza_geo
from app.services import constanza_forecast_map as forecast_map_svc
from app.services import forecast_store
from app.services.constanza_engine import estimate_avails
from app.services.constanza_audience import AudienceSpec, estimate_audience, load_interest_taxonomy, parse_audience_text
from app.services.line_modifier import clean_line_modifier_or_none


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
        return          # no database to talk to: nothing below could work either
    try:
        # The tables newer features need (products, Drive's shared file copy, FAQ rows): created from schema.sql's own
        # idempotent statements, so a deploy never waits on someone running SQL by hand. Never fatal.
        created = await asyncio.to_thread(schema_bootstrap.ensure_tables)
        if created:
            _logger.info("schema bootstrap ensured: %s", ", ".join(created))
    except Exception as e:
        _logger.warning("Startup schema bootstrap skipped: %s: %s", type(e).__name__, e)
    # Versioned migrations (migrations/*.sql), then the forecast reference data: the 30 forecast products and any series
    # that has never been imported. Runs in a background thread so a first-time load (about 15 s) never delays serving;
    # an advisory lock keeps two Autoscale instances from loading at once. Never fatal.
    threading.Thread(target=_bootstrap_forecast_data, name="forecast-bootstrap", daemon=True).start()


def _bootstrap_forecast_data() -> None:
    try:
        from app.services.forecast_import import service as forecast_import_service
        result = forecast_import_service.bootstrap()
        if result.get("migrations") or result.get("products") or result.get("series"):
            _logger.info("forecast data bootstrap: %s", result)
        forecast_store.invalidate()
        forecast_map_svc.invalidate()
    except Exception as e:  # noqa: BLE001
        _logger.warning("Forecast data bootstrap skipped: %s: %s", type(e).__name__, e)


@app.on_event("shutdown")
def _on_shutdown() -> None:
    """Return the database connections cleanly."""
    from app import db as _db
    _db.close_pool()


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
        "force_tabs": normalize_tab_keys(force_tabs) if force_tabs else force_tabs,   # dooh_summary/dooh_screenlist -> dooh
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
    # Step 06's master toggle was switched off for this line. Persisted (the client-side baseline that used to
    # carry this isn't) so a reopened proposal doesn't silently re-enable the breakdown. Not used by generation.
    mb_off: bool = False
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
    # Step 04's optional per-line "line note" ("Hispanic, Phoenix only"): a short plain-text label that tells two lines
    # of the same product apart. Shown in purple under the product name + objective in the export (and in the other
    # steps / decks / outline for reference). A LABEL only — it never touches a number. Distinct from `notes_override`
    # above, which goes into the export's Planner Notes COLUMN. Sanitized here (trimmed, control characters stripped,
    # capped — see app/services/line_modifier.py) so every consumer sees the same clean text; None for a line that has
    # none, and for every proposal saved before this field existed.
    line_modifier: Optional[str] = None

    @field_validator("line_modifier", mode="before")
    @classmethod
    def _clean_line_modifier(cls, v):
        return clean_line_modifier_or_none(v)


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
    # Set only when max_imps/est_uniques came from Constanza's "Apply" —
    # which geo/method/DMAs produced this figure (app/services/
    # constanza_engine.py), so a reopened proposal can show the planner
    # what it was actually calculated from instead of just a bare number
    # indistinguishable from any other "Est." value. None otherwise.
    geo_resolution: Optional[dict] = None


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
    roadblocks: Optional[dict] = None  # confirmed Step 07 roadblocks result, if not skipped — saved to reopen_state purely so a reopen can restore it; not otherwise used by generation itself
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
    # The billing period — "week" | "month" | "quarter" | "full_flight"
    # (Step 02's "Billing period" select and Step 04's pill). Proposal-wide
    # (not per-tier), drives how Curate/Avails/Step 05 label totals and
    # which of monthly_allocation.py's period functions Step 05 and the
    # export use to build each line's breakdown. "full_flight" bills the
    # whole flight as ONE period (see monthly_allocation.py's FULL FLIGHT
    # section). Defaults to "month" — this app's original behavior before
    # the toggle existed. STRICT here: this is the request that produces
    # the real export, and every downstream lookup silently falls back to
    # "month" for an unknown value, so a typo must be a 422, not a wrong
    # workbook (drafts and reopen coerce leniently instead — see
    # DraftSaveRequest).
    time_unit: str = "month"

    @field_validator("time_unit", mode="before")
    @classmethod
    def _time_unit_is_known(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return "month"
        if not isinstance(v, str) or v.strip().lower() not in monthly_allocation.TIME_UNITS:
            raise ValueError(f"time_unit must be one of: {', '.join(monthly_allocation.TIME_UNITS)}")
        return v.strip().lower()
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

    @field_validator("time_unit", mode="before")
    @classmethod
    def _lenient_time_unit(cls, v):
        # A draft must always be saveable: an unknown/empty value becomes "month" instead of a 422.
        return monthly_allocation.normalize_time_unit(v)
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
    # The proposal's billing period — only changes how the budget sentence is
    # worded (a full-flight budget is a flat total, never "$X/month x N months").
    time_unit: str = "month"

    @field_validator("time_unit", mode="before")
    @classmethod
    def _lenient_time_unit(cls, v):
        return monthly_allocation.normalize_time_unit(v)


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

    @field_validator("time_unit", mode="before")
    @classmethod
    def _lenient_time_unit(cls, v):
        # Suggest Mix is not the export: an unrecognized unit degrades to "month" exactly as it always did (only
        # /api/generate is strict, because only it produces the file that would silently be the wrong billing period).
        return monthly_allocation.normalize_time_unit(v)


class RoadblocksRequest(BaseModel):
    request: dict
    line_items: list[LineItemModel]
    strategy_brief: Optional[dict] = None
    # Restricted verticals (Restricted Verticals sheet tab names) the planner
    # confirmed apply to this client — verdicts for them come from the sheet.
    categories: Optional[list[str]] = None


class ConstanzaResolveRequest(BaseModel):
    geo_text: str
    # "dma" | "state" | "zip" | "county" | "congressional_district" |
    # "city" | "country" | None. Lets the UI's geography-type selector
    # force a specific resolver (see constanza_geo.resolve_geo's own
    # docstring for why this matters — "Sacramento" is genuinely ambiguous
    # between the DMA and the city without it). None keeps the default
    # DMA/state-first behavior, for backward compatibility with every
    # existing saved proposal's plain geo text.
    geo_type_hint: Optional[str] = None


class ConstanzaAudienceSpecModel(BaseModel):
    age_min: Optional[int] = None
    age_max: Optional[int] = None
    gender: Optional[str] = None
    interest_terms: list[str] = []


class ConstanzaEstimateRequest(BaseModel):
    product_name: str
    geo_text: str
    geo_type_hint: Optional[str] = None
    months: int = 1
    # The line's real length in days. Only weekly series (live sports) use it - they multiply a WEEKLY estimate by
    # flight_days / 7 instead of months x 4.35 - and it is optional.
    flight_days: Optional[float] = None
    # Only used for products with no workbook-modeled platform (VIX 360,
    # Netflix — see constanza_forecast_map.DEFAULT_PLATFORM_PRODUCTS and Admin -> Forecast): the
    # planner's own national monthly total, apportioned by DMA population
    # share. Ignored for platform-modeled products.
    national_monthly_total: Optional[float] = None
    # When present, routes through constanza_audience.py's age/gender/
    # interest layer instead of the plain geo/platform estimate.
    audience: Optional[ConstanzaAudienceSpecModel] = None


class ConstanzaParseAudienceRequest(BaseModel):
    text: str


class ForecastMapUpdateRequest(BaseModel):
    """Admin -> Forecast: one product's choice (see constanza_forecast_map.clean_config for the rules)."""
    product_name: str
    enabled: bool
    method: str
    platform: Optional[str] = None
    hispanic_weighted: bool = False


class ForecastMapProductRequest(BaseModel):
    product_name: str


class DriveUploadRequest(BaseModel):
    proposal_id: str
    seller_email: str = ""


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
    time_unit: str = "month"
    # The plan AS IT IS NOW, so the dollar figures in the revision are worked out from the current state and not from
    # whatever was true when the emails were first written (the SPA sends what /api/generate would). `tiers` wins over
    # `line_items`, which on a multi-option proposal only holds the ACTIVE option's lines.
    tiers: Optional[list[TierModel]] = None
    addons: list[AddonItemModel] = []
    monthly_distribution_mode: str = "even"

    @field_validator("time_unit", mode="before")
    @classmethod
    def _lenient_time_unit(cls, v):
        return monthly_allocation.normalize_time_unit(v)


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
    """Return the product list (as the admin has it in Admin -> Product Catalog), grouped by family."""
    grouped: dict[str, list] = {}
    products = effective_catalog()
    # Which products show Constanza's "Auto-calculate avails" button on Step 05, and what it computes (Admin -> Forecast).
    forecast = forecast_map_svc.forecast_for_products(products)
    for p in products:
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
            "forecast": forecast.get(p.name),
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
                                                    ad_presence=body.ad_presence, time_unit=body.time_unit)

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
            time_unit=body.time_unit,
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
    time_unit: str = "month"

    @field_validator("time_unit", mode="before")
    @classmethod
    def _lenient_time_unit(cls, v):
        return monthly_allocation.normalize_time_unit(v)


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
        time_unit=body.time_unit,
    )
    if not built:
        raise HTTPException(status_code=500, detail="Could not rebuild strategy brief doc.")
    return {"rebuilt": True}


@app.post("/api/roadblocks")
async def roadblocks(body: RoadblocksRequest) -> dict:
    """
    Step 07 — AI-researched platform restrictions/roadblocks for the
    confirmed product mix, grounded in the Step 03 strategy brief + Notion
    context via live web search. Also writes a downloadable .docx report
    and returns a token to fetch it via /api/download-roadblocks/{token}.
    """
    req = _reconstruct_proposal_request(body.request)

    line_items = [
        LineItem(id=li.id, product_name=li.product_name, monthly_budget=li.monthly_budget, months=li.months)
        for li in body.line_items
    ]

    await _refresh_restrictions_best_effort()      # judge against the current sheet, not an old copy

    result = await asyncio.to_thread(
        roadblocks_svc.generate_roadblocks, req, line_items,
        strategy_brief=body.strategy_brief, categories=body.categories)

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
            categories=result.get("categories") or None,
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
    """Download the Step 07 roadblocks report Word doc."""
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

    items = recommend_line_items(req, body.monthly_budget, strategy_brief=body.strategy_brief,
                                 time_unit=body.time_unit)
    return {"line_items": [asdict(li) for li in items]}


def _serialize_geo_resolution(resolution) -> dict:
    return {
        "method": resolution.method,
        "geo_confidence": resolution.geo_confidence,
        "resolved": resolution.resolved,
        "unmatched_tokens": resolution.unmatched_tokens,
        "unmatched_details": resolution.unmatched_details,
        "notes": resolution.notes,
        "ambiguous": resolution.ambiguous,
        # How the typed words were read ("Adams County Washinton" -> "Adams County, WA") and what else the planner can pick:
        # did-you-mean candidates, larger containing areas, "use the larger area only".
        "interpretations": resolution.interpretations,
        "suggestions": resolution.suggestions,
        "areas": [
            {
                "dma": a.dma, "area_population": a.area_population, "dma_share": a.dma_share,
                "source_type": a.source_type, "apportionment": a.apportionment,
                "state": a.state, "label": a.label, "geo_id": a.geo_id,
            }
            for a in resolution.areas
        ],
        # Back-compat for any caller still reading the Phase 1 shape.
        "dmas": resolution.dmas,
    }


_CONSTANZA_MAX_TEXT = 2000        # characters of geography / audience text
_CONSTANZA_MAX_MONTHS = 120
_CONSTANZA_MAX_INTERESTS = 10


def _check_constanza_text(label: str, text: str) -> None:
    if len(text or "") > _CONSTANZA_MAX_TEXT:
        raise HTTPException(status_code=422, detail=f"{label} is too long (at most {_CONSTANZA_MAX_TEXT:,} characters).")


def _check_constanza_estimate_input(body: "ConstanzaEstimateRequest") -> None:
    """Plain-language 422s for input no real line can have — an absurd month count or total used to reach the math (and a
    month count of 10**400 raised a 500); text is capped because the geography matcher runs regexes over it."""
    _check_constanza_text("Geography", body.geo_text)
    if not (1 <= body.months <= _CONSTANZA_MAX_MONTHS):
        raise HTTPException(status_code=422, detail=f"Months must be between 1 and {_CONSTANZA_MAX_MONTHS}.")
    if body.flight_days is not None and not (math.isfinite(body.flight_days) and 0 < body.flight_days <= 3660):
        raise HTTPException(status_code=422, detail="Flight days must be between 1 and 3,660.")
    total = body.national_monthly_total
    if total is not None and not (math.isfinite(total) and 0 <= total <= 1e13):
        raise HTTPException(status_code=422, detail="The monthly impressions total must be a number between 0 and 10 trillion.")
    a = body.audience
    if a is not None:
        for name, value in (("Minimum age", a.age_min), ("Maximum age", a.age_max)):
            if value is not None and not (0 <= value <= 120):
                raise HTTPException(status_code=422, detail=f"{name} must be between 0 and 120.")
        if a.age_min is not None and a.age_max is not None and a.age_min > a.age_max:
            raise HTTPException(status_code=422, detail="Minimum age can't be above maximum age.")
        if len(a.interest_terms) > _CONSTANZA_MAX_INTERESTS or any(len(t) > 100 for t in a.interest_terms):
            raise HTTPException(status_code=422, detail=f"Give at most {_CONSTANZA_MAX_INTERESTS} interests, each under 100 characters.")


# These handlers are plain `def` on purpose: they do blocking work (loading the large bundled data files, regex geography
# matching, and a model call per interest phrase), so FastAPI runs them in its thread pool instead of freezing the event
# loop — and with it every other request and login — for the duration.
@app.post("/api/constanza/resolve")
def constanza_resolve(body: ConstanzaResolveRequest) -> dict:
    """Resolve a campaign's free-text geo (ProposalRequest.geo / TierModel.geo)
    into a structured, area-weighted geography — DMA/state (default),
    or ZIP/county/congressional-district/city/country when
    `geo_type_hint` says so — for the planner to confirm/edit before any
    avails math runs against it. See app/services/constanza_geo.py."""
    _check_constanza_text("Geography", body.geo_text)
    resolution = constanza_geo.resolve_geo(body.geo_text, geo_type_hint=body.geo_type_hint)
    return _serialize_geo_resolution(resolution)


def _geo_warning_notes(resolution) -> list[str]:
    """Caveats from geo resolution that belong next to the numbers: advisory
    notes, plus — for a list that only partly resolved — exactly which inputs
    are NOT in the total (and why, when known), so a dropped item is never silent."""
    notes = list(resolution.notes)
    if resolution.unmatched_tokens:
        reasons = {d["input"]: d["reason"] for d in resolution.unmatched_details}
        dropped = "; ".join(f"{t!r}" + (f" ({reasons[t]})" if t in reasons else "") for t in resolution.unmatched_tokens)
        notes.insert(0, f"NOT INCLUDED in this estimate — couldn't be matched: {dropped}")
    return notes


def _geo_failure_detail(geo_text: str, resolution) -> str:
    if resolution.ambiguous:
        which = ", ".join(repr(a["input"]) for a in resolution.ambiguous)
        return f"{which} could mean more than one place — pick one and retry."
    guesses = [sg["label"] for sg in resolution.suggestions if sg.get("kind") == "did_you_mean"][:3]
    maybe = f" Did you mean {', '.join(guesses)}?" if guesses else ""
    if resolution.unmatched_details:
        return " ".join(d["reason"] for d in resolution.unmatched_details) + maybe
    return f"Could not resolve geo {geo_text!r} — check the spelling or choose the geography type." + maybe


def _serialize_avails_estimate(estimate, resolution) -> dict:
    return {
        "product_name": estimate.product_name,
        "platform": estimate.platform,
        "confidence": estimate.confidence,
        "geo_method": estimate.geo_method,
        "geo_confidence": estimate.geo_confidence,
        "months": estimate.months,
        "imps_low": estimate.imps_low,
        "imps_mid": estimate.imps_mid,
        "imps_high": estimate.imps_high,
        "est_uniques": estimate.est_uniques,
        "series": estimate.series,
        "weeks": estimate.weeks,
        "notes": _geo_warning_notes(resolution) + list(estimate.notes),
        "unmatched_geo_tokens": resolution.unmatched_tokens,
        "unmatched_geo_details": resolution.unmatched_details,
        "ambiguous": resolution.ambiguous,
        "geo_interpretations": resolution.interpretations,
        "geo_suggestions": resolution.suggestions,
        "by_area": [
            {
                "area_label": a.area_label, "dma": a.dma, "weight": a.weight,
                "imps_low": a.imps_low, "imps_mid": a.imps_mid, "imps_high": a.imps_high,
                "unique_viewers": a.unique_viewers, "unique_hh": a.unique_hh,
            }
            for a in estimate.by_area
        ],
    }


@app.post("/api/constanza/estimate")
def constanza_estimate(body: ConstanzaEstimateRequest) -> dict:
    """DMA-weighted avails estimate for one product over a resolved geo —
    Project Constanza's "Stanza" engine (app/services/constanza_engine.py).
    A fast starting point pre-filled from bundled CTV-avails-by-DMA data,
    not a live platform pull — the planner can still confirm/refine
    against Roku Ads Manager / Amazon DSP / Madhive / The Trade Desk. When
    `audience` is given, routes through constanza_audience.py's age/
    gender/interest layer on top of this same geo/platform estimate."""
    _check_constanza_estimate_input(body)
    resolution = constanza_geo.resolve_geo(body.geo_text, geo_type_hint=body.geo_type_hint)
    if resolution.ambiguous or not resolution.resolved:
        raise HTTPException(status_code=422, detail=_geo_failure_detail(body.geo_text, resolution))
    estimate = estimate_avails(
        body.product_name, resolution, months=body.months,
        national_monthly_total=body.national_monthly_total, flight_days=body.flight_days,
    )
    if estimate is None:
        if resolution.geo_confidence == "low_confidence_international":
            detail = (
                "There is no platform avails data outside the U.S. — enter a budget or impression total for the "
                "line and Constanza will spread it across the selected countries by population share "
                "(low confidence)."
            )
        else:
            detail = (
                f"There are no forecast avails loaded for {body.product_name!r} (or they couldn't be read just now) and no "
                "national_monthly_total was given to apportion by population share."
            )
        raise HTTPException(status_code=422, detail=detail)

    if body.audience is None:
        return _serialize_avails_estimate(estimate, resolution)

    audience_estimate = estimate_audience(estimate, AudienceSpec(
        age_min=body.audience.age_min, age_max=body.audience.age_max,
        gender=body.audience.gender, interest_terms=body.audience.interest_terms,
    ))
    return {
        **_serialize_avails_estimate(estimate, resolution),
        "audience": {
            "imps_low": audience_estimate.imps_low, "imps_mid": audience_estimate.imps_mid,
            "imps_high": audience_estimate.imps_high,
            "underlying_population": audience_estimate.underlying_population,
            "reliability_floor_population": audience_estimate.reliability_floor_population,
            "below_reliability_threshold": audience_estimate.below_reliability_threshold,
            "confidence_tier": audience_estimate.confidence_tier,
            "cuts_applied": audience_estimate.cuts_applied,
            "search_multiplier_applied": audience_estimate.search_multiplier_applied,
            "interests": [
                {
                    "input_text": i.input_text, "category": i.category, "supported": i.supported,
                    "source": i.source, "source_date": i.source_date,
                }
                for i in audience_estimate.interests
            ],
            "notes": audience_estimate.notes,
            "by_area": [
                {
                    "area_label": a.area_label, "dma": a.dma, "weight": a.weight,
                    "imps_low": a.imps_low, "imps_mid": a.imps_mid, "imps_high": a.imps_high,
                    "underlying_population": a.underlying_population,
                }
                for a in audience_estimate.by_area
            ],
        },
    }


@app.post("/api/constanza/parse-audience")
def constanza_parse_audience(body: ConstanzaParseAudienceRequest) -> dict:
    """Free text -> structured AudienceSpec (the LLM step — see
    constanza_audience.py's module docstring for the hard rule that the
    LLM only classifies/extracts here, never generates a number). Kept
    separate from /estimate so the UI can show the parsed structure for
    the planner to confirm/edit before any estimate runs."""
    _check_constanza_text("The description", body.text)
    spec = parse_audience_text(body.text)
    return {
        "age_min": spec.age_min, "age_max": spec.age_max, "gender": spec.gender,
        "interest_terms": spec.interest_terms, "geo_text": spec.geo_text,
        "geo_type_hint": spec.geo_type_hint,
    }


@app.get("/api/constanza/interest-taxonomy")
async def constanza_interest_taxonomy() -> dict:
    """The known, sourced interest categories constanza_audience.py can
    apply a real incidence rate for — drives the UI's "browse supported
    interests" picker so free text isn't the only way in."""
    return {
        "categories": [
            {"category": c["category"], "label": c["label"], "source": c["source"], "source_date": c["source_date"]}
            for c in load_interest_taxonomy()
        ],
    }


@app.get("/api/constanza/platform-products")
async def constanza_platform_products() -> dict:
    """Which catalog products Constanza platform-models directly (vs. falling
    back to a generic population-share estimate) — the LIVE mapping, i.e. the
    built-in default with every Admin -> Forecast override applied. (The
    Avails step itself reads each product's `forecast` from /api/catalog.)"""
    return {"platform_modeled_products": forecast_map_svc.live_platform_products()}


def _sync_monthly_budgets_to_allocations(tiers: list[dict]) -> None:
    """
    Whenever a line has a Monthly Breakdown, its REAL total is whatever
    that breakdown actually sums to — never monthly_budget * months
    treated as separately authoritative. Mutates each LineItem in place.

    The frontend already keeps these in sync as the planner edits
    (app.js's _mbSyncBudgetToAllocation, on every Step 06 field blur), but
    that sync never runs for a line whose monthly_allocations came from
    somewhere OTHER than a live edit in THIS session — reopening a past
    proposal, most commonly, where a since-stale monthly_budget/allocation
    pairing (from before this feature existed, or before a fix that
    touched either) gets reloaded verbatim with no edit ever "committing"
    a fresh sync. Normalizing here, in /api/generate, makes the SERVER —
    not a client-side event handler — the actual source of truth for
    what gets exported, so NET BUDGET, TOTAL DIGITAL MONTHLY, the grand
    total, and every other consumer of monthly_budget/total_budget() can
    never again disagree with the real, planner-entered monthly figures
    purely because of when/whether a blur event happened to fire.
    """
    for t in tiers:
        for li in t["line_items"]:
            if li.monthly_allocations:
                real_total = sum(li.monthly_allocations.values())
                li.monthly_budget = round(real_total / (li.months or 1), 2)


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


def _email_figures_for_tiers(req: ProposalRequest, tiers: list[dict], addons, time_unit: str,
                             distribution_mode: str = "even"):
    """The deterministic dollar figures the emails are written from — per option: per-period amounts in the plan's own
    unit (or the one flight total in Full Flight), media total, add-ons and the grand total, net and gross. Worked out
    from the same normalized tiers the workbook is built from, so they equal the Excel (see
    app/services/email_totals.py). Optional extra: any failure is logged and returns None (the prompts then derive
    the figures themselves) — it must never fail a Generate or a revision."""
    try:
        return email_totals_svc.compute_email_totals(
            req, tiers, addons, time_unit, distribution_mode,
            known_product=lambda name: by_name(name) is not None)
    except Exception:
        logging.getLogger(__name__).exception("email figures failed; the emails use the legacy figures")
        return None


def _email_tiers_from_models(tier_models, legacy_line_items, time_unit: str) -> list[dict]:
    """The tiers list /api/generate builds, from the request models of an email revision: the CURRENT options (or the
    legacy flat line list as one option), normalized the way Generate normalizes them — Full Flight folded into one
    period, and a customised Step 06 breakdown deciding each line's real total."""
    full_flight = monthly_allocation.is_full_flight(time_unit)

    def convert(models) -> list[LineItem]:
        return [
            LineItem(
                id=li.id, product_name=li.product_name, monthly_budget=li.monthly_budget,
                # see generate(): a Full Flight line whose `months` was never sent is ONE period, not the model default 3
                months=li.months if (not full_flight or "months" in li.model_fields_set) else 1,
                is_added_value=li.is_added_value, added_value_pct=li.added_value_pct,
                monthly_allocations=li.monthly_allocations,
                line_modifier=li.line_modifier,      # a label for the email prompts only (never a figure)
            )
            for li in models
        ]

    if tier_models:
        tiers = [{"label": t.label, "name": t.name, "start_date": t.start_date, "end_date": t.end_date,
                  "line_items": convert(t.line_items), "period_merge_groups": t.period_merge_groups}
                 for t in tier_models]
    else:
        tiers = [{"label": "A", "name": None, "start_date": None, "end_date": None,
                  "line_items": convert(legacy_line_items), "period_merge_groups": None}]
    if full_flight:
        for t in tiers:
            normalize_full_flight_lines(t["line_items"])
            t["period_merge_groups"] = None
    _sync_monthly_budgets_to_allocations(tiers)
    return tiers


@app.post("/api/generate")
async def generate(body: GenerateRequest, request: Request) -> dict:
    """
    Generate an Excel proposal with AI enrichment.
    Returns proposal_id, filename, proposal_title, summary, and enrichment content.
    """
    req = _reconstruct_proposal_request(body.request)

    full_flight = monthly_allocation.is_full_flight(body.time_unit)

    def _to_line_items(models: list[LineItemModel]) -> list[LineItem]:
        return [
            LineItem(
                id=li.id,
                product_name=li.product_name,
                monthly_budget=li.monthly_budget,
                # Full Flight is ONE period: a line whose `months` was never sent must not inherit the model's default of
                # 3 (normalize_full_flight_lines would fold it x3 — a silent tripling for any non-SPA client). An explicit
                # months > 1 is still folded (total preserved), see proposal_generator.normalize_full_flight_lines.
                months=li.months if (not full_flight or "months" in li.model_fields_set) else 1,
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
                line_modifier=li.line_modifier,
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
    # Full Flight: enforce months == 1 / whole-flight dollars / one {"full_flight": total} allocation per paid line
    # BEFORE the sync + validation below — a stale month/week/quarter-keyed allocation must never overwrite the
    # curated total (sync lets allocations win) or raise false below-minimum warnings (lookup is by period key).
    full_flight_warnings: list[str] = []
    if monthly_allocation.is_full_flight(body.time_unit):
        for t in tiers:
            full_flight_warnings.extend(normalize_full_flight_lines(t["line_items"]))
            t["period_merge_groups"] = None
    _sync_monthly_budgets_to_allocations(tiers)

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

    # Admin keyword disclaimers (Admin -> Disclaimers), matched ONCE per
    # option here and handed to both exporters so the Excel and the PPT can
    # never disagree about which banners apply. The loader already falls back
    # to the built-in Live Sports rule if the table is unreachable; this
    # try/except covers a bug in matching itself — an optional callout must
    # never take down the whole Generate call.
    try:
        disclaimers_by_tier = disclaimers_svc.resolve_for_tiers(req, tiers)
    except Exception:
        logging.getLogger(__name__).exception("disclaimer matching failed; exporting without banners")
        disclaimers_by_tier = {}

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
    # Every dollar figure the emails may state, computed here from the FULL option list (not the product-deduplicated
    # union above, which would drop a second line for the same product), the add-ons and any customised per-period
    # amounts — the same normalized state the workbook below is built from.
    email_figures = _email_figures_for_tiers(req, tiers, body.addons, body.time_unit, body.monthly_distribution_mode)
    enrichment = await asyncio.to_thread(
        ai_enricher.enrich_proposal,
        req, union_line_items, short_id, strategy_brief=body.strategy_brief, tiers=tier_context,
        time_unit=body.time_unit, totals=email_figures,
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
            disclaimers_by_tier=disclaimers_by_tier,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")
    if full_flight_warnings:
        # generate_proposal re-normalizes (idempotently, so it has nothing left to report) — surface what THIS pass folded.
        summary["warnings"] = full_flight_warnings + list(summary.get("warnings") or [])

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
                # The option's OWN flight-date override (None = the campaign dates). Only a Full Flight deck shows it —
                # it has no Months column, so the window is the only thing saying what the 'Flight Budget' covers.
                "start_date": t.get("start_date"), "end_date": t.get("end_date"),
            })
    ppt_banners = disclaimers_svc.union_for_ppt(disclaimers_by_tier)
    if pptx_tiers:
        if any(tb == f"Proposal {t['label']}" for t in tiers for tb in tabs_built_list):
            pptx_net_filename = f"{safe_base}_Net_Deck.pptx"
            try:
                pptx_builder.build_signature_deck(
                    req, pptx_tiers, PROPOSALS_DIR / pptx_net_filename,
                    gross=False, proposal_title=proposal_title, banners=ppt_banners, time_unit=body.time_unit,
                )
            except Exception:
                logging.getLogger(__name__).exception("PPT net deck build failed")
                pptx_net_filename = None  # never let an optional export break the main Generate call
        if any(tb == f"Proposal {t['label']} (Gross)" for t in tiers for tb in tabs_built_list):
            pptx_gross_filename = f"{safe_base}_Gross_Deck.pptx"
            try:
                pptx_builder.build_signature_deck(
                    req, pptx_tiers, PROPOSALS_DIR / pptx_gross_filename,
                    gross=True, proposal_title=proposal_title, banners=ppt_banners, time_unit=body.time_unit,
                )
            except Exception:
                logging.getLogger(__name__).exception("PPT gross deck build failed")
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
        # dollar amounts in the emails that match none of the plan's computed figures even after the automatic
        # correction pass — shown to the planner rather than shipped silently
        "figure_warnings": list(getattr(enrichment, "figure_warnings", None) or []),
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

    # Keep a shared copy of the workbook. The file above lives on THIS Autoscale instance's local disk only, and
    # the Google Drive upload (a separate request) may be served by an instance that never saw it - see
    # app/services/proposal_files.py. Fail-safe: a database hiccup here must never fail Generate.
    await asyncio.to_thread(proposal_files_svc.save_file, proposal_id, "xlsx", output_path)

    return {
        "proposal_id": proposal_id,
        "filename": filename,
        "proposal_title": proposal_title,
        "summary": summary,
        "enrichment": enrichment_out,
        # The per-option dollar figures the emails were written from (equal to the Excel's totals) — the SPA's Gamma
        # outline uses them for its option totals, so it can't disagree with the workbook either.
        "email_figures": email_figures.to_dict() if email_figures is not None else None,
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
        # Which admin disclaimers were drawn into this proposal, per option
        # (names only — the full text is already in the exports).
        "matched_disclaimers": {
            label: [{"id": b["id"], "name": b["name"], "applies_to": b["applies_to"]} for b in banners]
            for label, banners in disclaimers_by_tier.items()
        },
    }


def _with_current_product_names(reopen_state: dict) -> dict:
    """A saved proposal carries the product names it had when saved; a product that was renamed (or split, like
    "YouTube Ads") since then is shown under its current name — in the lines, the per-product avails, the add-ons,
    the picked-product list, and the AI roadblocks / blurbs — so the planner's pickers, minimums and rates still
    line up."""
    cache: dict = {}

    def current(name):
        if not isinstance(name, str) or not name:
            return name
        if name not in cache:
            product = by_name(name)
            cache[name] = product.name if product is not None else name
        return cache[name]

    def fix_rows(rows):
        for row in rows or []:
            if isinstance(row, dict) and row.get("product_name"):
                row["product_name"] = current(row["product_name"])

    def fix_keys(mapping):
        """A dict keyed by product name; an entry already under the current name wins over a renamed duplicate."""
        if not isinstance(mapping, dict):
            return mapping
        out = {k: v for k, v in mapping.items() if current(k) == k}
        for k, v in mapping.items():
            out.setdefault(current(k), v)
        return out

    fix_rows(reopen_state.get("line_items"))
    fix_rows(reopen_state.get("addons"))
    if reopen_state.get("avails_data"):
        reopen_state["avails_data"] = fix_keys(reopen_state["avails_data"])
    for tier in reopen_state.get("tiers") or []:
        if isinstance(tier, dict):
            fix_rows(tier.get("line_items"))
            if tier.get("avails_data"):
                tier["avails_data"] = fix_keys(tier["avails_data"])
    request = reopen_state.get("request")
    if isinstance(request, dict) and isinstance(request.get("products_selected"), list):
        picked = []
        for name in request["products_selected"]:
            name = current(name)
            if name not in picked:
                picked.append(name)
        request["products_selected"] = picked
    if isinstance(reopen_state.get("roadblocks"), dict):
        fix_rows(reopen_state["roadblocks"].get("product_roadblocks"))
    if isinstance(reopen_state.get("enrichment"), dict):
        fix_rows(reopen_state["enrichment"].get("product_blurbs"))
    return reopen_state


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
    reopen_state = _with_current_product_names(reopen_state)
    if isinstance(reopen_state.get("force_tabs"), dict):
        # A draft saved before "DOOH Deliverables" carries the two old DOOH tab keys; the wizard only knows "dooh".
        reopen_state["force_tabs"] = normalize_tab_keys(reopen_state["force_tabs"])
    return {
        "proposal_title": meta.get("proposal_title", ""),
        "status": meta.get("status") or "generated",
        **reopen_state,
        # A proposal saved before the billing-period toggle has no time_unit; one holding an unrecognized value
        # (e.g. after a rollback) must not poison the next save — coerce to a known unit ("month" = the original behavior).
        "time_unit": monthly_allocation.normalize_time_unit(reopen_state.get("time_unit")),
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
        LineItem(id=li.id, product_name=li.product_name, monthly_budget=li.monthly_budget, months=li.months,
                 line_modifier=li.line_modifier)
        for li in body.line_items
    ]

    # Re-price the plan from the state the SPA sends NOW (every option, add-on and customised per-period amount) —
    # never from whatever the emails were first written against — so a revision also refreshes figures the planner has
    # since changed. A client that only sends the legacy flat `line_items` still gets one option's figures.
    try:
        email_tiers = _email_tiers_from_models(body.tiers, body.line_items, body.time_unit)
    except Exception:
        logging.getLogger(__name__).exception("couldn't rebuild the plan for the email revision; using the legacy figures")
        email_tiers = None
    email_figures = (
        _email_figures_for_tiers(req, email_tiers, body.addons, body.time_unit, body.monthly_distribution_mode)
        if email_tiers is not None else None)

    result = await asyncio.to_thread(
        ai_enricher.reprompt_emails,
        req, line_items, body.campaign_name,
        body.current_internal_subject, body.current_internal_body,
        body.current_client_subject, body.current_client_body,
        body.reprompt, scope=body.scope, time_unit=body.time_unit, totals=email_figures,
    )
    if email_figures is not None:
        result = {**result, "email_figures": email_figures.to_dict()}

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


class GammaCreateRequest(BaseModel):
    outline: str = Field(min_length=20, max_length=400_000)
    title: Optional[str] = Field(default=None, max_length=500)


_GAMMA_MIN_INTERVAL = 8.0           # seconds between two deck builds by the same user — each one spends Gamma credits
_gamma_last_start: dict[str, float] = {}


@app.post("/api/gamma/create")
async def gamma_create(body: GammaCreateRequest, request: Request) -> dict:
    """Push the Step 07 outline to Gamma, which builds the presentation in the planner's workspace. Without a
    GAMMA_API_KEY nothing is called and `configured` is false — the page then falls back to copying the outline.
    Each build costs credits, so one user can't start another within a few seconds, and who started which build
    is logged."""
    if not gamma_client.is_configured():
        return {"configured": False}
    user = (getattr(request.state, "user", None) or {}).get("email") or "unknown"
    now = time.monotonic()
    if now - _gamma_last_start.get(user, -1e9) < _GAMMA_MIN_INTERVAL:
        raise HTTPException(status_code=429, detail="A deck was just started — give it a few seconds before starting another.")
    _gamma_last_start[user] = now
    try:
        generation_id = await asyncio.to_thread(gamma_client.start_generation, body.outline, body.title)
    except gamma_client.GammaError as e:
        _gamma_last_start.pop(user, None)                         # nothing was built, so nothing to wait out
        raise HTTPException(status_code=e.status_code, detail=str(e))
    logging.getLogger(__name__).info("gamma build %s started by %s (%r)", generation_id, user, body.title)
    return {"configured": True, "generation_id": generation_id}


@app.get("/api/gamma/status/{generation_id}")
async def gamma_status(generation_id: str) -> dict:
    """Where a Gamma build is: pending, completed (with the link to open) or failed."""
    if not gamma_client.is_configured():
        raise HTTPException(status_code=409, detail="Gamma isn't connected to this app.")
    try:
        return await asyncio.to_thread(gamma_client.get_generation, generation_id)
    except gamma_client.GammaError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


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


def _drive_upload_blocking(proposal_id: str, seller_email: str, redirect_uri: str) -> dict:
    """Everything /api/drive/upload does that waits on the database or Google (several sequential network calls,
    seconds in total). Runs in a worker thread - done inline in the async handler it froze the whole instance's
    event loop, stalling every other planner's request for as long as Google took. Raises drive_uploader.DriveError
    for anything the caller should translate into a status code."""
    DriveError = drive_uploader.DriveError
    with drive_uploader.upload_slot(proposal_id):
        meta = drive_uploader.call_with_retries(lambda: _get_proposal_metadata(proposal_id), "load proposal")
        if meta is None:
            raise DriveError("That proposal wasn't found. Generate it again, then upload.",
                             status_code=404, retryable=False, code="proposal_not_found")
        filename = meta.get("filename")
        if not filename or (meta.get("status") or "generated") == "draft":
            # A saved draft has no workbook yet - this used to be a KeyError('path') and a bare 500.
            raise DriveError("This proposal hasn't been generated yet - click Generate Proposal first.",
                             status_code=409, retryable=False, code="not_generated")
        path = PROPOSALS_DIR / filename
        # The shared copy first: the local file only exists on the instance that generated it (and a same-named
        # local file there can belong to an earlier proposal). The local disk is the fallback for proposals
        # generated before the shared copy existed.
        content = drive_uploader.call_with_retries(
            lambda: proposal_files_svc.load_bytes(proposal_id, "xlsx"), "load stored workbook"
        )
        if content is None:
            try:
                content = path.read_bytes()
            except FileNotFoundError:
                logging.getLogger(__name__).warning(
                    "Drive upload: workbook for proposal %s is on no disk and not in the database", proposal_id
                )
                raise DriveError(
                    "This proposal's .xlsx is no longer on the server (it was created on a server instance that "
                    "has since restarted). Click Generate Proposal again, then upload.",
                    status_code=410, retryable=False, code="file_unavailable",
                ) from None
        try:
            result = drive_uploader.upload_proposal(
                local_path=path,
                seller_email=seller_email or meta.get("seller_email") or "",
                as_google_sheet=True,
                redirect_uri=redirect_uri,
                content=content,
                proposal_id=proposal_id,
            )
        except drive_uploader.DriveAuthRequired:
            # Never an HTTP 401: session-guard.js treats ANY 401 as "your login expired" and bounces to /login.
            # Re-authorizing Drive is the 200 {needs_auth, auth_url} contract the page already handles.
            result = {"uploaded": False, "needs_auth": True, "reason": "Google Drive authorization required."}
        if result.get("needs_auth"):
            result["auth_url"] = drive_uploader.get_auth_url(redirect_uri)
        return result


def _drive_failure_response(err: "drive_uploader.DriveError") -> JSONResponse:
    """A Drive failure as a JSON answer the page can act on: `detail` is a plain-language reason (same key every
    other endpoint uses), `retryable` tells it whether trying again is worthwhile."""
    headers = {"Retry-After": "3"} if err.retryable else None
    return JSONResponse(status_code=err.status_code, content=err.payload(), headers=headers)


@app.post("/api/drive/upload")
async def drive_upload(body: DriveUploadRequest, request: Request):
    """
    Upload a generated proposal to Google Drive.
    Folder structure: <DRIVE_ROOT>/<seller_email>/<filename>
    Returns {needs_auth: true, auth_url: ...} when OAuth2 authorization is required.
    Returns a graceful no-op when Drive credentials aren't configured.

    Failures never surface as a bare 500: every one is a JSON {detail, retryable, code} -
      503 retryable=true   Google / the network / the database didn't answer (already retried with backoff here)
      404 / 409 / 410      unknown proposal / not generated yet / its file is gone (and not stored) - regenerate
      424                  Google refused for good (permissions, quota, missing folder) - an admin has to fix it
      500 (with `ref`)     a genuine bug, logged with the same reference
    """
    proposal_id = (body.proposal_id or "").strip()
    redirect_uri = str(request.base_url).rstrip("/") + "/api/drive/callback"
    try:
        return await asyncio.to_thread(
            _drive_upload_blocking, proposal_id, body.seller_email or "", redirect_uri
        )
    except drive_uploader.DriveError as err:
        return _drive_failure_response(err)
    except Exception as exc:  # noqa: BLE001 - last line of defence: never a bare 500
        return _drive_failure_response(
            drive_uploader.to_drive_error(exc, stage="endpoint", proposal_id=proposal_id)
        )


@app.get("/api/drive/callback")
async def drive_callback(request: Request, code: Optional[str] = None, error: Optional[str] = None) -> HTMLResponse:
    """Handle the OAuth2 redirect from Google after the user authorizes Drive access."""
    from html import escape as _escape_html
    redirect_uri = str(request.base_url).rstrip("/") + "/api/drive/callback"
    if not code:
        # Google sends ?error=access_denied (no code) when the consent screen is cancelled - a plain page, not a 422.
        return HTMLResponse(content=(
            "<!doctype html><html><body>"
            f"<p>Google Drive was not authorized ({_escape_html(error or 'no authorization code received')}).</p>"
            "<p>Close this tab and try again.</p></body></html>"
        ))
    try:
        # Token exchange + database write: blocking, so off the event loop like the upload itself.
        await asyncio.to_thread(drive_uploader.exchange_code, code, redirect_uri)
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
        logging.getLogger(__name__).warning("Drive authorization callback failed (%s)", type(e).__name__)
        html = (
            f"<!doctype html><html><body>"
            f"<p>Authorization failed: {_escape_html(str(e))}</p>"
            f"<p>Close this tab and try again.</p>"
            f"</body></html>"
        )
    return HTMLResponse(content=html)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "catalog_size": len(effective_catalog())}


# ---------------------------------------------------------------------------
# Admin — proposal history + products + user management
#
# Behind login (see _require_login above) — every /api/admin/* route AND
# the /admin page itself additionally require is_admin, not just a valid
# session.
# ---------------------------------------------------------------------------

class ProductRequest(BaseModel):
    """One product, as the Admin -> Product Catalog form sends it. Every field is optional: a key that is ABSENT keeps
    the product's current value, and a nullable key sent as null (rate, est. CPM, SLA days) clears it."""
    name: Optional[str] = Field(default=None, max_length=200)
    family: Optional[str] = Field(default=None, max_length=120)
    short_label: Optional[str] = Field(default=None, max_length=200)
    buying_model: Optional[str] = None  # "CPM" | "CPP" | "Fixed"
    base_rate: Optional[float] = None
    minimum_spend: Optional[float] = None
    estimated_cpm_for_imps: Optional[float] = None
    sizes: Optional[str] = Field(default=None, max_length=500)
    tech_platform: Optional[str] = Field(default=None, max_length=200)
    proposal_description: Optional[str] = Field(default=None, max_length=5000)
    notes: Optional[str] = Field(default=None, max_length=5000)
    is_addon: Optional[bool] = None
    minimum_flight_days_min: Optional[int] = None
    minimum_flight_days_max: Optional[int] = None
    sla_data_days: Optional[int] = None
    sla_creative_days: Optional[int] = None
    sla_activate_days: Optional[int] = None
    sla_total_days: Optional[int] = None
    margin_upper: Optional[float] = None
    margin_lower: Optional[float] = None
    wide_orbit_code: Optional[str] = Field(default=None, max_length=200)
    estimated_impressions: Optional[bool] = None
    discloses_impressions: Optional[bool] = None
    national_supported: Optional[bool] = None
    copy_from_id: Optional[int] = None  # create only: start from a copy of this product (a quick variant)


class MarketConfigRequest(BaseModel):
    market_key: str  # matched against the parsed "Salesperson market" field; "__default__" is the fallback every market uses until it has its own entry
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    dsc_email: Optional[str] = None  # Digital Sales Coordinator (assistant) — CC'd on the internal seller email
    dsm_email: Optional[str] = None  # Digital Sales Manager — CC'd on the internal seller email
    research_emails: Optional[list[str]] = Field(default=None, max_length=50)  # the market's research assignees — CC'd on the internal seller email too
    ccs: Optional[list[str]] = None


@app.get("/admin", response_class=HTMLResponse)
async def admin_page() -> HTMLResponse:
    """Serve the admin SPA (proposal history, products, users, settings)."""
    return _serve_html_with_cache_busted_static(TEMPLATES_DIR / "admin.html")


# --------------------------------------------------------------------------
# Disclaimers — admin-managed, keyword-triggered banner callouts drawn into
# the Excel + PPT exports (app/disclaimers.py). Everything under
# /api/admin/ is admin-only via the middleware; the preview route below it
# is login-only so the Generate step can show a planner what will be added.
# --------------------------------------------------------------------------

@app.exception_handler(psycopg.errors.UndefinedTable)
async def _schema_not_applied(request: Request, exc: psycopg.errors.UndefinedTable) -> JSONResponse:
    """A table added by a newer schema.sql isn't there yet (deploys that apply the SQL by hand): say so
    instead of a bare 500, so the admin knows the fix is to run schema.sql."""
    logging.getLogger(__name__).warning("missing table on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "A database table this feature needs doesn't exist yet — apply the latest schema.sql, then try again."},
    )


class DisclaimerRequest(BaseModel):
    name: str = Field(max_length=120)
    keywords: Annotated[str, Field(max_length=5000)] | Annotated[list[str], Field(max_length=200)] = []
    banner_text: str = Field(max_length=5000)
    color: str = "red"
    applies_to: str = "both"
    season_start: Optional[str] = None
    season_end: Optional[str] = None


class DisclaimerActiveRequest(BaseModel):
    is_active: bool


def _clean_disclaimer_fields(body: DisclaimerRequest) -> dict:
    try:
        return disclaimers_svc.validate_fields(
            name=body.name, keywords=body.keywords, banner_text=body.banner_text, color=body.color,
            applies_to=body.applies_to, season_start=body.season_start, season_end=body.season_end,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/admin/disclaimers")
async def admin_list_disclaimers() -> dict:
    try:
        rules = disclaimers_svc.list_all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Couldn't load disclaimers (has the schema been applied?): {e}")
    return {
        "disclaimers": [d.to_dict() for d in rules],
        "colors": {k: v["label"] for k, v in disclaimers_svc.COLOR_PRESETS.items()},
        "color_swatches": {k: {"fill": v["fill"], "text": v["text"]} for k, v in disclaimers_svc.COLOR_PRESETS.items()},
    }


@app.post("/api/admin/disclaimers")
async def admin_create_disclaimer(body: DisclaimerRequest) -> dict:
    created = disclaimers_svc.create(_clean_disclaimer_fields(body))
    return {"created": True, "disclaimer": created.to_dict()}


@app.put("/api/admin/disclaimers/{disclaimer_id}")
async def admin_update_disclaimer(disclaimer_id: str, body: DisclaimerRequest) -> dict:
    updated = disclaimers_svc.update(disclaimer_id, _clean_disclaimer_fields(body))
    if updated is None:
        raise HTTPException(status_code=404, detail="Disclaimer not found.")
    return {"saved": True, "disclaimer": updated.to_dict()}


@app.patch("/api/admin/disclaimers/{disclaimer_id}")
async def admin_toggle_disclaimer(disclaimer_id: str, body: DisclaimerActiveRequest) -> dict:
    updated = disclaimers_svc.set_active(disclaimer_id, body.is_active)
    if updated is None:
        raise HTTPException(status_code=404, detail="Disclaimer not found.")
    return {"saved": True, "disclaimer": updated.to_dict()}


@app.delete("/api/admin/disclaimers/{disclaimer_id}")
async def admin_delete_disclaimer(disclaimer_id: str) -> dict:
    try:
        deleted = disclaimers_svc.delete(disclaimer_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail="Disclaimer not found.")
    return {"deleted": True}


# --------------------------------------------------------------------------
# Restricted verticals — Entravision's "Restricted Verticals by Platforms"
# sheet as the source of truth for Roadblocks (app/services/restrictions.py).
# The planner-facing list is login-only; sync / upload / edit live under
# /api/admin/ (admin-only via the middleware).
# --------------------------------------------------------------------------

class RestrictionCategoriesRequest(BaseModel):
    request: dict
    product_names: list[str] = []


@app.post("/api/restrictions/categories")
async def restriction_categories(body: RestrictionCategoriesRequest) -> dict:
    """The restricted verticals a planner can confirm for Roadblocks, with the
    ones the plan's own text suggests flagged `suggested`."""
    from app import disclaimers as disclaimers_svc
    await _refresh_restrictions_best_effort()
    state = restrictions_svc.load_state()
    req = _reconstruct_proposal_request(body.request)
    plan_text = disclaimers_svc.request_text(req, include_products=True) + "\n" + "\n".join(body.product_names)
    suggested = set(restrictions_svc.suggest_categories(plan_text, state["categories"]))
    sync = state["sync"] or {}
    return {
        "categories": [
            {"name": c.name, "kind": c.kind, "summary": c.summary, "suggested": c.name in suggested, "manual": c.is_manual}
            for c in state["categories"]
        ],
        "using_builtin": state["using_builtin"],
        "synced_at": sync["synced_at"].isoformat() if sync.get("synced_at") else None,
    }


class RestrictionSyncRequest(BaseModel):
    sheet_url: Optional[str] = None


class RestrictionUploadRequest(BaseModel):
    filename: Optional[str] = Field(default=None, max_length=300)
    data_base64: str = Field(max_length=14_000_000)       # ~10 MB of workbook


class RestrictionUpdateRequest(BaseModel):
    keywords: Annotated[str, Field(max_length=5000)] | Annotated[list[str], Field(max_length=200)] = []
    product_map: dict[str, list[str]] = Field(default={}, max_length=300)
    # rules the AI reads on top of the sheet text (any category), and - only for a category added by hand - its own fields
    extra_notes: Optional[str] = Field(default=None, max_length=20000)
    kind: Optional[str] = Field(default=None, max_length=20)
    summary: Optional[str] = Field(default=None, max_length=2000)
    body: Optional[str] = Field(default=None, max_length=40000)
    items: Optional[list[str]] = Field(default=None, max_length=300)


class RestrictionCreateRequest(BaseModel):
    """A category an admin adds by hand (not a tab of the sheet)."""
    name: str = Field(max_length=200)
    kind: str = Field(default="guidance", max_length=20)
    keywords: Annotated[str, Field(max_length=5000)] | Annotated[list[str], Field(max_length=200)] = []
    summary: str = Field(default="", max_length=2000)
    body: str = Field(default="", max_length=40000)
    items: Annotated[str, Field(max_length=40000)] | Annotated[list[str], Field(max_length=300)] = []
    extra_notes: str = Field(default="", max_length=20000)


def _restriction_admin_payload(warnings: Optional[list[str]] = None) -> dict:
    state = restrictions_svc.load_state()
    sync = state["sync"] or {}
    known_names = sorted(p.name for p in effective_catalog())
    return {
        "warnings": warnings or [],
        "categories": [c.to_dict() for c in state["categories"]],
        "using_builtin": state["using_builtin"],
        "sheet_url": sync.get("sheet_url") or restrictions_svc.DEFAULT_SHEET_URL,
        "source_title": sync.get("source_title"),
        "synced_at": sync["synced_at"].isoformat() if sync.get("synced_at") else None,
        "synced_by": sync.get("synced_by"),
        "catalog_names": known_names,
    }


def _restrictions_read_failed(state: dict) -> HTTPException:
    if state.get("missing_table"):
        return HTTPException(status_code=503, detail="A database table this feature needs doesn't exist yet — apply the latest schema.sql, then try again.")
    return HTTPException(status_code=503, detail="Couldn't read the restrictions table — try again in a moment.")


def _ingest_restrictions(data: bytes, sheet_url: str, source_title: str, synced_by: str) -> list[str]:
    """Parse a workbook and replace the stored categories with it, keeping the keyword and product-mapping
    edits an admin already made to tabs that are still there. Returns the parser's warnings."""
    categories, warnings = restrictions_svc.parse_workbook_ex(data)
    previous = restrictions_svc.load_state()
    if previous.get("error"):
        # The read FAILED: replacing now would throw away the keyword / mapping edits an admin made.
        raise _restrictions_read_failed(previous)
    # Edits to carry over: everything stored when a real sync was there before; only the hand-added categories (their
    # notes and keywords) when the built-in snapshot was all there was.
    carried = previous["categories"] if not previous["using_builtin"] else [c for c in previous["categories"] if c.is_manual]
    restrictions_svc.carry_over_admin_edits(categories, carried)
    clashes = restrictions_svc.replace_all(categories, sheet_url=sheet_url, source_title=source_title, synced_by=synced_by) or []
    warnings = list(warnings) + [
        f"The sheet now has a tab called \"{name}\", so it replaced the category of that name you had added by hand "
        "(its extra notes and keywords were kept)." for name in clashes]
    return warnings


# Roadblocks should judge against the CURRENT "Restricted Verticals by Platforms" sheet, not against whatever copy an
# admin last pressed Sync on. So whenever the stored copy is older than this, the sheet is re-read from Google
# (through the app's Drive connection) before the Step 07 chooser / check uses it. 0 turns it off.
_RESTRICTIONS_REFRESH_MINUTES = float(os.environ.get("RESTRICTIONS_AUTO_SYNC_MINUTES", "60") or 0)
_RESTRICTIONS_RETRY_SECONDS = 600           # a failed refresh (Drive not connected, sheet not shared...) isn't retried every request
_RESTRICTIONS_WAIT_SECONDS = 15             # how long a request waits for a refresh before using the stored copy
_restrictions_refresh = {"attempted_at": None}
_restrictions_refresh_lock = threading.Lock()


def _refresh_restrictions_if_stale() -> bool:
    """Re-read the sheet from Google when the stored copy is stale. Best effort and never raises: any failure just
    leaves the stored copy in place (and is logged). Only refreshes a copy an admin already synced from the sheet -
    never the built-in snapshot (its curated product mappings would be replaced by a raw parse) and never an
    uploaded workbook (a deliberate override). Admin edits to keywords and product mappings are carried over, as
    for a manual sync. Returns True when a refresh happened."""
    if _RESTRICTIONS_REFRESH_MINUTES <= 0:
        return False
    if not _restrictions_refresh_lock.acquire(blocking=False):
        return False                         # another request is already refreshing
    try:
        state = restrictions_svc.load_state()
        sync = state.get("sync") or {}
        if state.get("error") or state.get("using_builtin") or not sync.get("synced_at"):
            return False
        if str(sync.get("source_title") or "").startswith("Uploaded:"):
            return False
        if datetime.now(timezone.utc) - sync["synced_at"] < timedelta(minutes=_RESTRICTIONS_REFRESH_MINUTES):
            return False
        last = _restrictions_refresh["attempted_at"]
        if last is not None and time.monotonic() - last < _RESTRICTIONS_RETRY_SECONDS:
            return False
        _restrictions_refresh["attempted_at"] = time.monotonic()
        sheet_url = sync.get("sheet_url") or restrictions_svc.DEFAULT_SHEET_URL
        data, title = restrictions_svc.fetch_sheet_xlsx(restrictions_svc.extract_file_id(sheet_url))
        _ingest_restrictions(data, sheet_url, title or sync.get("source_title") or "Restricted Verticals", "auto-sync")
        _logger.info("restricted-verticals sheet re-read from Google (stored copy was from %s)", sync["synced_at"])
        return True
    except Exception as exc:  # noqa: BLE001 - the stored copy is still a valid answer
        _logger.warning("couldn't refresh the restricted-verticals sheet; using the stored copy: %s", exc)
        return False
    finally:
        _restrictions_refresh_lock.release()


async def _refresh_restrictions_best_effort() -> None:
    """Run the stale-copy refresh without letting it hold a request up for long: past the wait, the stored copy is
    used and the refresh finishes in the background for the next request."""
    try:
        await asyncio.wait_for(asyncio.shield(asyncio.to_thread(_refresh_restrictions_if_stale)),
                               timeout=_RESTRICTIONS_WAIT_SECONDS)
    except Exception:  # noqa: BLE001 - timeouts included
        pass


@app.get("/api/admin/restrictions")
async def admin_get_restrictions() -> dict:
    return _restriction_admin_payload()


@app.post("/api/admin/restrictions/sync")
async def admin_sync_restrictions(body: RestrictionSyncRequest, request: Request) -> dict:
    sheet_url = (body.sheet_url or restrictions_svc.DEFAULT_SHEET_URL).strip()
    try:
        file_id = restrictions_svc.extract_file_id(sheet_url)
        data, title = await asyncio.to_thread(restrictions_svc.fetch_sheet_xlsx, file_id)
        warnings = await asyncio.to_thread(
            _ingest_restrictions, data, sheet_url, title or "Restricted Verticals", request.state.user["email"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"synced": True, **_restriction_admin_payload(warnings)}


@app.post("/api/admin/restrictions/upload")
async def admin_upload_restrictions(body: RestrictionUploadRequest, request: Request) -> dict:
    try:
        data = restrictions_svc.decode_upload(body.data_base64)
        warnings = await asyncio.to_thread(
            _ingest_restrictions, data, restrictions_svc.DEFAULT_SHEET_URL,
            f"Uploaded: {body.filename or 'workbook.xlsx'}", request.state.user["email"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"synced": True, **_restriction_admin_payload(warnings)}


@app.put("/api/admin/restrictions/{name}")
async def admin_update_restriction(name: str, body: RestrictionUpdateRequest, request: Request) -> dict:
    try:
        state = restrictions_svc.load_state()
        if state.get("error"):
            # The read FAILED (not "no rows yet"): seeding now would wipe whatever is really stored.
            raise _restrictions_read_failed(state)
        stored_names = {c.name for c in state["categories"] if c.is_manual}
        if state["using_builtin"] and name not in stored_names:
            # Editing the built-in snapshot: persist it first so there are rows to edit.
            restrictions_svc.seed_builtin_into_db(request.state.user["email"])
        updated = restrictions_svc.update_category(
            name, keywords=body.keywords, product_map=body.product_map, extra_notes=body.extra_notes,
            kind=body.kind, summary=body.summary, body=body.body, items=body.items)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if updated is None:
        raise HTTPException(status_code=404, detail="Category not found.")
    return {"saved": True, **_restriction_admin_payload(restrictions_svc.mapping_warnings([updated]) if updated.is_manual else None)}


@app.post("/api/admin/restrictions")
async def admin_create_restriction(body: RestrictionCreateRequest) -> dict:
    """Add a category (a restricted vertical with its rules) directly in the app: Step 07 offers it, and the
    Roadblocks AI reads it like a sheet tab."""
    try:
        category, warnings = await asyncio.to_thread(
            restrictions_svc.create_manual_category, name=body.name, kind=body.kind, keywords=body.keywords,
            summary=body.summary, body=body.body, items=body.items, extra_notes=body.extra_notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"created": category.name, **_restriction_admin_payload(warnings)}


@app.delete("/api/admin/restrictions/{name}")
async def admin_delete_restriction(name: str) -> dict:
    """Delete a category added by hand (a sheet tab can only go away by being removed from the sheet)."""
    try:
        deleted = await asyncio.to_thread(restrictions_svc.delete_manual_category, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail="Category not found.")
    return {"deleted": True, **_restriction_admin_payload()}


class SuggestTabsRequest(BaseModel):
    request: dict
    product_names: list[str] = []


@app.post("/api/suggest-tabs")
async def suggest_tabs(body: SuggestTabsRequest) -> dict:
    """Which export tabs the CURRENT plan calls for — the same classification
    /api/generate applies, so the Generate step's checkboxes can show what
    will really be built. The suggestion returned at parse time goes stale as
    soon as the planner changes the mix, request type or agency fee."""
    req = _reconstruct_proposal_request(body.request)
    return {"tabs": classify_output_tabs(
        req.request_type, body.product_names,
        has_agency_fee=req.agency_fee is not None and req.agency_fee > 0,
    )}


class DisclaimerPreviewRequest(BaseModel):
    request: dict
    tiers: list[TierModel] = []
    line_items: list[LineItemModel] = []


@app.post("/api/disclaimers/preview")
async def preview_disclaimers(body: DisclaimerPreviewRequest) -> dict:
    """Which disclaimers WOULD be drawn into each option's export, from the
    wizard's current state — same resolver /api/generate uses, so what the
    planner sees on the Generate step is what they'll get."""
    req = _reconstruct_proposal_request(body.request)
    if body.tiers:
        tier_dicts = [
            {"label": t.label, "name": t.name, "geo": t.geo, "start_date": t.start_date,
             "end_date": t.end_date, "line_items": t.line_items}
            for t in body.tiers
        ]
    else:
        tier_dicts = [{"label": "A", "line_items": body.line_items}]
    resolved = disclaimers_svc.resolve_for_tiers(req, tier_dicts)
    return {"tiers": {
        label: [{"id": b["id"], "name": b["name"], "color": b["color"], "applies_to": b["applies_to"]} for b in banners]
        for label, banners in resolved.items()
    }}


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
    at a glance, outside the app" audit use as the Product Catalog tab's export.
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


def _product_fields(body: ProductRequest) -> dict:
    return body.model_dump(exclude_unset=True, exclude={"copy_from_id"})


@app.get("/api/admin/products")
async def admin_list_products() -> dict:
    """The product list, in display order — every product an equal, fully editable row."""
    try:
        products = catalog_list_products()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"products": products, "families": families()}


@app.post("/api/admin/products")
async def admin_create_product(body: ProductRequest, request: Request) -> dict:
    """Add a product — blank, or (copy_from_id) a copy of an existing one with the form's changes laid over it.
    A copy also inherits the original's Restricted Verticals approvals, so a variant of an approved product
    isn't suddenly "not accepted"."""
    try:
        created = catalog_create_product(_product_fields(body), body.copy_from_id)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    inherited, warning = 0, None
    if created.get("copied_from"):
        try:
            inherited = restrictions_svc.inherit_product_approvals(created["copied_from"], created["name"], request.state.user["email"])
        except Exception:  # noqa: BLE001 — the copy is saved; only the approvals carry-over is best effort
            logging.getLogger(__name__).exception("couldn't copy restriction approvals to %r", created["name"])
            warning = ("The product was added, but copying its Restricted Verticals approvals failed — "
                       "map it in Admin → Restrictions or it will read as not accepted.")
        try:      # a copy of a forecast-enabled product forecasts the same way (best effort, like the approvals)
            forecast_map_svc.inherit_config(created["copied_from"], created["name"], request.state.user["email"])
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("couldn't copy the forecast mapping to %r", created["name"])
    return {"added": True, "product": created, "restrictions_inherited": inherited, "restrictions_warning": warning}


@app.put("/api/admin/products/{product_id}")
async def admin_update_product(product_id: int, body: ProductRequest) -> dict:
    """Edit any field of a product. A new name is recorded so the old one keeps resolving in saved proposals and
    pasted requests."""
    try:
        updated = catalog_update_product(product_id, _product_fields(body))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"saved": True, "product": updated}


@app.delete("/api/admin/products/{product_id}")
async def admin_delete_product(product_id: int) -> dict:
    """Remove a product from every list. Proposals that already use it keep working."""
    try:
        name = catalog_delete_product(product_id)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if name is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    return {"deleted": True, "name": name}


# ---------------------------------------------------------------------------
# Admin -> Forecast: which products get Constanza's "Auto-calculate avails" button (Step 05) and what it computes.
# See app/services/constanza_forecast_map.py. Admin-only through the blanket /api/admin/* rule.
# ---------------------------------------------------------------------------

@app.get("/api/admin/forecast-map")
async def admin_get_forecast_map() -> dict:
    """Every live product with its effective forecast config, the platform coverage and the totals."""
    return forecast_map_svc.admin_payload()


@app.put("/api/admin/forecast-map")
async def admin_set_forecast_map(body: ForecastMapUpdateRequest, request: Request) -> dict:
    """Save one product's choice (enabled / what it computes / which platform / Hispanic weighting)."""
    try:
        forecast_map_svc.set_override(body.product_name, body.model_dump(exclude={"product_name"}), request.state.user["email"])
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"saved": True, **forecast_map_svc.admin_payload()}


@app.post("/api/admin/forecast-map/reset")
async def admin_reset_forecast_map(body: ForecastMapProductRequest) -> dict:
    """Put one product back on its built-in default."""
    try:
        forecast_map_svc.clear_override(body.product_name)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"reset": True, **forecast_map_svc.admin_payload()}


@app.post("/api/admin/forecast-map/add-product")
async def admin_add_forecast_platform_product(body: ForecastMapProductRequest) -> dict:
    """Add one of the built-in platform SKUs (Hulu, Peacock, ...) that exists in the code seed but was never in a
    database that was seeded before it was added. Its rate is a PLACEHOLDER — the response says so."""
    try:
        created = forecast_map_svc.add_missing_platform_product(body.product_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"added": True, "product": created, **forecast_map_svc.admin_payload()}


class ForecastRollbackRequest(BaseModel):
    series_key: str
    to_version: Optional[int] = None


@app.get("/api/forecast/series")
def forecast_series_list() -> dict:
    """The forecast data series currently published (Postgres): what each one is, how it was produced, its unit and
    period, which geography levels it covers, and the catalog products that default to it."""
    series = forecast_store.current_series()
    links = forecast_store.products_for_series()
    return {
        "series": [
            {**ref.to_public(), "row_counts": ref.row_counts, "products": [{"name": n, "relation": r} for n, r in sorted(links.get(key, []))]}
            for key, ref in sorted(series.items(), key=lambda kv: (kv[1].channel, kv[0]))
        ],
    }


@app.get("/api/admin/forecast/data")
def admin_forecast_data() -> dict:
    """Admin -> Forecast -> Data: every series with its versions and validation result, and the latest imports."""
    try:
        return forecast_store.data_overview()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"The forecast data tables can't be read: {type(e).__name__}")


@app.post("/api/admin/forecast/rollback")
def admin_forecast_rollback(body: ForecastRollbackRequest, request: Request) -> dict:
    """Make a series' previous (or a chosen) version the current one again. Nothing is deleted or edited."""
    from app.services.forecast_import import service as forecast_import_service
    try:
        version = forecast_import_service.rollback_series(body.series_key, body.to_version, request.state.user["email"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Couldn't roll back: {type(e).__name__}")
    forecast_store.invalidate()
    forecast_map_svc.invalidate()
    return {"series_key": body.series_key, "version": version, **forecast_store.data_overview()}


# CSV columns shared by the export and the bulk upload, so a planner can export -> edit in Excel -> re-upload.
_PRODUCT_CSV_COLUMNS = [
    "name", "family", "short_label", "buying_model", "base_rate", "minimum_spend",
    "estimated_cpm_for_imps", "sizes", "tech_platform", "proposal_description", "notes", "is_addon",
]


@app.get("/api/admin/products/export")
async def admin_export_products() -> Response:
    """The product list as a CSV — for review, and as the starting point for the bulk upload (same columns)."""
    try:
        products = catalog_list_products()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_PRODUCT_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for p in products:
        writer.writerow({k: p.get(k) for k in _PRODUCT_CSV_COLUMNS})
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=products_export.csv"},
    )


_TRUE_CELLS = ("true", "1", "yes", "y")
_FALSE_CELLS = ("false", "0", "no", "n")


def _parse_csv_bool(v: str) -> bool:
    v = (v or "").strip().lower()
    if v in _TRUE_CELLS:
        return True
    if v in _FALSE_CELLS:
        return False
    raise ValueError(f"'{v}' isn't yes/no (use true/false, yes/no, 1/0).")


def _parse_csv_float(v: str, label: str = "value") -> Optional[float]:
    """A number from a spreadsheet cell: "$1,000.00" and "1,500" are what Excel shows, so those parse. A blank
    cell is None; anything else that isn't a number is an error (never a silent blank that would wipe the value)."""
    v = (v or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        raise ValueError(f"{label} '{v}' isn't a number.") from None


def _decode_csv(raw: bytes) -> str:
    """Excel on Windows saves "CSV" as cp1252 unless asked for UTF-8; accept both (a BOM is tolerated)."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


@app.post("/api/admin/products/bulk-upsert")
async def admin_bulk_upsert_products(file: UploadFile = File(...)) -> dict:
    """
    Add or update many products from an uploaded CSV (the export's columns), matched by `name` (case-insensitive):
    a name that's in the list updates that product, anything else is added. A blank text cell leaves the product's
    value alone; a blank rate / est. CPM clears it. One bad row never blocks the others — its error is reported
    instead — and the whole upload is one database transaction.
    """
    raw = _decode_csv(await file.read())
    reader = csv.DictReader(io.StringIO(raw))
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail="Empty or unreadable CSV file.")
    reader.fieldnames = [(f or "").strip() for f in reader.fieldnames]
    missing_cols = {"name", "family", "buying_model"} - set(reader.fieldnames)
    if missing_cols:
        raise HTTPException(status_code=400, detail=f"CSV is missing required column(s): {', '.join(sorted(missing_cols))}")

    items, errors = [], []
    rows_processed = 0
    for i, row in enumerate(reader, start=2):  # row 1 is the header
        rows_processed += 1
        name = (row.get("name") or "").strip()
        if not name:
            errors.append(f"Row {i}: missing name — skipped.")
            continue
        fields: dict = {"name": name}
        try:
            for key in ("family", "short_label", "buying_model", "sizes", "tech_platform", "proposal_description", "notes"):
                if (row.get(key) or "").strip():
                    fields[key] = row[key].strip()
            for key in ("base_rate", "estimated_cpm_for_imps"):
                if key in row:
                    fields[key] = _parse_csv_float(row.get(key), key.replace("_", " "))
            spend = _parse_csv_float(row.get("minimum_spend"), "minimum spend")
            if spend is not None:
                fields["minimum_spend"] = spend
            if (row.get("is_addon") or "").strip():
                fields["is_addon"] = _parse_csv_bool(row.get("is_addon"))
        except ValueError as e:
            errors.append(f"Row {i} ('{name}'): {e}")
            continue
        items.append({"line": i, "fields": fields})

    try:
        result = await asyncio.to_thread(catalog_bulk_upsert_products, items)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    errors.extend(result["errors"])
    return {"created": result["created"], "updated": result["updated"], "unchanged": result["unchanged"],
            "errors": errors, "total_rows_processed": rows_processed}


# ---------------------------------------------------------------------------
# Process FAQs — the rows of the exports' "Process FAQs" tab (app/services/faq_entries.py).
# Admin-only like every /api/admin/* route.
# ---------------------------------------------------------------------------

class FaqEntryRequest(BaseModel):
    section: Optional[str] = Field(default=None, max_length=40)
    services: Optional[str] = Field(default=None, max_length=2000)
    question: Optional[str] = Field(default=None, max_length=5000)
    link_title: Optional[str] = Field(default=None, max_length=2000)
    link_url: Optional[str] = Field(default=None, max_length=4000)


class FaqMoveRequest(BaseModel):
    direction: str = Field(max_length=10)


def _faq_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=503, detail=str(exc))


@app.get("/api/admin/faq")
async def admin_list_faq() -> dict:
    try:
        entries = await asyncio.to_thread(faq_svc.list_entries)
    except RuntimeError as e:
        raise _faq_error(e)
    return {"entries": entries, "sections": faq_svc.SECTION_LABELS,
            "guidelines_url": faq_svc.GUIDELINES_URL, "guidelines_title": faq_svc.GUIDELINES_LINK_TITLE}


@app.post("/api/admin/faq")
async def admin_create_faq(body: FaqEntryRequest) -> dict:
    try:
        entry = await asyncio.to_thread(faq_svc.create_entry, body.model_dump(exclude_unset=True))
    except (ValueError, RuntimeError) as e:
        raise _faq_error(e)
    return {"added": True, "entry": entry}


@app.put("/api/admin/faq/{entry_id}")
async def admin_update_faq(entry_id: int, body: FaqEntryRequest) -> dict:
    try:
        entry = await asyncio.to_thread(faq_svc.update_entry, entry_id, body.model_dump(exclude_unset=True))
    except (ValueError, LookupError, RuntimeError) as e:
        raise _faq_error(e)
    return {"saved": True, "entry": entry}


@app.delete("/api/admin/faq/{entry_id}")
async def admin_delete_faq(entry_id: int) -> dict:
    try:
        deleted = await asyncio.to_thread(faq_svc.delete_entry, entry_id)
    except RuntimeError as e:
        raise _faq_error(e)
    if not deleted:
        raise HTTPException(status_code=404, detail="FAQ entry not found.")
    return {"deleted": True}


@app.post("/api/admin/faq/{entry_id}/move")
async def admin_move_faq(entry_id: int, body: FaqMoveRequest) -> dict:
    try:
        await asyncio.to_thread(faq_svc.move_entry, entry_id, body.direction)
    except (ValueError, LookupError, RuntimeError) as e:
        raise _faq_error(e)
    return {"moved": True}


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
            "research_emails": entry.get("research_emails", []),
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
    fieldnames = ["market_key", "is_default", "address_line1", "address_line2", "dsc_email", "dsm_email", "research_emails", "ccs"]
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
            "research_emails": "; ".join(entry.get("research_emails", [])),
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
            "research_emails": body.research_emails,
            "ccs": body.ccs,
        }.items() if v is not None
    }
    try:
        if "research_emails" in fields:
            fields["research_emails"] = normalize_emails(fields["research_emails"])
        entry = set_market_entry(market_key, fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
