"""
Deterministic recommendation engine.

Given a parsed ProposalRequest and a target monthly budget, suggest a mix of
products that:
  1. Respects product minimums from the catalog
  2. Aligns with the campaign goal (Traffic, Awareness, Conversions, etc.)
  3. Honors products the salesperson already selected
  4. Stays within the monthly budget total

This is intentionally rule-based (not LLM-based) so billing logic is auditable.
An LLM layer can be added later for free-text rationale generation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.catalog import CATALOG, by_name, by_family
from app.services.notion_parser import ProposalRequest
from app.services.proposal_generator import LineItem


# ---------------------------------------------------------------------------
# Goal → product family priority
# ---------------------------------------------------------------------------

GOAL_PRIORITIES: dict[str, list[str]] = {
    "traffic": [
        "Search - SEM",
        "Facebook & Instagram Ads | Traffic / Conversion",
        "eDigital Network Display - Standard IAB",
        "YouTube Ads",
    ],
    "awareness": [
        "Entravision Plus CTV/OTT - English Content",
        "Video - Pre-roll (OLV)",
        "YouTube Ads",
        "AudioEngage",
        "Facebook & Instagram Ads | Awareness",
    ],
    "conversions": [
        "Search - SEM",
        "Facebook & Instagram Ads | Traffic / Conversion",
        "eDigital Network Display - Standard IAB",
    ],
    "lead_gen": [
        "Search - SEM",
        "Facebook & Instagram Ads | Lead Gen / Calls",
        "LinkedIn",
        "Email Campaigns and/or Email Campaigns - Re-Drop",
    ],
    "engagement": [
        "Facebook & Instagram Ads | Awareness",
        "Tiktok Ads",
        "Spotify",
        "YouTube Ads",
    ],
    "default": [
        "Search - SEM",
        "Facebook & Instagram Ads | Awareness",
        "Entravision Plus CTV/OTT - English Content",
        "eDigital Network Display - Standard IAB",
    ],
}


def _classify_goal(goal_text: str) -> str:
    """Map a free-text campaign goal to a known priority bucket."""
    if not goal_text:
        return "default"
    g = goal_text.lower()
    if "traffic" in g or "drive to website" in g or "clicks" in g or "visits" in g:
        return "traffic"
    if "awareness" in g or "reach" in g or "branding" in g or "impressions" in g:
        return "awareness"
    if "conversion" in g or "purchase" in g or "sale" in g or "transaction" in g:
        return "conversions"
    if "lead" in g or "form fill" in g or "sign up" in g or "sign-up" in g:
        return "lead_gen"
    if "engagement" in g or "follow" in g or "video view" in g:
        return "engagement"
    return "default"


# ---------------------------------------------------------------------------
# Recommender
# ---------------------------------------------------------------------------

def recommend_line_items(
    request: ProposalRequest,
    monthly_budget: float,
    strategy_brief: Optional[dict] = None,
) -> list[LineItem]:
    """
    Build a suggested set of LineItems given a target monthly budget.

    Strategy:
        1. Seed with whatever the salesperson already selected (matched products).
        2. If budget remains, add goal-priority products until budget is exhausted
           OR the priority list is empty.
        3. Each addition respects the product's monthly minimum.
        4. Allocate remaining budget proportionally to a sensible split.

    Returns a list of LineItem objects with sensible default flight length.
    """
    if monthly_budget <= 0:
        return []

    months = request.total_months or 3

    # If a confirmed AI strategy brief is provided, use its tactic recommendations
    # as the primary product priority + budget weights instead of goal-based rules.
    if strategy_brief and strategy_brief.get("recommended_tactics"):
        return _recommend_from_brief(request, monthly_budget, months, strategy_brief)

    goal = _classify_goal(request.campaign_goal)
    priority = GOAL_PRIORITIES.get(goal, GOAL_PRIORITIES["default"])

    # 1. Build chosen list: salesperson-selected first, then goal priorities
    chosen: list[str] = []
    for name in request.products_selected:
        if name not in chosen:
            chosen.append(name)
    for name in priority:
        if name not in chosen:
            chosen.append(name)

    # 2. Filter to products that fit at all (minimum <= budget)
    fitting: list[tuple[str, float]] = []
    for name in chosen:
        p = by_name(name)
        if p is None:
            continue
        min_spend = p.minimum_spend or 0.0
        if min_spend <= monthly_budget:
            fitting.append((name, min_spend))

    if not fitting:
        # Budget too small for any product — return the cheapest catalog item as a fallback
        cheapest = min(
            (p for p in CATALOG if (p.minimum_spend or 0) > 0),
            key=lambda p: p.minimum_spend or float("inf"),
        )
        return [LineItem(
            product_name=cheapest.name,
            monthly_budget=cheapest.minimum_spend or monthly_budget,
            months=months,
        )]

    # 3. Decide how many lines to use (cap at 5 for readability)
    max_lines = min(5, len(fitting))

    # 4. Allocate budget: start each at its minimum, then split remaining
    #    proportionally to a goal-weighted curve favoring the first products.
    selected = fitting[:max_lines]
    minimums_total = sum(m for _, m in selected)

    if minimums_total > monthly_budget:
        # Too many lines for the budget — peel them off from the end until we fit
        while selected and sum(m for _, m in selected) > monthly_budget:
            selected.pop()

    if not selected:
        # Same fallback as above
        cheapest = min(
            (p for p in CATALOG if (p.minimum_spend or 0) > 0),
            key=lambda p: p.minimum_spend or float("inf"),
        )
        return [LineItem(
            product_name=cheapest.name,
            monthly_budget=cheapest.minimum_spend or monthly_budget,
            months=months,
        )]

    minimums_total = sum(m for _, m in selected)
    leftover = monthly_budget - minimums_total

    # Weight curve: 40, 25, 15, 12, 8 for up to 5 items (sums to 100)
    base_weights = [0.40, 0.25, 0.15, 0.12, 0.08]
    weights = base_weights[:len(selected)]
    weight_sum = sum(weights)
    weights = [w / weight_sum for w in weights]

    line_items: list[LineItem] = []
    for (name, minimum), w in zip(selected, weights):
        extra = leftover * w
        monthly = round(minimum + extra, 2)
        # Round to a nicer increment (nearest $50)
        monthly = round(monthly / 50) * 50
        if monthly < minimum:
            monthly = minimum
        line_items.append(LineItem(
            product_name=name,
            monthly_budget=float(monthly),
            months=months,
            target_override=request.demo or None,
        ))

    # Fix rounding overrun: if total > budget, trim the largest line
    total = sum(li.monthly_budget for li in line_items)
    if total > monthly_budget and line_items:
        overage = total - monthly_budget
        largest = max(line_items, key=lambda li: li.monthly_budget)
        largest.monthly_budget = round(largest.monthly_budget - overage, 2)
        # Don't drop below minimum
        p = by_name(largest.product_name)
        if p and largest.monthly_budget < (p.minimum_spend or 0):
            largest.monthly_budget = p.minimum_spend or 0.0

    return line_items


# ---------------------------------------------------------------------------
# AI-brief-driven recommendation
# ---------------------------------------------------------------------------

def _recommend_from_brief(
    request: ProposalRequest,
    monthly_budget: float,
    months: int,
    strategy_brief: dict,
) -> list[LineItem]:
    """
    Build line items driven by the AI strategy brief's recommended tactics.
    Each tactic maps to a catalog family; we pick the first (cheapest-minimum)
    product from that family that fits the budget allocation.
    """
    tactics = strategy_brief.get("recommended_tactics") or []

    # Normalise percentages so they sum to 100
    total_pct = sum(t.get("suggested_budget_pct", 0) for t in tactics) or 100
    line_items: list[LineItem] = []

    for tactic in tactics:
        family = tactic.get("product_family", "")
        raw_pct = tactic.get("suggested_budget_pct", 0)
        pct = raw_pct / total_pct  # normalised fraction

        products_in_family = by_family(family)
        if not products_in_family:
            continue  # unknown family — skip

        # Pick the salesperson-selected product from this family if any; else
        # the cheapest VIABLE (independently biddable) product in it.
        #
        # "Viable" excludes custom-quote-only lines like "Talent endorsement
        # / fee" — minimum_spend=0 AND no base_rate is this catalog's
        # signature for "see Sales Planning for a quote," not a real $0
        # product. Picking bare-cheapest without this filter meant a family
        # match against e.g. "Social" (broader than the specific "Branded
        # Content" family a tactic actually meant) would always resolve to
        # whatever $0-minimum custom-quote line happened to sit in that
        # family, regardless of whether it made any sense as a recommendation.
        preferred = None
        for name in request.products_selected:
            p = by_name(name)
            if p and p.family == family:
                preferred = p
                break
        if preferred is None:
            viable = [p for p in products_in_family if (p.minimum_spend or 0) > 0 or p.base_rate is not None]
            candidates = viable or products_in_family  # fall back rather than error if a family is ALL custom-quote
            preferred = min(candidates, key=lambda p: p.minimum_spend or 0)

        alloc = round(monthly_budget * pct / 50) * 50  # round to $50
        min_spend = preferred.minimum_spend or 0.0
        alloc = max(alloc, min_spend)

        if alloc > monthly_budget:
            continue  # can't fit even at minimum — skip

        line_items.append(LineItem(
            product_name=preferred.name,
            monthly_budget=float(alloc),
            months=months,
            target_override=request.demo or None,
        ))

    # If brief mapping yielded nothing, fall back to goal-based logic
    if not line_items:
        return recommend_line_items(request, monthly_budget, strategy_brief=None)

    # Trim total to budget (adjust largest if over)
    total = sum(li.monthly_budget for li in line_items)
    if total > monthly_budget and line_items:
        overage = total - monthly_budget
        largest = max(line_items, key=lambda li: li.monthly_budget)
        p = by_name(largest.product_name)
        adjusted = largest.monthly_budget - overage
        largest.monthly_budget = max(adjusted, p.minimum_spend or 0.0) if p else max(adjusted, 0.0)

    return line_items
