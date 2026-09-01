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

import json
import os
import secrets
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Load .env (OPENAI_API_KEY, DRIVE_CLIENT_ID/SECRET/ROOT_FOLDER_ID, etc.) into
# the process environment before anything below reads os.environ — the repo
# has shipped a .env/.env.example convention for a while, but nothing ever
# actually loaded it, so values sitting in .env silently had no effect
# unless separately exported in the shell. python-dotenv never overwrites a
# variable that's already set in the real environment, so an explicit shell
# export still takes precedence over .env if both are present.
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.catalog import (
    CATALOG, by_name, families, by_family,
    effective_catalog, load_rate_overrides, save_rate_overrides, clear_rate_override,
    load_custom_products, add_custom_product, delete_custom_product,
    _OVERRIDABLE_FIELDS,
)
from app.services.notion_parser import (
    ProposalRequest,
    parse_notion,
    classify_output_tabs,
)
from app.services.proposal_generator import LineItem, generate_proposal
from app.services.recommender import recommend_line_items
from app.services import drive_uploader
from app.services import ai_enricher
from app.services import docx_builder
from app.services import strategy_brief as strategy_brief_svc
from app.services import roadblocks as roadblocks_svc


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


class AvailsEntry(BaseModel):
    max_imps: Optional[float] = None
    max_spend: Optional[float] = None
    est_uniques: Optional[float] = None
    # True when this value was derived from the other field (Fixed/estimated-CPM
    # products) rather than typed directly — rendered as "Est. …" text in Excel.
    max_imps_estimated: Optional[bool] = None
    max_spend_estimated: Optional[bool] = None
    # "imps" or "spend" — whichever field the planner directly typed most
    # recently; the other is always mechanically derived from it. Lets the
    # Excel export write the derived side as a live formula instead of a
    # static number. None for avails saved before this field existed.
    basis: Optional[str] = None


class TierModel(BaseModel):
    label: str  # "A" | "B" | "C" | "D"
    line_items: list[LineItemModel]
    avails_data: Optional[dict[str, AvailsEntry]] = None


class GenerateRequest(BaseModel):
    request: dict   # serialized ProposalRequest
    line_items: list[LineItemModel] = []   # legacy single-tier shape — used only when `tiers` is absent
    tiers: Optional[list[TierModel]] = None  # tiered-budget options (up to 4); preferred over `line_items` when present
    force_tabs: Optional[dict] = None
    avails_data: Optional[dict[str, AvailsEntry]] = None  # product_name -> avails; ignored when `tiers` is present
    strategy_brief: Optional[dict] = None  # confirmed Step 03 brief, if not skipped


class StrategyRequest(BaseModel):
    request: dict
    reprompt: Optional[str] = None


class RecommendRequest(BaseModel):
    request: dict
    monthly_budget: float
    strategy_brief: Optional[dict] = None


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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Serve the SPA."""
    html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


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
        })
    return {
        "families": families(),
        "products_by_family": grouped,
    }


@app.post("/api/parse")
async def parse(body: ParseRequest) -> dict:
    """Parse Notion text into a structured ProposalRequest."""
    catalog_names = [p.name for p in effective_catalog()]
    req = parse_notion(body.notion_text, catalog_names)
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
    from app.services.notion_parser import ProductSpecifics
    raw = dict(body.request)
    if "specifics" in raw and isinstance(raw["specifics"], dict):
        raw["specifics"] = ProductSpecifics(**raw["specifics"])
    valid_fields = set(ProposalRequest.__dataclass_fields__.keys())
    raw = {k: v for k, v in raw.items() if k in valid_fields}
    req = ProposalRequest(**raw)
    brief = strategy_brief_svc.generate_brief(req, reprompt=body.reprompt)

    doc_token: Optional[str] = None
    if brief.get("strategy_summary") or brief.get("recommended_tactics"):
        doc_token = secrets.token_urlsafe(12)
        safe_client = (req.client_name or "proposal").replace("/", "-").replace(" ", "_")[:50]
        doc_filename = f"{safe_client}_Strategy_Brief.docx"
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
        )
        if built:
            (PROPOSALS_DIR / f"strategy_{doc_token}.json").write_text(json.dumps({
                "path": str(doc_path), "filename": doc_filename,
            }))
        else:
            doc_token = None

    brief["doc_token"] = doc_token
    return brief


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


@app.post("/api/roadblocks")
async def roadblocks(body: RoadblocksRequest) -> dict:
    """
    Step 05 — AI-researched platform restrictions/roadblocks for the
    confirmed product mix, grounded in the Step 03 strategy brief + Notion
    context via live web search. Also writes a downloadable .docx report
    and returns a token to fetch it via /api/download-roadblocks/{token}.
    """
    from app.services.notion_parser import ProductSpecifics
    raw = dict(body.request)
    if "specifics" in raw and isinstance(raw["specifics"], dict):
        raw["specifics"] = ProductSpecifics(**raw["specifics"])
    valid_fields = set(ProposalRequest.__dataclass_fields__.keys())
    raw = {k: v for k, v in raw.items() if k in valid_fields}
    req = ProposalRequest(**raw)

    line_items = [
        LineItem(id=li.id, product_name=li.product_name, monthly_budget=li.monthly_budget, months=li.months)
        for li in body.line_items
    ]

    result = roadblocks_svc.generate_roadblocks(req, line_items, strategy_brief=body.strategy_brief)

    doc_token: Optional[str] = None
    if result.get("product_roadblocks"):
        doc_token = secrets.token_urlsafe(12)
        safe_client = (req.client_name or "proposal").replace("/", "-").replace(" ", "_")[:50]
        doc_filename = f"{safe_client}_Roadblocks_Report.docx"
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
    from app.services.notion_parser import ProductSpecifics
    raw = dict(body.request)
    if "specifics" in raw and isinstance(raw["specifics"], dict):
        raw["specifics"] = ProductSpecifics(**raw["specifics"])
    valid_fields = set(ProposalRequest.__dataclass_fields__.keys())
    raw = {k: v for k, v in raw.items() if k in valid_fields}
    req = ProposalRequest(**raw)

    items = recommend_line_items(req, body.monthly_budget, strategy_brief=body.strategy_brief)
    return {"line_items": [asdict(li) for li in items]}


@app.post("/api/generate")
async def generate(body: GenerateRequest, request: Request) -> dict:
    """
    Generate an Excel proposal with AI enrichment.
    Returns proposal_id, filename, proposal_title, summary, and enrichment content.
    """
    from app.services.notion_parser import ProductSpecifics
    raw = dict(body.request)
    if "specifics" in raw and isinstance(raw["specifics"], dict):
        raw["specifics"] = ProductSpecifics(**raw["specifics"])
    valid_fields = set(ProposalRequest.__dataclass_fields__.keys())
    raw = {k: v for k, v in raw.items() if k in valid_fields}
    req = ProposalRequest(**raw)

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
                "line_items": _to_line_items(t.line_items),
                "avails_data": {name: entry.model_dump() for name, entry in (t.avails_data or {}).items()},
            }
            for t in body.tiers
        ]
    else:
        tiers = [{
            "label": "A",
            "line_items": _to_line_items(body.line_items),
            "avails_data": {name: entry.model_dump() for name, entry in (body.avails_data or {}).items()},
        }]
    multi_tier = len(tiers) > 1

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
        {"label": t["label"], "line_items": t["line_items"]} for t in tiers
    ] if multi_tier else None
    enrichment = ai_enricher.enrich_proposal(
        req, union_line_items, short_id, strategy_brief=body.strategy_brief, tiers=tier_context,
    )

    # 3. Build the naming-convention title
    proposal_title = ai_enricher.build_proposal_title(
        short_id=short_id,
        campaign_name=enrichment.campaign_name or req.client_name or "Campaign",
        request_type=req.request_type,
        ref_date=req.start_date or "",
    )

    # 4. Derive filenames from the title
    proposal_id = secrets.token_urlsafe(16)
    safe_base = ai_enricher.safe_filename(proposal_title)
    filename = f"{safe_base}.xlsx"
    output_path = PROPOSALS_DIR / filename
    email_doc_filename: Optional[str] = None

    # 5. Generate Excel — one set of tabs (Net/wsections/Gross/Avails-Only)
    #    PER TIER when there's more than one budget option, lettered
    #    Proposal A/B/C/D; DOOH and Process FAQs stay single shared tabs.
    try:
        summary = generate_proposal(
            req, tiers[0]["line_items"], output_path,
            force_tabs=body.force_tabs,
            enrichment=enrichment,
            proposal_title=proposal_title,
            avails_data=tiers[0]["avails_data"],
            tiers=tiers if multi_tier else None,
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

    # 8. Build client email Word doc (if AI produced content)
    if enrichment.client_email_body:
        email_doc_filename = f"{safe_base}_Client_Email.docx"
        email_doc_path = PROPOSALS_DIR / email_doc_filename
        docx_builder.build_client_email_docx(
            output_path=email_doc_path,
            proposal_title=proposal_title,
            ae_name=req.requested_by or "",
            ae_email=req.salesperson_email or "",
            subject=enrichment.client_email_subject,
            body=enrichment.client_email_body,
        )

    # 9. Track requester device/IP for the admin view
    user_agent = request.headers.get("user-agent", "")
    client_ip = request.client.host if request.client else ""

    # 10. Store metadata
    (PROPOSALS_DIR / f"{proposal_id}.json").write_text(json.dumps({
        "filename": filename,
        "path": str(output_path),
        "client_name": req.client_name,
        "seller_email": req.salesperson_email,
        "requested_by": req.requested_by,
        "notion_id": notion_id,
        "summary": summary,
        "proposal_title": proposal_title,
        "email_doc_filename": email_doc_filename,
        "email_doc_path": str(PROPOSALS_DIR / email_doc_filename) if email_doc_filename else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requester_ip": client_ip,
        "requester_user_agent": user_agent,
        # Full state so this proposal can be reopened later and re-edited
        # (Admin → Reopen) without re-pasting from Notion. `tiers` is stored
        # for fidelity when present; the current reopen flow only restores
        # the single-tier shape (line_items/avails_data), so a multi-tier
        # proposal reopens as tier A's mix — full tiered reopen isn't wired
        # up on the frontend yet.
        "reopen_state": {
            "request": body.request,
            "line_items": [li.model_dump() for li in (body.line_items or (body.tiers[0].line_items if body.tiers else []))],
            "avails_data": {
                k: v.model_dump() for k, v in
                (body.avails_data or (body.tiers[0].avails_data if body.tiers else {}) or {}).items()
            },
            "tiers": [t.model_dump() for t in body.tiers] if body.tiers else None,
            "strategy_brief": body.strategy_brief,
            "force_tabs": body.force_tabs,
        },
    }))

    # 11. Build enrichment payload for the frontend
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

    return {
        "proposal_id": proposal_id,
        "filename": filename,
        "proposal_title": proposal_title,
        "summary": summary,
        "enrichment": enrichment_out,
    }


@app.get("/api/proposal/{proposal_id}/reopen")
async def reopen_proposal(proposal_id: str) -> dict:
    """
    Return the full saved state (request, line items, avails, strategy brief)
    for a previously generated proposal, so the app can pre-fill the wizard
    for edits instead of starting from a blank paste.
    """
    meta_file = PROPOSALS_DIR / f"{proposal_id}.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail="Proposal not found")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    reopen_state = meta.get("reopen_state")
    if not reopen_state:
        raise HTTPException(status_code=410, detail="This proposal was generated before reopening was supported.")
    return {
        "proposal_title": meta.get("proposal_title", ""),
        **reopen_state,
    }


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
    from app.services.notion_parser import ProductSpecifics
    raw = dict(body.request)
    if "specifics" in raw and isinstance(raw["specifics"], dict):
        raw["specifics"] = ProductSpecifics(**raw["specifics"])
    valid_fields = set(ProposalRequest.__dataclass_fields__.keys())
    raw = {k: v for k, v in raw.items() if k in valid_fields}
    req = ProposalRequest(**raw)

    line_items = [
        LineItem(id=li.id, product_name=li.product_name, monthly_budget=li.monthly_budget, months=li.months)
        for li in body.line_items
    ]

    result = ai_enricher.reprompt_emails(
        req, line_items, body.campaign_name,
        body.current_internal_subject, body.current_internal_body,
        body.current_client_subject, body.current_client_body,
        body.reprompt,
    )

    meta_file = PROPOSALS_DIR / f"{proposal_id}.json"
    if meta_file.exists() and not result.get("error") and result.get("client_email_body"):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        email_doc_path_str = meta.get("email_doc_path")
        if email_doc_path_str:
            docx_builder.build_client_email_docx(
                output_path=Path(email_doc_path_str),
                proposal_title=meta.get("proposal_title", ""),
                ae_name=req.requested_by or "",
                ae_email=req.salesperson_email or "",
                subject=result["client_email_subject"],
                body=result["client_email_body"],
            )

    return result


@app.get("/api/download/{proposal_id}")
async def download(proposal_id: str) -> FileResponse:
    """Download a previously generated proposal."""
    meta_file = PROPOSALS_DIR / f"{proposal_id}.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail="Proposal not found")
    meta = json.loads(meta_file.read_text())
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
    meta_file = PROPOSALS_DIR / f"{proposal_id}.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail="Proposal not found")
    meta = json.loads(meta_file.read_text())
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


@app.post("/api/drive/upload")
async def drive_upload(body: DriveUploadRequest, request: Request) -> dict:
    """
    Upload a generated proposal to Google Drive.
    Folder structure: <DRIVE_ROOT>/<seller_email>/<filename>
    Returns {needs_auth: true, auth_url: ...} when OAuth2 authorization is required.
    Returns a graceful no-op when Drive credentials aren't configured.
    """
    meta_file = PROPOSALS_DIR / f"{body.proposal_id}.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail="Proposal not found")
    meta = json.loads(meta_file.read_text())
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
# Admin — proposal history + rate overrides
#
# No auth layer (this is an internal single-team tool run locally); if this
# ever moves off localhost, put it behind a login before relying on it.
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


@app.get("/admin", response_class=HTMLResponse)
async def admin_page() -> HTMLResponse:
    """Serve the admin SPA (proposal history + rate overrides)."""
    html = (TEMPLATES_DIR / "admin.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/api/admin/proposals")
async def admin_list_proposals() -> dict:
    """
    List every generated proposal this server knows about (from its metadata
    JSON files), newest first — client, seller, Notion ID, title, and who/what
    device generated it.
    """
    proposals = []
    for meta_file in PROPOSALS_DIR.glob("*.json"):
        if meta_file.stem == "_counter":
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        proposals.append({
            "proposal_id": meta_file.stem,
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
        })
    proposals.sort(key=lambda p: p["generated_at"] or "", reverse=True)
    return {"proposals": proposals, "count": len(proposals)}


@app.get("/api/admin/rates")
async def admin_get_rates() -> dict:
    """Return the full catalog (built-in + admin-added) with current overrides flagged, for the admin rate editor."""
    overrides = load_rate_overrides()
    custom_names = {p.name for p in load_custom_products()}
    products = []
    for p in CATALOG + load_custom_products():
        override = overrides.get(p.name, {})
        products.append({
            "name": p.name,
            "family": p.family,
            "buying_model": p.buying_model,
            "base_rate": override.get("base_rate", p.base_rate),
            "minimum_spend": override.get("minimum_spend", p.minimum_spend),
            "estimated_cpm_for_imps": override.get("estimated_cpm_for_imps", p.estimated_cpm_for_imps),
            "catalog_base_rate": p.base_rate,
            "catalog_minimum_spend": p.minimum_spend,
            "catalog_estimated_cpm_for_imps": p.estimated_cpm_for_imps,
            "has_override": p.name in overrides,
            "is_custom": p.name in custom_names,
        })
    return {"products": products, "overridable_fields": list(_OVERRIDABLE_FIELDS)}


@app.post("/api/admin/rates")
async def admin_save_rate(body: RateOverrideRequest) -> dict:
    """Save (or update) a rate override for one product."""
    if by_name(body.product_name) is None and body.product_name not in {p.name for p in CATALOG}:
        raise HTTPException(status_code=404, detail=f"Unknown product '{body.product_name}'")

    overrides = load_rate_overrides()
    fields_ = {
        k: v for k, v in {
            "base_rate": body.base_rate,
            "minimum_spend": body.minimum_spend,
            "estimated_cpm_for_imps": body.estimated_cpm_for_imps,
        }.items() if v is not None
    }
    if fields_:
        overrides[body.product_name] = fields_
    else:
        overrides.pop(body.product_name, None)
    save_rate_overrides(overrides)
    return {"saved": True, "product_name": body.product_name, "override": fields_}


@app.delete("/api/admin/rates/{product_name}")
async def admin_clear_rate(product_name: str) -> dict:
    """Revert one product's rate override back to the catalog default."""
    clear_rate_override(product_name)
    return {"cleared": True, "product_name": product_name}


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
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"added": True, "product": asdict(product)}


@app.delete("/api/admin/products/{product_name}")
async def admin_delete_product(product_name: str) -> dict:
    """Remove a custom (admin-added) product. Built-in catalog products can't be deleted here."""
    if product_name in {p.name for p in CATALOG}:
        raise HTTPException(status_code=400, detail="Built-in catalog products can't be deleted — only admin-added ones.")
    deleted = delete_custom_product(product_name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Custom product '{product_name}' not found.")
    return {"deleted": True, "product_name": product_name}
