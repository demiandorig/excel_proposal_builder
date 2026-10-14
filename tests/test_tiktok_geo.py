"""Mapping a Constanza geography onto TikTok location IDs (app/services/tiktok_geo.py), against the fake TikTok API.

The names TikTok gives its TV markets are not known to the code, so the matching is checked against four different naming
conventions, for every one of the 210 markets: each must land on its own market, and a market that cannot be told apart from
another must be reported, never guessed."""
from __future__ import annotations

import pytest

from app.data.constanza import load_dma_reference
from app.services import tiktok_geo as tg
from app.services.constanza_geo import GeoResolution, ResolvedArea, resolve_geo
from tests.tiktok_support import STATE_IDS, US_ID, FakeResponse, FakeTikTok, dma_id, make_client, ok

STYLES = ["plain", "state", "comma_state", "dma_suffix"]
DMAS = list(load_dma_reference())


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


# --------------------------------------------------------------------------------------------- names

def test_the_words_of_a_name_leave_out_filler_and_state_codes_but_keep_a_leading_la():
    assert tg._words("Los Angeles, CA") == (["los", "angeles"], {"CA"})
    assert tg._words("Boston MA-Manchester NH") == (["boston", "manchester"], {"MA", "NH"})
    assert tg._words("Los Angeles DMA")[0] == ["los", "angeles"]
    assert tg._words("La Crosse-Eau Claire")[0] == ["la", "crosse", "eau", "claire"]
    assert tg._words("")[0] == []


def test_the_first_city_of_a_market_is_its_search_word():
    assert tg._primary("Dallas-Ft. Worth") == "Dallas"
    assert tg._primary("Phoenix (Prescott)") == "Phoenix"
    assert tg._primary("Portland, OR") == "Portland"
    assert tg._primary("New York") == "New York"


def candidate(name, geo_id, enabled=True):
    return {"name": name, "geo_id": geo_id, "enabled": enabled, "geo_type": "DMA"}


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
    cands = [candidate("Portland OR", "1"), candidate("Portland ME", "2")]
    assert tg.pick_market("Portland, OR", "OR", cands)[0]["geo_id"] == "1"
    assert tg.pick_market("Portland-Auburn", "ME", cands)[0]["geo_id"] == "2"


def test_a_market_that_is_switched_off_is_never_chosen():
    assert tg.pick_market("Phoenix", "AZ", [candidate("Phoenix AZ", "1", enabled=False)])[0] is None


# --------------------------------------------------------------------------------------------- the markets, whatever TikTok calls them

@pytest.mark.parametrize("style", STYLES)
def test_every_one_of_the_210_markets_lands_on_its_own_market_in_one_go(style):
    client = make_client(FakeTikTok(market_style=style))
    out = tg.map_resolution(resolution([market(d) for d in DMAS]), client)
    wrong = [(l.source, l.name) for l, d in zip(out.locations, DMAS) if l.tiktok_id != dma_id(d.rank)]
    assert wrong == [] and out.unmapped == [], (style, out.unmapped[:5])
    assert len(out.locations) == 210


@pytest.mark.parametrize("style", STYLES)
def test_each_market_alone_lands_on_its_own_market(style):
    client = make_client(FakeTikTok(market_style=style))
    bad = []
    for d in DMAS:
        tg.forget_everything()
        out = tg.map_resolution(resolution([market(d)]), client)
        if ids(out) != [dma_id(d.rank)]:
            bad.append((d.dma, [l.name for l in out.locations], out.unmapped))
    assert bad == []


def test_the_plain_names_of_two_portlands_each_find_their_own():
    client = make_client(FakeTikTok(market_style="state"))
    by_name = {d.dma: d for d in DMAS}
    one = tg.map_resolution(resolution([market(by_name["Portland, OR"])]), client)
    two = tg.map_resolution(resolution([market(by_name["Portland-Auburn"])]), client)
    assert ids(one) == [dma_id(by_name["Portland, OR"].rank)] and ids(two) == [dma_id(by_name["Portland-Auburn"].rank)]


def test_a_real_typed_list_of_markets_maps_in_the_order_it_was_typed():
    client = make_client()
    res = resolve_geo("Phoenix; Los Angeles; Dallas")
    out = tg.map_resolution(res, client)
    assert [l.source for l in out.locations] == [a.dma for a in res.areas] and out.unmapped == []
    assert all(l.kind == "location_id" and l.geo_type == "DMA" for l in out.locations)


def test_a_market_already_looked_up_is_not_looked_up_again():
    client = make_client()
    res = resolve_geo("Phoenix; Los Angeles")
    tg.map_resolution(res, client)
    calls = client.fake.count("/tool/targeting/search/")
    again = tg.map_resolution(res, client)
    assert client.fake.count("/tool/targeting/search/") == calls and len(again.locations) == 2


def test_only_the_markets_not_yet_known_are_looked_up():
    client = make_client()
    tg.map_resolution(resolve_geo("Phoenix"), client)
    client.fake.calls.clear()
    out = tg.map_resolution(resolve_geo("Phoenix; Chicago"), client)
    assert len(out.locations) == 2
    asked = client.fake.calls[0]["body"]["keywords"]
    assert asked == ["Chicago"]


def test_a_market_the_batch_missed_is_found_by_a_search_of_its_own():
    client = make_client()
    d = next(x for x in DMAS if x.dma == "Chicago")
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


def test_a_county_name_that_fits_several_states_is_not_guessed():
    client = make_client()
    out = tg.map_resolution(resolution([area("county", "Adams County", state=None)], method="county"), client)
    assert out.locations == [] and "couldn't tell which TikTok county" in out.unmapped[0]["reason"]


def test_a_city_that_is_one_place_is_found_and_one_that_is_several_is_narrowed_by_state():
    client = make_client()
    plano = tg.map_resolution(resolution([area("city", "Plano, TX", state="TX")], method="city"), client)
    assert [l.tiktok_id for l in plano.locations] == ["920004"]
    sp_mo = tg.map_resolution(resolution([area("city", "Springfield, MO", state="MO")], method="city"), client)
    assert [l.tiktok_id for l in sp_mo.locations] == ["920003"]
    sp_any = tg.map_resolution(resolution([area("city", "Springfield", state=None)], method="city"), client)
    assert sp_any.locations == [] and sp_any.unmapped


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
