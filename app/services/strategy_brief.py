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

import json
import os
import re
from typing import Optional

try:
    from openai import OpenAI as _OpenAI
    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False

from app.services.text_utils import normalize_newlines as _normalize_newlines


# Entravision catalog families + one-line description for the prompt
_CATALOG_FAMILIES = """
- Search: Paid search (SEM) & Performance Max — captures intent-driven clicks, high conversion rate
- Display: Programmatic banner & geo-fence display — local awareness, retargeting, low CPM
- Online Video: Pre-roll OLV & YouTube Ads — brand storytelling, high completion rates
- CTV / OTT: Entravision Plus (Connected TV / streaming) — premium non-skippable, living-room screen
- Audio: Digital radio, AudioEngage podcast network, Spotify — commuter and daily-routine reach
- Social: Meta Ads (FB/IG), TikTok, LinkedIn — audience targeting, engagement, UGC-friendly
- Email: Email marketing & display retargeting — nurturing, conversion, owned audience
- DOOH: Digital out-of-home screens — ambient local presence, high-traffic locations
- Services: Landing pages, creative production — support and conversion assets
- Measurement: Brand lift, attribution, call tracking, foot traffic — ROI validation
""".strip()

_ENTRAVISION_KB = """
CTV/OTT (Entravision Plus): Entravision's advanced programmatic and CTV/OTT advertising capabilities with premium publisher partnerships.
Audio (AudioEngage): Entravision's leading digital audio and podcast network — 160MM general market reach, 45MM Hispanic coverage.
Social (Meta/TikTok): Entravision's owned-and-operated Spanish-language properties and deep expertise in culturally relevant bilingual content.
SEM/Search: Entravision's certified SEM team and proprietary bidding strategies optimized for local market dominance.
General: Entravision's deep expertise in creating culturally relevant, bilingual content that resonates authentically with the target audience.
""".strip()


_SEARCH_MODEL = "gpt-4o"
_FALLBACK_MODEL = "gpt-4o"


def generate_brief(request, reprompt: Optional[str] = None) -> dict:
    """
    Generate (or regenerate with reprompt) a strategic brief for this proposal.

    Grounded in live web search (OpenAI Responses API + web_search_preview,
    same mechanism as the Roadblocks step) so the client summary, market
    context, and tactic-supporting stats are pulled from actual current
    sources instead of the model's static training-data guesses — the
    previous chat-completions-only version had no way to look anything up,
    which is exactly what made its "specific recent stat" asks come out
    generic. Falls back to a plain (non-searching) completion — with a
    clear disclaimer — if the Responses API or the search tool isn't
    available on this account/SDK version.

    Returns a dict with keys:
      client_summary, market_context, objectives_analysis, strategy_summary,
      recommended_tactics (list), key_insights (list), used_web_search (bool),
      error (str|None)
    """
    api_key = os.getenv("OPENAI_API_KEY")

    if not _HAS_OPENAI:
        return _error_brief("openai package not installed — run: pip install openai")
    if not api_key:
        return _error_brief("OPENAI_API_KEY not set.")

    client = _OpenAI(api_key=api_key)
    prompt = _build_prompt(request, reprompt)

    try:
        response = client.responses.create(
            model=_SEARCH_MODEL,
            tools=[{"type": "web_search_preview"}],
            input=prompt,
            max_output_tokens=3000,
        )
        raw = _extract_response_text(response)
        return _parse(raw, used_web_search=True)
    except Exception as search_exc:
        # Responses API / web_search_preview unavailable on this account or
        # SDK version — fall back to a plain completion, but say so clearly
        # rather than silently presenting static-knowledge guesses as
        # web-verified research.
        try:
            response = client.chat.completions.create(
                model=_FALLBACK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2500,
                temperature=0.7,
            )
            raw = response.choices[0].message.content or ""
            result = _parse(raw, used_web_search=False)
            if not result.get("error"):
                result["error"] = (
                    "Web search wasn't available on this account "
                    f"({search_exc}); this used the model's general knowledge "
                    "instead — verify stats before relying on them."
                )
            return result
        except Exception as fallback_exc:
            return _error_brief(f"AI request failed: {fallback_exc}")


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


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _build_prompt(request, reprompt: Optional[str]) -> str:
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

    return f"""You are a senior digital media sales strategist at Entravision. Research this campaign request and produce a data-backed strategic brief.

## CAMPAIGN REQUEST
- Client: {request.client_name or "TBD"} | Website: {request.client_website or "N/A"}
- Market: {request.salesperson_market or "TBD"}
- Campaign Goal: {request.campaign_goal or "Brand Awareness"}
- Budget: ${monthly:,.0f}/month × {months} months = ${total:,.0f} total flight
- Request Type: {request.request_type or "Proposal"}
- AE Comments: {request.salesperson_comments or "None"}

## TARGET AUDIENCE (the planner's actual inputs — this is the most important
## section in this brief; use these SPECIFIC values by name throughout your
## response, never a generic substitute like "the target audience"):
{target_block}

## AVAILABLE MEDIA PRODUCTS (Entravision catalog families)
{_CATALOG_FAMILIES}

## ENTRAVISION STRENGTHS
{_ENTRAVISION_KB}

## STATISTICS GUIDANCE
{stat_hint}
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

## YOUR TASK
1. Briefly summarize who this client is and what they do (use your knowledge to infer from name/website/category).
2. Identify the key market context: local competitive landscape, relevant seasonality or trends — tied to the actual geo/demo above, not a generic market.
3. Analyze the campaign objectives — what does success look like for THIS audience, and why the recommended tactics reach exactly the people described in the Target Audience section.
4. Recommend 2–5 media tactics (by catalog family). For each include:
   - Strategic rationale (1–2 sentences) that names the specific demo/geo/behavioral/contextual value it's built around — not a generic restatement of the tactic
   - One supporting data point with citation in format (Source, Year) — audience-specific where possible, general market only as a fallback (and say so if you fall back)
   - Entravision's specific advantage for this tactic
   - Suggested budget allocation as a percentage (all tactics must sum to 100)
5. Write a 2–3 sentence overall strategy summary that ties directly back to the named audience and client.
6. List 3 key insights the AE should highlight to the client — each one must reference the specific audience/client context, not generic advice that could apply to any campaign.

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


def _parse(raw: str, used_web_search: bool = False) -> dict:
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return _error_brief("AI response contained no JSON.")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return _error_brief(f"JSON parse error: {exc}")

    # Normalise: ensure all expected keys exist
    tactics = data.get("recommended_tactics") or []
    for t in tactics:
        t.setdefault("product_family", "")
        t.setdefault("rationale", "")
        t.setdefault("data_point", "")
        t.setdefault("citation", "")
        t.setdefault("entravision_advantage", "")
        t.setdefault("suggested_budget_pct", 0)
        for key in ("rationale", "data_point", "entravision_advantage"):
            t[key] = _normalize_newlines(t.get(key, ""))

    return {
        "client_summary": _normalize_newlines(data.get("client_summary", "")),
        "market_context": _normalize_newlines(data.get("market_context", "")),
        "objectives_analysis": _normalize_newlines(data.get("objectives_analysis", "")),
        "strategy_summary": _normalize_newlines(data.get("strategy_summary", "")),
        "recommended_tactics": tactics,
        "key_insights": [_normalize_newlines(i) for i in (data.get("key_insights") or [])],
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
