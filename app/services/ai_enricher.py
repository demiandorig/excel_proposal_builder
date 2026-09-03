"""
AI enrichment for the Entravision Proposal Builder.

Uses OpenAI gpt-4.1 (see _SEARCH_MODEL/_FALLBACK_MODEL below) to generate in
a single API call:
  - Campaign name (short, memorable, title-cased)
  - Per-product strategic blurbs with data citations (~70 words each)
  - Internal AE email (professional, friendly)
  - Client-facing email body (for the Word doc)

All features degrade gracefully when OPENAI_API_KEY is not set.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

from app.catalog import by_name as _catalog_by_name
from app.services.text_utils import normalize_newlines as _normalize_newlines

try:
    from openai import OpenAI as _OpenAI
    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False


# ---------------------------------------------------------------------------
# Entravision knowledge base (mirrors the recommendations agent prompt)
#
# Keyed by the CATALOG's own family name (not a loose category label) so
# _build_prompt() can filter this down to only the families actually
# curated — see the "podcast bleeding into a Meta blurb" bug this fixed:
# the block used to be one flat, unfiltered string with every category's
# advantage text always present, so a product with nothing else to
# disambiguate it (short name, no per-product description in context
# either — also fixed, see items_text below) had every OTHER category's
# language sitting right there for the model to draw on by mistake. Now a
# family's row simply isn't in context at all unless that family is
# actually in the curated line items.
# ---------------------------------------------------------------------------

_KNOWLEDGE_BASE_BY_FAMILY: dict[str, str] = {
    "Entravision Plus": "Entravision's advanced programmatic and CTV/OTT advertising capabilities with premium publisher partnerships.",
    "Audio": "Entravision's leading digital audio and podcast network, which has a 160MM general market reach and 45MM Hispanic coverage.",
    "Social": "Entravision's extensive portfolio of owned and operated Spanish-language properties and our deep expertise in creating culturally relevant, bilingual content.",
    "Branded Content": "Entravision's vast network of authentic Latino local influencers and creators (our 'Social Media Creators'), paired with deep expertise in culturally relevant, bilingual branded content.",
    "Search": "Entravision's certified SEM team and proprietary bidding strategies optimized for local market dominance.",
    "Email": "Entravision's first-party data network and precision email deployment capabilities.",
    "DOOH": "Entravision's premium out-of-home inventory network with hyper-local geo-targeting capabilities.",
    "Online Video": "Entravision's deep expertise in creating high-impact video content and our managed YouTube advertising capabilities.",
    "Display": "Entravision's programmatic display network with precision geo-targeting and retargeting capabilities.",
}
_KNOWLEDGE_BASE_DEFAULT = (
    "Entravision's deep expertise in creating culturally relevant, bilingual "
    "content that resonates authentically with the target audience."
)


def _knowledge_base_block(curated_families: set) -> str:
    """Only the families actually in the curated line items, plus a
    catch-all default — see the module comment above for why."""
    lines = [
        f'For {fam}: "{_KNOWLEDGE_BASE_BY_FAMILY[fam]}"'
        for fam in sorted(curated_families)
        if fam in _KNOWLEDGE_BASE_BY_FAMILY
    ]
    lines.append(f'For any other product: "{_KNOWLEDGE_BASE_DEFAULT}"')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ProductBlurb:
    product_name: str
    blurb: str  # ~70-80 words; Strategy + Data citation + Entravision advantage


@dataclass
class ProposalEnrichment:
    campaign_name: str = ""
    product_blurbs: list[ProductBlurb] = field(default_factory=list)
    internal_email_subject: str = ""
    internal_email_body: str = ""
    client_email_subject: str = ""
    client_email_body: str = ""
    used_web_search: bool = False
    error: Optional[str] = None

    def blurb_for(self, product_name: str) -> Optional[str]:
        for pb in self.product_blurbs:
            if pb.product_name == product_name:
                return pb.blurb
        return None


# ---------------------------------------------------------------------------
# Naming convention helpers
# ---------------------------------------------------------------------------

def _to_title_case(text: str) -> str:
    """Capitalize the first letter of every whitespace-separated word."""
    if not text:
        return text
    return " ".join(
        w[:1].upper() + w[1:] if w else w
        for w in text.split(" ")
    )


def _get_doc_type(request_type: str) -> str:
    """
    Map a Notion request type to its naming-convention Doc Type. Checks
    "question" and "renewal" first via substring (safe — no other request
    type's text contains those words), then matches "avails" ONLY against
    the exact canonical avails-only phrasings from notion_parser, not as a
    loose substring: several legitimate PROPOSAL request types describe
    themselves as e.g. "Proposal Page With Avails / Estimates Included" or
    "Full Presentation (Deck + Avails + Proposal Included)" — a bare
    `"avails" in rt` check mislabeled every one of those as Doc Type
    "Avails" instead of "Digital Media Proposal", because the word "avails"
    shows up in their own descriptive text despite them not being avails-only
    requests at all.
    """
    from app.services.notion_parser import REQUEST_TYPE_AVAILS_ONLY
    rt = (request_type or "").strip().lower()
    if "question" in rt:
        return "Question"
    if "renewal" in rt:
        return "Digital Renewal Plan"
    if "audit" in rt:
        return "Digital Research"
    if rt in REQUEST_TYPE_AVAILS_ONLY:
        return "Avails"
    return "Digital Media Proposal"


def build_proposal_title(
    short_id: str,
    campaign_name: str,
    request_type: str,
    ref_date: Optional[str] = None,
    doc_type_override: Optional[str] = None,
) -> str:
    """
    Build the full proposal title following the Entravision naming convention:
      {ID} | {Title Case Campaign Name} | Entravision | {MonYY} | {Doc Type}

    Example: "0042 | Texmex Curios July Awareness | Entravision | Jun26 | Digital Media Proposal"

    doc_type_override: skip the request_type -> Doc Type inference and use
    this instead — e.g. "Digital Media Deck" for the companion presentation
    deck on a Full Presentation request, which is a separate deliverable
    from the Excel proposal and always carries that fixed Doc Type
    regardless of what request_type would otherwise map to.
    """
    title_name = _to_title_case(campaign_name.strip()) if campaign_name else "Campaign"
    doc_type = doc_type_override or _get_doc_type(request_type)

    try:
        if ref_date:
            dt = datetime.strptime(ref_date[:10], "%Y-%m-%d")
        else:
            dt = datetime.now()
        mon_yy = dt.strftime("%b%y")  # e.g. "Jun26"
    except Exception:
        mon_yy = date.today().strftime("%b%y")

    title = f"{short_id} | {title_name} | Entravision | {mon_yy} | {doc_type}"
    # Collapse any double spaces
    return re.sub(r"  +", " ", title).strip()


def normalize_notion_id(raw: str) -> str:
    """
    Normalize a planner-entered Notion ID to the 'EVC-#####' convention.
    Accepts bare digits ("4821"), or an already-prefixed value ("EVC-4821"),
    or a value the user typed with stray spaces/case. Returns "" if no digits found.
    """
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    return f"EVC-{digits}"


def safe_filename(title: str) -> str:
    """Convert a proposal title into a safe filesystem name."""
    # Replace pipe separators with underscores, strip other unsafe chars
    name = title.replace(" | ", "_").replace("|", "_").replace(" ", "_")
    name = re.sub(r"[^a-zA-Z0-9._-]", "", name)
    return name[:100]  # cap length


# ---------------------------------------------------------------------------
# Main enrichment call
# ---------------------------------------------------------------------------

# Bumped one tier up from gpt-4o per explicit request ("beefier... not the
# mini one"). Not necessarily the newest OpenAI model available by the time
# this runs — my own knowledge cutoff is Jan 2026 — but it's a real,
# confirmed-capable step up that still supports the Responses API +
# web_search_preview tool this file depends on; flag it if something newer
# should be used instead.
_SEARCH_MODEL = "gpt-4.1"
_FALLBACK_MODEL = "gpt-4.1"


def enrich_proposal(request, line_items, short_id: str, strategy_brief: Optional[dict] = None,
                    tiers: Optional[list] = None) -> ProposalEnrichment:
    """
    Call OpenAI to generate all AI enrichment for a proposal — campaign
    name, per-product blurbs (with data citations), and both emails.
    Returns a ProposalEnrichment — empty fields (not an exception) on failure.

    Grounded in live web search (Responses API + web_search_preview, same
    mechanism as the Roadblocks and Strategy Brief steps) so a blurb's
    "specific recent stat" is something actually found via search, not the
    model's static training-data guess — falls back to a plain (non-
    searching) completion, with a clear disclaimer, if the Responses API
    or the search tool isn't available on this account/SDK version.

    strategy_brief: the confirmed brief from the app's Step 03 (if the planner
    didn't skip it) — when present, the product blurbs are grounded in the
    same audience-specific rationale/data the planner already reviewed and
    confirmed, instead of being regenerated from scratch and potentially
    drifting from it.

    tiers: when the proposal has more than one budget option, a list of
    {"label": "A", "line_items": [...]} — one per option. `line_items`
    itself stays the UNION of every tier's products (for blurb generation,
    which doesn't vary by tier); this only changes how the emails are
    structured, so they lay out each option explicitly instead of
    describing a single plan.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not _HAS_OPENAI:
        return ProposalEnrichment(
            campaign_name=_fallback_campaign_name(request),
            error="openai package not installed — run: pip install openai",
        )
    if not api_key:
        return ProposalEnrichment(
            campaign_name=_fallback_campaign_name(request),
            error="OPENAI_API_KEY not set — email + blurb generation skipped.",
        )

    client = _OpenAI(api_key=api_key)
    prompt = _build_prompt(request, line_items, strategy_brief=strategy_brief, tiers=tiers)

    try:
        response = client.responses.create(
            model=_SEARCH_MODEL,
            tools=[{"type": "web_search_preview"}],
            input=prompt,
            max_output_tokens=6000,
        )
        raw = _extract_response_text(response)
        return _parse_response(raw, request, line_items, used_web_search=True)
    except Exception as search_exc:
        # Responses API / web_search_preview unavailable on this account or
        # SDK version — fall back to a plain completion, but say so clearly
        # rather than silently presenting static-knowledge guesses as
        # web-verified stats.
        try:
            response = client.chat.completions.create(
                model=_FALLBACK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=5000,
                temperature=0.7,
            )
            raw = response.choices[0].message.content
            result = _parse_response(raw, request, line_items, used_web_search=False)
            if not result.error:
                result.error = (
                    "Web search wasn't available on this account "
                    f"({search_exc}); this used the model's general knowledge "
                    "instead — verify stats before relying on them."
                )
            return result
        except Exception as fallback_exc:
            return ProposalEnrichment(
                campaign_name=_fallback_campaign_name(request),
                error=f"Content generation failed: {fallback_exc}",
            )


def _extract_response_text(response) -> str:
    """
    Pull the text out of a Responses API result. `.output_text` is the
    SDK's convenience accessor; fall back to walking `.output` manually for
    older SDK versions that don't expose it.
    """
    text = getattr(response, "output_text", None)
    if text:
        return text
    chunks = []
    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            t = getattr(content, "text", None)
            if t:
                chunks.append(t)
    if chunks:
        return "\n".join(chunks)
    raise ValueError("Responses API returned no extractable text")


def _fallback_campaign_name(request) -> str:
    return request.client_name or "Campaign"


def reprompt_emails(
    request,
    line_items,
    campaign_name: str,
    current_internal_subject: str,
    current_internal_body: str,
    current_client_subject: str,
    current_client_body: str,
    reprompt: str,
) -> dict:
    """
    Step 07 — revise the internal + client-facing emails in place, based on
    the planner's final-review feedback (e.g. "make the client email
    shorter", "emphasize the Q4 start date"), WITHOUT touching campaign_name,
    product blurbs, or the already-generated Excel file/title — those are
    fixed by the time the planner is reviewing emails at this step.

    Returns {internal_email_subject, internal_email_body,
             client_email_subject, client_email_body, error}. On any
    failure, returns the ORIGINAL email content unchanged (with `error`
    set) rather than blanking it out.
    """
    def _unchanged(msg: str) -> dict:
        return {
            "internal_email_subject": current_internal_subject,
            "internal_email_body": current_internal_body,
            "client_email_subject": current_client_subject,
            "client_email_body": current_client_body,
            "error": msg,
        }

    api_key = os.getenv("OPENAI_API_KEY")
    if not _HAS_OPENAI:
        return _unchanged("openai package not installed — run: pip install openai")
    if not api_key:
        return _unchanged("OPENAI_API_KEY not set — reprompt skipped.")

    client = _OpenAI(api_key=api_key)
    total_budget = sum(li.monthly_budget * li.months for li in line_items)

    prompt = f"""You are revising two already-drafted emails for a digital media proposal, based on the planner's final review feedback. Respond ONLY with valid JSON — no preamble, no markdown fences.

## PROPOSAL CONTEXT
- Client: {request.client_name or "TBD"}
- Campaign: {campaign_name or "TBD"}
- Total Net Investment: ${total_budget:,.0f}

## CURRENT INTERNAL EMAIL (to the AE)
Subject: {current_internal_subject}
---
{current_internal_body}
---

## CURRENT CLIENT-FACING EMAIL
Subject: {current_client_subject}
---
{current_client_body}
---

## PLANNER'S REQUESTED CHANGE
{reprompt.strip()}

## YOUR TASK
Revise BOTH emails to incorporate the planner's requested change. Keep everything else about each email's structure, tone, and content the same unless the requested change implies otherwise.

CRITICAL — PRESERVE THESE LINES VERBATIM, EXACTLY AS WRITTEN, WHEREVER THEY APPEAR:
Any line starting with "Proposal:", "Presentation:", or "Google Drive Link:" is a system-inserted reference line, not AI-authored content — copy it into your revised email character-for-character, in the same position relative to the surrounding text. Never reword, remove, or relocate these lines even if the requested change is about tone or structure elsewhere in the email.

Respond with this exact JSON structure:
{{
  "internal_email_subject": "revised subject",
  "internal_email_body": "revised full internal email body",
  "client_email_subject": "revised subject",
  "client_email_body": "revised full client email body"
}}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=3000,
            temperature=0.6,
        )
        raw = response.choices[0].message.content or ""
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return _unchanged("No structured response received — emails left unchanged.")
        data = json.loads(match.group(0))
        return {
            "internal_email_subject": data.get("internal_email_subject") or current_internal_subject,
            "internal_email_body": _normalize_newlines(data.get("internal_email_body") or current_internal_body),
            "client_email_subject": data.get("client_email_subject") or current_client_subject,
            "client_email_body": _normalize_newlines(data.get("client_email_body") or current_client_body),
            "error": None,
        }
    except Exception as exc:
        return _unchanged(f"Reprompt failed: {exc}")


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _estimate_monthly_impressions(line_items) -> Optional[int]:
    """
    Best-effort total monthly impressions across a set of line items, using
    each product's buying model — the same math Excel itself uses in column
    I (see excel_template._write_product_row). Computed server-side and fed
    into the prompt as a real number rather than left for the model to
    guess, since a wrong impressions figure in a seller-facing email is
    worse than no figure at all. Returns None if nothing in the set
    supports an impressions estimate (e.g. all CPP, or no rate on file).
    """
    total = 0.0
    found_any = False
    for li in line_items:
        p = _catalog_by_name(li.product_name)
        if p is None:
            continue
        if p.buying_model == "CPM" and p.base_rate:
            total += li.monthly_budget / p.base_rate * 1000
            found_any = True
        elif (p.buying_model == "Fixed" or p.estimated_impressions) and p.estimated_cpm_for_imps:
            total += li.monthly_budget / p.estimated_cpm_for_imps * 1000
            found_any = True
        # CPP (rating points) and Fixed products without an estimated CPM
        # have no reliable impressions conversion — left out of the total
        # rather than guessed at.
    return int(round(total)) if found_any else None


def _format_impressions(n: Optional[int]) -> str:
    if not n:
        return ""
    if n >= 1_000_000:
        return f"~{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"~{round(n / 1000)}K"
    return f"~{n}"


def _build_prompt(request, line_items, strategy_brief: Optional[dict] = None,
                  tiers: Optional[list] = None) -> str:
    # Ground each product in what it ACTUALLY is (the catalog's own rate-
    # card description), not just its name + budget — the model had
    # nothing else to go on before this beyond the product's name and the
    # (also now-fixed) knowledge-base text, which is exactly how a Meta
    # blurb ended up describing a podcast: with no real per-product
    # description in context, "Facebook & Instagram Ads | Awareness" alone
    # wasn't enough of an anchor to keep every product's blurb from
    # drifting toward whatever else was sitting in the prompt.
    def _product_line(li) -> str:
        p = _catalog_by_name(li.product_name)
        desc = (p.proposal_description if p else "") or ""
        if len(desc) > 280:
            desc = desc[:280].rsplit(" ", 1)[0] + "…"
        desc_line = f"\n    What this actually is (from Entravision's own rate card — open the blurb grounded in this, don't guess): {desc}" if desc else ""
        return (
            f"  - {li.product_name}: ${li.monthly_budget:,.0f}/month × {li.months} months "
            f"= ${li.monthly_budget * li.months:,.0f} total{desc_line}"
        )

    items_text = "\n".join(_product_line(li) for li in line_items)
    total_budget = sum(li.monthly_budget * li.months for li in line_items)
    total_impressions = _estimate_monthly_impressions(line_items)

    # Multiple budget options ("tiers") — each becomes a lettered "Proposal
    # A/B/C/..." tab in the Excel export; the emails need to present them as
    # distinct options rather than describing one single plan.
    tiers_block = ""
    if tiers and len(tiers) > 1:
        option_lines = []
        for t in tiers:
            t_items = t.get("line_items") or []
            t_total = sum(li.monthly_budget * li.months for li in t_items)
            t_monthly = sum(li.monthly_budget for li in t_items)
            t_imps = _estimate_monthly_impressions(t_items)
            imps_str = f", {_format_impressions(t_imps)} monthly impressions" if t_imps else ""
            products_str = ", ".join(li.product_name for li in t_items) or "(no products)"
            option_lines.append(
                f"  - Option {t.get('label', '?')}: ${t_monthly:,.0f}/month "
                f"(${t_total:,.0f} total flight{imps_str}) — {products_str}"
            )
        tiers_block = f"""
## MULTIPLE BUDGET OPTIONS — THIS PROPOSAL HAS {len(tiers)} DISTINCT OPTIONS
The client is being presented {len(tiers)} alternative budget/product-mix
options (lettered to match their Excel tabs, "Proposal A", "Proposal B",
etc.). Do NOT describe this as a single plan — both emails must clearly lay
out EACH option separately (its own heading, budget, and product mix) so
the reader can compare them side by side. The monthly impressions figures
below (where given) are pre-computed from real rates — use them verbatim
if you reference impressions; never compute or guess your own.
{chr(10).join(option_lines)}
"""

    target_lines = []
    if request.demo:
        target_lines.append(f"  - Demographic: {request.demo}")
    if request.language:
        target_lines.append(f"  - Language: {request.language}")
    if request.geo:
        target_lines.append(f"  - Geography: {request.geo}")
    if request.behavioral:
        target_lines.append(f"  - Behavioral / audience segment: {request.behavioral}")
    if request.contextual:
        target_lines.append(f"  - Contextual environment: {request.contextual}")
    target_block = "\n".join(target_lines) if target_lines else "  - (not specified — infer a reasonable target from the client/category)"

    ae_raw = request.requested_by or request.salesperson_email or ""
    ae_first = ae_raw.split("@")[0].split(".")[0].replace("_", " ").title()

    is_hispanic = any(
        word in (request.language or "").lower() + (request.demo or "").lower() + (request.behavioral or "").lower()
        for word in ("hispanic", "spanish", "latino", "latina")
    )
    stat_guidance = (
        "Search the web for U.S. Hispanic-specific statistics (e.g., 'U.S. Hispanic CTV usage 2025', "
        "'Latino podcast listening 2024') — actually look them up, don't recall from memory."
        if is_hispanic
        else "Search the web for general market statistics from 2023–2026 — actually look them up, don't recall from memory."
    )

    # If the planner confirmed a Step 03 strategy brief, ground the blurbs in it
    # so the final proposal stays consistent with what was already reviewed —
    # reuse its per-tactic rationale/data instead of re-deriving from scratch.
    #
    # ONLY reuse tactics whose product_family is still actually in the
    # curated mix below. Step 03 runs before Step 04's curation — if the
    # planner swaps the mix afterward (e.g. drops the CTV tactic the brief
    # recommended and adds Email Marketing instead), a stale tactic for a
    # family that's no longer in the proposal has nothing to legitimately
    # attach to. Left unfiltered, that stale content (e.g. an "Audio
    # Engage: audio streaming is popular..." tactic) sits in context with
    # no other guardrail stopping the model from bleeding it into the
    # blurb for an unrelated product that isn't audio at all.
    curated_families = {
        p.family for p in (_catalog_by_name(li.product_name) for li in line_items) if p
    }
    strategy_block = ""
    if strategy_brief and strategy_brief.get("recommended_tactics"):
        tactic_lines = [
            f"  - {t.get('product_family', '')}: {t.get('rationale', '')} "
            f"Data: {t.get('data_point', '')} ({t.get('citation', '')})"
            for t in strategy_brief["recommended_tactics"]
            if not curated_families or t.get("product_family", "") in curated_families
        ]
        if tactic_lines:
            strategy_block = f"""
## CONFIRMED STRATEGY BRIEF (planner already reviewed and approved this — your
## product blurbs below MUST stay consistent with this rationale and reuse
## its data points where the product family matches; do not contradict it.
## Only tactics for families still in the current product mix are included
## below — if a product isn't listed here, write its blurb from the
## Target Audience / Knowledge Base sections instead, not from a tactic for
## a different product family)
{chr(10).join(tactic_lines)}
Overall direction: {strategy_brief.get('strategy_summary', '')}
"""

    # Only Full Presentation requests come with a separate presentation
    # deck deliverable (see main.py's "Presentation:" line insertion) — the
    # transition-line example below must not mention "presentation" for
    # any other request type, or the model reliably copies that example
    # phrase verbatim regardless of what's actually being delivered.
    is_full_presentation = "full presentation" in (request.request_type or "").lower()
    deliverable_example = (
        "Please see your requested plan and presentation in the links below:"
        if is_full_presentation else
        "Please see your requested plan in the link below:"
    )

    # Built as its own variable (not inline in the JSON block below) so its
    # own quoting doesn't have to fight the surrounding f-string's quoting —
    # a dash-based structure instead of numbered steps, so the optional
    # tiers-recommendation paragraph can be inserted or omitted without
    # having to renumber everything after it.
    internal_email_instruction = (
        f"Full internal email to {ae_first}, in a warm, casual, collegial voice — like a colleague sharing good news, not a stiff corporate memo. Write it as a sequence of short paragraphs, IN THIS ORDER: "
        f'— Open warmly and casually by first name, e.g. \\"Hi {ae_first}, hope your week is going well!\\" (vary the exact phrasing naturally each time, but always keep it warm/casual, never stiff/formal). '
        f'— A short transition line introducing the deliverable link(s) below, e.g. \\"{deliverable_example}\\". This request is {"a Full Presentation, so it DOES include a separate presentation deck" if is_full_presentation else "NOT a Full Presentation — do not mention a presentation, deck, or slides; there is only the one proposal/plan deliverable"}. '
        "— Immediately after that, on its own line, the literal placeholder text '{{PROPOSAL_LINE}}' (exactly these characters, nothing else on that line — it will be replaced with the real proposal name(s) and Drive link(s)). "
        "— One sentence noting the client-facing talking points are attached separately below, ready to copy and send once reviewed. "
        f"— A detailed strategy paragraph (2-4 sentences) that names the specific target audience/demo/geo by name, states the actual dollar budget split{' across every option and market/segment' if tiers_block else ''} using the REAL numbers given above (never invent or alter them), and names the recommended tactic/product-family strategy with a concrete reason grounded in the data above — never a generic restatement like 'reach the target audience.' "
        "— ONLY when the context above gives a real, specific basis for it (a stated timeline, seasonality, or creative consideration — never invented), a short paragraph with concrete creative/execution guidance: how messaging should evolve over the flight, or which ad lengths/formats suit which objective. Skip this paragraph entirely when there's no real basis for it above — never invent a campaign calendar or creative plan that isn't grounded in the given context. "
        + ("— A paragraph that explicitly recommends WHICH option the client should run when budget allows, and why (grounded in the reach/frequency tradeoff or channel-fragmentation risk visible in the data above), plus what to do if the client stays at the lower option instead. "
           if tiers_block else "")
        + "— Offer to adjust if needed. "
        "— Sign-off as 'Your Entravision Strategy Team'."
    )

    # Same reasoning as above — a separate variable so \" escaping needed
    # for the embedded "Option A"/"Option B" examples doesn't fight the
    # outer f-string. NOTE: the previous inline version of this ternary used
    # a single backslash (\") inside a Python string literal, which Python
    # itself consumes into a bare unescaped quote character in the actual
    # prompt text sent to the model — a real, separate bug from the one
    # above (this one predates the current feature; caught here by actually
    # validating the rendered prompt's JSON exemplar with json.loads()
    # rather than just eyeballing the source). \\" (double backslash) is
    # what's needed to make Python emit a literal backslash-quote pair.
    client_email_instruction = (
        "Full client-facing email (no internal references). Sections: (1) Opening paragraph on why digital matters now for this specific audience, (2) "
        + ('For EACH budget option: its own heading (\\"Option A\\", \\"Option B\\", ...), its total investment, and for each product in that option — product name, net budget, then the strategic blurb'
           if tiers_block else
           'Total investment line, then for EACH product: product name as heading, net budget, then the strategic blurb')
        + ", (3) 'What This Campaign Delivers' with 3 specific bullet points, (4) Call-to-action sentence inviting the client to pick an option"
        + (" and confirm" if tiers_block else "")
        + ", (5) Sign-off from AE name and email."
    )

    return f"""You are an expert media sales strategist at Entravision. Generate AI enrichment content for this digital media proposal. Respond ONLY with valid JSON — no preamble, no markdown fences, no trailing text.

## PROPOSAL CONTEXT
- Client: {request.client_name or "TBD"}
- Seller (AE): {request.requested_by or request.salesperson_email or "TBD"}
- AE Email: {request.salesperson_email or "TBD"}
- Campaign Goal: {request.campaign_goal or "Awareness"}
- Flight: {request.start_date or "TBD"} → {request.end_date or "TBD"} ({request.total_months or 3} months)
- Total Net Investment: ${total_budget:,.0f}{f" (~{_format_impressions(total_impressions)} monthly impressions — pre-computed from real rates, use verbatim, never recompute)" if total_impressions and not tiers_block else ""}
- Request Type: {request.request_type or "Proposal"}
- AE Comments: {request.salesperson_comments or "None"}
- Market: {request.salesperson_market or "TBD"}

## TARGET AUDIENCE (use these SPECIFIC values by name in every blurb — never
## a generic substitute like "the target audience" or "local consumers"):
{target_block}
{strategy_block}{tiers_block}
## PRODUCTS IN THIS PROPOSAL (union across every option, if more than one)
{items_text}

## ENTRAVISION KNOWLEDGE BASE (only the families actually in this proposal)
{_knowledge_base_block(curated_families)}

## STATISTICS GUIDANCE
{stat_guidance}
Prefer a statistic tied to the SPECIFIC audience/geo above over a generic
industry-wide number. Only use a generic market-wide stat when nothing more
specific is plausible, and say so if you do ("no audience-specific data
available, using general market benchmark").

## YOU HAVE LIVE WEB SEARCH — USE IT, DON'T GUESS
This is a real capability, not a hypothetical. Before writing each product
blurb, run an actual search for that stat — don't write a number that
merely sounds plausible for the category. If the client's website is
given above, search it too so the blurbs reflect what the client actually
does, not an assumption from the name. A citation you can't actually
verify via search should not be presented as sourced data.

BAD blurb (generic, reject this style): "Reach your target audience
through premium video content that drives engagement and results."
BAD blurb (fluent but WRONG PRODUCT — reject this style just as hard, even
though nothing about it individually reads as an error): a confident,
well-cited paragraph about podcast listenership written under a Meta/
Facebook product's name. Every product's own "What this actually is" line
above is the one and only source for what that product does — a stat or
Entravision advantage clearly written for a DIFFERENT family (podcasts,
CTV, DOOH, whatever isn't in that product's own description) never belongs
in this blurb, no matter how well it reads.
GOOD blurb (specific, required style): "[This product], per its own
description above, does [the specific thing it does — paraphrase, don't
just repeat the description verbatim]. For {request.demo or 'this demo'}
in {request.geo or 'this market'}, that matters because [real stat found
via search, tied to this product's actual category] (Source, Year).
Entravision's [specific advantage for THIS product's family] makes this
the right execution, not just a plausible one."

---

Return this exact JSON structure (no deviation):

{{
  "campaign_name": "Short memorable 4–6 word name in Title Case (e.g. 'Bill Luke July Awareness'). No quotes inside the string.",
  "product_blurbs": [
    {{
      "product_name": "exact product name as listed above",
      "blurb": "[What this product concretely is/does — grounded in its own 'What this actually is' line above, paraphrased not copied]. For [name the specific demo/geo/behavioral/contextual value from above], this matters because [specific recent stat tied to THAT audience and THIS product's real category, with year] (Source, Year). Entravision's [advantage from the knowledge base row for THIS product's own family] makes this the right execution for this client."
    }}
  ],
  "internal_email_subject": "Digital Strategy Pack: [Client] ([Month Year] Campaign)",
  "internal_email_body": "{internal_email_instruction}",
  "client_email_subject": "Maximizing Your Local Reach: [Month Year] Digital Strategy for [Client]",
  "client_email_body": "{client_email_instruction}"
}}

RULES:
- Each product blurb: 50–80 words, insightful (not a basic restatement of the category), include one real statistic with citation (Source Name, Year), and must name at least one specific targeting value from the Target Audience section above
- Before writing each blurb, re-read that product's own "What this actually is" line above and its own row in the Entravision Knowledge Base. That is the ONLY source of truth for what the product does and which Entravision advantage applies to it — not the product's name alone, not another product's blurb, not a family that merely sounds adjacent
- Every citation must name a real, specific, searchable source (publisher + year) you actually found via search — never a vague placeholder like "Industry Report, 2025." If you can't find a specific real source, don't present a number as sourced data — fold it into the blurb as directional context instead
- A blurb must accurately describe the NAMED product's own format/category, grounded in ITS OWN description above — e.g. never describe audio/podcast/streaming content for an email, display, or search product, or vice versa, even if that content is sitting elsewhere in this prompt for a different product. If the confirmed strategy brief above doesn't cover a product, base its blurb on the Target Audience section plus that product's own description and knowledge-base row — never borrow a rationale, stat, or example written for a different product family
- A product's blurb stays the SAME regardless of which option(s) it appears in — write it once per product, not once per option
- For Hispanic/Spanish targets: use U.S. Hispanic-specific stats
- Internal email: warm and collegial; do NOT include the client email body inline — just reference it
- Client email: professional but readable; absolutely no internal document references
- Do not mention a presentation, deck, or any deliverable that isn't actually part of this request (see Request Type above) — only reference what's really being delivered
- Campaign name: no quotes, no special characters
- Respond ONLY with the JSON object, starting with {{ and ending with }}"""


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------


def _parse_response(raw: str, request, line_items, used_web_search: bool = False) -> ProposalEnrichment:
    # Extract JSON object (defensive — model sometimes adds preamble despite instructions)
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return ProposalEnrichment(
            campaign_name=_fallback_campaign_name(request),
            used_web_search=used_web_search,
            error="No structured response received.",
        )

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return ProposalEnrichment(
            campaign_name=_fallback_campaign_name(request),
            used_web_search=used_web_search,
            error=f"JSON parse error: {exc}",
        )

    blurbs = []
    for pb in data.get("product_blurbs") or []:
        blurbs.append(ProductBlurb(
            product_name=pb.get("product_name", ""),
            blurb=_normalize_newlines(pb.get("blurb", "")),
        ))

    return ProposalEnrichment(
        campaign_name=data.get("campaign_name") or _fallback_campaign_name(request),
        product_blurbs=blurbs,
        internal_email_subject=data.get("internal_email_subject", ""),
        internal_email_body=_normalize_newlines(data.get("internal_email_body", "")),
        client_email_subject=data.get("client_email_subject", ""),
        client_email_body=_normalize_newlines(data.get("client_email_body", "")),
        used_web_search=used_web_search,
    )
