"""
Proposal generator — produces a populated Excel proposal from a ProposalRequest
plus planner-curated line items.

Reuses the Stage 1 template builders, then overrides the meta block with
real client/campaign info and overrides each product row's budget/months/notes
with the planner's values.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

from app.catalog import CATALOG, Product, by_name, families, by_family
from app.services.notion_parser import ProposalRequest, classify_output_tabs
from app import excel_template as et
from app.market_config import get_market_address


# ---------------------------------------------------------------------------
# Planner-curated line item
# ---------------------------------------------------------------------------

@dataclass
class LineItem:
    """
    One row in the proposal — the planner picks a catalog product and assigns it
    a budget. months/notes override defaults; rate_override is for KLYY-style
    custom CPMs flagged in the business logic spec.
    """
    product_name: str               # canonical catalog name
    monthly_budget: float           # net dollars per month
    months: int = 3                 # flight length in months
    rate_override: Optional[float] = None     # optional CPM/CPP override
    notes_override: Optional[str] = None      # appended to the catalog note
    target_override: Optional[str] = None     # column D — defaults to campaign target
    # Secondary audience for added scale/avails (e.g. a broader look-alike
    # segment layered on top of a narrow primary intent audience). When
    # set, column D becomes a two-line "Primary: .. / Secondary: .." cell
    # instead of the single target line.
    target_secondary: Optional[str] = None
    # Stable per-line identity from the frontend. Two lines can share the same
    # product_name (e.g. same product, different targeting) — avails_data is
    # keyed by this id, NOT product_name, so those lines don't collide.
    id: Optional[str] = None
    # Per-line override of the catalog's estimated CPM (Fixed/impressions-
    # estimate products only — Meta, YouTube, TikTok, LinkedIn, Spotify,
    # Branded Content, ...). Distinct from rate_override, which is a real
    # CPM/CPP billing rate: this only feeds the "Est. $" impressions-vs-
    # spend calculation, never an actual charge. None = use the catalog's
    # own estimated_cpm_for_imps.
    estimated_cpm_override: Optional[float] = None
    # Added Value: a $0 (or below-minimum) budget is valid and expected for
    # this line, not a planner oversight — exempts it from the below-
    # minimum validation highlight in the app, and sorts it to the bottom
    # of the export's line-items table (above the totals row, below every
    # paid line) rather than interleaved among real budget lines.
    is_added_value: bool = False
    # AV lines only: the planner's stated "we're giving away roughly this
    # % of the deal's real value as added value" — e.g. 5% of a $3,000/mo
    # tier reads as "Estimated $150 value." Purely a note/estimate, never
    # real budget (monthly_budget on an AV line is still $0) — None/0 means
    # the planner hasn't put a number on it yet, so the export just shows
    # the plain "Added Value" note with no dollar estimate.
    added_value_pct: Optional[float] = None
    # Step 04's per-line objective dropdown (Awareness, Website Conversion,
    # Click-To-Call, Conquesting, Lead Generation, or free-text "Other").
    # None falls back to the catalog's own short_label in the export,
    # exactly what column C showed before this field existed.
    objective_override: Optional[str] = None

    def total_budget(self) -> float:
        return self.monthly_budget * self.months


@dataclass
class AddonItem:
    """
    One planner-picked extra from Step 04's Add-Ons module — a fixed-price,
    one-time line (landing page, call tracking, brand lift study, ...),
    distinct from LineItem: no months/target/rate, just a name and an
    editable flat amount. Rolled into the proposal's ADD-ONS / ONE-TIME
    FEES export block instead of the main line-items table.
    """
    product_name: str               # canonical catalog name (a catalog.Product with is_addon=True)
    amount: float                   # planner-edited flat price; defaults to the catalog minimum_spend client-side
    notes_override: Optional[str] = None


# Excel forbids these characters anywhere in a sheet title and caps titles
# at 31 characters total.
_SHEET_NAME_FORBIDDEN = re.compile(r'[:\\/?*\[\]]')


def _safe_sheet_name(base: str, suffix: str, used: set) -> str:
    """
    Turn a planner-typed tier display name into a valid, unique Excel sheet
    title: strip characters Excel forbids, truncate to the 31-char limit
    (leaving room for `suffix`), and de-dupe against every sheet name
    already used in this workbook — two tiers could share a display name,
    or both be left blank, and Excel raises a hard error on any duplicate
    or invalid title, so this must never produce one. `used` is mutated
    (the returned name is added to it) — pass the SAME set across the whole
    workbook's build so tiers can't collide with each other, not just with
    their own other tabs.
    """
    base = _SHEET_NAME_FORBIDDEN.sub("-", (base or "").strip()) or "Option"
    full = f"{base} {suffix}".strip() if suffix else base
    full = full[:31]
    candidate = full
    n = 2
    while candidate.lower() in used:
        tail = f" ({n})"
        candidate = full[:31 - len(tail)] + tail
        n += 1
    used.add(candidate.lower())
    return candidate


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_proposal(
    request: ProposalRequest,
    line_items: list[LineItem],
    output_path: Path,
    *,
    force_tabs: Optional[dict] = None,
    enrichment=None,        # ProposalEnrichment | None
    proposal_title: Optional[str] = None,
    avails_data: Optional[dict] = None,   # product_name -> {max_imps, max_spend, est_uniques}
    tiers: Optional[list[dict]] = None,   # [{"label": "A", "line_items": [...], "avails_data": {...}}, ...]
    addons: Optional[list[AddonItem]] = None,   # Step 04's Add-Ons module picks — proposal-wide, not per-tier
) -> dict:
    """
    Generate an Excel proposal for the given request + line items.

    Args:
        request: parsed ProposalRequest (from notion_parser.parse_notion)
        line_items: planner-confirmed products with budgets — the single-tier
                    (legacy) call shape; ignored when `tiers` is given
        output_path: where to write the .xlsx
        force_tabs: optionally override the auto-classified tab set
                    (lets the planner force Gross, force wsections, etc.)
        tiers: tiered-budget options (up to 10). Each requested tab type
               (Net/wsections/Gross/Avails-Only) is built once PER TIER.
               `tabs_built`/admin-history always key off "Proposal {label}"
               ("A".."J") regardless of tier count — the visible Excel tab
               TITLE is a separately sanitized/de-duped version of the
               tier's own display name (see _safe_sheet_name below) once
               there's more than one tier; a single tier's tab keeps the
               plain "Proposal A" title unchanged. DOOH and Process FAQs
               stay single, shared tabs regardless of tier count (they're
               inventory/reference content, not a budget scenario). When
               omitted or a single tier, output is byte-for-byte the same
               as before this parameter existed.
        addons: Step 04's Add-Ons picks (fixed-price extras — landing pages,
                call tracking, etc.) — the SAME list appears on every tier's
                sheet, matching how the old hardcoded add-ons list already
                behaved before it became planner-driven. None falls back to
                that legacy hardcoded list; [] means "planner picked none."

    Returns:
        dict with summary: {tabs_built: [...], total_net: float,
        total_gross: float, warnings: [...], tiers: [...] | None}. `tiers`
        (present only when there's more than one) carries each option's own
        {label, total_net, total_gross}; the top-level total_net/total_gross
        always describe the FIRST option, matching what a single-tier
        proposal's totals have always meant.
    """
    if not tiers:
        tiers = [{"label": "A", "line_items": line_items, "avails_data": avails_data}]
    multi_tier = len(tiers) > 1

    # Convert AddonItems into the plain dicts excel_template expects — kept
    # dict-based at that boundary (like avails_data already is) so
    # excel_template.py doesn't need to import this module's dataclasses.
    addons_dicts: Optional[list[dict]] = None
    if addons is not None:
        addons_dicts = []
        for a in addons:
            p = by_name(a.product_name)
            desc = (p.proposal_description if p else "") or ""
            if a.notes_override:
                desc = f"{desc}\n— {a.notes_override}" if desc else a.notes_override
            addons_dicts.append({
                "name": a.product_name,
                "description": desc,
                "amount": a.amount,
                "type": "Added Value" if not a.amount else "Fixed",
            })

    # 1. Resolve which tabs to build — a single decision shared by every tier
    #    (they're all the same request/request_type; only the product mix and
    #    budgets differ), based on the UNION of products across all tiers so
    #    a DOOH product tucked into option C still gets its DOOH tabs.
    all_product_names = [li.product_name for t in tiers for li in t["line_items"]]
    tabs = classify_output_tabs(
        request.request_type,
        all_product_names,
        has_agency_fee=request.agency_fee is not None and request.agency_fee > 0,
    )
    if force_tabs:
        tabs.update(force_tabs)

    # Build a blurb lookup from enrichment (product_name → blurb text) — a
    # product's blurb doesn't depend on which tier it appears in, so this is
    # shared across every tier's sheets.
    blurbs: dict[str, str] = {}
    if enrichment and enrichment.product_blurbs:
        for pb in enrichment.product_blurbs:
            if pb.product_name and pb.blurb:
                blurbs[pb.product_name] = pb.blurb

    wb = Workbook()
    wb.remove(wb.active)

    tabs_built = []
    warnings: list[str] = list(request.warnings)
    start_date = request.start_date or ""
    end_date = request.end_date or ""
    total_months = request.total_months or 3
    campaign_name = getattr(enrichment, "campaign_name", "") if enrichment else ""

    tier_summaries = []
    # Shared across every tier/tab-type below so two tiers can't collide
    # with EACH OTHER's sheet titles, not just their own — see _safe_sheet_name.
    used_sheet_titles: set = set()

    for tier in tiers:
        label = (tier.get("label") or "A").strip() or "A"
        tier_avails = tier.get("avails_data")
        # Per-tier geo override (e.g. two options targeting different DMAs)
        # — None/blank falls back to the campaign-level request.geo exactly
        # like tier_display_name falls back to "Option {label}" below.
        tier_geo = (tier.get("geo") or "").strip() or None
        # Planner-given display name (e.g. "Independent") wins over the
        # generic "Option A" wherever a seller/client actually reads this —
        # proposal title, emails, AND (for a multi-tier proposal) the Excel
        # tab title itself, via _safe_sheet_name below. tabs_built/admin-
        # history still key off the plain `label` regardless (see
        # TierModel.name's own docstring for why that stays stable).
        tier_display_name = (tier.get("name") or "").strip() or f"Option {label}"
        tier_title_suffix = f" — {tier_display_name}" if multi_tier else ""

        # Materialize Product objects in the order the planner provided them.
        # Filter products and line_items together so a skipped/unmatched
        # product can't shift the pairing between the two lists (they must
        # stay index-aligned for every zip()/index-based lookup downstream).
        products: list[Product] = []
        matched_line_items: list[LineItem] = []
        for li in tier["line_items"]:
            p = by_name(li.product_name)
            if p is None:
                warn_prefix = f"Option {label}: " if multi_tier else ""
                warnings.append(f"{warn_prefix}Product '{li.product_name}' not found in catalog — skipping.")
                continue
            products.append(p)
            matched_line_items.append(li)
        tier_line_items = matched_line_items

        # Added Value lines sort to the bottom of the EXPORT (above the
        # totals row), regardless of how the planner ordered them while
        # curating — that ordering is fully planner-controlled (Step 04's
        # drag-to-reorder) and shouldn't be second-guessed for the
        # interactive table; a finished proposal document just reads
        # better with $0/bundled extras grouped at the end rather than
        # interleaved among real paid lines. Stable sort — only moves
        # Added Value items past non-Added-Value ones, doesn't otherwise
        # reorder within either group.
        if any(li.is_added_value for li in tier_line_items):
            reordered = sorted(zip(products, tier_line_items), key=lambda pair: pair[1].is_added_value)
            products = [p for p, _ in reordered]
            tier_line_items = [li for _, li in reordered]

        if tabs.get("net"):
            sheet_title = _safe_sheet_name(tier_display_name, "", used_sheet_titles) if multi_tier else f"Proposal {label}"
            ws = et.build_proposal_a(wb, products, with_sections=False,
                                     start_date=start_date, end_date=end_date, total_months=total_months,
                                     sheet_name=sheet_title, addons=addons_dicts)
            _populate_meta(ws, request, gross=False, proposal_title=proposal_title,
                           campaign_name=campaign_name, title_suffix=tier_title_suffix, tier_geo=tier_geo)
            _populate_line_items(ws, products, tier_line_items, gross=False, blurbs=blurbs,
                                 avails_data=tier_avails, request=request)
            tabs_built.append(f"Proposal {label}")

        if tabs.get("wsections"):
            sheet_title = _safe_sheet_name(tier_display_name, "(wsections)", used_sheet_titles) if multi_tier else f"Proposal {label} (wsections)"
            ws = et.build_proposal_a(wb, products, with_sections=True,
                                     start_date=start_date, end_date=end_date, total_months=total_months,
                                     sheet_name=sheet_title, addons=addons_dicts)
            _populate_meta(ws, request, gross=False, proposal_title=proposal_title,
                           campaign_name=campaign_name, title_suffix=tier_title_suffix, tier_geo=tier_geo)
            _populate_line_items(ws, products, tier_line_items, gross=False, with_sections=True, blurbs=blurbs,
                                 avails_data=tier_avails, request=request)
            tabs_built.append(f"Proposal {label} (wsections)")

        if tabs.get("gross"):
            sheet_title = _safe_sheet_name(tier_display_name, "(Gross)", used_sheet_titles) if multi_tier else f"Proposal {label} (Gross)"
            ws = et.build_proposal_a_gross(wb, products,
                                           start_date=start_date, end_date=end_date, total_months=total_months,
                                           sheet_name=sheet_title, addons=addons_dicts)
            _populate_meta(ws, request, gross=True, proposal_title=proposal_title,
                           campaign_name=campaign_name, title_suffix=tier_title_suffix, tier_geo=tier_geo)
            _populate_line_items(ws, products, tier_line_items, gross=True, blurbs=blurbs,
                                 avails_data=tier_avails, request=request)
            # Set agency fee in I14 (Gross sheet's variable input cell)
            if request.agency_fee is not None:
                ws["I14"] = request.agency_fee
                ws["I14"].number_format = "0.00%"
                ws["I14"].font = Font(name="Arial", size=10, bold=True, color="FF0000FF")
                ws["I14"].fill = PatternFill("solid", start_color="FFFFF2CC")
            tabs_built.append(f"Proposal {label} (Gross)")

        if tabs.get("avails_only"):
            avails_sheet_name = f"Avails-Only {label}" if multi_tier else "Avails-Only"
            sheet_title = _safe_sheet_name(tier_display_name, "(Avails)", used_sheet_titles) if multi_tier else avails_sheet_name
            ws = et.build_avails_only(wb, products, line_items=tier_line_items, request=request,
                                      start_date=start_date, end_date=end_date, avails_data=tier_avails,
                                      campaign_name=campaign_name, sheet_name=sheet_title)
            _populate_meta(ws, request, gross=False, proposal_title=proposal_title,
                           title_suffix=f" (Avails-Only){tier_title_suffix}",
                           include_billing=False, include_campaign_meta=False, tier_geo=tier_geo)
            tabs_built.append(avails_sheet_name)

        tier_total_net = sum(li.total_budget() for li in tier_line_items)
        fee = request.agency_fee or 0.0
        tier_summaries.append({
            "label": label,
            "name": tier_display_name,
            "total_net": tier_total_net,
            "total_gross": tier_total_net / (1 - fee) if fee else tier_total_net,
        })

    # DOOH / Process FAQs — shared, single instance regardless of tier count
    if tabs.get("dooh_summary"):
        ws = et.build_dooh_summary(wb)
        # DOOH summary has its own structure; just stamp the client name in title
        ws["A1"] = f"DOOH Summary — {request.client_name or 'TBD'}"
        tabs_built.append("DOOH Summary")

    if tabs.get("dooh_screenlist"):
        et.build_dooh_screenlist(wb)
        tabs_built.append("DOOH Screenlist")

    # Always include Process FAQs as a reference tab
    et.build_process_faqs(wb)
    tabs_built.append("Process FAQs")

    # Save
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)

    base = tier_summaries[0] if tier_summaries else {"total_net": 0.0, "total_gross": 0.0}
    return {
        "tabs_built": tabs_built,
        "total_net": base["total_net"],
        "total_gross": base["total_gross"],
        "tiers": tier_summaries if multi_tier else None,
        "output_path": str(output_path),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Meta block & line item population (overwrites template defaults)
# ---------------------------------------------------------------------------

def _populate_meta(
    ws,
    request: ProposalRequest,
    *,
    gross: bool,
    title_suffix: str = "",
    proposal_title: Optional[str] = None,
    include_billing: bool = True,
    include_campaign_meta: bool = True,
    campaign_name: str = "",
    tier_geo: Optional[str] = None,
) -> None:
    """
    Overwrite the meta block cells (rows 4-15) with real client info.
    Layout matches what _write_meta_block in excel_template.py established.

    include_campaign_meta: the C11..C14 "Media Proposal / Order Description /
    Geo" lines only apply to the standard Proposal A layout, where product
    rows start at row 19. The Avails-Only sheet uses a different layout
    (product rows start at row 11) and writes its own compact meta at
    C5/C6/C7 — writing here too would overwrite its first few product rows,
    so callers for that sheet must pass False.

    campaign_name: the AI-inferred short campaign name (e.g. "Summer
    Specials June 2026") written into the "Order Description:" line. Per-line
    targeting now lives solely in column D of each product row — there's no
    separate campaign-level "Target:" meta line anymore.

    tier_geo: this tier's own geo override (Step 04), if the planner set
    one — wins over request.geo for the "Geo:" line below when present,
    exactly like a tier's display name wins over "Option {label}" elsewhere.
    """
    # Title cell (E2 — merged E2:L2)
    if proposal_title:
        title = proposal_title + (title_suffix if title_suffix else "")
    else:
        title = f"Digital Media Proposal — {request.client_name or 'TBD'}{title_suffix}"
    ws["E2"] = title

    # Customer billing block (F4..F8) — skipped on Avails-Only (not relevant there)
    if include_billing:
        ws["F5"] = f"Attn: {request.requested_by or ''}"
        ws["F6"] = f"Billing Name: {request.agency_name or request.client_name or ''}"
        ws["F7"] = f"Contact e-mail: {request.salesperson_email or ''}"
        # F8 (Address) intentionally left blank for planner to fill

    if not include_campaign_meta:
        return

    # Entravision address (C6/C7) — market-specific, admin-configurable.
    # _write_meta_block wrote the Burbank HQ address here as a static
    # default; override it per the requesting salesperson's market so each
    # market can show its own office once an admin configures one (falls
    # back to the same Burbank default for any market that isn't
    # configured). Avails-Only reuses C6/C7 for its own different content
    # (see include_campaign_meta above) so this only applies here.
    address_line1, address_line2 = get_market_address(request.salesperson_market)
    ws["C6"] = address_line1
    ws["C7"] = address_line2

    # Campaign meta block (C11..C15) — Proposal A layout only. Media
    # Proposal + Order Description combined onto one row (C11) — used to be
    # two separate rows; freed row 12 entirely to shrink the frozen header
    # block, leaving more screen room for the unfrozen line-items view.
    media_proposal_line = f"Media Proposal: {request.client_name or 'TBD'}"
    if request.start_date and request.end_date:
        media_proposal_line += f" — {request.start_date} to {request.end_date}"
    elif request.renewal_campaign_dates:
        media_proposal_line += f" — {request.renewal_campaign_dates}"
    order_description = f"Order Description: {campaign_name}" if campaign_name else "Order Description: "
    ws["C11"] = f"{media_proposal_line}    |    {order_description}"

    effective_geo = tier_geo or request.geo
    if effective_geo:
        ws["C13"] = f"Geo: {effective_geo}"

    # "All rates are NET" is flatly wrong on a Gross sheet — it says the
    # opposite of what the sheet actually shows (rates marked up by the
    # agency fee). "Minimum 3 month Commitment" only applies (and should
    # only be shown) when the actual flight is 3+ months —
    # _write_meta_block's default text stated it unconditionally regardless
    # of the real flight length; that part is independent of net vs. gross.
    rate_basis_text = "Rates shown are GROSS (inclusive of agency commission)." if gross else "All rates are NET."
    if (request.total_months or 0) >= 3:
        ws["C14"] = f"{rate_basis_text} Minimum 3 month Commitment."
    else:
        ws["C14"] = rate_basis_text
    ws["C14"].font = et.BODY_BOLD


# ---------------------------------------------------------------------------
# AI blurb guard-rail
#
# Prompt-side fixes (family-filtered knowledge base, real per-product
# descriptions in context — see ai_enricher.py) make cross-contamination
# much less likely, but they can't GUARANTEE it — an LLM call has no hard
# ceiling on "confidently wrong." This is the backstop: the catalog's own
# proposal_description is ALWAYS the foundation for column E (it's already
# written there by _write_product_row before this module ever runs), and
# the AI's blurb is layered on top ONLY when a cheap, deterministic check
# doesn't catch it describing a different product family's own format —
# exactly the concrete way this broke once already (a Meta blurb
# confidently describing a podcast). Not a proof the surviving text is
# insightful — just a tripwire for the specific, known failure mode. A
# blurb that fails this check is dropped entirely; the row is left with
# just its real catalog description, per "verify it's specific to the
# product, or simply don't include it and just use the description."
# ---------------------------------------------------------------------------

_FAMILY_FINGERPRINT_TERMS: dict[str, tuple[str, ...]] = {
    "Audio": ("podcast", "audio streaming", "radio station", "audioengage"),
    "Online Video": ("pre-roll", "youtube", "video completion", "skippable ad"),
    "DOOH": ("out-of-home", "billboard", "digital signage"),
    "Email": ("inbox", "email deployment", "email list", "email database"),
    "Search": ("search engine", "keyword bidding", "google search", "paid search"),
    "Social": ("facebook", "instagram", "tiktok", "social feed", "linkedin"),
    "Display": ("banner ad", "programmatic display", "geo-fence", "retargeting pixel"),
    "Entravision Plus": ("connected tv", " ctv ", "streaming tv", " ott "),
    "Branded Content": ("branded content", "sponsored content", "talent endorsement"),
}


def _blurb_seems_cross_contaminated(blurb: str, own_family: str) -> bool:
    """True if `blurb` contains another family's distinctive terms — a
    product's own family is exempted so a legitimately Audio-family
    product can still say "podcast" about itself."""
    text = f" {blurb.lower()} "
    for family, terms in _FAMILY_FINGERPRINT_TERMS.items():
        if family == own_family:
            continue
        if any(term in text for term in terms):
            return True
    return False


def _populate_line_items(
    ws,
    products: list[Product],
    line_items: list[LineItem],
    *,
    gross: bool,
    with_sections: bool = False,
    blurbs: Optional[dict] = None,
    avails_data: Optional[dict] = None,
    request: Optional[ProposalRequest] = None,
) -> None:
    """
    Walk the product rows already written by build_proposal_a / _gross and
    overwrite L (NET BUDGET), K (NET RATE if rate_override given), and the
    notes column with planner's values.

    The template builders write product rows starting at PRODUCT_START_ROW.
    For with_sections=True they intersperse section banners — we walk the
    same way to land on the right rows.
    """
    # Row layout starts at row 19 (after meta + header rows) — must match
    # PRODUCT_START_ROW used by excel_template.build_proposal_a.
    # In with_sections mode, the banner takes row N and the product takes N+1.
    start_row = 19

    # Added Value lines carry no real budget by design (monthly_budget is
    # forced to 0 the moment the planner flips the switch) — their
    # estimated "gift value" is a % of what the tier is ACTUALLY billing
    # for, i.e. every other, non-AV line's real budget. Computed once here
    # so every AV line's % applies to the same real total rather than each
    # other's estimates compounding.
    tier_real_total = sum(li.monthly_budget for li in line_items if not li.is_added_value)

    row = start_row
    first_data_row = start_row
    last_family = None
    sov_col = "S" if gross else "Q"
    for product, li in zip(products, line_items):
        if with_sections and product.family != last_family:
            # Section banner just landed on `row`; product row is next.
            row += 1
            last_family = product.family

        av_value = (tier_real_total * (li.added_value_pct / 100.0)) if (li.is_added_value and li.added_value_pct) else None

        # RATE TYPE (J) / NET RATE (K) — an Added Value line is never priced
        # like a normal CPM/CPP/Fixed line (that's the whole point), so it
        # overrides both regardless of what the catalog or a stray
        # rate_override would otherwise show. K=0 (not text) so the sheet's
        # own "-"-for-zero number format displays it cleanly while staying
        # a real, formula-safe number (the Gross sheet's K/(1-fee) column
        # keeps working instead of erroring on non-numeric text).
        if li.is_added_value:
            ws[f"J{row}"] = "Added Value"
            ws[f"J{row}"].alignment = et.CENTER
            ws[f"K{row}"] = 0
            et._format_money_cell(ws[f"K{row}"], blue_input=True)
        elif li.rate_override is not None:
            ws[f"K{row}"] = li.rate_override
            et._format_money_cell(ws[f"K{row}"], blue_input=True)

        # NET BUDGET (L) — the planner's MONTHLY budget, not the flight total.
        # Every other formula on this sheet assumes that: "TOTAL DIGITAL
        # MONTHLY" is SUM(L), and the grand total then multiplies that by
        # the months cell (I10) to get the flight total. Writing the flight
        # total here instead (monthly × months) double-counts months in
        # the grand total, and mislabels the monthly total 3x too high for
        # a 3-month flight — the exact bug the planner reported.
        #
        # An Added Value line with a stated % instead shows its estimated
        # gift value as text ("Estimated $150 value") rather than the plain
        # $0 — still no real budget (SUM(L) below skips non-numeric cells
        # automatically), just a visible note of what's being given away.
        if av_value is not None:
            ws[f"L{row}"] = f"Estimated ${av_value:,.0f} value"
            et._format_av_value_cell(ws[f"L{row}"])
        else:
            ws[f"L{row}"] = li.monthly_budget
            et._format_money_cell(ws[f"L{row}"], blue_input=True)

        # TARGET (D) — per-line override, else Demo | Behavioral | Contextual
        # fallback (instead of leaving the template's default "TBD"); a
        # secondary audience turns this into a two-line Primary/Secondary
        # rich-text cell with bolded labels.
        ws[f"D{row}"] = et.build_target_cell_value(li.target_override, li.target_secondary, request)

        # PRODUCT NAME (C) — overwrite the sub-line _write_product_row wrote
        # at sheet-creation time (the catalog's static short_label) with the
        # planner's own per-line objective, styled in a visibly different
        # color so it doesn't read as a second product name. Falls back to
        # short_label when no objective was set (an older/manual request),
        # matching this cell's original text exactly in that case.
        ws[f"C{row}"] = et.build_product_name_cell_value(product.name, product.short_label, li.objective_override)

        # DETAILS (E) — the catalog's real proposal_description is already
        # the template default here (written by _write_product_row before
        # this function ever runs) and stays untouched unless the AI blurb
        # both exists AND passes the cross-contamination guard-rail above;
        # when it does, it's appended after the real description rather
        # than replacing it, so column E is never LESS accurate than the
        # catalog on its own, only potentially more insightful.
        blurb_text = blurbs.get(product.name) if blurbs else None
        if blurb_text and not _blurb_seems_cross_contaminated(blurb_text, product.family):
            combined_details = f"{product.proposal_description}\n\n{blurb_text}" if product.proposal_description else blurb_text
            ws[f"E{row}"] = combined_details
            # Re-estimate row height for the (usually longer) combined text
            ws.row_dimensions[row].height = et._estimate_row_height(combined_details, col_width=50)

        # Notes column — T for net, W for gross
        notes_col = "W" if gross else "T"
        note_parts = [product.notes or ""]
        if li.is_added_value:
            # A $0 (or below-minimum) budget on this line is deliberate, not
            # an oversight — flag it inline so a reader doesn't mistake it
            # for a data error. When the planner put a % on it, name the
            # actual estimated value too, not just that it's free.
            if av_value is not None:
                note_parts.append(
                    f"Added Value — no media cost. Estimated at {li.added_value_pct:g}% of order value (${av_value:,.0f})."
                )
            else:
                note_parts.append("Added Value — no media cost.")
        if li.notes_override:
            note_parts.append(li.notes_override)
        combined = "\n— ".join(p for p in note_parts if p)
        if combined:
            ws[f"{notes_col}{row}"] = combined

        # Avails (planner-entered from Step 06 of the app) — N/O/P net, P/Q/R gross,
        # plus the SOV% column right after (Q net, S gross). Always written,
        # even with an empty avails dict, so a line with nothing entered gets
        # write_avails_cells's grey "not entered" flag instead of the row
        # silently staying plain-blank with no visual signal either way.
        # Keyed by the line item's own id (falls back to product name for any
        # older/manual request that didn't send one) — id-keying is what lets
        # two lines with the same product carry independent avails.
        avails_by = avails_data or {}
        avail = avails_by.get(li.id) if li.id else None
        if avail is None:
            avail = avails_by.get(product.name)
        sov_pct = et.compute_sov_pct(product, li.monthly_budget, avail or {},
                                      cpm_override=li.estimated_cpm_override, rate_override=li.rate_override)
        et.write_avails_cells(ws, row, avail or {}, product, gross=gross, sov_pct=sov_pct, sov_col=sov_col,
                              budget_col="L", cpm_override=li.estimated_cpm_override)

        row += 1

    if sov_col:
        et._apply_sov_conditional_formatting(ws, sov_col, first_data_row, row - 1)
