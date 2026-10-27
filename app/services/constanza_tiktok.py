"""
Constanza x TikTok: the audience-size forecast for the TikTok product, set up the way TikTok Ads Manager sets up an ad group's
audience - locations, age, gender, languages, interests & behaviors (interest categories, additional interests, purchase intent,
video / creator / hashtag interactions), spending power, household income, device and placement - and answered by TikTok's own
estimator (POST /ad/audience_size/estimate/, see tiktok_client.py).

TikTok answers with a head count: the range of people ("accounts, 18 and over") who match. It does not forecast delivery, so
Entravision's planning rule turns the audience into impressions in three scenarios, all taken from the LOWER end of TikTok's range
(platform_scenarios.py): **monthly impressions = lower-end audience x 4.3 (conservative) / x 5.5 (mid, the default) / x 8.6 (upper)**.
A flight of N months is N times that; est. uniques is the lower-end audience (so the Avails step's frequency lands on exactly the mid
multiplier).

This module owns three things: the validated form of a targeting request (`Targeting`), the request TikTok's estimator takes
(`build_estimate_body`), and the answer as the estimate card shows it (`estimate`). The HTTP calls live in tiktok_client.py and the
mapping of Constanza's geography to TikTok location IDs in tiktok_geo.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from app.services import platform_scenarios, tiktok_client
from app.services.constanza_audience import normalize_gender
from app.services.constanza_geo import GeoResolution
from app.services.tiktok_client import STAGE_LABELS, STAGE_NOTES, AudienceSize, TikTokAPIError, TikTokClient
from app.services.tiktok_geo import Mapping, map_resolution

PRODUCT_KEY = "tiktok"                       # the forecast method that routes a product here (constanza_forecast_map)
PLACEMENT_TIKTOK = "PLACEMENT_TIKTOK"
PLACEMENT_PANGLE = "PLACEMENT_PANGLE"
PLACEMENTS = (PLACEMENT_TIKTOK, PLACEMENT_PANGLE)

# Ads Manager's age brackets (under-18s are left out of every estimate by TikTok itself).
AGE_GROUPS = (("AGE_18_24", "18–24"), ("AGE_25_34", "25–34"), ("AGE_35_44", "35–44"), ("AGE_45_54", "45–54"), ("AGE_55_100", "55+"))
# The campaign objective an estimate is asked as. It decides which targeting TikTok offers (purchase intent is not available for Reach).
OBJECTIVES = {
    "reach": {"label": "Reach", "objective_type": "REACH", "optimization_goal": "REACH"},
    "video_views": {"label": "Video views", "objective_type": "VIDEO_VIEWS", "optimization_goal": "ENGAGED_VIEW"},
    # TikTok asks for a promotion type with any objective other than Reach / Video views / Engagement (Get available locations)
    "traffic": {"label": "Traffic", "objective_type": "TRAFFIC", "optimization_goal": "CLICK", "promotion_type": "WEBSITE"},
}
# Purchase intent is offered for these objectives only (Estimate audience size: APP_PROMOTION, WEB_CONVERSIONS, LEAD_GENERATION, TRAFFIC, PRODUCT_SALES)
PURCHASE_INTENT_OBJECTIVES = ("traffic",)
ADULT_AGE_GROUPS = tuple(k for k, _ in AGE_GROUPS)
DEFAULT_OBJECTIVE = "reach"
HOUSEHOLD_INCOME = (("TOP5", "Top 5% of ZIP codes"), ("TOP10", "Top 10%"), ("TOP10_25", "Top 10–25%"), ("TOP25_50", "Top 25–50%"))
NETWORK_TYPES = (("WIFI", "Wi-Fi"), ("4G", "4G"), ("5G", "5G"), ("3G", "3G"), ("2G", "2G"))
OPERATING_SYSTEMS = (("IOS", "iOS"), ("ANDROID", "Android"))
VIDEO_PERIODS = (0, 7, 15)
VIDEO_ACTIONS = ["WATCHED_TO_END", "LIKED", "COMMENTED", "SHARED"]          # what Ads Manager uses for video interactions
CREATOR_ACTIONS = ["FOLLOWING", "VIEW_HOMEPAGE"]
HASHTAG_ACTIONS = ["VIEW_HASHTAG"]

MAX_IDS = 50
AGE_RESTRICTION_CODE = 40065          # "The targeting options are not supported for targeting the age group 13-17 in the US"
_ID_RE = re.compile(r"^\d{1,20}$", re.ASCII)
_LANG_RE = re.compile(r"^[a-z]{2,4}(?:-[A-Za-z]{2,4})?$")


def _as_list(label: str, raw: Any) -> list:
    """A list (or tuple) of values; None is an empty one. A bare string is not one: "12345" is not five IDs."""
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"{label} must be a list.")
    return list(raw)


def _ids(label: str, raw: Any) -> tuple[str, ...]:
    items = _as_list(label, raw)
    if len(items) > 10 * MAX_IDS:                          # refuse a flood before looking at it, then say the real limit below
        raise ValueError(f"Choose at most {MAX_IDS} {label.lower()}.")
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not _ID_RE.match(text):
            raise ValueError(f"{label} contains something that isn't a TikTok ID.")
        if text not in seen:
            seen.add(text)
            out.append(text)
    if len(out) > MAX_IDS:
        raise ValueError(f"Choose at most {MAX_IDS} {label.lower()}.")
    return tuple(out)


def _choices(label: str, raw: Any, allowed: dict[str, str] | set[str]) -> tuple[str, ...]:
    out: list[str] = []
    for item in _as_list(label, raw):
        text = str(item).strip()
        if text not in allowed:
            raise ValueError(f"{label}: “{text[:30]}” isn't one TikTok offers.")
        if text not in out:
            out.append(text)
    return tuple(out)


@dataclass(frozen=True)
class Targeting:
    """What the planner set up, validated. Empty means "not narrowed" (TikTok's own default), exactly like Ads Manager."""
    gender: Optional[str] = None                      # "male" | "female" | None (all)
    age_groups: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    interest_category_ids: tuple[str, ...] = ()       # general interests
    interest_keyword_ids: tuple[str, ...] = ()        # additional interests
    purchase_intention_ids: tuple[str, ...] = ()
    video_interaction_ids: tuple[str, ...] = ()
    creator_interaction_ids: tuple[str, ...] = ()
    hashtag_ids: tuple[str, ...] = ()
    video_period: int = 15                            # days of look-back for video interactions: 0 (any time), 7 or 15
    spending_power: Optional[str] = None              # "HIGH" | None
    household_income: tuple[str, ...] = ()
    operating_system: Optional[str] = None
    network_types: tuple[str, ...] = ()
    placements: tuple[str, ...] = (PLACEMENT_TIKTOK,)
    objective: str = DEFAULT_OBJECTIVE

    @property
    def interactions(self) -> bool:
        return bool(self.video_interaction_ids or self.creator_interaction_ids or self.hashtag_ids)

    @property
    def age_restricted(self) -> bool:
        """Does this targeting use options TikTok restricts for U.S. teens? TikTok's age-restriction rule (return code 40065): advertisers cannot
        reach 13-17 year olds with gender, interest & behavior, income, spending power or ZIP-code targeting, so the age groups must be spelled out
        without AGE_13_17. (A ZIP code lives in the mapped places, not here: estimate() adds it.) A plain location / age / language request never
        meets the rule."""
        return bool(self.gender or self.interest_category_ids or self.interest_keyword_ids or self.purchase_intention_ids or self.interactions
                    or self.household_income or self.spending_power)

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "Targeting":
        """Validate a request body's targeting. ValueError carries a planner-readable message; nothing is silently corrected."""
        raw = raw or {}
        if not isinstance(raw, dict):
            raise ValueError("The TikTok targeting must be an object.")
        try:
            gender = normalize_gender(raw.get("gender"))
        except ValueError:
            raise ValueError("Gender must be male, female or all.") from None
        age_groups = _choices("Age", raw.get("age_groups"), {k for k, _ in AGE_GROUPS})
        languages = []
        language_items = _as_list("Languages", raw.get("languages"))
        if len(language_items) > 200:
            raise ValueError("Choose at most 20 languages.")
        for code in language_items:
            code = str(code).strip()
            if not _LANG_RE.match(code):
                raise ValueError(f"“{code[:20]}” isn't a TikTok language code.")
            if code not in languages:
                languages.append(code)
        if len(languages) > 20:
            raise ValueError("Choose at most 20 languages.")
        placements = _choices("Placement", raw.get("placements") or [PLACEMENT_TIKTOK], set(PLACEMENTS))
        objective = str(raw.get("objective") or DEFAULT_OBJECTIVE)
        if objective not in OBJECTIVES:
            raise ValueError("Objective must be Reach, Video views or Traffic.")
        period = raw.get("video_period", 15)
        if period not in VIDEO_PERIODS:
            raise ValueError("The video interaction look-back must be 0, 7 or 15 days.")
        spending = raw.get("spending_power")
        if spending not in (None, "", "ALL", "HIGH"):
            raise ValueError("Spending power must be All or High.")
        os_ = raw.get("operating_system") or None
        if os_ is not None and os_ not in dict(OPERATING_SYSTEMS):
            raise ValueError("Operating system must be iOS or Android.")
        t = cls(
            gender=gender, age_groups=age_groups, languages=tuple(languages),
            interest_category_ids=_ids("Interest categories", raw.get("interest_category_ids")),
            interest_keyword_ids=_ids("Additional interests", raw.get("interest_keyword_ids")),
            purchase_intention_ids=_ids("Purchase intent categories", raw.get("purchase_intention_ids")),
            video_interaction_ids=_ids("Video interaction categories", raw.get("video_interaction_ids")),
            creator_interaction_ids=_ids("Creator interaction categories", raw.get("creator_interaction_ids")),
            hashtag_ids=_ids("Hashtags", raw.get("hashtag_ids")), video_period=int(period),
            spending_power="HIGH" if spending == "HIGH" else None,
            household_income=_choices("Household income", raw.get("household_income"), dict(HOUSEHOLD_INCOME)),
            operating_system=os_, network_types=_choices("Connection type", raw.get("network_types"), dict(NETWORK_TYPES)),
            placements=placements, objective=objective,
        )
        t.check_combinations()
        return t

    def check_combinations(self) -> None:
        """The combinations TikTok itself refuses (Estimate audience size, parameter notes), said before a call is spent on them."""
        if PLACEMENT_TIKTOK not in self.placements:
            # ZIP codes, household income and spending power need it, and it is what the product runs on
            raise ValueError("TikTok has to be one of the placements.")
        if self.interactions and self.placements != (PLACEMENT_TIKTOK,):
            raise ValueError("Video, creator and hashtag interactions work only when TikTok is the only placement.")
        if self.purchase_intention_ids and self.interest_keyword_ids:
            raise ValueError("Purchase intent and additional interests can't be used together - TikTok treats them as conflicting.")
        if self.purchase_intention_ids and self.objective not in PURCHASE_INTENT_OBJECTIVES:
            raise ValueError(f"Purchase intent isn't available for a {OBJECTIVES[self.objective]['label']} campaign - choose the Traffic objective to use it.")


def build_estimate_body(t: Targeting, mapping: Mapping, goal_override: Optional[str] = None, adult_ages: bool = False) -> dict:
    """The /ad/audience_size/estimate/ request for a targeting and its TikTok locations (the client adds the advertiser). A Reach
    estimate's optimization goal can be replaced (TIKTOK_OPTIMIZATION_GOAL, or `goal_override` from the diagnostics). `adult_ages` spells
    the five adult age groups out when the planner chose none: TikTok asks for that (return code 40065) when U.S. audiences are targeted
    by interests, and the estimate leaves out under-18s anyway, so it is the same audience."""
    preset = OBJECTIVES[t.objective]
    goal = preset["optimization_goal"]
    if t.objective == DEFAULT_OBJECTIVE:
        goal = (goal_override or tiktok_client.optimization_goal_override() or goal)
    body: dict[str, Any] = {
        "objective_type": preset["objective_type"], "optimization_goal": goal,
        "placement_type": "PLACEMENT_TYPE_NORMAL", "placements": list(t.placements),
    }
    if preset.get("promotion_type"):
        body["promotion_type"] = preset["promotion_type"]
    if mapping.location_ids:
        body["location_ids"] = mapping.location_ids
    if mapping.zipcode_ids:
        body["zipcode_ids"] = mapping.zipcode_ids
    if t.gender:
        body["gender"] = {"male": "GENDER_MALE", "female": "GENDER_FEMALE"}[t.gender]
    if t.age_groups:
        body["age_groups"] = [k for k, _ in AGE_GROUPS if k in t.age_groups]           # Ads Manager's order
    elif adult_ages:
        body["age_groups"] = list(ADULT_AGE_GROUPS)
    if t.languages:
        body["languages"] = list(t.languages)
    if t.interest_category_ids:
        body["interest_category_ids"] = list(t.interest_category_ids)
    if t.interest_keyword_ids:
        body["interest_keyword_ids"] = list(t.interest_keyword_ids)
    if t.purchase_intention_ids:
        body["purchase_intention_keyword_ids"] = list(t.purchase_intention_ids)
    actions = []
    if t.video_interaction_ids:
        actions.append({"action_scene": "VIDEO_RELATED", "action_period": t.video_period,
                        "video_user_actions": list(VIDEO_ACTIONS), "action_category_ids": list(t.video_interaction_ids)})
    if t.creator_interaction_ids:
        actions.append({"action_scene": "CREATOR_RELATED", "action_period": 0,
                        "video_user_actions": list(CREATOR_ACTIONS), "action_category_ids": list(t.creator_interaction_ids)})
    if t.hashtag_ids:
        actions.append({"action_scene": "HASHTAG_RELATED", "action_period": 0,
                        "video_user_actions": list(HASHTAG_ACTIONS), "action_category_ids": list(t.hashtag_ids)})
    if actions:
        body["actions"] = actions
    if t.household_income:
        body["household_income"] = list(t.household_income)
    if t.spending_power:
        body["spending_power"] = t.spending_power
    if t.operating_system:
        body["operating_systems"] = [t.operating_system]
    if t.network_types:
        body["network_types"] = list(t.network_types)
    return body


# ------------------------------------------------------------------------------------------------------------- the estimate

SLOT_NAME = platform_scenarios.SLOT        # conservative / mid / upper -> the estimate's low / mid / high

@dataclass
class TikTokEstimate:
    size: AudienceSize
    multipliers: dict                      # platform_scenarios.multipliers(): conservative / mid / upper, applied to the LOWER end of TikTok's range
    months: int
    mapping: Mapping
    targeting: Targeting
    request: dict
    adult_ages_sent: bool = False          # the adult age groups were spelled out because TikTok asked for it (40065)

    @property
    def factor(self) -> float:
        """The mid (default) multiplier: the frequency the Avails step shows, since the est. uniques are the lower-end audience."""
        return self.multipliers["mid"]

    @property
    def monthly(self) -> dict:
        """Monthly impressions in the three scenarios: the LOWER end of TikTok's audience x conservative / mid / upper multiplier."""
        return {SLOT_NAME[name]: self.size.lower * self.multipliers[name] for name in platform_scenarios.SCENARIOS}

    @property
    def scenarios(self) -> dict:
        return {name: {"multiplier": self.multipliers[name], "monthly": self.size.lower * self.multipliers[name],
                       "total": self.size.lower * self.multipliers[name] * self.months} for name in platform_scenarios.SCENARIOS}

    @property
    def total(self) -> dict:
        return {k: v * self.months for k, v in self.monthly.items()}

    def _rule(self) -> str:
        m = self.multipliers
        return f"TikTok's lower-end audience ({self.size.lower:,.0f}) x {m['conservative']:g} / {m['mid']:g} / {m['upper']:g} (conservative / mid / upper; mid is the default)"

    def notes(self) -> list[str]:
        t, size = self.targeting, self.size
        out = [
            f"Audience size is TikTok's own estimate of the TikTok accounts (18 and over) that match these settings. Monthly impressions are "
            f"{self._rule()} - Entravision's planning scenarios, not a delivery forecast from TikTok; real delivery depends on "
            "budget, bid and creative."
        ]
        if size.stage in (1, 2):
            out.append(f"TikTok calls this audience “{STAGE_LABELS[size.stage]}” ({STAGE_NOTES[size.stage]}); a narrow audience can limit delivery.")
        if t.objective != DEFAULT_OBJECTIVE:
            out.append(f"Asked of TikTok as a {OBJECTIVES[t.objective]['label']} campaign.")
        if self.adult_ages_sent:
            out.append("TikTok needs the age groups spelled out when U.S. audiences are targeted by interests, so all five adult groups were sent — "
                       "the same audience, since the estimate leaves out under-18s.")
        return out


def estimate(res: GeoResolution, targeting: Targeting, months: int, client: Optional[TikTokClient] = None,
             goal_override: Optional[str] = None) -> TikTokEstimate:
    """TikTok's audience size for a resolved geography and a targeting. TikTokAPIError for anything TikTok-side (not configured,
    refused, rate-limited, unreachable); ValueError for a request TikTok could not be asked (nothing placed, too many places)."""
    client = client or tiktok_client.get_client()
    mapping = map_resolution(res, client)
    if not mapping.usable:
        why = "; ".join(f"{u['input']} ({u['reason']})" for u in mapping.unmapped[:4])
        raise ValueError("None of those places could be found in TikTok" + (f": {why}." if why else "."))
    # once TikTok has asked for the ages (40065) the next request that carries such options says so up front - one that does not is asked
    # exactly as the planner set it up, so what is sent never depends on what was asked before
    adult = (bool(getattr(client, "adult_ages_needed", False)) and not targeting.age_groups
             and (targeting.age_restricted or bool(mapping.zipcode_ids)))
    body = build_estimate_body(targeting, mapping, goal_override, adult_ages=adult)
    try:
        size = client.estimate_audience(body)
    except TikTokAPIError as refused:
        if refused.code != AGE_RESTRICTION_CODE or "age_groups" in body:
            raise
        # TikTok: "manually specify an age_groups value that does not contain AGE_13_17". Spell the adult groups out and remember it
        body = build_estimate_body(targeting, mapping, goal_override, adult_ages=True)
        size = client.estimate_audience(body)
        client.adult_ages_needed = True
        adult = True
    return TikTokEstimate(size=size, multipliers=platform_scenarios.multipliers(), months=max(1, int(months)),
                          mapping=mapping, targeting=targeting, request=body, adult_ages_sent=adult and not targeting.age_groups)


# ------------------------------------------------------------------------------------------------------------- what the dock needs to draw its form

def static_options() -> dict:
    """The fixed menus of the TikTok panel (the lists that come from TikTok - languages, categories - are fetched on demand)."""
    return {
        "age_groups": [{"id": k, "label": v} for k, v in AGE_GROUPS],
        "objectives": [{"id": k, "label": v["label"]} for k, v in OBJECTIVES.items()],
        "default_objective": DEFAULT_OBJECTIVE,
        "household_income": [{"id": k, "label": v} for k, v in HOUSEHOLD_INCOME],
        "network_types": [{"id": k, "label": v} for k, v in NETWORK_TYPES],
        "operating_systems": [{"id": k, "label": v} for k, v in OPERATING_SYSTEMS],
        "placements": [{"id": PLACEMENT_TIKTOK, "label": "TikTok"}, {"id": PLACEMENT_PANGLE, "label": "Pangle (TikTok's partner apps)"}],
        "video_periods": list(VIDEO_PERIODS),
    }


def status() -> dict:
    """Whether the connection is set up, never its secrets: which settings are missing, the ad account's last four digits."""
    missing = tiktok_client.missing_config()
    return {
        "configured": not missing, "missing": missing, "advertiser": tiktok_client.advertiser_hint(),
        "impressions_per_person": platform_scenarios.multipliers()["mid"], "scenarios": platform_scenarios.to_public(),
        "setup_hint": tiktok_client.SETUP_HINT,
        "options": static_options(),
    }


def with_paths(items: list[dict]) -> list[dict]:
    """Interest categories come as a tree (children_ids); each item gets the names of its parents as `path`
    ("Apparel & Accessories › Women's Apparel") so a flat list can still be read and searched."""
    parent: dict[str, str] = {}
    by_id = {i["id"]: i for i in items}
    for i in items:
        for c in i.get("children_ids") or []:
            parent.setdefault(c, i["id"])
    out = []
    for i in items:
        names, seen, cur = [], {i["id"]}, parent.get(i["id"])
        while cur and cur in by_id and cur not in seen:
            seen.add(cur)
            names.append(by_id[cur]["name"])
            cur = parent.get(cur)
        out.append({"id": i["id"], "name": i["name"], "level": i.get("level"), "path": " › ".join(reversed(names)),
                    "has_children": bool(i.get("children_ids")), "hashtag_type": i.get("hashtag_type")})
    return out


CATALOG_KINDS = ("general_interest", "purchase_intention", "video_interaction", "creator_interaction", "hashtag_interaction")


def catalog(kind: str, client: Optional[TikTokClient] = None) -> list[dict]:
    """Every category of one kind TikTok offers (cached for hours by the client)."""
    if kind == "languages":
        return (client or tiktok_client.get_client()).languages()
    if kind not in CATALOG_KINDS:
        raise ValueError("Unknown kind of targeting.")
    # One request answers every kind (it is what TikTok sends by default) and is cached for hours, so each browse tab reads the same answer
    return with_paths((client or tiktok_client.get_client()).targeting_list().get(kind, []))


def search(q: str, client: Optional[TikTokClient] = None) -> dict[str, list[dict]]:
    """What a keyword finds: interest categories, additional interests, purchase intent, video / creator / hashtag interactions."""
    q = (q or "").strip()
    if len(q) < 2:
        return {}
    found = (client or tiktok_client.get_client()).targeting_search(q)
    return {kind: with_paths(items)[:25] for kind, items in found.items() if items}
