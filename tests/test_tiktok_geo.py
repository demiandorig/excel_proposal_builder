"""Mapping a Constanza geography onto TikTok location IDs (app/services/tiktok_geo.py), against the fake TikTok API.

The names TikTok gives its TV markets are not known to the code, so the matching is checked against six naming conventions - with
and without a state, abbreviated (the Nielsen workbook's "Orlando-Daytona Bch-Melbrn") and written out - through TikTok's market
list AND through its search: every one of the 210 markets must land on its own market, and a market that cannot be told apart from
another, or that names another state, or that TikTok has switched off, must be REPORTED - a wrong place is worse than a missing one."""
from __future__ import annotations

import pytest

from app.data.constanza import load_dma_reference
from app.services import tiktok_geo as tg
from app.services.constanza_geo import GeoResolution, ResolvedArea, resolve_geo
from tests import tiktok_support as ts
from tests.tiktok_support import STATE_IDS, US_ID, FakeResponse, FakeTikTok, dma_id, make_client, ok

STYLES = ["plain", "state", "comma_state", "dma_suffix", "full_plain", "full_state", "google", "state_name"]
DMAS = list(load_dma_reference())
BY_NAME = {d.dma: d for d in DMAS}


@pytest.fixture(autouse=True)
def forget():
    tg.forget_everything()
    yield
    tg.forget_everything()


def area(source_type, label, state=None, dma=None, geo_id=None):
    return ResolvedArea(dma=dma, area_population=1.0, dma_share=1.0, source_type=source_type, apportionment="exact", state=state, label=label, geo_id=geo_id)


def market(d):
    return ResolvedArea(dma=d.dma, area_population=d.population, dma_share=1.0, source_type="dma", apportionment="exact", state=d.state, label=d.dma, geo_id=d.dma)


def resolution(areas, method="exact_dma", whole_states=(), confidence="high"):
    return GeoResolution(input_text="x", method=method, areas=list(areas), geo_confidence=confidence, whole_states=list(whole_states))


def ids(mapping):
    return [l.tiktok_id for l in mapping.locations]


def candidate(name, geo_id, enabled=True):
    return {"name": name, "geo_id": geo_id, "enabled": enabled, "geo_type": "DMA"}


# --------------------------------------------------------------------------------------------- names

def test_the_words_of_a_name_leave_out_filler_and_state_codes_but_keep_a_leading_la():
    assert tg._words("Los Angeles, CA") == (("los", "angeles"), {"CA"})
    assert tg._words("Boston MA-Manchester NH") == (("boston", "manchester"), {"MA", "NH"})
    assert tg._words("Los Angeles DMA")[0] == ("los", "angeles")
    assert tg._words("La Crosse-Eau Claire")[0] == ("la", "crosse", "eau", "claire")
    assert tg._words("Champaign & Springfield-Decatur")[0] == ("champaign", "springfield", "decatur")
    assert tg._words("")[0] == ()


def test_saint_fort_and_mount_are_the_same_word_written_short_or_long():
    assert tg._words("St. Louis")[0] == tg._words("Saint Louis MO")[0] == ("saint", "louis")
    assert tg._words("Dallas-Ft. Worth")[0] == tg._words("Dallas-Fort Worth TX")[0] == ("dallas", "fort", "worth")
    assert tg._words("Mt. Pleasant, MI") == (("mount", "pleasant"), {"MI"})
    assert tg._words("Billings, MT") == (("billings",), {"MT"})          # MT after a name is Montana, not Mount


def test_a_word_written_short_is_the_same_word_but_a_different_word_is_not():
    for short, long_ in [("ashevll", "asheville"), ("lncstr", "lancaster"), ("fals", "falls"), ("kalmzoo", "kalamazoo"), ("hztn", "hazleton"), ("pnt", "point")]:
        assert tg._same_word(short, long_) and tg._same_word(long_, short)
    for a, b in [("la", "los"), ("columbus", "columbia"), ("albany", "alexandria"), ("dallas", "denver"), ("ab", "abc")]:
        assert not tg._same_word(a, b)


def test_the_first_city_of_a_market_is_its_search_word():
    assert tg._primary("Dallas-Ft. Worth") == "Dallas"
    assert tg._primary("Phoenix (Prescott)") == "Phoenix"
    assert tg._primary("Portland, OR") == "Portland"
    assert tg._lead("Dallas-Ft. Worth") == ("dallas",) and tg._lead("St. Louis") == ("saint", "louis")


def test_a_market_must_share_the_words_of_the_name():
    best, why = tg.pick_market("Los Angeles", "CA", [candidate("Los Angeles CA", "1"), candidate("Santa Barbara CA", "2")])
    assert best["geo_id"] == "1" and why is None
    best, why = tg.pick_market("Los Angeles", "CA", [candidate("Santa Barbara CA", "2")])
    assert best is None and "no market" in why
    assert tg.pick_market("Los Angeles", "CA", [])[0] is None


def test_two_markets_that_fit_equally_are_reported_not_guessed():
    best, why = tg.pick_market("Columbus", None, [candidate("Columbus", "1"), candidate("Columbus", "2")])
    assert best is None and "more than one" in why


def test_the_home_state_decides_between_markets_of_one_name_when_the_names_carry_it():
    cands = [candidate("Portland OR", "1"), candidate("Portland-Auburn ME", "2")]
    assert tg.pick_market("Portland, OR", "OR", cands)[0]["geo_id"] == "1"
    assert tg.pick_market("Portland-Auburn", "ME", cands)[0]["geo_id"] == "2"


def test_a_market_is_never_matched_to_one_that_names_another_state():
    """The review's reproductions: Columbus OH -> "Columbus GA", Jackson MS -> "Jackson TN" were silently picked when it was the only candidate."""
    assert tg.pick_market("Columbus, OH", "OH", [candidate("Columbus GA", "1")])[0] is None
    assert tg.pick_market("Jackson, MS", "MS", [candidate("Jackson TN", "1")])[0] is None
    assert tg.pick_market("Portland, OR", "OR", [candidate("Portland-Auburn ME", "1")])[0] is None
    # ... while a name that carries its states in a different shape is still the same market
    assert tg.pick_market("Washington, DC (Hagerstown)", "MD", [candidate("Washington DC", "1")])[0]["geo_id"] == "1"
    assert tg.pick_market("Columbus, GA (Opelika, AL)", "GA", [candidate("Columbus GA", "1")])[0]["geo_id"] == "1"


def test_a_market_tiktok_lists_but_has_switched_off_is_reported_not_swapped_for_another():
    cands = [candidate("Portland OR", "1", enabled=False), candidate("Portland-Auburn ME", "2")]
    best, why = tg.pick_market("Portland, OR", "OR", cands)
    assert best is None and "not available for targeting" in why


def test_the_very_same_first_city_beats_a_longer_one_that_merely_starts_with_it():
    cands = [candidate("Charlotte", "1"), candidate("Charlottesville", "2")]
    assert tg.pick_market("Charlotte", "NC", cands)[0]["geo_id"] == "1"
    assert tg.pick_market("Charlottesville", "VA", cands)[0]["geo_id"] == "2"


def test_a_first_city_written_short_still_finds_its_market_when_nothing_is_exact():
    cands = [candidate("Idaho Falls-Pocatello (Jackson) ID", "1"), candidate("Boise ID", "2")]
    assert tg.pick_market("Idaho Fals-Pocatllo(Jcksn)", "ID", cands)[0]["geo_id"] == "1"
    assert tg.pick_market("Santa Barbra-Sntmra-Slobs", "CA", [candidate("Santa Barbara-Santa Maria-San Luis Obispo CA", "1")])[0]["geo_id"] == "1"


# --------------------------------------------------------------------------------------------- the markets, whatever TikTok calls them

@pytest.mark.parametrize("region_list", [True, False])
@pytest.mark.parametrize("style", STYLES)
def test_every_one_of_the_210_markets_lands_on_its_own_market_in_one_go(style, region_list):
    client = make_client(FakeTikTok(market_style=style, region_list=region_list))
    out = tg.map_resolution(resolution([market(d) for d in DMAS]), client)
    wrong = [(l.source, l.name) for l, d in zip(out.locations, [d for d in DMAS if d.dma in {x.source for x in out.locations}]) if l.tiktok_id != dma_id(BY_NAME[l.source].rank)]
    assert wrong == []
    assert out.market_source == ("list" if region_list else "search")
    # "Santa Barbra" is a misspelling in the workbook: a search by words cannot find it in a written-out name, and says so (and "Idaho Fals",
    # the same with a name written short, in Google's convention). Through TikTok's market list - every name side by side - nothing is missed.
    allowed = set()
    if not region_list and style in ("full_plain", "full_state", "google", "state_name"):
        allowed.add("Santa Barbra-Sntmra-Slobs")
    if not region_list and style == "google":
        allowed.add("Idaho Fals-Pocatllo(Jcksn)")
    assert {u["input"] for u in out.unmapped} == allowed, out.unmapped[:5]
    assert len(out.locations) == 210 - len(allowed)


@pytest.mark.parametrize("style", ["plain", "state", "full_plain"])
def test_each_market_alone_lands_on_its_own_market(style):
    client = make_client(FakeTikTok(market_style=style))
    bad = []
    for d in DMAS:
        tg.forget_everything()
        out = tg.map_resolution(resolution([market(d)]), client)
        if ids(out) != [dma_id(d.rank)]:
            bad.append((d.dma, [l.name for l in out.locations], out.unmapped))
    assert bad == []


def test_the_two_portlands_and_the_three_columbuses_each_find_their_own():
    client = make_client(FakeTikTok(market_style="state"))
    names = ["Portland, OR", "Portland-Auburn", "Columbus, OH", "Columbus, GA (Opelika, AL)", "Columbus-Tupelo-W Pnt-Hstn"]
    out = tg.map_resolution(resolution([market(BY_NAME[n]) for n in names]), client)
    assert ids(out) == [dma_id(BY_NAME[n].rank) for n in names] and out.unmapped == []


# A market TikTok does not list must come back as missing - never as the neighbour that shares its first city. The review's repro: with
# TikTok's names carrying no state, Chicago went to Chico-Redding, Columbus OH to Columbus-Tupelo, Albany GA to Albany-Schenectady-Troy...
LOOK_ALIKES = [("Chicago", "Chico-Redding"), ("Chico-Redding", "Chicago"), ("Columbus, OH", "Columbus-Tupelo-W Pnt-Hstn"), ("Columbus-Tupelo-W Pnt-Hstn", "Columbus, OH"),
               ("Springfield, MO", "Springfield-Holyoke"), ("Springfield-Holyoke", "Springfield, MO"), ("Albany, GA", "Albany-Schenectady-Troy"),
               ("Albany-Schenectady-Troy", "Albany, GA"), ("Charlottesville", "Charlotte"), ("Charlotte", "Charlottesville"), ("Harrisonburg", "Harrisburg-Lncstr-Leb-York"),
               ("Jackson, TN", "Jacksonville"), ("Rochester, NY", "Rochestr-Mason City-Austin"), ("Greenville-N.Bern-Washngtn", "Greenville-Spart-Ashevll-And")]


@pytest.mark.parametrize("style", ["plain", "google", "state_name"])
@pytest.mark.parametrize("asked,only_other", LOOK_ALIKES)
def test_a_market_tiktok_does_not_list_is_reported_missing_not_swapped_for_its_look_alike(style, asked, only_other):
    fake = FakeTikTok(market_style=style)
    fake.dmas = [d for d in fake.dmas if d.dma != asked]                      # TikTok has the neighbour, not this market
    assert any(d.dma == only_other for d in fake.dmas)
    out = tg.map_resolution(resolution([market(BY_NAME[asked])]), make_client(fake))
    assert out.locations == [] and [u["input"] for u in out.unmapped] == [asked], out.locations
    assert "no market" in out.unmapped[0]["reason"]


@pytest.mark.parametrize("style", ["plain", "google", "state_name", "full_plain"])
def test_whichever_single_market_tiktok_lacks_it_is_never_given_another_one(style):
    """Every one of the 210 markets, in turn, missing from TikTok's list: none may land on a different market."""
    swapped = []
    for d in DMAS:
        tg.forget_everything()
        fake = FakeTikTok(market_style=style)
        fake.dmas = [x for x in fake.dmas if x.rank != d.rank]
        out = tg.map_resolution(resolution([market(d)]), make_client(fake))
        if out.locations:
            swapped.append((d.dma, out.locations[0].name))
    assert swapped == []


@pytest.mark.parametrize("style", ["plain", "google"])
def test_a_list_with_only_the_biggest_150_markets_never_gives_the_missing_ones_another_market(style):
    swapped, placed = [], 0
    for d in DMAS:
        tg.forget_everything()
        fake = FakeTikTok(market_style=style)
        fake.dmas = [x for x in fake.dmas if x.rank <= 150]
        out = tg.map_resolution(resolution([market(d)]), make_client(fake))
        if out.locations and d.rank > 150:
            swapped.append((d.dma, out.locations[0].name))
        placed += bool(out.locations) and d.rank <= 150
    assert swapped == [] and placed == 150


def test_a_market_is_the_rivals_when_another_market_fits_the_tiktok_name_at_least_as_well():
    """pick_market on its own, with a TikTok name that carries no state: it fits Chico-Redding better than it fits Chicago, so Chicago may not have it."""
    cands = [candidate("Chico-Redding", "1")]
    assert tg.pick_market("Chicago", "IL", cands)[0]["geo_id"] == "1"                                   # nobody else was asked about
    best, why = tg.pick_market("Chicago", "IL", cands, rivals=[("Chico-Redding", "CA"), ("Chicago", "IL")])
    assert best is None and "fits Chico-Redding at least as well" in why
    assert tg.pick_market("Chico-Redding", "CA", cands, rivals=[("Chicago", "IL")])[0]["geo_id"] == "1"


def test_a_complete_list_lets_every_market_through_the_comparison_with_every_other():
    """The comparison must not turn good matches into misses: all 210 against all 209 others, in the conventions where TikTok names carry no state."""
    for style in ("plain", "google", "state_name", "full_plain"):
        tg.forget_everything()
        out = tg.map_resolution(resolution([market(d) for d in DMAS]), make_client(FakeTikTok(market_style=style)))
        assert len(out.locations) == 210 and out.unmapped == [], (style, out.unmapped[:3])


def test_a_written_out_state_dc_and_a_typeset_dash_are_read_like_their_short_forms():
    assert tg._words("Columbus Ohio", True) == ((("columbus",), frozenset({"OH"})))
    assert tg._words("Greenville-Spartanburg South Carolina-Asheville North Carolina", True)[1] == {"SC", "NC"}
    assert tg._words("Washington D.C. (Hagerstown)", True) == tg._words("Washington, DC (Hagerstown)", True) == (("washington", "hagerstown"), frozenset({"DC"}))
    assert tg._words("Albany\u2013Schenectady\u2013Troy NY", True) == tg._words("Albany-Schenectady-Troy NY", True)
    assert tg._words("Washington District of Columbia", True)[1] == {"DC"}
    # a state's name that IS the name stays a word: the lead city of "Kansas City", "Oklahoma City" and "New York", and a piece like "Washington NC"
    assert tg._words("New York", True)[0] == ("new", "york") and tg._words("Oklahoma City", True)[0] == ("oklahoma",)
    assert "washington" in tg._words("Greenville-New Bern-Washington NC", True)[0]
    # place names (counties, cities) keep their words: Fort Washington is not "Fort" in Washington state
    assert tg._words("Fort Washington")[0] == ("fort", "washington")
    assert tg._place_key("Fort Washington") == "fort washington"


@pytest.mark.parametrize("asked,written", [("Washington, DC (Hagerstown)", "Washington D.C. (Hagerstown MD)"),
                                           ("Albany-Schenectady-Troy", "Albany\u2013Schenectady\u2013Troy NY"), ("Columbus, OH", "Columbus Ohio")])
def test_markets_whose_names_are_written_with_dc_dashes_or_the_state_in_full_find_their_market(monkeypatch, asked, written):
    monkeypatch.setattr(ts, "market_name", lambda d, style: written if d.dma == asked else d.dma)        # TikTok writes this one market its own way
    out = tg.map_resolution(resolution([market(BY_NAME[asked])]), make_client(FakeTikTok(market_style="plain")))
    assert ids(out) == [dma_id(BY_NAME[asked].rank)], out.unmapped


def _columbus_only_once(monkeypatch):
    """A TikTok that lists ONE market called "Columbus" (no state in the name), while the planner's list has two Columbuses."""
    monkeypatch.setattr(ts, "market_name", lambda d, style: "Columbus" if d.dma == "Columbus, OH" else d.dma)
    fake = FakeTikTok(market_style="plain")
    fake.dmas = [d for d in fake.dmas if d.dma not in ("Columbus, GA (Opelika, AL)", "Columbus-Tupelo-W Pnt-Hstn")]
    return make_client(fake)


def test_when_tiktok_lists_one_columbus_the_market_named_exactly_that_gets_it_and_the_other_is_reported(monkeypatch):
    """TikTok knowing one "Columbus" (no state in the name), while the planner's list has two: the market whose name that is gets it, the
    other is reported as not found - it is never handed the same TikTok market."""
    client = _columbus_only_once(monkeypatch)
    out = tg.map_resolution(resolution([market(BY_NAME["Columbus, OH"]), market(BY_NAME["Columbus, GA (Opelika, AL)"]), market(BY_NAME["Chicago"])]), client)
    assert [l.source for l in out.locations] == ["Columbus, OH", "Chicago"]
    assert [u["input"] for u in out.unmapped] == ["Columbus, GA (Opelika, AL)"] and "fits Columbus, OH at least as well" in out.unmapped[0]["reason"]


def test_two_markets_that_land_on_one_tiktok_market_are_both_reported_and_neither_is_used_when_nothing_tells_them_apart(monkeypatch):
    """The last line of defence, with the comparison against the other markets switched off: two of the planner's markets that picked ONE
    TikTok market cannot both be right, and which one is wrong is not known - neither goes into the estimate."""
    monkeypatch.setattr(tg, "_reference_markets", lambda: ())
    client = _columbus_only_once(monkeypatch)
    out = tg.map_resolution(resolution([market(BY_NAME["Columbus, OH"]), market(BY_NAME["Columbus, GA (Opelika, AL)"]), market(BY_NAME["Chicago"])]), client)
    assert [l.source for l in out.locations] == ["Chicago"]
    assert {u["input"] for u in out.unmapped} == {"Columbus, OH", "Columbus, GA (Opelika, AL)"}
    assert all("fits more than one of your markets" in u["reason"] for u in out.unmapped)


def test_when_tiktok_has_two_markets_of_that_name_the_market_is_reported_as_ambiguous(monkeypatch):
    monkeypatch.setattr(ts, "market_name", lambda d, style: "Columbus" if d.dma in ("Columbus, OH", "Columbus, GA (Opelika, AL)") else d.dma)
    out = tg.map_resolution(resolution([market(BY_NAME["Columbus, OH"])]), make_client(FakeTikTok(market_style="plain")))
    assert out.locations == [] and "more than one TikTok market fits" in out.unmapped[0]["reason"]


def test_a_market_that_clashed_is_not_remembered_for_the_next_estimate(monkeypatch):
    monkeypatch.setattr(tg, "_reference_markets", lambda: ())
    client = _columbus_only_once(monkeypatch)
    tg.map_resolution(resolution([market(BY_NAME["Columbus, OH"]), market(BY_NAME["Columbus, GA (Opelika, AL)"]), market(BY_NAME["Chicago"])]), client)
    assert tg._recall(("dma", "columbus, oh")) is None and tg._recall(("dma", "columbus, ga (opelika, al)")) is None
    assert tg._recall(("dma", "chicago")) is not None                                      # the one that did not clash is remembered
    alone = tg.map_resolution(resolution([market(BY_NAME["Columbus, OH"])]), client)       # alone it IS the one TikTok market called Columbus
    assert [l.source for l in alone.locations] == ["Columbus, OH"]


def test_a_market_tiktok_has_switched_off_is_reported_by_search_too():
    client = make_client(FakeTikTok(market_style="state", region_list=False))
    off = ts.FakeResponse(200, ok({"targeting_tag_list": [
        {"keyword": "Portland", "name": "Portland OR", "status_info": {"status": "DISABLED", "reason": "OFFLINE"}, "geo": {"geo_id": "1", "geo_type": "DMA", "parent_id": US_ID, "region_code": "US"}},
        {"keyword": "Portland", "name": "Portland-Auburn ME", "status_info": {"status": "ENABLED"}, "geo": {"geo_id": "2", "geo_type": "DMA", "parent_id": US_ID, "region_code": "US"}},
    ], "parent_tags": []}))
    client.fake.queue("/tool/targeting/search/", off)
    out = tg.map_resolution(resolution([market(BY_NAME["Portland, OR"])]), client)
    assert out.locations == [] and "not available for targeting" in out.unmapped[0]["reason"]


def test_a_real_typed_list_of_markets_maps_in_the_order_it_was_typed():
    client = make_client()
    res = resolve_geo("Phoenix; Los Angeles; Dallas")
    out = tg.map_resolution(res, client)
    assert [l.source for l in out.locations] == [a.dma for a in res.areas] and out.unmapped == []
    assert all(l.kind == "location_id" and l.geo_type == "DMA" for l in out.locations)


def test_the_market_list_is_read_once_and_a_market_already_looked_up_is_not_looked_up_again():
    client = make_client()
    res = resolve_geo("Phoenix; Los Angeles")
    tg.map_resolution(res, client)
    calls = len(client.fake.calls)
    again = tg.map_resolution(res, client)
    assert len(client.fake.calls) == calls and len(again.locations) == 2
    assert client.fake.count("/tool/region/") == 1


def test_only_the_markets_not_yet_known_are_looked_up_by_search():
    client = make_client(FakeTikTok(region_list=False))
    tg.map_resolution(resolve_geo("Phoenix"), client)
    client.fake.calls.clear()
    out = tg.map_resolution(resolve_geo("Phoenix; Chicago"), client)
    assert len(out.locations) == 2
    asked = [c for c in client.fake.calls if c["path"] == "/tool/targeting/search/"][0]["body"]["keywords"]
    assert asked == ["Chicago"]


def test_when_tiktoks_market_list_is_not_there_markets_are_searched_for():
    client = make_client(FakeTikTok(region_list=False))
    out = tg.map_resolution(resolve_geo("Phoenix; Los Angeles"), client)
    assert out.market_source == "search" and len(out.locations) == 2 and client.fake.count("/tool/region/") == 1


def test_a_list_that_is_not_the_us_market_list_is_not_trusted():
    client = make_client()
    client.fake.queue("/tool/region/", FakeResponse(200, ok({"region_info": [
        {"location_id": "1", "name": "Los Angeles CA", "parent_id": "0", "region_code": "US", "area_type": "METROPOLITAN_OR_DMA", "level": "PROVINCE"}]})))
    out = tg.map_resolution(resolve_geo("Phoenix"), client)
    assert out.market_source == "search" and len(out.locations) == 1


def test_a_market_the_batch_missed_is_found_by_a_search_of_its_own():
    client = make_client(FakeTikTok(region_list=False))
    d = BY_NAME["Chicago"]
    client.fake.queue("/tool/targeting/search/", FakeResponse(200, ok({"targeting_tag_list": []})))        # the batch finds nothing
    out = tg.map_resolution(resolution([market(d)]), client)
    assert ids(out) == [dma_id(d.rank)]
    assert client.fake.calls[-1]["body"]["search_type"] == "FUZZY_SEARCH"


def test_a_market_tiktok_does_not_have_is_reported_with_the_reason_not_dropped():
    client = make_client()
    fake_market = ResolvedArea(dma="Nowhere-Ville", area_population=1.0, dma_share=1.0, source_type="dma", apportionment="exact", state="ZZ", label="Nowhere-Ville")
    out = tg.map_resolution(resolution([fake_market]), client)
    assert out.locations == [] and out.unmapped and out.unmapped[0]["input"].startswith("Nowhere-Ville") and "no market" in out.unmapped[0]["reason"]


def test_one_by_one_look_ups_are_bounded():
    client = make_client()
    many = [ResolvedArea(dma=f"Missing-{i}", area_population=1.0, dma_share=1.0, source_type="dma", apportionment="exact", state="CA", label=f"Missing-{i}")
            for i in range(30)]
    out = tg.map_resolution(resolution(many), client)
    assert len(out.unmapped) == 30
    singles = [c for c in client.fake.calls if (c["body"] or {}).get("search_type") == "FUZZY_SEARCH"]
    assert len(singles) <= tg.MAX_ONE_BY_ONE


# --------------------------------------------------------------------------------------------- country, states, ZIPs

def test_national_is_the_united_states_with_no_lookup():
    client = make_client()
    out = tg.map_resolution(resolve_geo("National"), client)
    assert ids(out) == [US_ID] and out.locations[0].geo_type == "COUNTRY" and client.fake.calls == []


def test_a_state_asked_for_whole_is_the_state_not_the_markets_homed_in_it():
    client = make_client()
    res = resolve_geo("Texas")
    assert res.whole_states == ["TX"] and len(res.areas) > 5
    out = tg.map_resolution(res, client)
    assert ids(out) == [STATE_IDS["TX"]] and out.locations[0].geo_type == "PROVINCE" and out.locations[0].name == "Texas"


def test_several_whole_states_are_each_their_own_state():
    out = tg.map_resolution(resolve_geo("CA, NV, AZ"), make_client())
    assert sorted(ids(out)) == sorted(STATE_IDS[s] for s in ("CA", "NV", "AZ")) and out.unmapped == []


def test_a_state_plus_a_market_outside_it_keeps_the_market_as_a_market():
    res = resolve_geo("Texas; Phoenix")
    out = tg.map_resolution(res, make_client())
    assert STATE_IDS["TX"] not in ids(out) or len(out.locations) > 2          # not collapsed to the state alone
    assert any(l.geo_type == "DMA" and l.source.startswith("Phoenix") for l in out.locations)


def test_zip_codes_become_zipcode_ids():
    out = tg.map_resolution(resolve_geo("90210, 10001"), make_client())
    assert [l.kind for l in out.locations] == ["zipcode_id", "zipcode_id"] and ids(out) == ["8840000490210", "8840000410001"]
    assert out.locations[0].source == "ZIP 90210" and out.unmapped == []


def test_a_zip_code_tiktok_does_not_have_is_reported():
    client = make_client()
    out = tg.map_resolution(resolution([area("zip", "ZIP 99999", geo_id="99999"), area("zip", "ZIP 90210", geo_id="90210")], method="zip"), client)
    assert ids(out) == ["8840000490210"] and out.unmapped == [{"input": "ZIP 99999", "reason": "TikTok has no ZIP code like that"}]


def test_a_mixed_list_keeps_markets_and_zip_codes_apart_in_their_own_fields():
    out = tg.map_resolution(resolve_geo("Phoenix; 90210"), make_client())
    assert [l.kind for l in out.locations].count("zipcode_id") == 1 and out.location_ids and out.zipcode_ids == ["8840000490210"]


def test_the_same_place_twice_is_sent_once():
    m = tg.Mapping(locations=[tg.Location("a", "A", "1", "DMA", "location_id"), tg.Location("a again", "A", "1", "DMA", "location_id")])
    assert m.location_ids == ["1"]


# --------------------------------------------------------------------------------------------- counties, cities, districts, countries

def test_a_county_is_told_apart_by_its_state():
    client = make_client()
    out = tg.map_resolution(resolve_geo("Adams County, WA"), client)
    assert [l.name for l in out.locations] == ["Adams County"] and out.locations[0].tiktok_id == "910002" and out.unmapped == []


def test_a_county_name_that_fits_several_states_is_not_guessed_when_no_state_was_given():
    out = tg.map_resolution(resolution([area("county", "Adams County", state=None)], method="county"), make_client())
    assert out.locations == [] and "couldn't tell which TikTok county" in out.unmapped[0]["reason"] and "no state was given" in out.unmapped[0]["reason"]


def test_a_county_in_a_state_that_has_none_of_that_name_is_not_swapped_for_another_states():
    """The review's repro: Franklin County, OH was mapped to Franklin County, KY when no hit was inside the state."""
    client = make_client()
    ohio = tg.map_resolution(resolution([area("county", "Franklin County, OH", state="OH")], method="county"), client)
    assert ids(ohio) == ["910009"]
    texas = tg.map_resolution(resolution([area("county", "Franklin County, TX", state="TX")], method="county"), client)
    assert texas.locations == [] and "no county with that name in that state" in texas.unmapped[0]["reason"]


def test_a_city_is_told_apart_by_its_state_through_its_county():
    """A city's parent is its COUNTY (the docs: Frankfort -> Franklin County -> Kentucky), so the state is read from the parent chain."""
    client = make_client()
    for label, state, expected in [("Plano, TX", "TX", "920004"), ("Springfield, MO", "MO", "920003"), ("Springfield, IL", "IL", "920002"),
                                   ("Sacramento, CA", "CA", "920001"), ("Frankfort, KY", "KY", "920005")]:
        tg.forget_everything()
        out = tg.map_resolution(resolution([area("city", label, state=state)], method="city"), client)
        assert ids(out) == [expected], (label, out.unmapped)


def test_a_city_is_never_swapped_for_the_same_name_in_another_state():
    """The review's repro: Plano, IL was silently mapped to Plano, TX."""
    out = tg.map_resolution(resolution([area("city", "Plano, IL", state="IL")], method="city"), make_client())
    assert out.locations == [] and "no city with that name in that state" in out.unmapped[0]["reason"]


def test_a_city_is_not_guessed_when_tiktok_does_not_say_where_it_sits():
    client = make_client(FakeTikTok(parent_tags=False))
    out = tg.map_resolution(resolution([area("city", "Springfield, MO", state="MO")], method="city"), client)
    assert out.locations == [] and "did not say which state" in out.unmapped[0]["reason"]
    county = tg.map_resolution(resolution([area("county", "Adams County, WA", state="WA")], method="county"), client)       # a county's parent IS its state
    assert ids(county) == ["910002"]


def test_a_city_with_no_state_is_used_only_when_its_name_is_unique():
    client = make_client()
    unique = tg.map_resolution(resolution([area("city", "Plano", state=None)], method="city"), client)
    assert ids(unique) == ["920004"]
    several = tg.map_resolution(resolution([area("city", "Springfield", state=None)], method="city"), client)
    assert several.locations == [] and "no state was given" in several.unmapped[0]["reason"]


def test_a_county_is_not_taken_for_a_city_of_the_same_name_and_the_other_way_round():
    client = make_client()
    city = tg.map_resolution(resolution([area("city", "Sacramento, CA", state="CA")], method="city"), client)
    county = tg.map_resolution(resolution([area("county", "Sacramento County, CA", state="CA")], method="county"), client)
    assert ids(city) == ["920001"] and ids(county) == ["910006"]


def _tag(name, geo_id, parent, desc="County", geo_type="CITY"):
    return {"keyword": "x", "name": name, "targeting_type": "GEO", "status_info": {"status": "ENABLED", "reason": None},
            "geo": {"geo_id": geo_id, "geo_type": geo_type, "parent_id": parent, "region_code": "US", "description": desc}}


@pytest.mark.parametrize("text,county_text,city_id,county_id", [("Baltimore city, MD", "Baltimore County, MD", "920101", "910013"),
                                                                 ("Fairfax city, VA", "Fairfax County, VA", "920102", "910014"),
                                                                 ("St. Louis city, MO", "St. Louis County, MO", "920103", "910010")])
def test_an_independent_city_is_never_the_county_of_the_same_name(text, county_text, city_id, county_id):
    """Baltimore city, Fairfax city and St. Louis city are Census "independent cities": county-sized areas that ARE cities, next to a county of the
    same name that is a different (and often much bigger) place. TikTok has both; each finds its own, in either order, with the memory shared."""
    client = make_client()
    city = tg.map_resolution(resolve_geo(text, "county"), client)
    county = tg.map_resolution(resolve_geo(county_text, "county"), client)
    assert ids(city) == [city_id] and ids(county) == [county_id], (city.unmapped, county.unmapped)
    again = tg.map_resolution(resolve_geo(text, "county"), client)                 # remembered: the county did not overwrite the city
    assert ids(again) == [city_id]


@pytest.mark.parametrize("text,state,county_name,county_id", [("Baltimore city, MD", "MD", "Baltimore County", "910013"), ("Fairfax city, VA", "VA", "Fairfax County", "910014"),
                                                               ("St. Louis city, MO", "MO", "St. Louis County", "910010")])
def test_an_independent_city_is_reported_not_mapped_when_tiktok_answers_only_with_the_county(text, state, county_name, county_id):
    """The review's repro: TikTok's answer held only the county, and the city was handed it (Fairfax city is about 24,000 people, Fairfax County
    about 1.1 million). "City" and "County" are not part of a name's identity when two places are compared, but they decide WHICH kind it is."""
    client = make_client()
    client.fake.queue("/tool/targeting/search/", FakeResponse(200, ok({"targeting_tag_list": [_tag(county_name, county_id, STATE_IDS[state])], "parent_tags": []})))
    out = tg.map_resolution(resolve_geo(text, "county"), client)
    assert out.locations == [] and "no city with that name" in out.unmapped[0]["reason"], out.locations


def test_a_county_with_no_suffix_in_tiktoks_name_is_still_told_from_a_city_by_its_description():
    client = make_client()
    client.fake.queue("/tool/targeting/search/", FakeResponse(200, ok({"targeting_tag_list": [_tag("Baltimore", "1", STATE_IDS["MD"], desc="County"),
                                                                                              _tag("Baltimore", "2", STATE_IDS["MD"], desc="City", geo_type="DISTRICT")],
                                                                      "parent_tags": []})))
    city = tg.map_resolution(resolve_geo("Baltimore city, MD", "county"), client)
    assert ids(city) == ["2"]


def test_parishes_and_boroughs_map_as_counties():
    client = make_client()
    parish = tg.map_resolution(resolve_geo("Orleans Parish, LA", "county"), client)
    borough = tg.map_resolution(resolution([area("county", "Anchorage Borough, AK", state="AK")], method="county"), client)
    assert ids(parish) == ["910015"] and ids(borough) == ["910016"], (parish.unmapped, borough.unmapped)


def test_what_a_place_name_says_it_is():
    assert tg._place_class("Baltimore County") == "county" and tg._place_class("Orleans Parish") == "county" and tg._place_class("Anchorage Borough") == "county"
    assert tg._place_class("Baltimore city") == "city" and tg._place_class("Carson City") == "city" and tg._place_class("Kansas City") == "city"
    assert tg._place_class("Baltimore") is None and tg._place_class("Baltimore", "City") == "city" and tg._place_class("Baltimore", "County") == "county"
    assert tg._place_class("Orleans", "Parish") == "county" and tg._place_class("Springfield", "") is None


def test_saint_and_st_and_accents_do_not_matter_when_a_county_or_city_name_is_compared():
    assert tg._place_key("St. Louis County") == tg._place_key("Saint Louis County") == tg._place_key("saint louis")
    assert tg._place_key("Doña Ana County") == tg._place_key("Dona Ana County")
    assert tg._place_key("Orleans Parish") == tg._place_key("Orleans") and tg._place_key("Anchorage Borough") == tg._place_key("Anchorage")
    out = tg.map_resolution(resolution([area("county", "St. Louis County, MO", state="MO")], method="county"), make_client())
    assert ids(out) == ["910010"]


def test_places_looked_up_one_by_one_are_bounded_and_the_state_look_ups_count():
    client = make_client()
    many = [area("county", f"Adams County, {st}", state=st) for st in ("WA", "CO", "IL", "TX", "CA", "KY", "OH", "MO", "NY", "FL", "GA", "NC", "PA", "VA")]
    out = tg.map_resolution(resolution(many, method="county"), client)
    assert any("too many places to look up one by one" in u["reason"] for u in out.unmapped)
    singles = [c for c in client.fake.calls if (c["body"] or {}).get("search_type") == "FUZZY_SEARCH"]
    state_batches = [c for c in client.fake.calls if (c["body"] or {}).get("geo_types") == ["PROVINCE"]]
    assert len(singles) + len(state_batches) <= tg.MAX_ONE_BY_ONE + 1


def test_a_congressional_district_cannot_be_targeted_and_says_so():
    out = tg.map_resolution(resolve_geo("CA-12"), make_client())
    assert out.locations == [] and "congressional districts" in out.unmapped[0]["reason"]


def test_another_country_is_found_by_name_and_the_us_needs_no_lookup():
    client = make_client()
    out = tg.map_resolution(resolve_geo("Canada"), client)
    assert ids(out) == ["6251999"] and out.locations[0].geo_type == "COUNTRY"
    nowhere = tg.map_resolution(resolution([area("country", "Narnia")], method="country", confidence="low_confidence_international"), client)
    assert nowhere.locations == [] and "doesn't list that country" in nowhere.unmapped[0]["reason"]


def test_too_many_places_are_refused_before_a_call_is_spent():
    zips = [area("zip", f"ZIP {i:05d}", geo_id=f"{i:05d}") for i in range(10_000, 10_000 + tg.MAX_LOCATIONS + 1)]
    client = make_client()
    with pytest.raises(ValueError, match="at most 3,000"):
        tg.map_resolution(resolution(zips, method="zip"), client)
    assert client.fake.calls == []


def test_a_long_list_of_zip_codes_is_looked_up_in_batches_of_a_thousand_and_none_is_lost():
    zips = [area("zip", f"ZIP {i:05d}", geo_id=f"{i:05d}") for i in range(10_000, 12_500)]
    client = make_client()
    out = tg.map_resolution(resolution(zips, method="zip"), client)
    assert len(out.locations) == 2500 and out.unmapped == []
    batches = [len(c["body"]["keywords"]) for c in client.fake.calls]
    assert batches == [1000, 1000, 500]


# ------------------------------------------------------------------------------------------------ TikTok's own documented market list

def test_every_one_of_the_210_markets_in_tiktoks_documented_list_is_placed_on_its_own_market():
    """TikTok's Location IDs appendix (v1.3) lists all 210 U.S. DMAs with their IDs (tests/tiktok_doc_dmas.json). Every market Constanza knows must
    land on exactly one of them and no two on the same one: this is the names-as-TikTok-writes-them check the fake's own styles could not give
    (it caught Duluth-Superior, which TikTok names with Wisconsin, and Wheeling-Steubenville, with Ohio)."""
    import json
    from pathlib import Path
    listed = json.loads((Path(__file__).parent / "tiktok_doc_dmas.json").read_text())
    assert len(listed) == 210 and len({i for _, i in listed}) == 210
    cands = [{"name": n, "geo_id": i, "geo_type": "DMA", "enabled": True, "parent_id": "6252001"} for n, i in listed]
    rivals = tg._reference_markets()
    placed, missing = {}, []
    for name, state in rivals:
        best, why = tg.pick_market(name, state, cands, rivals=rivals)
        if best is None:
            missing.append((name, why))
        else:
            placed[name] = best["geo_id"]
    assert not missing, missing
    assert len(set(placed.values())) == 210                                                   # no two markets on one TikTok market
    assert placed["Duluth-Superior"] == "676" and placed["Wheeling-Steubenville"] == "554"
    assert placed["Columbus, OH"] == "535" and placed["Columbus, GA (Opelika, AL)"] == "522" and placed["Albany, GA"] == "525"


def test_washington_state_and_the_district_are_told_apart_when_tiktok_lists_both(client_factory=None):
    from tests import tiktok_support as S
    fake = S.FakeTikTok("state")
    cl = S.make_client(fake)
    tg.forget_everything()
    wa = tg.map_resolution(_state_resolution("WA"), cl)
    dc = tg.map_resolution(_state_resolution("DC"), cl)
    both = tg.map_resolution(_state_resolution("WA", "DC"), cl)
    assert [l.name for l in wa.locations] == ["Washington"] and not wa.unmapped
    assert [l.name for l in dc.locations] == ["Washington, D.C."] and not dc.unmapped
    assert sorted(l.name for l in both.locations) == ["Washington", "Washington, D.C."] and len({l.tiktok_id for l in both.locations}) == 2


def _state_resolution(*abbrs):
    from app.services.constanza_geo import GeoResolution, ResolvedArea
    areas = [ResolvedArea(dma=None, area_population=1.0, dma_share=1.0, source_type="state", apportionment="exact", state=a, label=a, geo_id=a) for a in abbrs]
    return GeoResolution(input_text=",".join(abbrs), method="state_list", areas=areas, geo_confidence="high", whole_states=list(abbrs))
