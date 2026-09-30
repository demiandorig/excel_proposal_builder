"""
Constanza's audience/demographic estimation layer — extends the DMA/geo-
weighted avails engine (constanza_engine.py) with age, gender, and
interest targeting: "men 18-24 in Sacramento who like sports."

Method: independence-assumption synthetic estimation — a real, named
small-area-estimation technique (see the Phase 2 plan doc's research
citations), applied honestly. This is NOT equivalent to how a syndicated
panel (Nielsen Scarborough, MRI-Simmons) produces an audience number —
those rake an actual JOINT survey sample to census controls. Here, each
factor (age/gender share, interest incidence, geographic search skew) was
measured independently and is multiplied together assuming no
correlation between them — a real, disclosed simplification, not a claim
of parity with panel-based measurement.

    audience_imps(geo, age x gender, interests, months) =
        dma_baseline(geo, product, months)        # constanza_engine.py, already area-weighted
      x age_gender_share(geo)                       # this area's own ACS age x sex data when available
      x PRODUCT of interest_incidence(interest_i)    # curated, SOURCED rates only — see interest_incidence.json
      x PRODUCT of geo_interest_multiplier(interest_i, area)  # Google Ads search-volume skew — see constanza_search.py; defaults to 1.0 (no-op) until configured

The LLM (reused from app/services/llm_utils.py, same OPENAI_API_KEY/
_HAS_OPENAI degrade pattern as app/services/ai_enricher.py) has exactly
two jobs, both classification, NEVER number generation:
  1. Parse free text into a structured AudienceSpec.
  2. Map a free-text interest phrase to the nearest category in
     interest_incidence.json's known taxonomy, or report it unsupported.
The LLM is never asked for, and its output never becomes, an incidence
rate or a final number. When no LLM is configured, parsing falls back to
a plain keyword/substring match against the same taxonomy — narrower, but
still never invents a rate.

Confidence: every additional targeting cut compounds uncertainty from
factors that were never jointly measured. Rather than attach a caveat
sentence next to an unchanged, falsely-precise range (the exact "dressing
up guesswork in formula-shaped language" failure an adversarial review of
this design caught), each active factor gets a documented, coarse
coefficient-of-variation guess, combined via root-sum-of-squares to widen
imps_low/imps_high — not a real derived statistic (there's no sample to
compute one from), but honest about compounding rather than static.
Per product decision: never refuse a number outright — always return one,
with `below_reliability_threshold` + the actual floor stated alongside it
so the planner can judge for themselves rather than see a blank.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.data.constanza import read_json_maybe_gz
from app.services.constanza_engine import AreaEstimate, AvailsEstimate

try:
    from openai import OpenAI as _OpenAI
    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False

import os

from app.services import llm_utils

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "constanza"
_MODEL = "gpt-5.1"

# Coarse, documented uncertainty guesses per factor (see module docstring)
# — NOT derived statistics. Combined via root-sum-of-squares when active.
_CV_GEO = {"exact": 0.05, "hud_ratio": 0.10, "tract_overlap": 0.15,
           "census_relationship_file": 0.20, "single_county_place": 0.10,
           "population_share_national": 0.35}
_CV_AGE_GENDER = 0.05      # ACS-anchored — well measured
_CV_INTEREST = 0.30        # curated, not sampled
_CV_SEARCH_APPLIED = 0.20  # only counted when the multiplier actually fired (not a no-op 1.0)

RELIABILITY_FLOOR_POPULATION = 10_000  # a starting heuristic, not a derived constant — see module docstring


@dataclass
class AudienceSpec:
    age_min: int | None = None
    age_max: int | None = None
    gender: str | None = None          # "male" | "female" | "all" | None (unspecified)
    interest_terms: list[str] = field(default_factory=list)
    geo_text: str = ""
    geo_type_hint: str | None = None   # "dma" | "state" | "zip" | "county" | "congressional_district" | "city" | "country"


@dataclass
class InterestResolution:
    input_text: str
    category: str | None          # matched interest_incidence.json category key, or None
    supported: bool
    rate_by_gender: dict | None = None
    source: str | None = None
    source_date: str | None = None


@dataclass
class AudienceAreaEstimate:
    area_label: str
    dma: str | None
    weight: float
    imps_low: float
    imps_mid: float
    imps_high: float
    underlying_population: float


@dataclass
class AudienceEstimate:
    base: AvailsEstimate
    age_gender_share_applied: bool
    interests: list[InterestResolution]
    search_multiplier_applied: bool
    imps_low: float
    imps_mid: float
    imps_high: float
    underlying_population: float
    reliability_floor_population: float
    below_reliability_threshold: bool
    confidence_tier: str   # "audience_modeled_geo_only" | "audience_modeled_single_cut" | "audience_modeled_stacked"
    cuts_applied: int
    by_area: list[AudienceAreaEstimate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def load_interest_taxonomy() -> list[dict]:
    return json.loads((_DATA_DIR / "interest_incidence.json").read_text())["categories"]


def _keyword_match_interest(text: str) -> dict | None:
    text_lower = text.lower()
    for cat in load_interest_taxonomy():
        if any(kw in text_lower for kw in cat["keywords"]):
            return cat
    return None


def _openai_client():
    if not _HAS_OPENAI:
        return None
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    return _OpenAI(api_key=api_key)


def resolve_interest(text: str) -> InterestResolution:
    """Maps free text to a taxonomy category via keyword match first (fast,
    deterministic, no API call); if that fails and an LLM is configured,
    asks it to pick the nearest category from the known list — NEVER to
    invent a rate. Unmatched either way comes back unsupported, per the
    stats research's own recommendation: treat any trait without a
    sourced rate as unsupported rather than having the model guess."""
    direct = _keyword_match_interest(text)
    if direct:
        return InterestResolution(
            input_text=text, category=direct["category"], supported=True,
            rate_by_gender=direct["rate_by_gender"], source=direct["source"], source_date=direct["source_date"],
        )

    client = _openai_client()
    if client is None:
        return InterestResolution(input_text=text, category=None, supported=False)

    categories = load_interest_taxonomy()
    options = "\n".join(f"- {c['category']}: {c['label']}" for c in categories)
    prompt = (
        f"A media planner described a target audience interest as: {text!r}\n\n"
        f"Which ONE of these known categories, if any, does it most closely match? "
        f"Reply with ONLY the category key (e.g. \"sports_fan\") or the single word \"none\" "
        f"if it doesn't confidently match any of them. Do not invent a new category.\n\n{options}"
    )
    try:
        raw = llm_utils.chat_text(client, model=_MODEL, prompt=prompt, max_completion_tokens=20).strip().lower()
    except Exception:
        return InterestResolution(input_text=text, category=None, supported=False)

    match = next((c for c in categories if c["category"].lower() == raw), None)
    if match is None:
        return InterestResolution(input_text=text, category=None, supported=False)
    return InterestResolution(
        input_text=text, category=match["category"], supported=True,
        rate_by_gender=match["rate_by_gender"], source=match["source"], source_date=match["source_date"],
    )


def parse_audience_text(text: str) -> AudienceSpec:
    """Free text -> AudienceSpec. Falls back to a spec with only geo_text
    set (age/gender/interests left unspecified) when no LLM is configured
    — the UI's manual fields let the planner fill in the rest directly
    rather than blocking the feature."""
    client = _openai_client()
    if client is None:
        return AudienceSpec(geo_text=text)

    prompt = (
        "Parse this media-planning audience description into JSON with keys "
        "age_min (int or null), age_max (int or null), gender (\"male\"|\"female\"|\"all\"|null), "
        "interest_terms (list of short phrases, e.g. [\"sports\"]), geo_text (the geography "
        "phrase, verbatim, e.g. \"Sacramento\"), geo_type_hint (one of \"dma\",\"state\",\"zip\","
        "\"county\",\"congressional_district\",\"city\",\"country\", or null if unclear). "
        "Return ONLY the JSON object, no other text.\n\n"
        f"Description: {text!r}"
    )
    try:
        raw = llm_utils.chat_text(client, model=_MODEL, prompt=prompt, max_completion_tokens=300)
        data = llm_utils.parse_json_object(raw, expect_any=("geo_text",), client=client)
    except Exception:
        return AudienceSpec(geo_text=text)

    return AudienceSpec(
        age_min=data.get("age_min"), age_max=data.get("age_max"),
        gender=data.get("gender"), interest_terms=data.get("interest_terms") or [],
        geo_text=data.get("geo_text") or text, geo_type_hint=data.get("geo_type_hint"),
    )


@lru_cache(maxsize=None)  # 5 distinct ACS bundles are cycled through per request — maxsize=4 would thrash
def _acs_bundle(name: str) -> dict:
    return read_json_maybe_gz(_DATA_DIR / name)


_ACS_FILE_BY_SOURCE_TYPE = {
    "zip": "acs_age_sex_zcta.json", "county": "acs_age_sex_county.json",
    "congressional_district": "acs_age_sex_cd.json", "city": "acs_age_sex_place.json",
    # DMA/state-level age/sex data is AGGREGATED from county-level ACS data
    # (scripts/aggregate_constanza_acs_dma_state.py) — without it, the
    # majority of today's traffic (whole-DMA/state geo queries) would have
    # no age/gender data to narrow against at all.
    "dma": "acs_age_sex_dma.json",
}


def _age_gender_share(area: AreaEstimate, age_min: int | None, age_max: int | None, gender: str | None) -> tuple[float, bool]:
    """Returns (share, used_real_area_data). Uses this exact area's ACS
    age/sex bands when available (area.geo_id + area.source_type point at
    one of the ACS bundles); a whole-DMA/state/country area has no single
    ACS geo_id of its own, so this falls back to `False` (share=1.0,
    i.e. no age/gender narrowing applied) rather than guessing at a
    national distribution — the caller should treat that as "this cut
    wasn't actually applied", not as a confident 100% match."""
    if age_min is None and age_max is None and (gender is None or gender == "all"):
        return 1.0, True  # no demographic narrowing requested at all — trivially "applied"

    acs_file = _ACS_FILE_BY_SOURCE_TYPE.get(area.source_type)
    if acs_file is None or area.geo_id is None:
        return 1.0, False

    bundle = _acs_bundle(acs_file)
    entry = bundle.get(area.geo_id)
    if entry is None or not entry.get("total_population"):
        return 1.0, False

    total = entry["total_population"]
    matched = 0.0
    for band in entry["age_sex"]:
        band_lo, band_hi = band["age_min"], band["age_max"] if band["age_max"] is not None else 200
        lo = age_min if age_min is not None else 0
        hi = age_max if age_max is not None else 200
        if band_hi < lo or band_lo > hi:
            continue  # this age band doesn't overlap the requested range at all
        # Partial-band overlap is apportioned by the overlapping fraction
        # of the band's own width — a coarse but reasonable treatment
        # given ACS only publishes these fixed 5ish-year bands, not
        # single-year counts.
        band_width = (band["age_max"] if band["age_max"] is not None else band_lo + 15) - band_lo + 1
        overlap = min(hi, band_hi if band["age_max"] is not None else hi) - max(lo, band_lo) + 1
        frac = max(0.0, min(1.0, overlap / band_width))
        if gender in ("male", "female"):
            matched += band[gender] * frac
        else:
            matched += (band["male"] + band["female"]) * frac

    return (matched / total if total else 0.0), True


def estimate_audience(base: AvailsEstimate, spec: AudienceSpec) -> AudienceEstimate:
    """Applies the age/gender/interest multiplier stack on top of an
    already-computed, already-area-weighted AvailsEstimate (constanza_
    engine.py) — the geo/platform weighting is untouched; this only
    narrows it further by demographic and interest cuts. Each
    constanza_engine.AreaEstimate in `base.by_area` carries its own
    originating ResolvedArea fields (population/geo_id/apportionment/
    state) forward, so this never needs to re-resolve geo text or assume
    a positional match against a separate GeoResolution."""
    interests = [resolve_interest(t) for t in spec.interest_terms]
    supported_interests = [i for i in interests if i.supported]
    unsupported = [i for i in interests if not i.supported]

    cvs = []
    notes = list(base.notes)
    if unsupported:
        notes.append(
            f"No sourced incidence rate for: {', '.join(i.input_text for i in unsupported)} — "
            "excluded from the estimate rather than guessed. Ask the planner to confirm the "
            "nearest supported interest, or drop this targeting dimension."
        )

    age_gender_requested = spec.age_min is not None or spec.age_max is not None or (spec.gender not in (None, "all"))
    any_age_gender_applied = False

    total_underlying_pop = 0.0
    imps_mid_total = 0.0
    by_area: list[AudienceAreaEstimate] = []

    for area_est in base.by_area:
        share, applied = _age_gender_share(area_est, spec.age_min, spec.age_max, spec.gender)
        any_age_gender_applied = any_age_gender_applied or applied

        interest_multiplier = 1.0
        for interest in supported_interests:
            gender_key = spec.gender if spec.gender in interest.rate_by_gender else "all"
            interest_multiplier *= interest.rate_by_gender.get(gender_key, interest.rate_by_gender["all"])

        area_mid = area_est.imps_mid * share * interest_multiplier
        area_low = area_est.imps_low * share * interest_multiplier
        area_high = area_est.imps_high * share * interest_multiplier
        area_pop = area_est.area_population * share

        total_underlying_pop += area_pop
        imps_mid_total += area_mid
        by_area.append(AudienceAreaEstimate(
            area_label=area_est.area_label, dma=area_est.dma, weight=area_est.weight,
            imps_low=area_low, imps_mid=area_mid, imps_high=area_high, underlying_population=area_pop,
        ))

    if age_gender_requested and not any_age_gender_applied:
        notes.append(
            "Age/gender narrowing was requested but this geography has no ACS data of its own "
            "at this resolution (only whole-DMA/state/country matches lack this) — the age/gender "
            "cut was NOT applied; the number below is geography-only."
        )

    cuts_applied = (1 if (age_gender_requested and any_age_gender_applied) else 0) + len(supported_interests)

    # Confidence widening: root-sum-of-squares across active factors —
    # see module docstring. Geo CV comes from whichever apportionment
    # methods actually appear across the resolved areas (worst case wins).
    geo_cvs = [_CV_GEO.get(a.apportionment, 0.20) for a in base.by_area]
    cvs.append(max(geo_cvs) if geo_cvs else 0.05)
    if age_gender_requested and any_age_gender_applied:
        cvs.append(_CV_AGE_GENDER)
    if supported_interests:
        cvs.append(_CV_INTEREST)
    # constanza_search.py's geo_interest_multiplier() is ready to call here
    # once two things exist: real Google Ads credentials, AND a ZIP/county/
    # city -> Google Ads geo-target-criteria-ID crosswalk (not yet built —
    # see that module's docstring for why area.dma can't be used directly).
    # Until both exist the cache is empty and every lookup would fail open
    # to 1.0 anyway, so it's left uncalled here rather than wired to a
    # crosswalk that doesn't exist yet.
    search_multiplier_applied = False
    if search_multiplier_applied:
        cvs.append(_CV_SEARCH_APPLIED)

    cv_total = math.sqrt(sum(c * c for c in cvs))
    imps_low_total = imps_mid_total * max(0.0, 1 - cv_total)
    imps_high_total = imps_mid_total * (1 + cv_total)

    below_floor = total_underlying_pop < RELIABILITY_FLOOR_POPULATION and cuts_applied > 0
    if cuts_applied == 0:
        tier = "audience_modeled_geo_only"
    elif cuts_applied == 1:
        tier = "audience_modeled_single_cut"
    else:
        tier = "audience_modeled_stacked"
        notes.append(f"{cuts_applied} targeting cuts stacked on this geography — directional, not precise.")

    if below_floor:
        notes.append(
            f"Underlying population ~{total_underlying_pop:,.0f} — below the "
            f"~{RELIABILITY_FLOOR_POPULATION:,.0f} reliability floor for this cut. Shown as an "
            "order-of-magnitude estimate, not a precise figure."
        )

    return AudienceEstimate(
        base=base, age_gender_share_applied=any_age_gender_applied, interests=interests,
        search_multiplier_applied=search_multiplier_applied,
        imps_low=imps_low_total, imps_mid=imps_mid_total, imps_high=imps_high_total,
        underlying_population=total_underlying_pop,
        reliability_floor_population=RELIABILITY_FLOOR_POPULATION,
        below_reliability_threshold=below_floor, confidence_tier=tier, cuts_applied=cuts_applied,
        by_area=by_area, notes=notes,
    )
