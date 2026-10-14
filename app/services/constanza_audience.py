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
      x PRODUCT of interest_incidence(interest_i)    # SOURCED / DERIVED rates only — see audience_taxonomy.py and audience_taxonomy.json
      x PRODUCT of geo_interest_multiplier(interest_i, area)  # Google Ads search-volume skew — see constanza_search.py; defaults to 1.0 (no-op) until configured

The LLM (reused from app/services/llm_utils.py, same OPENAI_API_KEY/
_HAS_OPENAI degrade pattern as app/services/ai_enricher.py) has exactly
two jobs, both classification, NEVER number generation:
  1. Parse free text into a structured AudienceSpec.
  2. Map a free-text interest phrase to the nearest category in
     the audience taxonomy (audience_taxonomy.py), or report it unsupported.
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
from app.services import audience_taxonomy
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
_CV_INTEREST = 0.30        # curated, not sampled (used when a segment carries no confidence grade)
# How far a published rate can be trusted, from the research verdict (audience_taxonomy.json): a direct recent survey of the
# exact definition is tighter than a proxy or an old figure. Still a coarse heuristic, not a measured standard error.
_CV_INTEREST_BY_CONFIDENCE = {"high": 0.15, "moderate": 0.25, "low": 0.40}
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
    category: str | None          # the matched taxonomy segment id (audience_taxonomy.py), or None
    supported: bool               # True only when the segment has a SOURCED rate that is applied
    rate_by_gender: dict | None = None
    source: str | None = None
    source_date: str | None = None
    label: str | None = None
    group: str | None = None
    status: str | None = None     # sourced | derived | no_public_rate; None when nothing matched at all
    confidence: str | None = None
    definition: str | None = None
    source_url: str | None = None
    caveats: str | None = None
    how: str | None = None        # how it was matched: name | keyword | partial | words | fuzzy | llm
    gender_split: bool = False    # True when the source gives its OWN male and female rates (else 'all' stands in for both)
    suggestions: list = field(default_factory=list)   # the closest OTHER segments, [{id, label, supported}]


@dataclass
class AudienceAreaEstimate:
    area_label: str
    dma: str | None
    weight: float
    imps_low: float
    imps_mid: float
    imps_high: float
    underlying_population: float       # the CENSUS population of the market that fits the cuts (see AudienceEstimate)
    audience_people: float | None = None   # the product's reach universe here, narrowed by the same cuts
    unique_viewers: float | None = None    # its est. monthly uniques here, narrowed by the same cuts


@dataclass
class AudienceEstimate:
    base: AvailsEstimate
    age_gender_share_applied: bool
    interests: list[InterestResolution]
    search_multiplier_applied: bool
    imps_low: float
    imps_mid: float
    imps_high: float
    # The CENSUS population of the market that fits the cuts (adults when an interest rate, which is a share of adults, is
    # applied): how many people are in the area and fit, across all media. It is context and the basis of the reliability
    # floor — NOT what the product reaches, which is `audience_people`.
    underlying_population: float
    reliability_floor_population: float
    below_reliability_threshold: bool
    confidence_tier: str   # "audience_modeled_geo_only" | "audience_modeled_single_cut" | "audience_modeled_stacked"
    cuts_applied: int
    by_area: list[AudienceAreaEstimate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # What THIS PRODUCT reaches: the platform's reach universe for the geography (AvailsEstimate.reach_universe) narrowed by the
    # same age / gender / interest cuts as the impressions, so impressions >= audience always holds. None when the data behind
    # the estimate has no reach universe.
    audience_people: float | None = None
    est_uniques: float | None = None           # the est. monthly uniques narrowed the same way
    impressions_per_person: float | None = None   # monthly impressions / audience people (unchanged by the cuts)

    # True when the market population counts ADULTS (an interest rate was applied and every area had age data to clip to 18+); False
    # when it is everyone in the area that fits the age / gender cuts (no interest, or an area - a country - with no age data).
    market_population_adults: bool = False

    @property
    def market_population(self) -> float:
        return self.underlying_population


def load_interest_taxonomy() -> list[dict]:
    """The segments that have a sourced rate, one dict each (id as `category`, label, keywords, rate_by_gender, source...)."""
    return audience_taxonomy.load_taxonomy().categories()


def _openai_client():
    if not _HAS_OPENAI:
        return None
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    return _OpenAI(api_key=api_key)


def _suggestions(tax, text: str, exclude: str | None = None, limit: int = 3) -> list[dict]:
    return [{"id": h.segment.id, "label": h.segment.label, "supported": h.segment.supported}
            for h in tax.search(text, limit=limit + 1) if h.segment.id != exclude][:limit]


def _resolution(text: str, seg: "audience_taxonomy.Segment", how: str, tax) -> InterestResolution:
    rates = None
    if seg.supported:
        rates = {"all": seg.rate_all, "male": seg.rate_male if seg.rate_male is not None else seg.rate_all,
                 "female": seg.rate_female if seg.rate_female is not None else seg.rate_all}
    return InterestResolution(
        input_text=text, category=seg.id, supported=seg.supported, rate_by_gender=rates, source=seg.source_line() or None,
        source_date=seg.source_date, label=seg.label, group=seg.group_key, status=seg.status, confidence=seg.confidence,
        definition=seg.definition or None, source_url=seg.source_url, caveats=seg.caveats or None, how=how,
        gender_split=seg.supported and seg.rate_male is not None and seg.rate_female is not None,
        suggestions=_suggestions(tax, text, exclude=seg.id))


def resolve_interest(text: str) -> InterestResolution:
    """Maps free text to a taxonomy segment: its id or name, an alias, a phrase inside the text ("likes sports"), a near
    spelling — all deterministic, no API call. Only when none of that is close enough, and an LLM is configured, it is asked to
    pick the nearest segment from the known list — NEVER to invent a rate. A segment that is known but has no sourced rate
    comes back as not supported, with its status, so the planner is told why nothing was applied; text that matches
    nothing comes back unsupported with the closest segments to choose from."""
    tax = audience_taxonomy.load_taxonomy()
    hit = tax.best_match(text)
    if hit is not None:
        return _resolution(text, hit.segment, hit.how, tax)

    client = _openai_client()
    if client is not None:
        options = "\n".join(f"- {s.id}: {s.label}" for s in tax.ordered())
        prompt = (
            f"A media planner described a target audience interest as: {text!r}\n\n"
            f"Which ONE of these known audience segments, if any, does it most closely match? "
            f"Reply with ONLY the segment id (e.g. \"sports_fan\") or the single word \"none\" "
            f"if it doesn't confidently match any of them. Do not invent a new segment.\n\n{options}"
        )
        try:
            raw = llm_utils.chat_text(client, model=_MODEL, prompt=prompt, max_completion_tokens=20).strip().lower()
        except Exception:
            raw = "none"
        seg = tax.segments.get(raw)
        if seg is not None:
            return _resolution(text, seg, "llm", tax)
    return InterestResolution(input_text=text, category=None, supported=False, suggestions=_suggestions(tax, text, limit=4))


_GENDER_ALIASES = {
    "male": "male", "m": "male", "man": "male", "men": "male", "boy": "male", "boys": "male",
    "female": "female", "f": "female", "woman": "female", "women": "female", "girl": "female", "girls": "female",
}
_GENDER_ALL = {"", "all", "any", "both", "everyone", "all genders", "no preference"}


def normalize_gender(value) -> str | None:
    """'male' / 'female', or None for no gender cut ('all', blank). Anything else raises ValueError: a typo must be told
    to the planner, not read as 'everyone' while still being counted as an applied cut."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in _GENDER_ALL:
        return None
    if text in _GENDER_ALIASES:
        return _GENDER_ALIASES[text]
    raise ValueError("Gender must be male, female or all.")


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

    try:
        gender = normalize_gender(data.get("gender"))
    except ValueError:
        gender = None                                   # a model's odd answer is dropped, never applied
    return AudienceSpec(
        age_min=data.get("age_min"), age_max=data.get("age_max"),
        gender=gender, interest_terms=data.get("interest_terms") or [],
        geo_text=data.get("geo_text") or text, geo_type_hint=data.get("geo_type_hint"),
    )


def _acs_bundle(name: str) -> dict:
    # The geo resolver already caches these (large) ACS bundles; reading them through the same cache keeps ONE parsed
    # copy per file in memory instead of one for the resolver and another for this layer.
    from app.services.constanza_geo_crosswalk import _load_acs
    return _load_acs(name)


_ACS_FILE_BY_SOURCE_TYPE = {
    "zip": "acs_age_sex_zcta.json", "county": "acs_age_sex_county.json",
    "congressional_district": "acs_age_sex_cd.json", "city": "acs_age_sex_place.json",
    # DMA/state-level age/sex data is AGGREGATED from county-level ACS data
    # (scripts/aggregate_constanza_acs_dma_state.py) — without it, the
    # majority of today's traffic (whole-DMA/state geo queries) would have
    # no age/gender data to narrow against at all.
    "dma": "acs_age_sex_dma.json",
}


ADULT_AGE = 18


def _age_gender_share(area: AreaEstimate, age_min: int | None, age_max: int | None, gender: str | None,
                      adults_only: bool = False) -> tuple[float, bool]:
    """Returns (share, used_real_area_data). `adults_only` counts only people from 18 up (an interest rate is a share of
    ADULTS, so the population it is applied to must not include children). Uses this exact area's ACS
    age/sex bands when available (area.geo_id + area.source_type point at
    one of the ACS bundles); a whole-DMA/state/country area has no single
    ACS geo_id of its own, so this falls back to `False` (share=1.0,
    i.e. no age/gender narrowing applied) rather than guessing at a
    national distribution — the caller should treat that as "this cut
    wasn't actually applied", not as a confident 100% match."""
    if not adults_only and age_min is None and age_max is None and (gender is None or gender == "all"):
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
        if adults_only:
            lo = max(lo, ADULT_AGE)
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
    try:
        gender = normalize_gender(spec.gender)
        gender_note = None
    except ValueError:
        gender = None
        gender_note = f"Gender {spec.gender!r} wasn't recognised (use male, female or all), so no gender cut was applied."
    interests = [resolve_interest(t) for t in spec.interest_terms]
    # Two phrases that map to the same category ("sports" and "football") are ONE interest — multiplying its incidence
    # rate in twice would shrink the audience by the rate squared for no reason.
    supported_interests = []
    seen_categories: set = set()
    duplicates: list[str] = []
    for i in interests:
        if not i.supported:
            continue
        if i.category in seen_categories:
            duplicates.append(i.input_text)
            continue
        seen_categories.add(i.category)
        supported_interests.append(i)
    unsupported = [i for i in interests if not i.supported]

    cvs = []
    notes = list(base.notes)
    if gender_note:
        notes.append(gender_note)
    known_without_rate = [i for i in unsupported if i.status == "no_public_rate"]
    unmatched = [i for i in unsupported if i.status is None]
    if known_without_rate:
        notes.append(
            "No sourced incidence rate exists for: " + ", ".join(f"{i.label or i.input_text}" for i in known_without_rate) +
            " — it is a known audience, but no defensible public number was found, so it was NOT applied (the estimate is "
            "wider than that audience). Ask the platform for its own segment size, or drop this targeting dimension."
        )
    if unmatched:
        close = {s_["label"] for i in unmatched for s_ in i.suggestions[:2]}
        notes.append(
            f"No sourced incidence rate for: {', '.join(i.input_text for i in unmatched)} — excluded from the estimate rather "
            "than guessed." + (f" Closest known audiences: {', '.join(sorted(close))}." if close else "")
        )
    for i in supported_interests:
        if i.confidence == "low":
            notes.append(f"'{i.label or i.input_text}' uses a low-confidence rate ({i.source or 'derived estimate'}); treat the result as directional.")

    if duplicates:
        notes.append(f"{', '.join(duplicates)} map to an interest already counted — applied once, not stacked.")

    age_gender_requested = spec.age_min is not None or spec.age_max is not None or gender is not None
    if gender is not None:
        for i in supported_interests:
            if not i.gender_split:
                notes.append(f"'{i.label or i.input_text}' has no sourced male/female split, so its all-adults rate was applied to the "
                             f"{gender} audience unchanged; for a gender-skewed audience that can over- or understate it.")
    any_age_gender_applied = False

    total_underlying_pop = 0.0
    imps_mid_total = 0.0
    people_total = 0.0
    people_known = False
    uniques_total = 0.0
    uniques_known = False
    adults_everywhere = True
    by_area: list[AudienceAreaEstimate] = []
    not_narrowed: list[str] = []

    for area_est in base.by_area:
        share, applied = _age_gender_share(area_est, spec.age_min, spec.age_max, gender)
        any_age_gender_applied = any_age_gender_applied or applied
        if age_gender_requested and not applied:
            not_narrowed.append(area_est.area_label)

        interest_multiplier = 1.0
        for interest in supported_interests:
            gender_key = gender if gender in interest.rate_by_gender else "all"
            interest_multiplier *= interest.rate_by_gender.get(gender_key, interest.rate_by_gender["all"])

        cut = share * interest_multiplier
        area_mid = area_est.imps_mid * cut
        area_low = area_est.imps_low * cut
        area_high = area_est.imps_high * cut
        # The product's audience and its uniques are cut exactly like its impressions, so the three always agree (the
        # audience is never larger than the impressions it is shown beside).
        area_people = area_est.reach_universe * cut if area_est.reach_universe is not None else None
        area_uniques = area_est.unique_viewers * cut if area_est.unique_viewers is not None else None
        # The census population that fits the cuts. An interest rate is a share of ADULTS, so with one applied the
        # population it multiplies is the adults' (children are not auto intenders); without one the share stands as is. The
        # interest cut is part of the size: a segment that is 0.1% of adults is a tiny audience however large the
        # geography, and the reliability warning has to see that.
        if supported_interests:
            pop_share, adults_known = _age_gender_share(area_est, spec.age_min, spec.age_max, gender, adults_only=True)
            adults_everywhere = adults_everywhere and adults_known          # no age data for the area (a country): everyone is counted, not adults
        else:
            pop_share = share
        area_pop = area_est.area_population * pop_share * interest_multiplier

        total_underlying_pop += area_pop
        imps_mid_total += area_mid
        if area_people is not None:
            people_total += area_people
            people_known = True
        if area_uniques is not None:
            uniques_total += area_uniques
            uniques_known = True
        by_area.append(AudienceAreaEstimate(
            area_label=area_est.area_label, dma=area_est.dma, weight=area_est.weight,
            imps_low=area_low, imps_mid=area_mid, imps_high=area_high, underlying_population=area_pop,
            audience_people=area_people, unique_viewers=area_uniques,
        ))

    if age_gender_requested and any_age_gender_applied and not_narrowed:
        notes.append(
            f"The age/gender cut was NOT applied to {', '.join(not_narrowed)} (no ACS data for it) — "
            "those areas are counted at full size, so the total reads high."
        )
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
    # One factor per distinct interest: each is its own curated, independence-assumed cut, so stacking them must widen
    # the band (they are the least-independent pair in practice — sports and gaming overlap).
    cvs.extend(_CV_INTEREST_BY_CONFIDENCE.get(i.confidence or "", _CV_INTEREST) for i in supported_interests)
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

    # The band starts from the BASE estimate's own range (the platform data's planning range, or +/-30% for the generic
    # fallback) and every active factor widens it; it is never narrower than the estimate it narrows.
    base_rel = 0.0
    if base.imps_mid > 0:
        base_rel = max(0.0, 1 - base.imps_low / base.imps_mid, base.imps_high / base.imps_mid - 1)
    cv_total = math.sqrt(base_rel * base_rel + sum(c * c for c in cvs))
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
            f"The market population that fits this cut (census) is ~{total_underlying_pop:,.0f} — below the "
            f"~{RELIABILITY_FLOOR_POPULATION:,.0f} reliability floor for this cut. Shown as an "
            "order-of-magnitude estimate, not a precise figure."
        )

    audience_people = people_total if people_known else None
    if supported_interests and age_gender_requested and spec.age_max is not None and spec.age_max < ADULT_AGE:
        notes.append("Interest rates describe adults (18+) and this age range has none: the market population is shown as 0, and the interest's "
                     "rate is applied to the age group only as a rough proxy.")
    elif audience_people is not None and total_underlying_pop > 0 and audience_people > total_underlying_pop:
        notes.append(f"This product's reach universe ({audience_people:,.0f}) is larger than the census population that fits these cuts "
                     f"({total_underlying_pop:,.0f}): the platform counts devices or IDs, not people, so read it as the product's own audience measure.")
    months = max(1, base.months)
    return AudienceEstimate(
        base=base, age_gender_share_applied=any_age_gender_applied, interests=interests,
        search_multiplier_applied=search_multiplier_applied,
        imps_low=imps_low_total, imps_mid=imps_mid_total, imps_high=imps_high_total,
        underlying_population=total_underlying_pop,
        reliability_floor_population=RELIABILITY_FLOOR_POPULATION,
        below_reliability_threshold=below_floor, confidence_tier=tier, cuts_applied=cuts_applied,
        by_area=by_area, notes=notes,
        audience_people=audience_people,
        est_uniques=uniques_total if uniques_known else None,
        impressions_per_person=(imps_mid_total / months / audience_people) if audience_people else None,
        market_population_adults=bool(supported_interests) and adults_everywhere,
    )
