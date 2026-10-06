"""
AI Roadblocks / Restrictions Check — Step 07 of the app.

Given the confirmed product mix (Step 04), the confirmed Step 03 strategy
brief, and the full Notion request context, this searches the web for
current platform ad-policy risks per product — targeting restrictions,
common rejection reasons, category-specific requirements — grounded in the
client's category and the actual target audience, not generic boilerplate.

WHETHER A VERTICAL IS ALLOWED ON A PRODUCT is NOT the model's call. That comes
from Entravision's own "Restricted Verticals by Platforms" sheet (see
app/services/restrictions.py): the planner confirms which verticals apply,
verdicts are computed deterministically from the sheet, handed to the model as
facts it may not contradict, and enforced again in code after it answers
(_apply_matrix) — so a product the sheet lists as allowed (e.g. political ads
on Display and OLV) can never come back flagged not-feasible. The old
hard-coded catalog flags (political_policy/cannabis_policy) are no longer
used here: they had drifted from the sheet and nothing checked them.

Uses OpenAI's Responses API with the `web_search` tool so the risks are
grounded in live policy pages rather than the model's static knowledge
(the older gpt-4o-mini-search-preview chat model this used previously has
been deprecated by OpenAI). Falls back to a plain (non-searching) chat
completion — with a clear disclaimer — if the Responses API or the
web_search tool isn't available on this account/SDK version.
"""
from __future__ import annotations

import os
from typing import Any, Optional

try:
    from openai import OpenAI as _OpenAI
    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False

import re

from app.services import llm_utils, restrictions
from app.services.text_utils import normalize_newlines as _normalize_newlines, split_sentences
from app.services.writing_style import HOUSE_VOICE_GUIDE


# Bumped from gpt-4o to gpt-5.1, OpenAI's current flagship as of Sep 2026 —
# confirmed via live web search against OpenAI's own model docs, not
# guessed from static training knowledge, since a wrong model string here
# would hard-fail every call. Two things changed together with the model
# and must not be separated: (1) the Responses API tool below is now
# "web_search" — GPT-5-series models don't support the legacy
# "web_search_preview" this used to call, they error on it outright; (2)
# the chat.completions fallback below no longer passes `temperature=`,
# since GPT-5-series models reject any value but the default (1).
_SEARCH_MODEL = "gpt-5.1"
_FALLBACK_MODEL = "gpt-5.1"
# Shared by reasoning + visible tokens; output grows with the product count (>=5 cited sources).
_MAX_OUTPUT_TOKENS = 16000
_ROADBLOCKS_KEYS = ("overall_summary", "product_roadblocks")
_TRUNCATED_MSG = "The roadblocks check was cut off before it finished, even after an automatic retry."


def generate_roadblocks(request, line_items, strategy_brief: Optional[dict] = None,
                        categories: Optional[list[str]] = None) -> dict:
    """
    `categories`: the restricted verticals (sheet tab names) the planner
    confirmed apply to this client. Verdicts for them come from the
    restrictions matrix, not the model.

    Returns:
      {
        "overall_summary": str,
        "product_roadblocks": [
          {"product_name", "risk_level", "risks": [{"issue","detail","source"}],
           "recommended_mitigation",
           "matrix": {"verdict", "checks": [{"category","status","note"}]}  # when verticals were confirmed
          }
        ],
        "categories": [str],        # the verticals this result was computed for
        "used_web_search": bool,
        "error": str | None,
      }
    """
    matrix = restrictions.build_matrix([li.product_name for li in line_items], categories)
    api_key = os.getenv("OPENAI_API_KEY")
    if not _HAS_OPENAI:
        return _error_result("openai package not installed — run: pip install openai", matrix, line_items)
    if not api_key:
        return _error_result("OPENAI_API_KEY not set — roadblocks check skipped.", matrix, line_items)

    client = _OpenAI(api_key=api_key)
    prompt = _build_prompt(request, line_items, strategy_brief, matrix)

    try:
        raw = llm_utils.responses_text(client, model=_SEARCH_MODEL, prompt=prompt,
                                       tools=[{"type": "web_search"}],
                                       max_output_tokens=_MAX_OUTPUT_TOKENS)
    except llm_utils.ResponseTruncated:
        return _error_result(_TRUNCATED_MSG, matrix, line_items)
    except Exception as search_exc:
        try:
            raw = llm_utils.chat_text(client, model=_FALLBACK_MODEL, prompt=prompt,
                                      max_completion_tokens=_MAX_OUTPUT_TOKENS)
        except llm_utils.ResponseTruncated:
            return _error_result(_TRUNCATED_MSG, matrix, line_items)
        except Exception as fallback_exc:
            return _error_result(f"Roadblocks check failed: {fallback_exc}", matrix, line_items)
        result = _parse(raw, used_web_search=False, client=client, matrix=matrix, line_items=line_items)
        if not result.get("error"):
            result["error"] = (
                f"Live web search failed ({search_exc}); this used the model's general knowledge "
                "instead — verify against current platform policies before relying on it."
            )
        return result
    return _parse(raw, used_web_search=True, client=client, matrix=matrix, line_items=line_items)


def _error_result(msg: str, matrix: Optional[dict] = None, line_items=None) -> dict:
    """A failed model call still returns whatever the restrictions sheet alone
    settles (those verdicts don't need the model), so the planner isn't left
    with nothing when only the web-research half is down."""
    result = {
        "overall_summary": "",
        "product_roadblocks": [],
        "categories": [c.name for c in (matrix or {}).get("categories", [])],
        "used_web_search": False,
        "error": msg,
    }
    if matrix and matrix.get("categories") and line_items:
        _apply_matrix(result, matrix, line_items)
    return result


def _build_prompt(request, line_items, strategy_brief: Optional[dict], matrix: Optional[dict] = None) -> str:
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

    # Whether a vertical is allowed on a product comes from the restrictions
    # matrix (Entravision's sheet), never from the model or the old catalog flags.
    matrix_block = restrictions.matrix_prompt_block(
        matrix if matrix is not None else {"categories": [], "verdicts": {}, "using_builtin": False, "synced_at": None})

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
- Question Details: {getattr(request, 'question_details', '') or 'None'}
{strategy_block}
## TARGET AUDIENCE
{target_block}

## PRODUCTS IN THIS PROPOSAL
{items_text}

{matrix_block}

{HOUSE_VOICE_GUIDE}

overall_summary/detail/recommended_mitigation should read like an ad-ops
specialist telling a colleague what they actually found, per the VOICE
section above — not a template restated per product. Keep every "source"
citation itself factual and specific (that rigor is the point of this
report); the writing AROUND each citation is what should sound human.

## YOUR TASK
For EACH product listed above, run a SEPARATE web search for that specific
platform/product — do not do one general search and apply it to everything.
Different platforms (Google/YouTube, Meta, TikTok, LinkedIn, Netflix, Roku,
Spotify, DOOH networks, etc.) have distinct, independently-published policy
pages; treat each one as its own research task:
1. Search the web for THAT platform's CURRENT advertising policy relevant to this client's industry/category and the target audience described. Whether a restricted vertical is ALLOWED on the product is already settled by the matrix above — research the platform's practical requirements (ad review, creative rules, required approvals), not whether Entravision accepts the vertical.
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
- Never contradict the Restricted Verticals Matrix above. If it says a product is ALLOWED for a vertical, do not describe that vertical as not allowed, prohibited or not feasible on that product
- Respond ONLY with the JSON object, starting with {{ and ending with }}"""


# Phrases that make a sentence a claim that something is NOT permitted.
_NEGATIVE_CLAIM = re.compile(
    r"\bnot (?:currently |yet )?(?:be )?(?:allowed|permitted|accepted|feasible|eligible|supported|available|approved|possible)\b|"
    r"\bno longer (?:accepted|allowed|permitted|available|supported|approved)\b|"
    r"\b(?:has|have|is|are) (?:been )?(?:paused|suspended|on hold)\b|"
    r"\bnot an option\b|\boff[- ]limits\b|\bclosed to\b|\bno-?go\b|"
    r"\b(?:is|are|was|were|does|do|did)n[\u2019']t (?:be )?(?:allow\w*|permit\w*|accept\w*|feasible|eligible|supported|available|approved|run|serve\w*)\b|"
    r"\b(?:does|do|did) not (?:allow|permit|accept|support|run|serve)\b|"
    r"\b(?:prohibit\w*|disallow\w*|banned|bans|forbidden|infeasible|unfeasible|ineligible|unavailable|rejected|rejects|blocked)\b|"
    r"\b(?:can[\u2019']t|cannot|unable to|will not|won[\u2019']t|should not|shouldn[\u2019']t|must not|do not|don[\u2019']t) "
    r"(?:be )?(?:run|serve\w*|accept\w*|booked|sold|placed|carry|sell|take)\b|"
    r"\bnot approved\b",
    re.I,
)

# A sentence that sets a CONDITION ("cannot run until verification is complete",
# "will not be served without a Paid-for-by disclosure") is a practical caveat on
# a product the sheet allows — exactly what the prompt's "Additional:" issues are
# for — not a denial, so it is never treated as contradicting the sheet.
_CONDITIONAL = re.compile(
    r"\b(?:without|until|unless|before|if|when|once|after|requires?|required|requiring|verification|verified|"
    r"authori[sz]ation|authori[sz]ed|disclosure|disclaimers?|paid for|sponsor\w*|approval|pre-?approval|lead time|"
    r"certif\w+|registration|registered|missing|during (?:the )?(?:ad )?review)\b",
    re.I,
)
# A restriction on HOW the vertical may run (a feature, a geography, a length), not a refusal of the vertical on
# the product — also a real caveat, so also never dropped.
_QUALIFIER = re.compile(
    r"\b(?:to (?:use|target|exceed|geo-?target|run (?:longer|more))|using|lookalike|custom audiences?|customer[- ]lists?|"
    r"detailed targeting|interest[- ]based|dynamic creative|retarget\w*|remarket\w*|state law|in the eu|"
    r"in (?:[a-z]+ )?states?|certain|some (?:platforms|apps|publishers|states|inventory)|seconds|"
    r"frequently|often|sometimes|may be|might be|can be|un-?verified|id check|minors|under 1[0-9]|"
    r"advertisers who|ads that (?:contain|include|use|feature)|from (?:targeting|using|running))\b",
    re.I,
)
# Words that don't identify a product when a summary sentence is matched back to one.
_PRODUCT_STOP = {
    "edigital", "entravision", "network", "standard", "iab", "video", "audio", "ads", "ad", "campaign", "campaigns",
    "and", "or", "the", "of", "for", "run", "pre", "roll", "with", "only", "all", "any", "plus", "evc",
}


def _norm_key(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().casefold()


def _mentions(text: str, words: list[str]) -> bool:
    return any(re.search(r"(?<![a-z0-9])" + re.escape(w.lower()) + r"(?![a-z0-9])", text) for w in words if w)


def _denies_category(sentence: str, allowed_cats: list) -> bool:
    """A flat denial (not a conditional or qualified caveat) of a vertical the sheet ALLOWS."""
    low = sentence.lower()
    if not _NEGATIVE_CLAIM.search(low) or _CONDITIONAL.search(low) or _QUALIFIER.search(low):
        return False
    return any(_mentions(low, [cat.name, *cat.keywords]) for cat in allowed_cats)


def _risk_contradicts_allowed(risk: dict, allowed_cats: list) -> bool:
    """The risk's own headline denies a vertical the sheet ALLOWS on this
    product — wrong by definition, so the whole risk is dropped."""
    issue, detail = str(risk.get("issue", "")), str(risk.get("detail", ""))
    if _denies_category(issue, allowed_cats):
        return True
    sentences = [s for s in split_sentences(detail.strip()) if s.strip()]
    return bool(sentences) and all(_denies_category(s, allowed_cats) for s in sentences)


def _strip_denials(text: str, allowed_cats: list) -> tuple[str, list[str]]:
    """(text without its denial sentences, the sentences removed). Text with nothing to remove comes back
    untouched — line breaks and numbering included."""
    segments = split_sentences((text or "").strip())
    removed = [s.strip() for s in segments if _denies_category(s, allowed_cats)]
    if not removed:
        return (text or ""), []
    out = ""
    for seg in segments:
        if not _denies_category(seg, allowed_cats):
            out += seg
            continue
        gap = seg[len(seg.rstrip()):]
        trailing = len(out) - len(out.rstrip())
        if "\n" in gap and out and gap.count("\n") > out[len(out) - trailing:].count("\n"):
            out = out.rstrip() + gap                 # the removed sentence closed a paragraph / list item: keep that break
    return out.strip(), removed


def _product_tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if len(t) >= 3 and t not in _PRODUCT_STOP}


def _mentions_product(sentence: str, name: str) -> bool:
    low = sentence.lower()
    return name.lower() in low or any(re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", low) for t in _product_tokens(name))


def _scrub_summary(summary: str, matrix: dict, line_items) -> tuple[str, list[str]]:
    """Drops overall-summary sentences that deny a vertical on products the
    sheet approves — when they name an approved product (and no refused one),
    or when the vertical is approved for EVERY product in the plan, so there is
    nothing the sentence could rightly be about. Returns (text, removed)."""
    status_by_cat: dict[str, list[tuple[str, str]]] = {}
    for li in line_items:
        for check in (matrix["verdicts"].get(li.product_name) or {}).get("checks", []):
            status_by_cat.setdefault(check["category"], []).append((li.product_name, check["status"]))
    segments = split_sentences((summary or "").strip())
    kept: list[str] = []
    removed: list[str] = []
    for sentence in segments:
        low = sentence.lower()
        if not _NEGATIVE_CLAIM.search(low) or _CONDITIONAL.search(low) or _QUALIFIER.search(low):
            kept.append(sentence)
            continue
        contradicts = False
        for cat in matrix["categories"]:
            if not _mentions(low, [cat.name, *cat.keywords]):
                continue
            rows = status_by_cat.get(cat.name, [])
            allowed_names = [n for n, st in rows if st == "allowed"]
            refused_names = [n for n, st in rows if st == "not_allowed"]
            everywhere_allowed = bool(rows) and all(st in ("allowed", "n/a") for _, st in rows) and bool(allowed_names)
            names_allowed = any(_mentions_product(sentence, n) for n in allowed_names)
            names_refused = any(_mentions_product(sentence, n) for n in refused_names)
            if (names_allowed and not names_refused) or (everywhere_allowed and not names_refused):
                contradicts = True
                break
        if contradicts:
            removed.append(sentence.strip())
        else:
            kept.append(sentence)
    if not removed:
        return (summary or ""), []
    return "".join(kept).strip(), removed


_RISK_RANK = {"low": 0, "medium": 1, "high": 2}


def _level(value: Any) -> str:
    """A model's risk_level as one of low/medium/high text (a list, None or "High" must not break the merge)."""
    text = str(value).strip().lower() if isinstance(value, (str, int, float)) else ""
    return text if text in _RISK_RANK else "low"


def _canonical_product_name(raw_name: Any, canon: dict[str, str]) -> Optional[str]:
    """The line item a model-written product name stands for — exact (case/space-insensitive), else the single
    best token match ("eDigital Display" for "eDigital Network Display - Standard IAB"). Two equally good
    candidates, or a weak match, is no match."""
    key = _norm_key(raw_name)
    if not key:
        return None
    if key in canon:
        return canon[key]
    toks = set(re.findall(r"[a-z0-9]+", key)) - _PRODUCT_STOP
    if not toks:
        return None
    best, best_score, tied = None, 0.0, False
    for k, name in canon.items():
        ktoks = set(re.findall(r"[a-z0-9]+", k)) - _PRODUCT_STOP
        if not ktoks:
            continue
        score = len(toks & ktoks) / len(toks | ktoks)
        if score > best_score:
            best, best_score, tied = name, score, False
        elif score == best_score and name != best:
            tied = True
    return best if best_score >= 0.5 and not tied else None


def _merge_model_entries(result: dict, line_items) -> None:
    """One entry per line-item product: the model's retyped names are mapped back to the product they mean and
    entries that turn out to be the same product are merged, so none escapes the verdict filtering below."""
    canon = {_norm_key(li.product_name): li.product_name for li in line_items}
    merged: list[dict] = []
    by_name: dict[str, dict] = {}
    for entry in result["product_roadblocks"]:
        target = _canonical_product_name(entry.get("product_name"), canon)
        if target is None:
            merged.append(entry)
            continue
        entry["product_name"] = target
        prev = by_name.get(target)
        if prev is None:
            by_name[target] = entry
            merged.append(entry)
            continue
        prev["risks"] = (prev.get("risks") or []) + (entry.get("risks") or [])
        if _RISK_RANK.get(_level(entry.get("risk_level")), 0) > _RISK_RANK.get(_level(prev.get("risk_level")), 0):
            prev["risk_level"] = _level(entry.get("risk_level"))
        extra = (entry.get("recommended_mitigation") or "").strip()
        if extra and extra not in (prev.get("recommended_mitigation") or ""):
            prev["recommended_mitigation"] = ((prev.get("recommended_mitigation") or "").strip() + "\n" + extra).strip()
    result["product_roadblocks"] = merged


def _apply_matrix(result: dict, matrix: dict, line_items) -> None:
    """Enforces the sheet's verdicts on whatever the model produced: attaches
    each product's verdict, drops risks (and mitigation / summary sentences)
    that contradict an ALLOWED verdict, prepends deterministic risks for NOT
    ACCEPTED ones, and keeps risk_level consistent with the verdict. What it
    removed is listed in result["removed_claims"] so the planner can see it.
    Mutates `result`."""
    cats_by_name = {c.name: c for c in matrix["categories"]}
    _merge_model_entries(result, line_items)
    by_product = {_norm_key(r.get("product_name")): r for r in result["product_roadblocks"]}
    seen_products: set[str] = set()
    removed_claims: list[dict] = []
    for li in line_items:
        key = _norm_key(li.product_name)
        if key in seen_products:
            continue
        seen_products.add(key)
        verdict = matrix["verdicts"].get(li.product_name)
        if not verdict or not verdict["checks"]:
            continue
        entry = by_product.get(key)
        if entry is None:
            entry = {"product_name": li.product_name, "risk_level": "low", "risks": [], "recommended_mitigation": ""}
            result["product_roadblocks"].append(entry)
            by_product[key] = entry
        entry["matrix"] = verdict

        allowed_cats = [cats_by_name[c["category"]] for c in verdict["checks"] if c["status"] == "allowed" and c["category"] in cats_by_name]
        removed_here = 0
        kept_risks = []
        for r in entry.get("risks") or []:
            if _risk_contradicts_allowed(r, allowed_cats):
                removed_here += 1
                removed_claims.append({"product": li.product_name, "text": f"{r.get('issue', '')}: {r.get('detail', '')}".strip(": ")})
                continue
            new_detail, gone = _strip_denials(r.get("detail", ""), allowed_cats)
            if gone:
                r["detail"] = new_detail
                removed_here += len(gone)
                removed_claims.extend({"product": li.product_name, "text": g} for g in gone)
            kept_risks.append(r)
        entry["risks"] = kept_risks
        new_mitigation, gone = _strip_denials(entry.get("recommended_mitigation") or "", allowed_cats)
        if gone:
            entry["recommended_mitigation"] = new_mitigation
            removed_here += len(gone)
            removed_claims.extend({"product": li.product_name, "text": g} for g in gone)

        stamp = f"Restricted Verticals sheet{', synced ' + matrix['synced_at'][:10] if matrix.get('synced_at') else ''}"
        injected = [
            {"issue": f"Not accepted for {c['category']}", "detail": c["note"], "source": stamp}
            for c in verdict["checks"] if c["status"] == "not_allowed"
        ]
        entry["risks"] = injected + entry["risks"]

        if verdict["verdict"] == "not_allowed":
            entry["risk_level"] = "high"
        elif verdict["verdict"] == "guidance" and entry.get("risk_level", "low") == "low":
            entry["risk_level"] = "medium"
        elif verdict["verdict"] == "allowed" and removed_here:
            # The "high" may have come from the claim just removed — re-derive from what's left.
            if not entry["risks"]:
                entry["risk_level"] = "low"
            elif entry.get("risk_level") == "high":
                entry["risk_level"] = "medium"

    # A model entry that matched no plan product (an ambiguous shortened name, an unrelated product) can't be judged
    # product by product — but a vertical the sheet allows on EVERY plan product can't be denied by any of them.
    everywhere = [cats_by_name[name] for name in cats_by_name
                  if seen_products and all(
                      any(c["category"] == name and c["status"] in ("allowed", "n/a")
                          for c in (matrix["verdicts"].get(li.product_name) or {}).get("checks", []))
                      for li in line_items if _norm_key(li.product_name) in seen_products)]
    if everywhere:
        for entry in result["product_roadblocks"]:
            if entry.get("matrix") is not None:
                continue
            kept = []
            for r in entry.get("risks") or []:
                if _risk_contradicts_allowed(r, everywhere):
                    removed_claims.append({"product": entry.get("product_name", ""), "text": f"{r.get('issue', '')}: {r.get('detail', '')}".strip(": ")})
                    continue
                new_detail, gone_d = _strip_denials(r.get("detail", ""), everywhere)
                if gone_d:
                    r["detail"] = new_detail
                    removed_claims.extend({"product": entry.get("product_name", ""), "text": g} for g in gone_d)
                kept.append(r)
            entry["risks"] = kept
            new_m, gone_m = _strip_denials(entry.get("recommended_mitigation") or "", everywhere)
            if gone_m:
                entry["recommended_mitigation"] = new_m
                removed_claims.extend({"product": entry.get("product_name", ""), "text": g} for g in gone_m)

    scrubbed, gone = _scrub_summary(result.get("overall_summary", ""), matrix, line_items)
    if gone:
        removed_claims.extend({"product": "Overall summary", "text": g} for g in gone)
        result["overall_summary"] = scrubbed or (
            "Restricted verticals confirmed: " + ", ".join(c.name for c in matrix["categories"])
            + ". See each product's verdict from the Restricted Verticals sheet below."
        )
    result["categories"] = [c.name for c in matrix["categories"]]
    result["unresolved_categories"] = list(matrix.get("unresolved") or [])
    result["removed_claims"] = removed_claims


def _parse(raw: str, used_web_search: bool, client=None, matrix: Optional[dict] = None, line_items=None) -> dict:
    try:
        data = llm_utils.parse_json_object(raw, expect_any=_ROADBLOCKS_KEYS, client=client)
    except ValueError as exc:
        return _error_result(str(exc), matrix, line_items)

    roadblocks = [r for r in (data.get("product_roadblocks") or []) if isinstance(r, dict)]
    for r in roadblocks:
        r.setdefault("product_name", "")
        r["risk_level"] = _level(r.get("risk_level"))
        r["recommended_mitigation"] = _normalize_newlines(str(r.get("recommended_mitigation") or ""))
        risks = [risk for risk in (r.get("risks") or []) if isinstance(risk, dict)]
        for risk in risks:
            risk.setdefault("issue", "")
            risk["detail"] = _normalize_newlines(str(risk.get("detail") or ""))
            risk.setdefault("source", "")
        r["risks"] = risks

    result = {
        "overall_summary": _normalize_newlines(data.get("overall_summary", "")),
        "product_roadblocks": roadblocks,
        "categories": [],
        "used_web_search": used_web_search,
        "error": None,
    }
    if matrix is not None and line_items is not None:
        _apply_matrix(result, matrix, line_items)
    return result
