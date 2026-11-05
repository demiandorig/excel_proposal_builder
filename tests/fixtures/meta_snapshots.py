"""Saved Meta estimates, as data, for the tests of the Meta plugin (app/static/constanza-meta.js) and the saved-proposal compatibility tests.

Three families, all plain dicts with no import from the app (so a test can feed them to the browser or the JS engine as they are):

* the OLD shape, availsData[lid].meta_forecast: what the Step 05 panel saved before estimates went into forecast_snapshot. Four variants exist in saved
  proposals: a live pull with languages, a live pull saved before languages existed, a figure typed by hand ("manual"), and a budget with no audience.
* the NEW shape, availsData[lid].forecast_snapshot: {v, method, saved_at, request, result, line}, built over the estimate answer of POST /api/constanza/estimate
  (the standard estimate plus a `meta` block; docs/constanza_methods.md and docs/meta_forecasting.md describe it).
* corrupt variants of a snapshot (a saved proposal is data anyone could have edited): the plugin must draw none of them as markup or throw out of a card.

Every number is the one `constanza_meta.model_auction` returns for a lower end of 1,000,000 (upper 1,200,000) over 3 months at the $12 Awareness CPM and a $2,000
retail budget: conservative / mid / upper = 4,300,000 / 5,500,000 / 8,600,000 a month.
"""
from __future__ import annotations

import copy

HOSTILE = '<img src=x onerror="window.__pwned=1">'

# ------------------------------------------------------------------------------------------------------------------ the old shape (meta_forecast)

LIVE_WITH_LANGUAGES = {
    "metric_type": "meta_audience_scenarios", "product_name": "P", "as_of": "2026-10-26T12:00:00+00:00", "retail_budget": 2000, "media_budget": 1000,
    "retail_cpm": 12, "media_cpm_assumed": 6, "retail_to_media_multiplier": 2, "months": 3, "budget_impressions": 166667,
    "potential_audience": {"lower": 1_000_000, "upper": 1_200_000, "source": "meta_api"},
    "scenarios": {"basis": "lower_end", "default": "mid", "multipliers": {"conservative": 4.3, "mid": 5.5, "upper": 8.6}, "months": 3, "est_uniques": 1_000_000,
                  "monthly": {"conservative": 4_300_000, "mid": 5_500_000, "upper": 8_600_000}, "flight": {"conservative": 12_900_000, "mid": 16_500_000, "upper": 25_800_000},
                  "retail_spend_at_flight": {"conservative": 154800, "mid": 198000, "upper": 309600.5}},
    "notes": ["Meta's audience size counts eligible accounts; it is not delivered reach and not a delivery forecast."],
    "meta_targeting": {"geo_type": "geo_market", "geo_key": "803", "geo_name": "Los Angeles · California · US", "age_min": 25, "age_max": 44, "gender": "female",
                       "detailed": [{"id": "6003107902433", "name": "Soccer", "type": "interests"}],
                       "languages": [{"id": "1001", "name": "Spanish"}, {"id": "1002", "name": "Spanish (Mexico)"}]},
    "proposal_geo": "Los Angeles", "target_text": "Adults 25-54",
}

LIVE_WITHOUT_LANGUAGES = copy.deepcopy(LIVE_WITH_LANGUAGES)
del LIVE_WITHOUT_LANGUAGES["meta_targeting"]["languages"]

MANUAL = copy.deepcopy(LIVE_WITH_LANGUAGES)
MANUAL["potential_audience"] = {"lower": 500, "upper": 500, "source": "manual"}
del MANUAL["meta_targeting"]

BUDGET_ONLY = copy.deepcopy(LIVE_WITH_LANGUAGES)
BUDGET_ONLY["potential_audience"] = None
BUDGET_ONLY["scenarios"] = None
del BUDGET_ONLY["meta_targeting"]

LEGACY = {"live+languages": LIVE_WITH_LANGUAGES, "live": LIVE_WITHOUT_LANGUAGES, "manual": MANUAL, "budget-only": BUDGET_ONLY}
SNAPSHOT = LIVE_WITH_LANGUAGES            # the name the old tests used

# What Step 05 knows about the line these were pulled for (Constanza's LineFacts): the same product, months, geography, target, budget and CPM, so the
# adopted snapshot is fresh; change any one of the six and it is stale.
LINE_FACTS = {"id": "L1", "product_name": "P", "method": "meta", "label": "P", "months": 3, "flight_days": 61, "has_dates": True,
              "geo_text": "Los Angeles", "target_text": "Adults 25-54", "budget": 2000, "cpm": 12, "hispanic": None}

# ------------------------------------------------------------------------------------------------------------------ the new shape (forecast_snapshot)

MULTIPLIERS = {"conservative": 4.3, "mid": 5.5, "upper": 8.6}

ESTIMATE = {
    "method": "meta", "product_name": "P", "platform": "meta", "confidence": "live_api", "geo_method": "meta_audience", "geo_confidence": "selected",
    "months": 3, "imps_low": 12_900_000, "imps_mid": 16_500_000, "imps_high": 25_800_000, "est_uniques": 1_000_000,
    "reach_universe": None, "series": None, "weeks": None,
    "notes": [
        "Meta's audience size counts eligible accounts; it is not delivered reach and not a delivery forecast.",
        "Monthly impressions are lower-end audience x 4.3 (conservative) / 5.5 (mid) / 8.6 (upper); mid is the default and the figure the Avails step takes. "
        "A flight is the monthly figure times its months.",
        "Budget impressions (what the retail budget buys at the planning CPM, 2x retail/media) are a cross-check, not avails.",
        "Meta counts people on its default placements (Facebook, Instagram, Messenger, Audience Network), as Ads Manager's Estimated audience size does.",
        "Meta's TV markets are Comscore markets, so a market's footprint can differ from the Nielsen markets used elsewhere in this app.",
    ],
    "unmatched_geo_tokens": [], "unmatched_geo_details": [], "ambiguous": [], "geo_interpretations": [], "geo_suggestions": [], "by_area": [],
    "hispanic_targeting": {"default": False, "on": False, "applies": False},
    "meta": {
        "audience": {"low": 1_000_000, "high": 1_200_000, "source": "meta_api"},
        "monthly": {"low": 4_300_000, "mid": 5_500_000, "high": 8_600_000},
        "scenarios": {"basis": "lower_end", "default": "mid", "multipliers": dict(MULTIPLIERS), "months": 3,
                      "monthly": {"conservative": 4_300_000, "mid": 5_500_000, "upper": 8_600_000},
                      "flight": {"conservative": 12_900_000, "mid": 16_500_000, "upper": 25_800_000},
                      "retail_spend_at_flight": {"conservative": 154800.0, "mid": 198000.0, "upper": 309600.5},
                      "media_spend_at_flight": {"conservative": 77400.0, "mid": 99000.0, "upper": 154800.25}, "est_uniques": 1_000_000},
        "targeting": {"geo": {"type": "geo_market", "key": "803", "label": "Los Angeles · California · US"}, "age_min": 25, "age_max": 44, "gender": "female",
                      "languages": [{"id": "1001", "name": "Spanish"}, {"id": "1002", "name": "Spanish (Mexico)"}],
                      "detailed": [{"id": "6003107902433", "name": "Soccer", "type": "interests"}]},
        "budget_check": {"retail_budget": 2000.0, "media_budget": 1000.0, "retail_cpm": 12.0, "media_cpm_assumed": 6.0, "retail_to_media_multiplier": 2.0,
                         "months": 3, "budget_impressions": 166667},
        "language_note": "Meta's languages are the language people have set Facebook or Instagram to, not a count of who speaks it.",
        "as_of": "2026-10-26T12:00:00+00:00",
    },
}

REQUEST = {
    "product_name": "P", "geo_text": "", "geo_type_hint": None, "months": 3, "flight_days": None,
    "meta": {"geo": {"type": "geo_market", "key": "803", "label": "Los Angeles · California · US"}, "age_min": 25, "age_max": 44, "gender": "female",
             "languages": [{"id": "1001", "name": "Spanish"}, {"id": "1002", "name": "Spanish (Mexico)"}],
             "targeting": [{"id": "6003107902433", "name": "Soccer", "type": "interests"}], "retail_budget": 2000.0, "retail_cpm": 12.0},
}

NATIVE = {
    "v": 1, "method": "meta", "saved_at": "2026-10-26T12:00:00+00:00", "request": REQUEST, "result": ESTIMATE,
    "line": {"product_name": "P", "method": "meta", "months": 3, "geo_text": "Los Angeles", "target_text": "Adults 25-54", "budget": 2000, "cpm": 12},
}


def native(**overrides) -> dict:
    """A fresh copy of the native snapshot (a snapshot is replaced, never mutated: copy before editing) with top-level keys replaced."""
    return {**copy.deepcopy(NATIVE), **copy.deepcopy(overrides)}


def estimate(**overrides) -> dict:
    """A fresh copy of the estimate answer with top-level keys replaced."""
    return {**copy.deepcopy(ESTIMATE), **copy.deepcopy(overrides)}


# ------------------------------------------------------------------------------------------------------------------ corrupt snapshots

def _map_leaves(value, fn):
    if isinstance(value, dict):
        return {k: _map_leaves(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_map_leaves(v, fn) for v in value]
    return fn(value)


def hostile(snapshot: dict) -> dict:
    """An otherwise valid snapshot with markup in every string (the method and the version stay, so it is still read as a Meta snapshot)."""
    out = _map_leaves(snapshot, lambda leaf: HOSTILE + leaf if isinstance(leaf, str) else leaf)
    out["v"], out["method"] = snapshot["v"], snapshot["method"]
    return out


def corrupt_variants(snapshot: dict | None = None) -> dict[str, dict]:
    """The saved snapshots a card must survive: nothing thrown out of its draw, nothing run, the other cards drawn (name -> snapshot)."""
    base = copy.deepcopy(snapshot or NATIVE)
    no_meta = copy.deepcopy(base)
    no_meta["result"]["meta"] = None
    bad_notes = copy.deepcopy(base)
    bad_notes["result"]["notes"] = "x"
    no_request = copy.deepcopy(base)
    no_request["request"] = None
    empty_result = copy.deepcopy(base)
    empty_result["result"] = {}
    strings = _map_leaves(copy.deepcopy(base), lambda leaf: str(leaf) if isinstance(leaf, (int, float)) and not isinstance(leaf, bool) else leaf)
    strings["v"] = 1
    return {
        "empty": {}, "only v and method": {"v": 1, "method": base["method"]}, "empty result": empty_result, "meta is null": no_meta, "notes is a string": bad_notes,
        "request is null": no_request, "numbers are strings": strings, "markup in every string": hostile(base),
    }
