"""
Smoke-test Constanza's US sub-DMA geo crosswalks (ZIP/county/congressional
district/city) — built from real spatial joins against Nielsen DMA
boundaries (scripts/extract_constanza_geo_crosswalks.py) and real Census
ACS population (scripts/extract_constanza_acs.py), not assumptions.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.constanza_geo import resolve_geo

print("=" * 80)
print("Constanza sub-DMA geo crosswalks")
print("=" * 80)


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol * max(abs(a), abs(b), 1)


# --- ZIP -------------------------------------------------------------------
r = resolve_geo("95814")  # downtown Sacramento
assert r.method == "zip"
assert len(r.areas) == 1 and r.areas[0].dma == "Sacramento-Stockton-Modesto"
assert r.areas[0].area_population > 0
print(f"ZIP 95814 -> {r.areas[0].dma}, pop {r.areas[0].area_population:,.0f}: OK")

# --- City (the exact motivating example: Sacramento the CITY, not the DMA) -
r_default = resolve_geo("Sacramento")
assert r_default.method == "exact_dma"  # plain text still means the DMA by default (backward compatible)
r_city_ambiguous = resolve_geo("Sacramento", geo_type_hint="city")
assert r_city_ambiguous.method == "unresolved" and r_city_ambiguous.ambiguous
labels = {c["label"] for e in r_city_ambiguous.ambiguous for c in e["candidates"]}
assert "Sacramento, CA" in labels
print("Bare 'Sacramento' + city hint -> correctly ambiguous (CA/KY/NM): OK")

r_city = resolve_geo("Sacramento, CA", geo_type_hint="city")
assert r_city.method == "city"
assert len(r_city.areas) == 1
area = r_city.areas[0]
assert area.dma == "Sacramento-Stockton-Modesto"
assert 400_000 < area.area_population < 700_000  # Sacramento city's real population is ~525K
print(f"'Sacramento, CA' city hint -> {area.dma}, pop {area.area_population:,.0f}: OK")

# --- County, including the real Solano County DMA split --------------------
# NOTE ON THE TWO DIFFERENT "shares" IN PLAY HERE: `area_population` per
# area correctly reflects the GEOMETRIC split — how much of the county's
# OWN population sits in each DMA (Sacramento County: ~99.5%/0.5%; Solano:
# ~90%/10%). `dma_share` means something else — this area's population AS
# A FRACTION OF ITS PARENT DMA'S TOTAL population (used by
# constanza_engine.py to scale that DMA's national-derived avails total
# down to just this area) — for any sub-DMA area that's necessarily well
# under 1.0, however geometrically "whole" the area's containment is.
r_county = resolve_geo("Sacramento County, CA")
assert r_county.method == "county"
county_area_pop = {a.dma: a.area_population for a in r_county.areas}
total_county_pop = sum(county_area_pop.values())
assert county_area_pop.get("Sacramento-Stockton-Modesto", 0) / total_county_pop > 0.98  # geometric split
assert all(0 < a.dma_share < 1 for a in r_county.areas)  # population share of parent DMA, never the whole DMA
print(f"'Sacramento County, CA' -> geometric split {[(k, round(v/total_county_pop, 4)) for k, v in county_area_pop.items()]}, "
      f"dma_share (pop. share of parent DMA) {[(a.dma, round(a.dma_share, 4)) for a in r_county.areas]}: OK")

r_solano = resolve_geo("Solano County, CA")
assert r_solano.method == "county"
solano_area_pop = {a.dma: a.area_population for a in r_solano.areas}
total_solano_pop = sum(solano_area_pop.values())
solano_geometric_split = {k: round(v / total_solano_pop, 2) for k, v in solano_area_pop.items()}
assert "Sacramento-Stockton-Modesto" in solano_geometric_split and "San Francisco-Oakland-San Jose" in solano_geometric_split
assert solano_geometric_split["Sacramento-Stockton-Modesto"] > solano_geometric_split["San Francisco-Oakland-San Jose"]
print(f"'Solano County, CA' (real split county) -> geometric split {solano_geometric_split}: OK")

# --- The specific bug an adversarial spatial-join check caught: a bare
# "County, ST" query must NOT get swallowed by the plain state matcher
# just because it contains a trailing state abbreviation. ------------------
assert r_county.method != "state_list"
print("'Sacramento County, CA' is NOT swallowed by the state-list matcher: OK")

# --- Congressional district --------------------------------------------
r_cd = resolve_geo("CA-06")
assert r_cd.method == "congressional_district"
assert any(a.dma == "Sacramento-Stockton-Modesto" for a in r_cd.areas)
cd_total_pop = sum(a.area_population for a in r_cd.areas)
assert cd_total_pop > 0
assert all(0 < a.dma_share < 1 for a in r_cd.areas)  # each slice is a fraction of ITS parent DMA, never the whole DMA
print(f"'CA-06' (total pop {cd_total_pop:,.0f}) -> "
      f"{[(a.dma, f'{a.area_population / cd_total_pop:.4f} of CD', f'{a.dma_share:.4f} of parent DMA') for a in r_cd.areas]}: OK")

r_cd_long = resolve_geo("California's 6th district")
assert r_cd_long.method == "congressional_district"
assert {a.dma for a in r_cd_long.areas} == {a.dma for a in r_cd.areas}
print("Free-text CD phrasing ('California's 6th district') resolves the same as 'CA-06': OK")

# --- The name-collision bug an adversarial check caught: "Columbus" must
# come back ambiguous, not silently resolve to whichever DMA happens to be
# processed first (verified: Columbus, OH / Columbus, GA / Columbus-Tupelo
# all exist as real, distinct DMAs). ----------------------------------------
r_columbus = resolve_geo("Columbus")
assert r_columbus.method == "unresolved" and r_columbus.ambiguous
columbus_labels = {c["label"] for e in r_columbus.ambiguous for c in e["candidates"]}
assert {"Columbus, OH", "Columbus, GA (Opelika, AL)"}.issubset(columbus_labels)
print(f"'Columbus' (real 3-way DMA name collision) -> ambiguous {columbus_labels}: OK")

# --- Nothing real ------------------------------------------------------
r_none = resolve_geo("00000")
assert r_none.method == "unresolved"
print("Invalid ZIP '00000' -> unresolved: OK")

# --- The single most serious bug this whole crosswalk layer had: a sub-
# DMA area's `dma_share` was being populated from the geometric "what
# fraction of MYSELF falls in this DMA" figure (used correctly for
# apportioning a split geography's OWN population, e.g. Solano County
# above) instead of "what fraction of the DMA's TOTAL population do I
# represent" — which is what constanza_engine.py actually needs to scale
# the DMA's aggregate avails down to just this area's slice. A single ZIP
# (dma_share should be near 0) was coming back with dma_share=1.0 and
# therefore claiming the ENTIRE parent DMA's avails for itself — verified
# live in the browser (ZIP 95814, pop. 12,355, returned the identical
# figure as the whole city of Sacramento, pop. 524,802) before the fix.
# This regression test checks the actual scaling relationship holds:
# smaller population -> proportionally smaller dma_share -> proportionally
# smaller avails, strictly ordered ZIP < city < county < whole DMA. -------
from app.services.constanza_engine import estimate_avails

ROKU = "Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)"
geo_dma = resolve_geo("Sacramento")
pop_dma = sum(a.area_population for a in geo_dma.areas)
zip_geo = resolve_geo("95814", geo_type_hint="zip")
imps_zip = estimate_avails(ROKU, zip_geo, months=1).imps_mid
imps_city = estimate_avails(ROKU, r_city, months=1).imps_mid
imps_county = estimate_avails(ROKU, r_county, months=1).imps_mid
imps_full_dma = estimate_avails(ROKU, geo_dma, months=1).imps_mid

assert imps_zip < imps_city < imps_county < imps_full_dma, (imps_zip, imps_city, imps_county, imps_full_dma)
# And proportional to population, not identical to the whole DMA regardless of size:
assert close(imps_zip / imps_full_dma, zip_geo.areas[0].area_population / pop_dma, tol=1e-3)
print(f"Avails scale strictly with population (ZIP {imps_zip:,.0f} < city {imps_city:,.0f} < "
      f"county {imps_county:,.0f} < whole DMA {imps_full_dma:,.0f}): OK")

print()
print("All Constanza sub-DMA crosswalk checks passed.")
