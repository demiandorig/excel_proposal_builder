"""
AI Strategy Brief generator for Entravision Proposal Builder.

Runs before the product curation step. Given the parsed ProposalRequest, it:
  1. Researches the client / business category using the model's knowledge
  2. Analyzes campaign objectives and audience
  3. Recommends 2-5 media tactics with data-backed rationale and budget splits
  4. Supports reprompting — user can correct context and regenerate

The confirmed brief feeds back into the recommender (suggest-mix) so allocations
are AI-informed rather than purely rule-based.
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

try:
    from openai import OpenAI as _OpenAI
    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False

from app.services import llm_utils
from app.services.text_utils import normalize_newlines as _normalize_newlines
from app.services.writing_style import HOUSE_VOICE_GUIDE
from app.catalog import by_name as _catalog_by_name


# Entravision catalog families + one-line description for the prompt. Keyed
# by the EXACT family string every Product in the catalog carries (see
# catalog.py's own `family=` values) — this used to show a friendlier
# label ("CTV / OTT") instead of the real family name ("Entravision
# Plus"), which meant a model instructed to echo "the exact family name
# from the catalog list above" (see the JSON schema below) would return a
# product_family that _recommend_from_brief()'s by_family() lookup could
# never match, silently dropping that tactic. Real names throughout now —
# also required so generate_brief()'s allowed_families guardrail (derived
# from the SAME real family strings via by_name(...).family) can compare
# apples to apples.
_CATALOG_FAMILY_DESCRIPTIONS = {
    "Search": "Paid search (SEM) & Performance Max — captures intent-driven clicks, high conversion rate",
    "Display": "Programmatic banner & geo-fence display — local awareness, retargeting, low CPM",
    "Online Video": "Pre-roll OLV & YouTube Ads — brand storytelling, high completion rates",
    "Entravision Plus": "Connected TV / streaming (CTV/OTT) — premium non-skippable, living-room screen",
    "Audio": "Digital radio, AudioEngage podcast network, Spotify — commuter and daily-routine reach",
    "Social": "Meta Ads (FB/IG), TikTok, LinkedIn — audience targeting, engagement, UGC-friendly",
    "Email": "Email marketing & display retargeting — nurturing, conversion, owned audience",
    "DOOH": "Digital out-of-home screens — ambient local presence, high-traffic locations",
    "Services": "Landing pages, creative production — support and conversion assets",
    "Measurement": "Brand lift, attribution, call tracking, foot traffic — ROI validation",
}


def _catalog_families_block(families: Optional[set] = None) -> str:
    """Renders the family list for the prompt — every family when `families`
    is None, or just that subset (in the dict's own canonical order) when
    given. Falls back to the full list if `families` filters out everything
    recognizable (e.g. every selected product is in a family this prompt
    doesn't offer, like Branded Content/Sponsorships) rather than showing
    the model an empty, meaningless section."""
    keys = [k for k in _CATALOG_FAMILY_DESCRIPTIONS if families is None or k in families]
    if not keys:
        keys = list(_CATALOG_FAMILY_DESCRIPTIONS.keys())
    return "\n".join(f"- {k}: {_CATALOG_FAMILY_DESCRIPTIONS[k]}" for k in keys)

_ENTRAVISION_KB = """
CTV/OTT (Entravision Plus): Entravision's advanced programmatic and CTV/OTT advertising capabilities with premium publisher partnerships.
Audio (AudioEngage): Entravision's leading digital audio and podcast network — 160MM general market reach, 45MM Hispanic coverage.
Social (Meta/TikTok): Entravision's owned-and-operated Spanish-language properties and deep expertise in culturally relevant bilingual content.
SEM/Search: Entravision's certified SEM team and proprietary bidding strategies optimized for local market dominance.
General: Entravision's deep expertise in creating culturally relevant, bilingual content that resonates authentically with the target audience.
""".strip()


# Bumped from gpt-4o to gpt-5.1, OpenAI's current flagship as of Sep 2026 —
# confirmed via live web search against OpenAI's own model docs, not
# guessed from static training knowledge, since a wrong model string here
# would hard-fail every call. Two things changed together with the model
# and must not be separated: (1) the Responses API tool below is now
# "web_search" — GPT-5-series models don't support the legacy
# "web_search_preview" this used to call, they error on it outright; (2)
# the chat.completions fallback below no longer passes `temperature=`,
# since GPT-5-series models reject any value but the default (1).
# Splitting these apart would silently kill web search (quietly falls
# through to the fallback) and then break the fallback too.
_SEARCH_MODEL = "gpt-5.1"
_FALLBACK_MODEL = "gpt-5.1"
# Reasoning + visible tokens share this cap; a full brief has run ~12k chars (~3k tokens) and
# an old 3,000 cap truncated it mid-object. llm_utils retries once at double this if still cut off.
_MAX_OUTPUT_TOKENS = 16000
_BRIEF_KEYS = ("client_summary", "strategy_summary", "recommended_tactics", "key_insights")
_TRUNCATED_MSG = "The brief was cut off before it finished, even after an automatic retry."


async def generate_brief(request, reprompt: Optional[str] = None, mode: str = "consistent",
                         ad_presence: Optional[dict] = None) -> dict:
    """
    Generate (or regenerate with reprompt) a strategic brief for this proposal.

    ad_presence: the result of an optional, separately-run /api/ad-presence check. When
    given, it's folded into the prompt and echoed back on the result; the brief itself
    no longer runs that check.

    mode: "consistent" (default) constrains recommended_tactics to the
    product FAMILIES already present in request.products_selected (Step
    02's parse) — both by only showing the model those families in the
    prompt, and by hard-filtering the response afterward in case it
    ignores that instruction. This keeps the brief from steering a
    planner toward a family they never selected upstream. "new_mix" skips
    the constraint entirely — today's original unconstrained behavior,
    for when the planner explicitly wants a from-scratch recommendation
    instead of a rationale for what's already selected. Any other value
    falls back to "consistent". Falls back to unconstrained even in
    "consistent" mode when products_selected is empty or maps to no
    recognizable family — "stay consistent with nothing selected" has no
    meaningful constraint to apply.

    Grounded in live web search (Responses API + the web_search tool); falls back
    to a plain completion, with a disclaimer in `error`, if search fails.

    Returns a dict with keys:
      client_summary, market_context, objectives_analysis, strategy_summary,
      recommended_tactics (list), key_insights (list), used_web_search (bool),
      error (str|None), and ad_presence (dict) when one was passed in.
    """
    api_key = os.getenv("OPENAI_API_KEY")

    if not _HAS_OPENAI:
        return _error_brief("openai package not installed — run: pip install openai")
    if not api_key:
        return _error_brief("OPENAI_API_KEY not set.")

    ad_intel = ad_presence if (ad_presence and ad_presence.get("summary")) else None

    allowed_families = None
    if mode != "new_mix":
        selected = getattr(request, "products_selected", None) or []
        families = {
            p.family for p in (_catalog_by_name(name) for name in selected)
            if p and p.family in _CATALOG_FAMILY_DESCRIPTIONS
        }
        if families:
            allowed_families = families
        # else: nothing parsed/recognizable to be consistent WITH — falls
        # through to the unconstrained prompt/no guardrail, same as
        # mode="new_mix", rather than constraining to an empty set.

    client = _OpenAI(api_key=api_key)
    prompt = _build_prompt(request, reprompt, ad_intel=ad_intel, allowed_families=allowed_families)
    # The OpenAI client is synchronous; off the event loop so one brief can't stall every other request.
    result = await asyncio.to_thread(_run_brief_call, client, prompt)

    if allowed_families and result.get("recommended_tactics"):
        # Hard guardrail, not just a prompt ask — the model can still
        # ignore the constraint above (or, per the module comment on
        # _CATALOG_FAMILY_DESCRIPTIONS, echo a family name that's close
        # but not exact). Whatever slips through gets dropped here rather
        # than reaching Step 04's "Suggest Mix" and seeding a product
        # family the planner never selected.
        filtered = [
            t for t in result["recommended_tactics"]
            if t.get("product_family") in allowed_families
        ]
        # ...but never let this filter empty the whole list out — a real,
        # confirmed case: with only one allowed family (a single-family
        # product selection, e.g. two Audio products), the model gave
        # several genuinely on-topic tactics but labeled product_family
        # with a specific sub-product/channel name instead of the family
        # itself, so an exact-match filter dropped every single one,
        # leaving the brief's tactics section completely blank even
        # though the free-text strategy_summary was clearly on-scope. An
        # unfiltered-but-imperfectly-labeled set of tactics the planner
        # can see and edit is a far better failure mode than an empty
        # section with no explanation.
        result["recommended_tactics"] = filtered or result["recommended_tactics"]

    if ad_presence:
        result["ad_presence"] = ad_presence
    return result


def _run_brief_call(client, prompt: str) -> dict:
    try:
        raw = llm_utils.responses_text(client, model=_SEARCH_MODEL, prompt=prompt,
                                       tools=[{"type": "web_search"}],
                                       max_output_tokens=_MAX_OUTPUT_TOKENS)
    except llm_utils.ResponseTruncated:
        return _error_brief(_TRUNCATED_MSG)
    except Exception as search_exc:
        try:
            raw = llm_utils.chat_text(client, model=_FALLBACK_MODEL, prompt=prompt,
                                      max_completion_tokens=_MAX_OUTPUT_TOKENS)
        except llm_utils.ResponseTruncated:
            return _error_brief(_TRUNCATED_MSG)
        except Exception as fallback_exc:
            return _error_brief(f"Request failed: {fallback_exc}")
        result = _parse(raw, used_web_search=False, client=client)
        if not result.get("error"):
            result["error"] = (
                f"Live web search failed ({search_exc}); this used the model's general "
                "knowledge instead — verify stats before relying on them."
            )
        return result
    return _parse(raw, used_web_search=True, client=client)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _build_prompt(request, reprompt: Optional[str], ad_intel: Optional[dict] = None,
                   allowed_families: Optional[set] = None) -> str:
    monthly = request.monthly_budget or 0
    months = request.total_months or 3
    total = monthly * months

    # Build an explicit, labeled targeting block — this is the single most
    # important input to get right. Every downstream section must anchor to
    # these exact specifics rather than falling back to generic media-buying
    # language ("the target audience", "local consumers").
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
    target_block = "\n".join(target_lines) if target_lines else "  - (planner did not specify — infer a reasonable target from the client/category and flag this as an assumption)"

    is_hispanic = any(
        w in (request.language or "").lower() + (request.demo or "").lower() + (request.behavioral or "").lower()
        for w in ("hispanic", "spanish", "latino", "latina")
    )
    stat_hint = (
        "Search the web for U.S. Hispanic-specific industry statistics (2023–2026) — actually look them up, don't recall from memory."
        if is_hispanic
        else "Search the web for general U.S. digital advertising industry statistics (2023–2026) — actually look them up, don't recall from memory."
    )

    reprompt_block = ""
    if reprompt and reprompt.strip():
        reprompt_block = f"""
--- USER CORRECTION / ADDITIONAL CONTEXT ---
{reprompt.strip()}
Please revise your strategy taking this into account.
---
"""

    ad_intel_block = ""
    if ad_intel and ad_intel.get("summary"):
        ad_intel_block = f"""
## CURRENT AD PRESENCE (live-checked against the public Meta and Google
## ad libraries — use this, don't guess or contradict it)
{ad_intel['summary']}

Where this is specific, use it directly rather than restating it blandly —
e.g. active Meta ads with no Spanish-language variant is a real, callable
opportunity; a client already dominant on a channel changes what the
"opening" is. TikTok's public library only covers ads shown in the EU/UK,
so a US client's TikTok activity is unknown — never treat it as a positive
or a negative.
"""

    return f"""You are a senior digital media sales strategist at Entravision. Research this campaign request and produce a data-backed strategic brief.

## CAMPAIGN REQUEST
- Client: {request.client_name or "TBD"} | Website: {request.client_website or "N/A"}
- Market: {request.salesperson_market or "TBD"}
- Campaign Goal: {request.campaign_goal or "Brand Awareness"}
- Budget: ${monthly:,.0f}/month × {months} months = ${total:,.0f} total flight
- Request Type: {request.request_type or "Proposal"}
- AE Comments: {request.salesperson_comments or "None"}
- Question Details: {getattr(request, "question_details", "") or "None"}

## TARGET AUDIENCE (the planner's actual inputs — this is the most important
## section in this brief; use these SPECIFIC values by name throughout your
## response, never a generic substitute like "the target audience"):
{target_block}

{"## AVAILABLE MEDIA PRODUCTS — the planner already selected products in these families during request intake. Build every recommended tactic from ONLY the families below; do not introduce a different family, even if you think it would fit better." if allowed_families else "## AVAILABLE MEDIA PRODUCTS (Entravision catalog families)"}
{_catalog_families_block(allowed_families)}

## ENTRAVISION STRENGTHS
{_ENTRAVISION_KB}

## STATISTICS GUIDANCE
{stat_hint}
{ad_intel_block}
{reprompt_block}
## YOU HAVE LIVE WEB SEARCH — USE IT, DON'T GUESS
This is a real capability, not a hypothetical: before writing the client
summary, run an actual search on the client's name/website to find out
what they really do (don't infer from the name alone if a website is
given). Before citing a statistic anywhere in this brief, search for it —
every data_point below must come from a real source you actually found,
not a number that merely sounds plausible for the category. If a search
turns up nothing specific enough, say so explicitly in that field ("no
audience-specific data found, using general market benchmark of X") rather
than presenting an invented-sounding number as if it were verified.

## CRITICAL RULE — SPECIFICITY OVER JARGON
Every tactic's rationale and every key insight MUST explicitly name the
targeting values above (the demo, geo, behavioral segment, or contextual
environment — whichever apply) rather than generic media-buying language.

BAD (generic, reject this style): "This tactic builds awareness with the
target audience through premium video content."
GOOD (specific, required style): "For {request.demo or 'this demo'} in
{request.geo or 'this market'}, CTV captures {request.behavioral or 'this audience'}
during appointment-viewing hours when linear reach is declining."

Every "citation" field must name a real, specific, searchable source
(publisher + year) — never a vague placeholder like "Industry Report,
2025." If you can't find a specific real source for a claim, don't
present the claim as sourced data at all; fold it into the rationale as
directional context instead.

If a data point can be tied to the SPECIFIC demo/geo/behavioral/contextual
values above (e.g. a Hispanic-specific stat when the target is Hispanic, a
regional stat when a DMA is given), use that over a generic industry-wide
stat. Only fall back to a generic market-wide statistic when nothing more
specific is plausible — and when you do, say so explicitly (e.g. "no
audience-specific data available, using general market benchmark").

{HOUSE_VOICE_GUIDE}

The client_summary/market_context/objectives_analysis/strategy_summary
fields are exactly the kind of writing the VOICE section above describes —
a senior planner's own reasoning, not a report generated about the client.
recommended_tactics/key_insights keep their own citation-backed structure
below (that rigor is the point of this document), but every rationale/
insight sentence should still read like a person wrote it, not a template.

## YOUR TASK
1. Briefly summarize who this client is and what they do (use your knowledge to infer from name/website/category).
2. Identify the key market context: local competitive landscape, relevant seasonality or trends — tied to the actual geo/demo above, not a generic market. Any specific number here (a market size, a growth rate, a competitor count) needs the same real citation as a tactic's data_point below — see the RULE right after this list.
3. Analyze the campaign objectives — what does success look like for THIS audience, and why the recommended tactics reach exactly the people described in the Target Audience section.
4. Recommend {"1–5 media tactics — every single one MUST use the product_family field set to one of the EXACT family names listed above, verbatim, even when that means multiple tactics share the same family (e.g. two tactics both using product_family \"Audio\" but naming different specific products/channels within it for variety — that's expected when only one or two families are available, not an error). Never invent a new/different family name or use a specific product name in the product_family field" if allowed_families else "2–5 media tactics (by catalog family)"}. For each include:
   - Strategic rationale (1–2 sentences) that names the specific demo/geo/behavioral/contextual value it's built around — not a generic restatement of the tactic
   - One supporting data point with citation in format (Source, Year) — audience-specific where possible, general market only as a fallback (and say so if you fall back)
   - Entravision's specific advantage for this tactic
   - Suggested budget allocation as a percentage (all tactics must sum to 100)
   - **If the rationale names more than one specific product within the family** (e.g. "Pre-Roll OLV and YouTube" both under Online Video), say explicitly that the suggested % is the family's combined ceiling and each named product still carries its OWN separate minimum spend — never imply they share one pooled minimum. When only one product is named, this doesn't apply.
5. Write a 2–3 sentence overall strategy summary that ties directly back to the named audience and client.
6. List 3 key insights the AE should highlight to the client — each one must reference the specific audience/client context, not generic advice that could apply to any campaign. Same citation rule as #2 applies here too — a specific number needs a named source right in the sentence, e.g. "...(Nielsen, 2025)."

RULE — EVERY SPECIFIC NUMBER ANYWHERE IN THIS BRIEF NEEDS A REAL SOURCE, NOT
JUST THE TACTICS' OWN data_point/citation FIELDS: client_summary,
market_context, and key_insights are free-form prose with no dedicated
citation field, which makes it easy to slip in an unattributed-but-
specific-sounding number ("the local market grew 12% last year") that
reads as sourced without actually being verified. If you state a specific
figure in ANY field, name where it came from inline in that same sentence
(publisher + year, e.g. "(Pew Research Center, 2025)") — a real one you
actually found via search, never a plausible-sounding placeholder. If you
don't have a real source for a number, don't state that number — describe
the point directionally instead ("a fast-growing local market" rather
than an invented "12% growth").

Respond ONLY with valid JSON — no markdown fences, no preamble:

{{
  "client_summary": "2-3 sentences on who the client is and their business",
  "market_context": "2-3 sentences on local market, competition, seasonality",
  "objectives_analysis": "2-3 sentences on what the campaign needs to achieve and why the recommended approach fits",
  "strategy_summary": "2-3 sentence overall strategy direction",
  "recommended_tactics": [
    {{
      "product_family": "exact family name from the catalog list above",
      "rationale": "Why this tactic fits this specific client and goal",
      "data_point": "Specific statistic supporting this tactic",
      "citation": "Source Name, Year",
      "entravision_advantage": "How Entravision specifically delivers this",
      "suggested_budget_pct": 45
    }}
  ],
  "key_insights": [
    "Insight 1 the AE should emphasize to the client",
    "Insight 2",
    "Insight 3"
  ]
}}"""


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------


def _parse(raw: str, used_web_search: bool = False, client=None) -> dict:
    try:
        data = llm_utils.parse_json_object(raw, expect_any=_BRIEF_KEYS, client=client)
    except ValueError as exc:
        return _error_brief(str(exc))

    # Normalise: ensure all expected keys exist
    tactics = [t for t in (data.get("recommended_tactics") or []) if isinstance(t, dict)]
    for t in tactics:
        t.setdefault("product_family", "")
        t.setdefault("rationale", "")
        t.setdefault("data_point", "")
        t.setdefault("citation", "")
        t.setdefault("entravision_advantage", "")
        t.setdefault("suggested_budget_pct", 0)
        for key in ("rationale", "data_point", "entravision_advantage"):
            t[key] = _normalize_newlines(str(t.get(key) or ""))

    return {
        "client_summary": _normalize_newlines(data.get("client_summary", "")),
        "market_context": _normalize_newlines(data.get("market_context", "")),
        "objectives_analysis": _normalize_newlines(data.get("objectives_analysis", "")),
        "strategy_summary": _normalize_newlines(data.get("strategy_summary", "")),
        "recommended_tactics": tactics,
        "key_insights": [_normalize_newlines(i if isinstance(i, str) else str(i))
                         for i in (data.get("key_insights") or [])],
        "used_web_search": used_web_search,
        "error": None,
    }


def _error_brief(msg: str) -> dict:
    return {
        "client_summary": "",
        "market_context": "",
        "objectives_analysis": "",
        "strategy_summary": "",
        "recommended_tactics": [],
        "key_insights": [],
        "used_web_search": False,
        "error": msg,
    }
