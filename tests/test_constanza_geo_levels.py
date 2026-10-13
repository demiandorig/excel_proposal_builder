"""
Every geography level a planner can type into Constanza — ZIP (+4, lists),
county, congressional district, city, DMA, state, US/national, non-US
country — must resolve, scale with population, carry through the audience
layer, and say so out loud when part of an input can't be used. This is the
regression net for the "will it work for any geo level?" audit, which found
(before the fixes these assertions pin down): lists silently dropping every
item after the first, ZIP+4 / at-large districts / county-without-the-word-
"county" unrecognized, "United States" + the country selector unresolved,
Rochester MN mislabeled as Rochester NY in the crosswalk build, and
Hawaii / Connecticut districts missing entirely.
"""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.constanza import load_dma_reference, read_json_maybe_gz
from app.services.constanza_audience import AudienceSpec, estimate_audience
from app.services.constanza_engine import estimate_avails
from app.services.constanza_geo import resolve_geo

ROKU = "Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)"
VIX = "Entravision Plus - VIX 360 (Includes VIX + UnivisionNow + Univision.com / Univision Apps)"
DATA = Path(__file__).resolve().parents[1] / "app" / "data" / "constanza"


def pop(g):
    return sum(a.area_population for a in g.areas)


def labels(g):
    return [a.label for a in g.areas]


print("=" * 80)
print("Constanza: every geo level")
print("=" * 80)

# --- ZIP, ZIP+4, ZIP lists --------------------------------------------------
z = resolve_geo("95814")
assert z.method == "zip" and len(z.areas) == 1 and z.areas[0].dma == "Sacramento-Stockton-Modesto"
for variant in ("95814-1234", "ZIP 95814", "zip code: 95814"):
    zv = resolve_geo(variant)
    assert zv.method == "zip" and labels(zv) == ["ZIP 95814"], (variant, zv)

for text, hint in (("95814, 95815, 90210", None), ("95814 95815 90210", None),
                   ("95814, 95815, 90210", "zip"), ("95814\n95815\n90210", None), ("95814; 95815; 90210", None)):
    zl = resolve_geo(text, hint)
    assert zl.method == "zip" and labels(zl) == ["ZIP 95814", "ZIP 95815", "ZIP 90210"], (text, hint, labels(zl))
    assert not zl.unmatched_tokens
assert abs(pop(resolve_geo("95814, 95815, 90210")) - sum(pop(resolve_geo(x)) for x in ("95814", "95815", "90210"))) < 1e-6
assert labels(resolve_geo("95814, 95814")) == ["ZIP 95814"], "a repeated ZIP must count once"
print("ZIP, ZIP+4, and ZIP lists (comma / space / newline / semicolon): OK")

# --- County -----------------------------------------------------------------
c = resolve_geo("Sacramento County, CA")
assert c.method == "county" and labels(c) == ["Sacramento County, CA"]
assert labels(resolve_geo("Sacramento, CA", "county")) == ["Sacramento County, CA"], "no 'county' keyword needed once the type is chosen"
assert labels(resolve_geo("Sacramento CA", "county")) == ["Sacramento County, CA"]
cl = resolve_geo("Sacramento County, CA; Placer County, CA")
assert labels(cl) == ["Sacramento County, CA", "Placer County, CA"], labels(cl)
cl2 = resolve_geo("Sacramento County, CA, Placer County, CA, El Dorado County, CA")
assert len(cl2.areas) >= 3 and not cl2.unmatched_tokens, labels(cl2)
cl3 = resolve_geo("Sacramento, Placer, El Dorado", "county")  # no keyword, no states
assert not cl3.unmatched_tokens and not cl3.ambiguous, (cl3.unmatched_tokens, cl3.ambiguous)
assert {a.label for a in cl3.areas} >= {"Sacramento County, CA", "Placer County, CA", "El Dorado County, CA"}, labels(cl3)
assert labels(resolve_geo("Orleans Parish, LA")) == ["Orleans Parish, LA"]
assert labels(resolve_geo("Richmond city, VA", "county")) == ["Richmond city, VA"], "VA independent city is not Richmond County"
assert labels(resolve_geo("Saint Louis County, MO")) == ["St. Louis County, MO"]
print("County (keyword, hint, lists, parishes, independent cities, saint/st): OK")

# --- Congressional district --------------------------------------------------
cd = resolve_geo("CA-06")
assert cd.method == "congressional_district" and cd.geo_confidence == "medium"
for variant in ("CA6", "CA 6", "California's 6th district", "6th congressional district of California"):
    assert labels(resolve_geo(variant)) == labels(cd), variant
cdl = resolve_geo("CA-06, CA-07")
assert cdl.method == "congressional_district" and len({a.geo_id for a in cdl.areas}) == 2
wy = resolve_geo("WY-AL")
assert labels(wy) == labels(resolve_geo("Wyoming at-large")) == labels(resolve_geo("WY-00")) and wy.areas
assert wy.areas[0].label == "WY at-large congressional district"
assert resolve_geo("DC-AL").resolved
ct = resolve_geo("CT-05")  # was missing: 2023 CT county-equivalents broke the old county-based route
assert ct.resolved and abs(pop(ct) - 722_000) < 60_000, pop(ct)
hi = resolve_geo("HI-01")  # was missing: no Honolulu DMA polygon
assert hi.resolved and hi.areas[0].dma == "Honolulu"
bad = resolve_geo("WY-1")
assert not bad.resolved and "at-large" in bad.unmatched_details[0]["reason"]
bad = resolve_geo("CA-99")
assert not bad.resolved and "1–52" in bad.unmatched_details[0]["reason"]
cd_bundle = read_json_maybe_gz(DATA / "geo" / "cd_dma.json")
assert len(cd_bundle["cd_dma"]) >= 435, len(cd_bundle["cd_dma"])
print("Congressional district (shapes, lists, at-large, DC, CT, HI, bad district reasons, 435+ districts): OK")

# --- City ---------------------------------------------------------------------
assert labels(resolve_geo("Sacramento, CA", "city")) == ["Sacramento, CA"]
assert labels(resolve_geo("Sacramento CA", "city")) == ["Sacramento, CA"]
assert labels(resolve_geo("Saint Louis, MO", "city")) == ["St. Louis, MO"]
cityl = resolve_geo("Sacramento, CA; Fresno, CA", "city")
assert labels(cityl) == ["Sacramento, CA", "Fresno, CA"]
assert labels(resolve_geo("Sacramento, CA, Fresno, CA", "city")) == ["Sacramento, CA", "Fresno, CA"]
for text, expect in (("Honolulu, HI", "Honolulu"), ("Nashville, TN", "Nashville"), ("Louisville, KY", "Louisville"),
                     ("Lexington, KY", "Lexington")):
    g = resolve_geo(text, "city")
    assert g.resolved and expect in g.areas[0].label, (text, labels(g), g.unmatched_details)
spring = resolve_geo("Springfield", "city")
assert not spring.resolved and spring.ambiguous
cands = [c_["label"] for e in spring.ambiguous for c_ in e["candidates"]]
assert any(c_.startswith("Springfield, MO") for c_ in cands) and len(cands) == len(set(cands)), "no duplicate picker rows"
assert cands[0].startswith("Springfield, MO"), "biggest first"
mixed_amb = resolve_geo("Springfield, Sacramento, CA", "city")
assert labels(mixed_amb) == ["Sacramento, CA"] and mixed_amb.ambiguous, "an ambiguous item must not block or vanish"
# Two places with the SAME name in one state (OH has two Lafayettes): the largest is used and the planner is told.
# ("Goodyear Village, AZ" used to collide with "Goodyear, AZ" only because its type word was stripped from the name.)
lafayette = resolve_geo("Lafayette, OH", "city")
assert lafayette.resolved and lafayette.notes and "largest" in lafayette.notes[0]
assert [a.label for a in resolve_geo("Goodyear, AZ", "city").areas] == ["Goodyear, AZ"]
print("City (state forms, lists, saint/st, consolidated governments, ambiguity, same-name dedupe): OK")

# --- DMA, state, national, US-as-country --------------------------------------
dma = resolve_geo("Los Angeles")
assert dma.method == "exact_dma" and len(dma.areas) == 1
assert resolve_geo("Los Angeles", "dma").method == "exact_dma", "dma hint must not break resolution"
assert resolve_geo("California", "state").method == "state_list"
st = resolve_geo("CA, NV, AZ")
assert st.method == "state_list" and len(st.dmas) >= 14 and st.notes, "state caveat note must be present"
assert resolve_geo("Los Angeles, San Diego").method == "exact_dma" and len(resolve_geo("Los Angeles, San Diego").areas) == 2
nat = resolve_geo("National")
assert nat.method == "national" and len(nat.areas) == 210
for us in ("US", "USA", "United States", "U.S.", "United States of America", "America"):
    for hint in (None, "country"):
        g = resolve_geo(us, hint)
        assert g.method == "national" and len(g.areas) == 210 and g.geo_confidence == "high", (us, hint, g.method)
print("DMA, state, national, and US-as-country (with and without the country selector): OK")

# Every workbook DMA name must resolve to exactly itself — a planner copies them
# straight from the avails workbook, and the ambiguity picker offers them as
# resolve hints. (Audit found 25 of 210 failing: "Washington, DC (Hagerstown)"
# returned Seattle, "Columbus, OH" returned every Ohio market, "Hartford & New
# Haven" and "Tri-Cities, TN-VA" didn't resolve, and picking a candidate from the
# picker just re-raised the same ambiguity.)
for d in load_dma_reference():
    g = resolve_geo(d.dma)
    assert g.method == "exact_dma" and [a.dma for a in g.areas] == [d.dma] and not g.ambiguous and not g.unmatched_tokens, d.dma
print("All 210 workbook DMA names round-trip to exactly themselves: OK")

# A state right after a place QUALIFIES it ("Sacramento, CA" = the Sacramento market,
# not Sacramento plus all 11 California markets) — only when that place has a market there.
assert [a.dma for a in resolve_geo("Sacramento, CA").areas] == ["Sacramento-Stockton-Modesto"]
assert [a.dma for a in resolve_geo("Columbus, OH").areas] == ["Columbus, OH"]
assert [a.dma for a in resolve_geo("Columbus, GA").areas] == ["Columbus, GA (Opelika, AL)"]
assert [a.dma for a in resolve_geo("Opelika, AL").areas] == ["Columbus, GA (Opelika, AL)"]
assert [a.dma for a in resolve_geo("Washington, DC").areas] == ["Washington, DC (Hagerstown)"]
assert [a.dma for a in resolve_geo("Austin, MN").areas] == ["Rochestr-Mason City-Austin"]
assert [a.dma for a in resolve_geo("Austin, TX").areas] == ["Austin"]
assert [a.dma for a in resolve_geo("Rochester, MN").areas] == ["Rochestr-Mason City-Austin"]
assert [a.dma for a in resolve_geo("Portland, Oregon").areas] == ["Portland, OR"]
assert [a.dma for a in resolve_geo("Hartford & New Haven, Boston").areas] == ["Hartford & New Haven", "Boston (Manchester)"]
nv = resolve_geo("Los Angeles, NV")  # LA has no market in NV: two separate items (LA + all of NV)
assert nv.method == "mixed" and "Los Angeles" in nv.dmas and "Las Vegas" in nv.dmas
ny_dma, ny_state = resolve_geo("New York"), resolve_geo("New York State")
assert ny_dma.method == "exact_dma" and len(ny_dma.areas) == 1 and ny_dma.notes, "bare 'New York' is the NYC market, with a note"
assert ny_state.method == "state_list" and len(ny_state.areas) > 3 and resolve_geo("NY").dmas == ny_state.dmas
assert resolve_geo("Washington State").dmas == resolve_geo("WA").dmas
assert resolve_geo("Rochester").ambiguous and not resolve_geo("Rochester").resolved, "bare Rochester is NY vs MN — surfaced, not guessed"
print("City+state qualifiers, punctuated DMA names, 'X State', New York, Rochester: OK")

# --- Non-US country -------------------------------------------------------------
mx = resolve_geo("Mexico")
assert mx.method == "country" and mx.geo_confidence == "low_confidence_international"
mxl = resolve_geo("Mexico, Canada", "country")
assert [a.label for a in mxl.areas] == ["Mexico", "Canada"]
est = estimate_avails(VIX, mxl, months=1, national_monthly_total=1_000_000)
assert est is not None and abs(est.imps_mid - 1_000_000) < 1 and len(est.by_area) == 2
assert estimate_avails(ROKU, mxl, months=1, national_monthly_total=None) is None
print("Non-US country (single, list, population-share spread, no data without a total): OK")

# --- Lists mixing levels / partial failures are never silent ----------------------
mix = resolve_geo("95814, Los Angeles")
assert mix.method == "mixed" and {a.source_type for a in mix.areas} == {"zip", "dma"} and mix.notes
mix2 = resolve_geo("95814\n95815\nPlacer County, CA")
assert len(mix2.areas) == 3 and mix2.method == "mixed"
part = resolve_geo("95814, 99501, 90210")  # 99501 = Anchorage
assert labels(part) == ["ZIP 95814", "ZIP 90210"]
assert part.unmatched_tokens == ["99501"] and "Alaska" in part.unmatched_details[0]["reason"]
for text, hint, needle in (
    ("99501", None, "Alaska"), ("Anchorage, AK", "city", "Alaska"), ("Anchorage Municipality, AK", "county", "Alaska"),
    ("AK-00", None, "Alaska"), ("00000", None, "ZCTA"), ("asdfgh", "zip", "ZIP code"), ("asdfgh", "city", "city"),
    ("Sacramento County, XX", None, None),
):
    g = resolve_geo(text, hint)
    if needle:
        assert not g.resolved and needle in g.unmatched_details[0]["reason"], (text, hint, g.unmatched_details)
    else:
        assert "XX" in g.unmatched_tokens, "an invalid state must be reported, not ignored"
print("Mixed-level lists, partial failures, and unsupported places (AK/ZIP/state typos) are reported with reasons: OK")

# --- Every level carries real avails + the audience layer ---------------------------
CASES = [("95814", None), ("95814, 95815", None), ("Sacramento County, CA", None), ("CA-06", None),
         ("Sacramento, CA", "city"), ("Sacramento", None), ("CA", None), ("National", None), ("HI-01", None),
         ("CT-05", None), ("Honolulu, HI", "city")]
spec = AudienceSpec(age_min=18, age_max=24, gender="male", interest_terms=["likes sports"])
for text, hint in CASES:
    g = resolve_geo(text, hint)
    base = estimate_avails(ROKU, g, months=1)
    assert base is not None and base.imps_mid > 0, (text, hint)
    aud = estimate_audience(base, spec)
    assert 0 < aud.imps_mid < base.imps_mid, (text, hint, aud.imps_mid, base.imps_mid)
    assert aud.imps_low < aud.imps_mid < aud.imps_high
    assert aud.underlying_population <= pop(g) * 1.0001, (text, aud.underlying_population, pop(g))
print(f"Roku avails + 'men 18-24 who like sports' work at all {len(CASES)} sampled geography levels: OK")

# --- Avails scale with population across levels ---------------------------------------
imps = {k: estimate_avails(ROKU, resolve_geo(t, h), months=1).imps_mid for k, (t, h) in {
    "zip": ("95814", None), "city": ("Sacramento, CA", "city"), "county": ("Sacramento County, CA", None),
    "dma": ("Sacramento", None), "national": ("National", None)}.items()}
assert imps["zip"] < imps["city"] < imps["county"] < imps["dma"] < imps["national"], imps
print("ZIP < city < county < DMA < national: OK")

# --- The crosswalk data itself ---------------------------------------------------------
acs_dma = read_json_maybe_gz(DATA / "acs_age_sex_dma.json")
ratios = {}
for d in load_dma_reference():
    if d.state == "AK":
        assert d.dma not in acs_dma, "Alaska has no polygon — must be reported unsupported, not silently invented"
        continue
    ratios[d.dma] = acs_dma[d.dma]["total_population"] / d.population
assert 0.95 <= statistics.median(ratios.values()) <= 1.10, statistics.median(ratios.values())
outliers = {k: round(v, 2) for k, v in ratios.items() if not 0.5 <= v <= 1.4}
assert not outliers, f"DMAs whose boundary-derived population is far from the workbook's: {outliers}"
# The Rochester bug: two polygons shared one label, Rochester NY came out ~31% high and Rochester MN had nothing.
assert 0.8 <= ratios["Rochestr-Mason City-Austin"] <= 1.25, ratios["Rochestr-Mason City-Austin"]
assert 0.8 <= ratios["Rochester, NY"] <= 1.25, ratios["Rochester, NY"]
assert 0.9 <= ratios["Honolulu"] <= 1.2, ratios["Honolulu"]  # synthesized polygon = the state of Hawaii
print(f"Crosswalk coverage: median DMA ratio {statistics.median(ratios.values()):.2f}, Rochester MN/NY and Honolulu sane: OK")

print()
print("All Constanza geo-level checks passed.")
