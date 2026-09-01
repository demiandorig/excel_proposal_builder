"""
AI Roadblocks / Restrictions Check — Step 05 of the app.

Given the confirmed product mix (Step 04), the confirmed Step 03 strategy
brief, and the full Notion request context, this searches the web for
current platform ad-policy risks per product — targeting restrictions,
common rejection reasons, category-specific requirements — grounded in the
client's inferred category and the actual target audience, not generic
boilerplate. Falls back to the catalog's already-known policy flags
(cannabis_policy/political_policy/hispanic_targeting_forced) as a supplement
when the model can't find something more specific.

Uses OpenAI's Responses API with the `web_search_preview` tool so the risks
are grounded in live policy pages rather than the model's static knowledge
(the older gpt-4o-mini-search-preview chat model this used previously has
been deprecated by OpenAI). Falls back to a plain (non-searching) chat
completion — with a clear disclaimer — if the Responses API or the
web_search_preview tool isn't available on this account/SDK version.
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

from app.catalog import by_name
from app.services.text_utils import normalize_newlines as _normalize_newlines


_SEARCH_MODEL = "gpt-4o"
_FALLBACK_MODEL = "gpt-4o"


def generate_roadblocks(request, line_items, strategy_brief: Optional[dict] = None) -> dict:
    """
    Returns:
      {
        "overall_summary": str,
        "product_roadblocks": [
          {"product_name", "risk_level", "risks": [{"issue","detail","source"}],
           "recommended_mitigation"}
        ],
        "used_web_search": bool,
        "error": str | None,
      }
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not _HAS_OPENAI:
        return _error_result("openai package not installed — run: pip install openai")
    if not api_key:
        return _error_result("OPENAI_API_KEY not set — roadblocks check skipped.")

    client = _OpenAI(api_key=api_key)
    prompt = _build_prompt(request, line_items, strategy_brief)

    try:
        response = client.responses.create(
            model=_SEARCH_MODEL,
            tools=[{"type": "web_search_preview"}],
            input=prompt,
            max_output_tokens=3500,
        )
        raw = _extract_response_text(response)
        return _parse(raw, used_web_search=True)
    except Exception as search_exc:
        # Responses API / web_search_preview tool unavailable on this
        # account or SDK version — fall back to a plain completion, but say
        # so clearly rather than silently presenting static-knowledge
        # guesses as web-verified.
        try:
            response = client.chat.completions.create(
                model=_FALLBACK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=3500,
                temperature=0.5,
            )
            raw = response.choices[0].message.content or ""
            result = _parse(raw, used_web_search=False)
            if not result.get("error"):
                result["error"] = (
                    "Web search wasn't available on this account "
                    f"({search_exc}); this used the model's general knowledge "
                    "instead — verify against current platform policies before relying on it."
                )
            return result
        except Exception as fallback_exc:
            return _error_result(f"Roadblocks check failed: {fallback_exc}")


def _extract_response_text(response) -> str:
    """
    Pull the text out of a Responses API result. `.output_text` is the SDK's
    convenience accessor; fall back to walking `.output` manually for older
    SDK versions that don't expose it.
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


def _error_result(msg: str) -> dict:
    return {
        "overall_summary": "",
        "product_roadblocks": [],
        "used_web_search": False,
        "error": msg,
    }


def _build_prompt(request, line_items, strategy_brief: Optional[dict]) -> str:
    items_text = "\n".join(f"  - {li.product_name}" for li in line_items)

    target_lines = []
    if getattr(request, "demo", ""):
        target_lines.append(f"  - Demographic: {request.demo}")
    if getattr(request, "language", ""):
        target_lines.append(f"  - Language: {request.language}")
    if getattr(request, "geo", ""):
        target_lines.append(f"  - Geography: {request.geo}")
    if getattr(request, "behavioral", ""):
        target_lines.append(f"  - Behavioral / audience segment: {request.behavioral}")
    if getattr(request, "contextual", ""):
        target_lines.append(f"  - Contextual environment: {request.contextual}")
    target_block = "\n".join(target_lines) if target_lines else "  - (not specified)"

    # Known internal policy flags from the catalog — supplement, not replace, live search
    known_policy_lines = []
    for li in line_items:
        p = by_name(li.product_name)
        if p is None:
            continue
        flags = []
        if p.cannabis_policy != "allowed":
            flags.append(f"cannabis: {p.cannabis_policy}")
        if p.political_policy != "allowed":
            flags.append(f"political: {p.political_policy}")
        if p.hispanic_targeting_forced is True:
            flags.append("Hispanic targeting required")
        elif p.hispanic_targeting_forced is False:
            flags.append("Hispanic targeting not supported")
        if flags:
            known_policy_lines.append(f"  - {li.product_name}: {'; '.join(flags)}")
    known_policy_block = "\n".join(known_policy_lines) if known_policy_lines else "  - (none flagged internally)"

    strategy_block = ""
    if strategy_brief and strategy_brief.get("client_summary"):
        strategy_block = f"""
## CONFIRMED CLIENT CONTEXT (from Step 03 — use this to infer the client's
## industry/category, since ad platform restrictions are heavily category-driven)
Client summary: {strategy_brief.get('client_summary', '')}
Market context: {strategy_brief.get('market_context', '')}
"""

    return f"""You are a digital ad operations compliance specialist. Research CURRENT platform advertising policies and identify realistic roadblocks for this specific campaign — targeting restrictions, common rejection reasons, and category-specific requirements. Ground every claim in an actual, current policy page you find via web search; do not invent policy details.

## CLIENT & CAMPAIGN
- Client: {getattr(request, 'client_name', '') or 'TBD'} | Website: {getattr(request, 'client_website', '') or 'N/A'}
- Campaign Goal: {getattr(request, 'campaign_goal', '') or 'Awareness'}
- AE Comments: {getattr(request, 'salesperson_comments', '') or 'None'}
{strategy_block}
## TARGET AUDIENCE
{target_block}

## PRODUCTS IN THIS PROPOSAL
{items_text}

## KNOWN INTERNAL POLICY FLAGS (Entravision catalog — supplement your web
## research with these, don't just repeat them verbatim)
{known_policy_block}

## YOUR TASK
For EACH product listed above, run a SEPARATE web search for that specific
platform/product — do not do one general search and apply it to everything.
Different platforms (Google/YouTube, Meta, TikTok, LinkedIn, Netflix, Roku,
Spotify, DOOH networks, etc.) have distinct, independently-published policy
pages; treat each one as its own research task:
1. Search the web for THAT platform's CURRENT advertising policy relevant to this client's likely industry/category (infer the category from the client name/website/context above) and the target audience described.
2. Identify realistic roadblocks: targeting restrictions that would limit this specific campaign, common reasons ads in this category get rejected, required certifications/approvals, or creative restrictions.
3. Recommend a concrete mitigation for each risk (e.g. "submit for pre-approval 5 business days before launch", "avoid X phrasing in creative").
4. Assign an overall risk_level (low/medium/high) for running this product with this client/audience.

SOURCE COVERAGE REQUIREMENT: across your ENTIRE response you must cite at
least 5 distinct sources in total (distinct policy pages/documents, not the
same one repeated). If there are 5 or more distinct platforms among the
products above, that means at least one distinct source per platform. If
there are fewer than 5 distinct platforms, find additional distinct sources
per platform — a general policy page plus a category-specific one (e.g.
Meta's general ads policy AND Meta's financial-services ad restrictions
page), or a second risk with its own source on the same product. Do not
let every risk across every product cite the same single source — that
means you searched once and stopped; go back and search again per platform.

Respond ONLY with valid JSON — no markdown fences, no preamble:

{{
  "overall_summary": "2-3 sentences on the campaign's overall compliance risk profile across all products",
  "product_roadblocks": [
    {{
      "product_name": "exact product name as listed above",
      "risk_level": "low",
      "risks": [
        {{"issue": "Short label for the risk", "detail": "1-2 sentence explanation specific to this client/audience", "source": "Platform Policy Name, Year"}}
      ],
      "recommended_mitigation": "Concrete action the AE/planner should take"
    }}
  ]
}}

RULES:
- Every "source" must name a real, specific policy page/document, with a year — not a vague "platform guidelines"
- Across ALL products combined, use at least 5 distinct sources — do not cite the same source for every product
- Search each distinct platform separately; don't rely on one search result to cover multiple products
- If you cannot find a specific policy relevant to this client's category, say so explicitly in that risk's detail rather than fabricating one
- risk_level must be exactly one of: low, medium, high
- Respond ONLY with the JSON object, starting with {{ and ending with }}"""


def _parse(raw: str, used_web_search: bool) -> dict:
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return _error_result("AI response contained no JSON.")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return _error_result(f"JSON parse error: {exc}")

    roadblocks = data.get("product_roadblocks") or []
    for r in roadblocks:
        r.setdefault("product_name", "")
        r.setdefault("risk_level", "low")
        r["recommended_mitigation"] = _normalize_newlines(r.get("recommended_mitigation", ""))
        risks = r.get("risks") or []
        for risk in risks:
            risk.setdefault("issue", "")
            risk["detail"] = _normalize_newlines(risk.get("detail", ""))
            risk.setdefault("source", "")
        r["risks"] = risks

    return {
        "overall_summary": _normalize_newlines(data.get("overall_summary", "")),
        "product_roadblocks": roadblocks,
        "used_web_search": used_web_search,
        "error": None,
    }
