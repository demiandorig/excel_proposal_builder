"""
AI enrichment for the Entravision Proposal Builder.

Uses OpenAI gpt-5.1 (see _SEARCH_MODEL/_FALLBACK_MODEL below) to generate in
a single API call:
  - Campaign name (short, memorable, title-cased)
  - Per-product blurbs: one short plain paragraph on what the product is and
    does (25-55 words; at most one short channel fact, never audience/geo/
    language/variant talk — see PRODUCT BLURB RULES in _build_prompt)
  - Internal AE email (professional, friendly)
  - Client-facing email body (for the Word doc)

All features degrade gracefully when OPENAI_API_KEY is not set.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

from app.catalog import by_name as _catalog_by_name
from app.services import llm_utils
from app.services.text_utils import normalize_newlines as _normalize_newlines, split_sentences, strip_variant_pricing
from app.services.writing_style import HOUSE_VOICE_GUIDE

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
    blurb: str  # one short plain paragraph (<= _BLURB_MAX_WORDS) — what the product is/does


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


def _first_name_from_requested_by(requested_by: Optional[str], salesperson_email: Optional[str] = "") -> str:
    """The AE's first name for the internal email's opening greeting
    ("Hi {ae_first}, ..."). requested_by (the Notion paste's own
    "Requested by:" field) is a plain human name like "Lauren Sandford",
    NOT an email — the old .split("@")[0].split(".")[0] logic silently
    did nothing against a plain multi-word name (no "@" or "." to split
    on), which is why the greeting used to show the AE's FULL name
    instead of just their first. salesperson_email (the fallback when
    requested_by is blank) IS an email, so this still needs to handle
    that shape too — take the email local-part if there's an "@", else
    the raw string as-is, then split on whitespace/./_ and take the
    first token, covering both "Lauren Sandford" and
    "lauren.sandford@entravision.com" alike."""
    ae_raw = requested_by or salesperson_email or ""
    ae_local = ae_raw.split("@")[0].strip()
    return re.split(r"[.\s_]+", ae_local)[0].title() if ae_local else ""


def _display_name_from_email(email: str) -> str:
    """'irvin.villa@entravision.com' -> 'Irvin Villa'; 'irvin@entravision.com'
    (no dot to split on) -> just 'Irvin'. There's no real display-name
    field stored anywhere in this app (see schema.sql's users table —
    email/password/is_admin only) — this dot-separated-local-part
    convention is the same assumption this file already makes elsewhere
    for the AE greeting (see `ae_first` below), extended here to also
    capture a last-name segment when present, for a full-name signature."""
    local_part = (email or "").split("@")[0]
    segments = [s.replace("_", " ").title() for s in local_part.split(".") if s]
    return " ".join(segments)


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
    client_name: str = "",
) -> str:
    """
    Build the full proposal title following the Entravision naming convention:
      {ID} | {Client Name} - {Order Description} | Entravision | {MonYY} | {Doc Type}

    Example: "0042 | Texmex Curios - July Awareness Push | Entravision | Jun26 | Digital Media Proposal"

    client_name: prepended before campaign_name with a " - " separator —
    campaign_name (the AI's "order description," e.g. "July Awareness
    Push") is instructed to never include the client's own name (see
    _build_prompt's campaign_name schema/RULES entries), so this is the
    ONE place the client name appears rather than it showing up twice.
    Omitted/blank falls back to just the order description alone, same as
    before this param existed — every pre-existing caller that doesn't
    pass it keeps its exact prior title shape.

    doc_type_override: skip the request_type -> Doc Type inference and use
    this instead — e.g. "Digital Media Deck" for the companion presentation
    deck on a Full Presentation request, which is a separate deliverable
    from the Excel proposal and always carries that fixed Doc Type
    regardless of what request_type would otherwise map to.
    """
    order_description = _to_title_case(campaign_name.strip()) if campaign_name else "Campaign"
    client_name = (client_name or "").strip()
    # Don't double up if the order description somehow still starts with
    # the client's own name (a model that ignores the prompt instruction,
    # or a manual campaign_name_override) — comparison is case-insensitive
    # since Title Case may differ from however the client name is typed.
    if client_name and order_description.lower().startswith(client_name.lower()):
        title_name = order_description
    elif client_name:
        title_name = f"{client_name} - {order_description}"
    else:
        title_name = order_description
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

# Bumped from gpt-4.1 to gpt-5.1, OpenAI's current flagship as of Sep 2026
# — confirmed via live web search against OpenAI's own model docs, not
# guessed from static training knowledge (my own cutoff is Jan 2026), since
# a wrong model string here would hard-fail every call. Two things changed
# together with the model and must not be separated: (1) the Responses API
# tool below is now "web_search" — GPT-5-series models don't support the
# legacy "web_search_preview" this used to call, they error on it
# outright; (2) the chat.completions fallback below no longer passes
# `temperature=`, since GPT-5-series models reject any value but the
# default (1). Splitting these apart would silently kill web search
# (quietly falls through to the fallback) and then break the fallback too.
_SEARCH_MODEL = "gpt-5.1"
_FALLBACK_MODEL = "gpt-5.1"
# Caps count reasoning + visible tokens (gpt-5-mini reasons by default); llm_utils retries/flags truncation.
_ENRICH_MAX_OUTPUT_TOKENS = 20000
_REVISE_MODEL = "gpt-5-mini"
_REVISE_MAX_COMPLETION_TOKENS = 16000


def enrich_proposal(request, line_items, short_id: str, strategy_brief: Optional[dict] = None,
                    tiers: Optional[list] = None) -> ProposalEnrichment:
    """
    Call OpenAI to generate all AI enrichment for a proposal — campaign
    name, per-product blurbs (short plain descriptions), and both emails.
    Returns a ProposalEnrichment — empty fields (not an exception) on failure.

    Grounded in live web search (Responses API + web_search, same
    mechanism as the Roadblocks and Strategy Brief steps) so a blurb's
    "specific recent stat" is something actually found via search, not the
    model's static training-data guess — falls back to a plain (non-
    searching) completion, with a clear disclaimer, if the Responses API
    or the search tool isn't available on this account/SDK version.

    strategy_brief: the confirmed brief from the app's Step 03 (if the planner
    didn't skip it) — when present, the EMAILS stay consistent with the
    rationale/data the planner already reviewed and confirmed. Product blurbs
    deliberately don't reuse it: they stay a short plain description.

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
    truncated = ProposalEnrichment(
        campaign_name=_fallback_campaign_name(request),
        error="Content generation was cut off before it finished, even after an automatic retry.",
    )

    try:
        raw = llm_utils.responses_text(client, model=_SEARCH_MODEL, prompt=prompt,
                                       tools=[{"type": "web_search"}],
                                       max_output_tokens=_ENRICH_MAX_OUTPUT_TOKENS)
    except llm_utils.ResponseTruncated:
        return truncated
    except Exception as search_exc:
        try:
            raw = llm_utils.chat_text(client, model=_FALLBACK_MODEL, prompt=prompt,
                                      max_completion_tokens=_ENRICH_MAX_OUTPUT_TOKENS)
        except llm_utils.ResponseTruncated:
            return truncated
        except Exception as fallback_exc:
            return ProposalEnrichment(
                campaign_name=_fallback_campaign_name(request),
                error=f"Content generation failed: {fallback_exc}",
            )
        result = _parse_response(raw, request, line_items, used_web_search=False, client=client)
        if not result.error:
            result.error = (
                f"Live web search failed ({search_exc}); this used the model's general "
                "knowledge instead — verify stats before relying on them."
            )
        return result
    return _parse_response(raw, request, line_items, used_web_search=True, client=client)


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
    scope: str = "both",
) -> dict:
    """
    Step 07 — revise the internal + client-facing emails in place, based on
    the planner's final-review feedback (e.g. "make the client email
    shorter", "emphasize the Q4 start date"), WITHOUT touching campaign_name,
    product blurbs, or the already-generated Excel file/title — those are
    fixed by the time the planner is reviewing emails at this step.

    scope: "both" (default) revises both emails, the original behavior.
    "internal"/"client" revises only that one — enforced as a hard
    guardrail below (not just a prompt ask, same "don't trust the model to
    follow the instruction alone" reasoning strategy_brief.py's family
    filter uses): the OTHER email's subject/body is force-set back to its
    exact original value regardless of what the model returned, so a
    single-sided regenerate can never accidentally drift the email the
    planner didn't ask to change.

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

    # Built as its own plain variable, not inline in the f-string below —
    # an inline dict-literal/ternary with its own quotes nested inside an
    # f-string is exactly the class of prompt-string-escaping bug that
    # bit this codebase before (see AI writing-voice project notes on the
    # client_email_body ternary): easy to get the brace/quote nesting
    # subtly wrong and hard to catch without actually rendering the prompt.
    scope_instruction = {
        "internal": "Revise ONLY the INTERNAL email (to the AE) to incorporate the planner's requested change. Return the CLIENT-FACING email's subject and body EXACTLY as shown above, character-for-character unchanged — the planner only asked to change the internal one this time.",
        "client": "Revise ONLY the CLIENT-FACING email to incorporate the planner's requested change. Return the INTERNAL email's subject and body EXACTLY as shown above, character-for-character unchanged — the planner only asked to change the client-facing one this time.",
    }.get(scope, "Revise BOTH emails to incorporate the planner's requested change.")

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

{HOUSE_VOICE_GUIDE}

## YOUR TASK
{scope_instruction} Keep everything else about each email's structure, tone, and content the same unless the requested change implies otherwise — and if the email(s) you ARE revising have drifted toward the generic AI-sounding style the VOICE section above warns against, fix that too while you're in there, not just the requested change.

CRITICAL — PRESERVE THESE LINES VERBATIM, EXACTLY AS WRITTEN, WHEREVER THEY APPEAR:
Any line starting with "Proposal:", "Presentation:", or "Google Drive Link:" is a system-inserted reference line, not AI-authored content — copy it into your revised email character-for-character, in the same position relative to the surrounding text. Never reword, remove, or relocate these lines even if the requested change is about tone or structure elsewhere in the email.
The INTERNAL email's final two lines (a "{{Name}}, part of your digital strategy team" line followed by an email address line) are the real planner's system-inserted signature, not AI-authored content — keep them character-for-character, at the very end, exactly as given. Never invent a different sign-off in their place.

Respond with this exact JSON structure:
{{
  "internal_email_subject": "revised subject",
  "internal_email_body": "revised full internal email body",
  "client_email_subject": "revised subject",
  "client_email_body": "revised full client email body"
}}"""

    try:
        # No `temperature=` (GPT-5-series rejects it) and `max_completion_tokens`, not `max_tokens`.
        raw = llm_utils.chat_text(client, model=_REVISE_MODEL, prompt=prompt,
                                  max_completion_tokens=_REVISE_MAX_COMPLETION_TOKENS)
        data = llm_utils.parse_json_object(
            raw, expect_any=("internal_email_body", "client_email_body"), client=client)
        result = {
            "internal_email_subject": data.get("internal_email_subject") or current_internal_subject,
            "internal_email_body": _normalize_newlines(data.get("internal_email_body") or current_internal_body),
            "client_email_subject": data.get("client_email_subject") or current_client_subject,
            "client_email_body": _normalize_newlines(data.get("client_email_body") or current_client_body),
            "error": None,
        }
        # Hard guardrail, not just the prompt instruction above — force the
        # email OUTSIDE the requested scope back to its exact original
        # value regardless of what the model returned, so a single-sided
        # regenerate can never accidentally drift the other email.
        if scope == "internal":
            result["client_email_subject"] = current_client_subject
            result["client_email_body"] = current_client_body
        elif scope == "client":
            result["internal_email_subject"] = current_internal_subject
            result["internal_email_body"] = current_internal_body
        return result
    except llm_utils.ResponseTruncated:
        return _unchanged("The revision was cut off before it finished — emails left unchanged. Try again.")
    except Exception as exc:
        return _unchanged(f"Reprompt failed: {exc}")


def refine_gamma_outline(outline: str, reprompt: str) -> dict:
    """
    Revises the Gamma/media-strategy-co-pilot outline text based on the
    planner's free-text feedback — same "revise in place, don't
    regenerate from scratch" shape as reprompt_emails() above, but
    simpler: the outline itself (built entirely CLIENT-SIDE from data
    already on the page — see app.js's buildGammaOutline, which makes no
    AI call at all) IS the full context here, so there's no separate
    structured request/line-items payload to re-supply. This function is
    the ONLY AI call anywhere in the Gamma-outline feature — it only
    runs when the planner explicitly clicks "Refine", so the always-free
    base outline stays exactly that.

    Returns {outline, error}. On any failure, returns the ORIGINAL
    outline unchanged (with `error` set) rather than blanking it out —
    same failure-safety as reprompt_emails().
    """
    def _unchanged(msg: str) -> dict:
        return {"outline": outline, "error": msg}

    api_key = os.getenv("OPENAI_API_KEY")
    if not _HAS_OPENAI:
        return _unchanged("openai package not installed — run: pip install openai")
    if not api_key:
        return _unchanged("OPENAI_API_KEY not set — reprompt skipped.")

    client = _OpenAI(api_key=api_key)
    prompt = f"""You are revising a plain-text media-plan outline (a handoff doc fed into a separate presentation-building co-pilot, not shown to the client directly) based on the planner's feedback. Respond ONLY with valid JSON — no preamble, no markdown fences.

## CURRENT OUTLINE
{outline}

## PLANNER'S REQUESTED CHANGE
{reprompt.strip()}

## YOUR TASK
Revise the outline to incorporate the planner's requested change. Keep
its section headers and overall structure, and keep every real number,
date, product name, and client detail EXACTLY as given — never invent or
alter a figure that's already in the outline. Change only what the
planner actually asked for.

Respond with this exact JSON structure:
{{"outline": "the full revised outline text"}}"""

    try:
        raw = llm_utils.chat_text(client, model=_REVISE_MODEL, prompt=prompt,
                                  max_completion_tokens=_REVISE_MAX_COMPLETION_TOKENS)
        data = llm_utils.parse_json_object(raw, expect_any=("outline",), client=client)
        revised = data.get("outline")
        if not revised or not isinstance(revised, str):
            return _unchanged("No structured response received — outline left unchanged.")
        return {"outline": _normalize_newlines(revised), "error": None}
    except llm_utils.ResponseTruncated:
        return _unchanged("The revision was cut off before it finished — outline left unchanged. Try again.")
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
        desc = strip_variant_pricing(p.proposal_description if p else "")
        if len(desc) > 280:
            cut = desc[:280]
            # end on a sentence boundary when there is one, so the prompt doesn't dangle on a half-sentence that
            # cues exactly the format menus (skippable, tiers) the blurb rules forbid
            cut_at = max(cut.rfind(". "), cut.rfind(".\n"))
            desc = cut[:cut_at + 1] if cut_at >= 80 else cut.rsplit(" ", 1)[0] + "…"
        desc_line = (
            f"\n    What this actually is (from Entravision's own rate card — ground the blurb in this, don't guess): {desc}"
            if desc else
            "\n    What this actually is: (no rate-card description on file — describe it in one plain, generic sentence from its "
            "name and its Knowledge Base row only; invent no specifics)"
        )
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
            option_name = (t.get("name") or "").strip() or f"Option {t.get('label', '?')}"
            option_lines.append(
                f"  - {option_name}: ${t_monthly:,.0f}/month "
                f"(${t_total:,.0f} total flight{imps_str}) — {products_str}"
            )
        tiers_block = f"""
## MULTIPLE BUDGET OPTIONS — THIS PROPOSAL HAS {len(tiers)} DISTINCT OPTIONS
The client is being presented {len(tiers)} alternative budget/product-mix
options (lettered to match their Excel tabs, "Proposal A", "Proposal B",
etc., internally — but each is ALSO given a planner-chosen name below, e.g.
"Independent" or "Democrat" for a political client). Do NOT describe this
as a single plan — both emails must clearly lay out EACH option separately
(its own heading, budget, and product mix) so the reader can compare them
side by side. **Use the exact option name given below as its heading** —
never the generic "Option A"/"Option B" unless that's literally what's
given (no planner name set). The monthly impressions figures below (where
given) are pre-computed from real rates — use them verbatim if you
reference impressions; never compute or guess your own.
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

    ae_first = _first_name_from_requested_by(request.requested_by, request.salesperson_email)

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

    # If the planner confirmed a Step 03 strategy brief, keep the EMAILS consistent
    # with what was already reviewed — reuse its per-tactic rationale/data instead of
    # re-deriving from scratch. (Product blurbs don't use it; see PRODUCT BLURB RULES.)
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
## CONFIRMED STRATEGY BRIEF (planner already reviewed and approved this — the
## EMAILS below MUST stay consistent with this rationale and may reuse its data
## points where the product family matches; do not contradict it. Product
## blurbs do NOT reuse it: they stay a plain description of the product. Only
## tactics for families still in the current product mix are included below)
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
        f"Full internal email to {ae_first}. Follow the VOICE section above exactly — this email is precisely the kind of writing it describes: explain the REAL thinking behind the plan (why this mix, why this budget split, any real constraint or trade-off that shaped it) the way you'd actually explain it to a colleague, grounded in the specific numbers already given above, never generic praise or a restated summary of the plan. Write it as a sequence of short paragraphs, IN THIS ORDER: "
        f'— Open warmly and casually by first name, e.g. \\"Hi {ae_first}, hope your week is going well!\\" (vary the exact phrasing naturally each time, but always keep it warm/casual, never stiff/formal). '
        f'— A short transition line introducing the deliverable link(s) below, e.g. \\"{deliverable_example}\\". This request is {"a Full Presentation, so it DOES include a separate presentation deck" if is_full_presentation else "NOT a Full Presentation — do not mention a presentation, deck, or slides; there is only the one proposal/plan deliverable"}. '
        "— Immediately after that, on its own line, the literal placeholder text '{{PROPOSAL_LINE}}' (exactly these characters, nothing else on that line — it will be replaced with the real proposal name(s) and Drive link(s)). "
        "— One sentence noting the client-facing talking points are attached separately below, ready to copy and send once reviewed. "
        f"— A detailed strategy paragraph (2-4 sentences) that names the specific target audience/demo/geo by name, states the actual dollar budget split{' across every option and market/segment' if tiers_block else ''} using the REAL numbers given above (never invent or alter them), and names the recommended tactic/product-family strategy with a concrete reason grounded in the data above — never a generic restatement like 'reach the target audience.' If the context above shows a real constraint that shaped this mix (a minimum-spend limit, an inventory or reach ceiling, a channel deliberately weighted down or up to fit the budget), say so plainly and explain the trade-off, the way the VOICE example does — that reads as real judgment, not a generic pitch. "
        "— ONLY when the context above gives a real, specific basis for it (a stated timeline, seasonality, or creative consideration — never invented), a short paragraph with concrete creative/execution guidance: how messaging should evolve over the flight, or which ad lengths/formats suit which objective. Skip this paragraph entirely when there's no real basis for it above — never invent a campaign calendar or creative plan that isn't grounded in the given context. "
        + ("— A paragraph that explicitly recommends WHICH option the client should run when budget allows, and why (grounded in the reach/frequency tradeoff or channel-fragmentation risk visible in the data above), plus what to do if the client stays at the lower option instead. "
           if tiers_block else "")
        + "— Offer to adjust if needed. "
        "— End with, on its own final line, the literal placeholder text '{{PLANNER_SIGNATURE}}' "
        "(exactly these characters, nothing else on that line, no sign-off text of your own before or after it — "
        "it will be replaced with the real planner's name/role and email)."
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
        "Full client-facing email (no internal references). Follow the VOICE section above throughout — specific and grounded in this client's real numbers and targeting, never generic. Sections: (1) Opening paragraph on why digital matters now for this specific audience, (2) "
        + ('For EACH budget option: its own heading (\\"Option A\\", \\"Option B\\", ...), its total investment, and for each product in that option — product name, net budget, then a short plain description of that product'
           if tiers_block else
           'Total investment line, then for EACH product: product name as heading, net budget, then a short plain description of that product')
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
- Question Details: {getattr(request, "question_details", "") or "None"}
- Market: {request.salesperson_market or "TBD"}

## TARGET AUDIENCE (background for the EMAILS and the campaign name — use these
## specific values by name there, never a generic substitute like "the target
## audience". Do NOT repeat any of it in a product blurb: audience, geography
## and language each already have their own column in the proposal):
{target_block}
{strategy_block}{tiers_block}
## PRODUCTS IN THIS PROPOSAL (union across every option, if more than one)
{items_text}

## ENTRAVISION KNOWLEDGE BASE (only the families actually in this proposal)
{_knowledge_base_block(curated_families)}

## STATISTICS GUIDANCE (for the EMAILS — product blurbs follow their own, tighter rule below)
{stat_guidance}
Prefer a statistic tied to the SPECIFIC audience/geo above over a generic
industry-wide number. Only use a generic market-wide stat when nothing more
specific is plausible, and say so if you do ("no audience-specific data
available, using general market benchmark").

## PRODUCT BLURBS — WHAT THEY ARE FOR
Each blurb becomes the short DESCRIPTION cell for that product in the proposal
spreadsheet, sitting beside separate columns that already show the target
audience, geography, flight dates, sizes and rates. So a blurb must add what
those columns don't: plainly what this product is and does, in terms a client
who has never bought it would understand (the rate-card sentence is printed directly above your blurb in the same cell, so do not restate it — say what the buyer actually gets and how it reaches people) — and, ONLY when one genuinely helps,
a single short, real fact about the channel itself (how people use the medium,
why the format works). Never a fact about this client's audience or market.

## YOU HAVE LIVE WEB SEARCH — USE IT WHEN A STAT GENUINELY HELPS, DON'T FORCE IT
This is a real capability, not a hypothetical. For the emails, search for a
real external stat when it would strengthen a point rather than writing a
number that merely sounds plausible. For a product blurb a plain description
with no stat at all is the NORMAL, good outcome. If the client's website is
given above, search it too so the emails reflect what the client actually
does, not an assumption from the name. A citation you can't actually verify
via search should not be presented as sourced data.

{HOUSE_VOICE_GUIDE}

The voice guide above governs the EMAILS. Product blurbs are the one deliberate
exception: they are a short, plain product description (see PRODUCT BLURB RULES
below), not a reasoned argument — don't apply the budget-split / trade-off
reasoning to them.

BAD blurb (too long, and repeats what the targeting columns already say): "For
Hispanic adults 25-54 in Los Angeles, premium CTV matters because ... (Source,
2025). Entravision's expertise makes this the right execution."
BAD blurb (talks about a variant or a menu of formats the client didn't buy):
"This is the Hispanic-focused version of our CTV product." / "Skippable and
non-skippable formats are available, though skippable is recommended."
BAD blurb (language claim about the inventory): "Runs on Spanish-language sites
and apps."
BAD blurb (fluent but WRONG PRODUCT — reject this just as hard): a confident
paragraph about podcast listenership written under a Meta/Facebook product's
name. Every product's own "What this actually is" line above is the one and
only source for what that product does.
GOOD blurb (plain, no stat — an equally good outcome): "Banner ads shown across
a network of websites and apps, retargeting local shoppers as they browse so
your offer stays in front of them after they leave your site."
GOOD blurb (plain, a different family): "Text ads that appear on Google when
people search for what you offer, so your business shows up at the moment
they're ready to call or visit."
GOOD blurb (one relevant channel fact, only when it genuinely helps): "[One or
two plain sentences on what the product is and does.] [One short, real, searched
fact about the channel itself.] (Source, Year)"

Do NOT close a blurb with a generic "Entravision's [X] advantage/expertise
makes this the right execution/choice for this client" sentence — cut it
outright rather than reword it.

---

Return this exact JSON structure (no deviation):

{{
  "campaign_name": "Short memorable 4–6 word name in Title Case describing the CAMPAIGN ITSELF — objective, timing, and/or audience (e.g. 'July Awareness Push', 'Back-to-School Conquesting'). Do NOT include the client/advertiser's own name — it's already shown separately alongside this. No quotes inside the string.",
  "product_blurbs": [
    {{
      "product_name": "exact product name as listed above",
      "blurb": "[One or two plain sentences: what this product is and does, grounded in its own 'What this actually is' line above but adding what the buyer gets and how it reaches people — not a restatement of that line. Optionally end with ONE short, real fact about the channel itself — only if it genuinely helps the client — as (Source, Year). No audience, geo, language, variant or format talk.]"
    }}
  ],
  "internal_email_subject": "Digital Strategy Pack: [Client] ([Month Year] Campaign)",
  "internal_email_body": "{internal_email_instruction}",
  "client_email_subject": "Maximizing Your Local Reach: [Month Year] Digital Strategy for [Client]",
  "client_email_body": "{client_email_instruction}"
}}

RULES:
PRODUCT BLURB RULES (for the product_blurbs field only — these override anything above that sounds longer or more elaborate when you write a blurb; the emails follow the voice and statistics guidance above):
- Each product blurb is ONE short paragraph, 25–55 words, plain and concrete, no filler: what the product is and what it does. Add a single short channel fact only if it genuinely helps the client; otherwise stop after the description. A blurb with no stat and no citation is the normal outcome
- A blurb must tell the reader something the product name and the other columns don't already say — what the buyer actually gets and how it reaches people, in plain words. Don't just restate the name ("Search - SEM is search engine marketing") or copy the rate-card description line word for word
- Do NOT repeat the audience, geography, language, demographic, behavioral or contextual targeting, flight dates, budget, sizes or placement in a blurb — those already have their own columns in the proposal
- In a blurb, describe ONLY the product as named. Never mention variants, tiers, versions or format options (e.g. "Hispanic version", "standard vs. custom", "skippable vs. non-skippable"), and never recommend one over another. If the description above lists several, describe the product generally and don't enumerate them
- Never make claims about the language of the content, sites, apps, stations or publishers a product runs on (e.g. "Spanish-language sites", "English-only inventory") unless the product's own name states it. Targeting a language audience is not the same as the inventory being in that language
- In a blurb, any fact must be about the channel/format itself (never the client's audience or market), found via search, with a real named source and year — if you can't find one, leave it out
- Never close a blurb with a generic "Entravision's [X] expertise/advantage makes this the right execution/choice for this client" sentence — cut it outright rather than reword it. A sentence that would read the same with the product name swapped out doesn't belong
- Before writing each blurb, re-read that product's own "What this actually is" line above and its own row in the Entravision Knowledge Base. They are the ONLY source of truth for what the product does — not the product's name alone, not another product's blurb, not a family that merely sounds adjacent. Use the Knowledge Base row only to decide WHICH capability applies; never copy its numbers, audience, language or geography wording into a blurb (the rows describe Entravision's network, and those details have their own columns or belong in the emails)
- Every citation must name a real, specific, searchable source (publisher + year) you actually found via search — never a vague placeholder like "Industry Report, 2025." If you can't find a specific real source, leave the number out
- A blurb must accurately describe the NAMED product's own format/category, grounded in ITS OWN description above — e.g. never describe audio/podcast/streaming content for an email, display, or search product, or vice versa, even if that content is sitting elsewhere in this prompt for a different product. Base every blurb on that product's own description and knowledge-base row only — never on the Target Audience section, and never borrow a rationale, stat, or example written for a different product family
- A product's blurb stays the SAME regardless of which option(s) it appears in — write it once per product, not once per option

EMAIL AND OUTPUT RULES:
- Internal email: warm and collegial; do NOT include the client email body inline — just reference it
- Client email: professional but readable; absolutely no internal document references
- Do not mention a presentation, deck, or any deliverable that isn't actually part of this request (see Request Type above) — only reference what's really being delivered
- Campaign name: no quotes, no special characters, and must NOT contain the client/advertiser's name (it's combined with the client name separately downstream — including it here would repeat it)
- Respond ONLY with the JSON object, starting with {{ and ending with }}"""


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------


# Hard cap on a blurb's length. The prompt asks for 25-55 words; this is the
# ceiling past which trailing sentences are dropped (never cut mid-sentence).
_BLURB_MAX_WORDS = 70

# Things the prompt forbids in a blurb (variants/"versions", skippable-format
# menus). A model occasionally writes them anyway, so any sentence containing
# one is dropped rather than trusting compliance — a wrong claim in a
# client-facing cell is worse than a missing sentence. "skippable" is allowed
# when the product's own name says it.
_BLURB_FORBIDDEN = re.compile(
    r"\bvariants?\b"
    r"|\b(?:hispanic|spanish|english|custom|standard|premium|basic|lite|pro|enhanced)(?:[- ]\w+)? versions?\b|\bversions of (?:this|our|the)\b"
    r"|\b(?:non-?skippable|skippable)\b",
    re.IGNORECASE,
)
# Claims about the LANGUAGE OF THE INVENTORY ("Spanish-language radio stations", "Spanish-only sites").
# Targeting a language audience isn't the same as the content being in that language, so these are never
# made — unless the product's own name states the language.
_LANGUAGE_CLAIM = re.compile(
    r"\b(?:spanish|english)[- ]language\s+(?:[\w-]+\s+){0,2}?(?:sites?|websites?|publishers?|content|properties|apps?|stations?|"
    r"inventory|radio|podcasts?|programming|media|channels?|networks?|streams?|outlets?|platforms?)\b"
    r"|\b(?:spanish|english)[- ]only\b",
    re.IGNORECASE,
)
# Only a product name that STATES a language exempts it — "Hispanics CTV" or "...Espacio Latino Hub" names an
# audience, not the language of the inventory.
_LANGUAGE_IN_NAME = re.compile(r"\b(?:spanish|english)\b", re.IGNORECASE)
def _split_sentences(text: str) -> list[str]:
    """Sentences of a blurb — abbreviation-aware, with a citation kept together with the sentence it supports
    (so dropping, keeping and word-counting treat the pair as one unit)."""
    return [seg.strip() for seg in split_sentences(text) if seg.strip()]


# One-word values that are ordinary descriptive words, not places or audiences, never count as a repeat.
_GENERIC_TARGET_WORDS = {
    "local", "national", "nationwide", "regional", "statewide", "mobile", "adults", "adult", "women", "men", "families",
    "general", "market", "markets", "hispanic", "latino", "latina", "spanish", "english", "bilingual", "all", "usa",
}


def _targeting_phrases(request) -> list[str]:
    """The request's own geo / demo values, as phrases a blurb must not repeat — those already have their own
    columns in the proposal. Short values and one-word generic descriptors ("Local", "Hispanic") are skipped."""
    out: list[str] = []
    for field in ("geo", "demo"):
        raw = str(getattr(request, field, "") or "")
        for part in re.split(r"[;,|/\n]+|\band\b", raw, flags=re.IGNORECASE):
            part = part.strip(" .-()")
            variants = {part, re.sub(r"\b(?:dma|msa)\b", "", part, flags=re.IGNORECASE).strip(" .-()")}
            for v in variants:
                v = re.sub(r"\s+", " ", v).lower()
                if len(v) < 5 or v in out or (" " not in v and v in _GENERIC_TARGET_WORDS):
                    continue
                out.append(v)
    return out[:200]


def _compile_avoid(phrases) -> Optional["re.Pattern"]:
    """One alternation compiled once (a per-phrase pattern would blow Python's regex cache on a long ZIP list)."""
    phrases = [p for p in phrases if p]
    if not phrases:
        return None
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(p) for p in phrases) + r")(?![a-z0-9])")


def _blurb_sentence_ok(sentence: str, product_name: str, avoid: Optional["re.Pattern"] = None) -> bool:
    name = (product_name or "").lower()
    if avoid is not None and avoid.search(sentence.lower()):
        return False
    for m in _BLURB_FORBIDDEN.finditer(sentence):
        if "skippable" in m.group(0).lower() and "skippable" in name:
            continue
        return False
    if _LANGUAGE_CLAIM.search(sentence) and not _LANGUAGE_IN_NAME.search(name):
        return False
    return True


def _tighten_blurb(text: str, product_name: str = "", avoid: Optional["re.Pattern"] = None) -> str:
    """Enforces the blurb rules the prompt asks for: drops sentences that
    mention variants / language-of-inventory claims / skippable-format menus
    or repeat the request's own geo / demo (together with the citation that
    supported them), then keeps leading
    sentences up to _BLURB_MAX_WORDS. Returns "" if every sentence was
    disallowed (the export then shows just the catalog text)."""
    text = (text or "").strip()
    if not text:
        return ""
    kept = [s for s in _split_sentences(text) if _blurb_sentence_ok(s, product_name, avoid)]
    out, words = [], 0
    for sentence in kept:
        count = len(sentence.split())
        if out and words + count > _BLURB_MAX_WORDS:
            break
        out.append(sentence)
        words += count
    return " ".join(out)


def _parse_response(raw: str, request, line_items, used_web_search: bool = False,
                    client=None) -> ProposalEnrichment:
    try:
        data = llm_utils.parse_json_object(
            raw, expect_any=("campaign_name", "product_blurbs", "internal_email_body", "client_email_body"),
            client=client)
    except ValueError as exc:
        return ProposalEnrichment(
            campaign_name=_fallback_campaign_name(request),
            used_web_search=used_web_search,
            error=str(exc),
        )

    blurbs = []
    avoid = _compile_avoid(_targeting_phrases(request))
    for pb in data.get("product_blurbs") or []:
        if not isinstance(pb, dict):
            continue
        name, blurb = pb.get("product_name", ""), pb.get("blurb", "")
        if not isinstance(name, str) or not isinstance(blurb, str):
            continue   # a malformed entry costs that one blurb, not the whole enrichment (emails, campaign name)
        blurbs.append(ProductBlurb(product_name=name, blurb=_tighten_blurb(_normalize_newlines(blurb), name, avoid)))

    return ProposalEnrichment(
        campaign_name=data.get("campaign_name") or _fallback_campaign_name(request),
        product_blurbs=blurbs,
        internal_email_subject=data.get("internal_email_subject", ""),
        internal_email_body=_normalize_newlines(data.get("internal_email_body", "")),
        client_email_subject=data.get("client_email_subject", ""),
        client_email_body=_normalize_newlines(data.get("client_email_body", "")),
        used_web_search=used_web_search,
    )
