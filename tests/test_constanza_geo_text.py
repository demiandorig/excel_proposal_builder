"""The forgiving geography layer (constanza_geo_text.py + its wiring in constanza_geo.resolve_geo): the planner's real
inputs, what must NOT be touched, and the rule that every guess is reported and every suggestion is a real place."""
from __future__ import annotations

import time

import pytest

import app.main as main
from app.services import constanza_geo_text as gt
from app.services.constanza_geo import resolve_geo
from app.services.forecast_import import geo_reference as geo_ref


def labels(res):
    return [a.label or a.dma for a in res.areas]


def understood(res):
    return {i["input"]: i["became"] for i in res.interpretations}


# --------------------------------------------------------------------------------------------- the planner's examples

def test_a_county_with_its_state_in_words_resolves():
    res = resolve_geo("Adams County, Washington")
    assert res.method == "county" and labels(res) == ["Adams County, WA"] and not res.interpretations


@pytest.mark.parametrize("text,why", [("Adams County Washington", "at the end as the state"), ("Adams County Washinton", "as Washington (WA)"),
                                       ("Adams County Wa", None)])
def test_a_state_with_no_comma_or_misspelled_is_read_and_reported(text, why):
    res = resolve_geo(text)
    if why is None:
        assert not res.resolved or labels(res) == ["Adams County, WA"]      # 'Wa' is not an upper-case code: never guessed
        return
    assert res.method == "county" and labels(res) == ["Adams County, WA"]
    assert understood(res) == {text: "Adams County, WA"} and why in res.interpretations[0]["why"]


def test_a_county_name_in_many_states_offers_every_one_biggest_first():
    res = resolve_geo("Adams County")
    assert not res.resolved and len(res.ambiguous) == 1
    cands = res.ambiguous[0]["candidates"]
    assert len(cands) == 12 and cands[0]["label"] == "Adams County, CO" and cands[0]["population"] > 400_000
    assert [c["population"] for c in cands] == sorted((c["population"] for c in cands), reverse=True)
    assert {"Adams County, WA", "Adams County, PA"} <= {c["label"] for c in cands}
    for c in cands:                                                         # picking any of them works (what the picker does)
        again = resolve_geo(c["resolve_hint"])
        assert again.resolved and labels(again) == [c["label"]], c


def test_a_place_and_its_surrounding_areas_in_a_county_means_the_county():
    res = resolve_geo("Othello, WA and Surrounding areas in Adams County, WA")
    assert res.method == "county" and labels(res) == ["Adams County, WA"]
    assert "larger area" in res.interpretations[0]["why"] and "Othello, WA" in res.interpretations[0]["why"]


def test_the_county_inherits_the_state_the_planner_named_for_the_place():
    res = resolve_geo("Othello, WA and surrounding areas in Adams County")
    assert labels(res) == ["Adams County, WA"]


def test_a_place_in_a_county_means_the_county():
    res = resolve_geo("Othello in Adams County, WA")
    assert labels(res) == ["Adams County, WA"] and "inside Adams County, WA" in res.interpretations[0]["why"]


def test_two_places_joined_by_and_are_a_list_and_a_probable_overlap_is_offered_not_applied():
    res = resolve_geo("Othello, WA and Adams County, WA")
    assert res.method == "mixed" and sorted(labels(res)) == ["Adams County, WA", "Othello, WA"]            # both kept: containment is not provable
    offer = next(s for s in res.suggestions if s["kind"] == "overlap")
    assert offer["label"] == "Drop Othello, WA — it may be inside Adams County, WA" and offer["drops"] == ["Othello, WA"]
    assert offer["drop_inputs"] == ["Othello, WA"] and "can't prove" in offer["why"]                      # an offer, never worded as fact
    assert any("counted twice" in n for n in res.notes)
    assert labels(resolve_geo(offer["resolve_hint"])) == ["Adams County, WA"]                                # the fallback text


def test_overlap_is_not_offered_for_a_city_bigger_than_the_county_or_in_another_state():
    assert not [s for s in resolve_geo("Spokane, WA and Adams County, WA").suggestions if s["kind"] == "overlap"]       # the city is bigger
    assert not [s for s in resolve_geo("Othello, NJ and Adams County, WA").suggestions if s["kind"] == "overlap"]       # another state


# --------------------------------------------------------------------------------------------- larger geography wins

def test_a_place_inside_a_listed_market_is_counted_once():
    res = resolve_geo("Spokane; Othello, WA")
    assert labels(res) == ["Spokane"] and any("counted once" in n and "Othello, WA" in n for n in res.notes)
    res = resolve_geo("Plano, TX and Dallas, TX")
    assert labels(res) == ["Dallas-Ft. Worth"] and any("Plano, TX" in n for n in res.notes)


def test_only_the_piece_inside_the_listed_market_is_dropped_from_a_county_split_across_markets():
    from app.services import constanza_geo_crosswalk as cw
    fips, shares = next((f, s) for f, s in cw._load_geo("county_dma.json").items() if len(s) >= 2 and min(x["share"] for x in s) > 0.1)
    county = cw._load_geo("county_names.json")[fips]
    state = cw._STATE_FIPS_TO_ABBR[county["state_fips"]]
    full = resolve_geo(f"{county.get('full_name') or county['name'] + ' County'}, {state}")
    assert len(full.areas) >= 2
    whole_dma = full.areas[0].dma
    res = resolve_geo(f"{full.areas[0].label}; {whole_dma}")
    kept_dmas = {a.dma for a in res.areas}
    assert whole_dma in kept_dmas and len(kept_dmas) >= 2                   # the whole market, plus the county's piece in the OTHER market
    assert [a for a in res.areas if a.source_type == "county" and a.dma == whole_dma] == []


# --------------------------------------------------------------------------------------------- typos and suggestions

def test_a_near_spelling_in_a_named_state_is_applied_and_reported():
    res = resolve_geo("Othelo, WA")
    assert labels(res) == ["Othello, WA"] and "closest spelling" in res.interpretations[0]["why"]


def test_a_near_spelling_with_no_state_is_only_suggested():
    res = resolve_geo("Othelo")
    assert not res.resolved
    assert any(s["kind"] == "did_you_mean" and s["label"] == "Othello, WA" for s in res.suggestions)


def test_a_misspelled_county_is_suggested_and_the_failure_message_says_so():
    res = resolve_geo("Adms County, WA")
    assert not res.resolved and [s["label"] for s in res.suggestions][:1] == ["Adams County, WA"]
    detail = main._geo_failure_detail("Adms County, WA", res)
    assert detail.endswith("Did you mean Adams County, WA?")


def test_every_suggestion_is_a_real_place_the_resolver_accepts():
    for typed in ("Sprngfield", "Adms County", "Los Angles", "Sacramnto", "Hartfrd"):
        for s in gt.suggest(typed):
            again = resolve_geo(s["resolve_hint"])
            assert again.resolved or again.ambiguous, (typed, s)


def test_suggestions_rank_the_best_spelling_first_and_bigger_places_before_smaller_ones():
    s = gt.suggest("Springfeld")
    assert s and s[0]["label"].startswith("Springfield")
    same_spelling = [x for x in s if x["label"].split(",")[0] == s[0]["label"].split(",")[0]]
    pops = [x["population"] for x in same_spelling]
    assert pops == sorted(pops, reverse=True)


def test_nothing_is_suggested_for_text_that_resembles_nothing():
    assert gt.suggest("qqzzxx") == [] and gt.fuzzy_best("qqzzxx, TX", None) is None


# --------------------------------------------------------------------------------------------- broaden

def test_a_single_place_offers_its_market_and_state_but_a_list_or_a_market_does_not():
    kinds = {(s["geo_type"], s["resolve_hint"]) for s in resolve_geo("Santa Monica, CA").suggestions if s["kind"] == "broaden"}
    assert ("dma", "Los Angeles") in kinds and ("state", "CA") in kinds
    assert not [s for s in resolve_geo("Los Angeles").suggestions if s["kind"] == "broaden"]
    assert not [s for s in resolve_geo("Santa Monica, CA; Plano, TX").suggestions if s["kind"] == "broaden"]
    for s in resolve_geo("90210").suggestions:
        assert resolve_geo(s["resolve_hint"]).resolved


# --------------------------------------------------------------------------------------------- DMA names as exports spell them

def test_every_spelling_in_the_alias_file_resolves_to_its_market():
    by_name = geo_ref.dma_by_name()
    bad = []
    for row in geo_ref.alias_rows():
        res = resolve_geo(row["alias"])
        if not (res.method == "exact_dma" and [a.dma for a in res.areas] == [by_name[row["dma"]].name]):
            bad.append((row["alias"], res.method, labels(res)))
    assert bad == []


@pytest.mark.parametrize("text,dma", [("Cedar Rapids - Waterloo & Dubuque", "Cedar Rapids-Wtrlo-IWC&Dub"),
                                      ("Champaign & Springfield - Decatur", "Champaign&Sprngfld-Decatur"),
                                      ("Tampa - Saint Petersburg (Sarasota)", "Tampa-St. Pete (Sarasota)")])
def test_markets_with_an_ampersand_or_a_long_name_are_one_market(text, dma):
    res = resolve_geo(text)
    assert res.method == "exact_dma" and [a.dma for a in res.areas] == [dma] and not res.unmatched_tokens


# --------------------------------------------------------------------------------------------- cleanup rules

@pytest.mark.parametrize("text,became", [
    ("Dallas and surrounding areas", "Dallas"), ("Dallas & nearby communities", "Dallas"), ("Los Angeles and vicinity", "Los Angeles"),
    ("all of California", "California"), ("the entire state of Ohio", None), ("greater Boston", "Boston"),
    ("cities in Harris County, TX", "Harris County, TX"), ("Adams Co., WA", "Adams County, WA"), ("Adams Cty, WA", "Adams County, WA"),
])
def test_filler_is_dropped(text, became):
    out = gt.prepare(text).text
    if became is not None:
        assert out == became


@pytest.mark.parametrize("text", ["all markets", "all us", "entire us", "all", "nationwide", "National", "USA"])
def test_national_phrases_are_never_cleaned(text):
    assert gt.prepare(text).text == text and gt.prepare(text).interpretations == []
    assert resolve_geo(text).method == "national"


@pytest.mark.parametrize("text", ["Cedar Rapids - Waterloo & Dubuque", "Texas and Oklahoma", "Dallas and Houston", "CA, NV, AZ", "Los Angeles", "Sacramento, CA"])
def test_text_that_needed_no_cleanup_is_left_exactly_alone(text):
    assert gt.prepare(text).text == text and gt.prepare(text).interpretations == []


def test_an_and_list_is_split_only_when_every_part_stands_alone():
    assert gt.prepare("Othello, WA and Adams County, WA").text == "Othello, WA; Adams County, WA"
    assert gt.prepare("90210 and 94105").text == "90210; 94105"
    assert gt.prepare("Plano TX and Dallas TX").text == "Plano TX; Dallas TX"
    assert gt.prepare("Texas and Oklahoma").text == "Texas and Oklahoma"
    assert gt.prepare("Los Angeles and Orange County, CA").text == "Los Angeles and Orange County, CA"      # 'Los Angeles' alone has no state


def test_typed_geography_types_skip_phrase_cleanup():
    assert gt.prepare("90210 and 94105 and surrounding areas", "zip").text == "90210 and 94105 and surrounding areas"
    assert gt.prepare("Mexico and surrounding areas", "country").interpretations == []


def test_over_long_text_is_not_cleaned_and_never_slow():
    for text in ("x" + " " * 400 + "district", "a " * 2500, "Los Angeles and surrounding areas in " * 40, "A " * 99 + "Co"):
        t0 = time.time()
        gt.prepare(text)
        resolve_geo(text)
        assert time.time() - t0 < 2.0


# --------------------------------------------------------------------------------------------- states

@pytest.mark.parametrize("text,abbr,how", [("Washington", "WA", "exact"), ("washington", "WA", "exact"), ("WA", "WA", "abbr"),
                                           ("Washinton", "WA", "fuzzy"), ("Missisipi", "MS", "fuzzy"), ("Tenessee", "TN", "fuzzy"),
                                           ("Wisconson", "WI", "fuzzy"), ("District of Columbia", "DC", "exact")])
def test_states_exact_abbreviated_and_misspelled(text, abbr, how):
    assert gt.fuzzy_state(text) == (abbr, how)


@pytest.mark.parametrize("text", ["wa", "in", "me", "Springs", "Orange", "Oregano", "", "Kansas City", "x"])
def test_words_that_are_not_states_are_not_guessed_into_states(text):
    assert gt.fuzzy_state(text) == (None, None)


@pytest.mark.parametrize("text,expected", [("Adams County Washington", ("Adams County", "WA", "exact")), ("Adams County Washinton", ("Adams County", "WA", "fuzzy")),
                                          ("Plano TX", ("Plano", "TX", "abbr")), ("Washington District of Columbia", ("Washington", "DC", "exact")),
                                          ("Kansas City", None), ("Texas", None), ("TX", None)])
def test_a_state_at_the_end_of_a_name(text, expected):
    assert gt.split_trailing_state(text) == expected


# --------------------------------------------------------------------------------------------- API shape

def test_the_resolve_endpoint_reports_interpretations_and_suggestions():
    out = main.constanza_resolve(main.ConstanzaResolveRequest(geo_text="Adams County Washinton"))
    assert out["resolved"] and out["interpretations"][0]["became"] == "Adams County, WA"
    assert {s["kind"] for s in out["suggestions"]} == {"broaden"} and out["areas"][0]["geo_id"] == "53001"
    amb = main.constanza_resolve(main.ConstanzaResolveRequest(geo_text="Adams County"))
    assert not amb["resolved"] and amb["ambiguous"][0]["candidates"][0]["population"] > 0


def test_an_estimate_carries_how_the_geography_was_read():
    out = main.constanza_estimate(main.ConstanzaEstimateRequest(
        product_name="Search - SEM", geo_text="Adams County Washinton", national_monthly_total=1_000_000))
    assert out["geo_interpretations"][0]["became"] == "Adams County, WA" and out["geo_suggestions"]
    assert out["by_area"][0]["area_label"] == "Adams County, WA"


# --------------------------------------------------------------------------------------------- review fixes
# Each of these was a confirmed defect found by an independent review of the branch.

@pytest.mark.parametrize("text,label", [
    ("Richmond, CA", "Richmond, CA"), ("Washington, PA", "Washington, PA"), ("Washington, MO", "Washington, MO"), ("Indiana, PA", "Indiana, PA"),
    ("Alexandria, VA", "Alexandria, VA"), ("Peoria, AZ", "Peoria, AZ"), ("Meridian, ID", "Meridian, ID"), ("Springfield, IL", "Springfield, IL"),
    ("Kansas City, KS", "Kansas City, KS"), ("Kansas City, Kansas", "Kansas City, KS"),
])
def test_a_city_and_state_is_that_city_not_a_market_elsewhere_plus_the_whole_state(text, label):
    """'Richmond, CA' used to be the Richmond-Petersburg market PLUS every California market, with high confidence."""
    res = resolve_geo(text)
    assert res.method == "city" and labels(res) == [label] and not res.unmatched_tokens and not res.ambiguous


@pytest.mark.parametrize("text", ["Washington, D.C.", "Washington D.C.", "washington, d.c.", "Washington, DC"])
def test_washington_dc_is_the_market_however_it_is_dotted(text):
    res = resolve_geo(text)
    assert res.method == "exact_dma" and labels(res) == ["Washington, DC (Hagerstown)"] and not res.unmatched_tokens


def test_the_readings_that_were_always_lists_are_still_lists():
    assert resolve_geo("Los Angeles, NV").method == "mixed" and "Las Vegas" in labels(resolve_geo("Los Angeles, NV"))
    assert resolve_geo("Washington, Oregon").method == "state_list" and "Seattle-Tacoma" in labels(resolve_geo("Washington, Oregon"))
    assert labels(resolve_geo("New York, NY")) == ["New York"] and labels(resolve_geo("Dallas, TX")) == ["Dallas-Ft. Worth"]
    assert resolve_geo("CA, NV, AZ").method == "state_list"


def test_a_county_named_in_a_metro_that_spans_two_states_is_not_given_the_inner_places_state():
    res = resolve_geo("Kansas City, MO and surrounding areas in Johnson County")
    assert not res.resolved and [c["label"] for c in res.ambiguous[0]["candidates"]] == ["Johnson County, KS", "Johnson County, MO"]
    assert any("same TV market" in n for n in res.notes)
    assert labels(resolve_geo("Johnson County, KS")) == ["Johnson County, KS"]                               # picking one works


def test_a_container_name_that_exists_in_many_states_is_narrowed_to_the_one_in_the_places_market():
    res = resolve_geo("Othello, WA and surrounding areas in Adams County")
    assert labels(res) == ["Adams County, WA"] and any("same TV market as Othello, WA" in i["why"] for i in res.interpretations)
    res = resolve_geo("Texarkana, TX and surrounding areas in Miller County")                                  # Miller County, TX does not exist
    assert labels(res) == ["Miller County, AR"]


def test_a_container_that_cannot_be_matched_falls_back_to_the_place_named_inside_it():
    res = resolve_geo("Dallas and surrounding areas in Nowhereville County")
    assert labels(res) == ["Dallas-Ft. Worth"] and any("couldn't match" in i["why"] for i in res.interpretations)


def test_a_suggestion_names_the_text_the_planner_typed_so_the_ui_can_replace_it():
    res = resolve_geo("Plano, TX; Travus Co., TX")
    sg = next(s for s in res.suggestions if s["kind"] == "did_you_mean")
    assert sg["label"] == "Travis County, TX" and sg["replaces"] == "Travus Co., TX"                          # not 'Travus County, TX'
    for typed in ("Plano, TX; Travus Co., TX", "Plano, TX; Kolin County, TX", "Adams Co.; Othello, WA", "Othelo; Adams Cty, WA"):
        r = resolve_geo(typed)
        for s in r.suggestions:
            if s.get("replaces"):
                assert s["replaces"].lower() in typed.lower(), (typed, s)
        for entry in r.ambiguous:
            assert entry["input"].lower() in typed.lower(), (typed, entry)


@pytest.mark.parametrize("text", ["Trenton, NJ", "Bergen County, NJ", "Hudson County, NJ", "Dover, DE", "Concord, NH", "Hillsborough County, NH"])
def test_a_state_with_no_market_homed_in_it_is_never_offered_as_a_larger_area(text):
    """The 'all its TV markets' button for NJ, DE, NH and DC resolved to nothing and wiped the planner's text."""
    res = resolve_geo(text)
    assert res.resolved
    assert not [s for s in res.suggestions if s["kind"] == "broaden" and s["geo_type"] == "state"]
    for s in res.suggestions:
        assert resolve_geo(s["resolve_hint"]).resolved, (text, s)


@pytest.mark.parametrize("text,labels_expected", [
    ("Austin and Round Rock, TX", ["Austin"]), ("Orlando and Kissimmee, FL", ["Orlando-Daytona Bch-Melbrn"]),
    ("Tucson and Mesa, AZ", None), ("Boise & Nampa, ID", None), ("Salt Lake City and Provo, UT", None),
    ("Dallas or Houston", ["Dallas-Ft. Worth", "Houston"]), ("Dallas, Houston or Austin", ["Dallas-Ft. Worth", "Houston", "Austin"]),
])
def test_places_listed_with_and_or_a_shared_state_all_resolve(text, labels_expected):
    res = resolve_geo(text)
    assert res.resolved and not res.unmatched_tokens, (text, res.unmatched_tokens)
    if labels_expected:
        assert labels(res) == labels_expected
    assert any(i["why"].startswith("read as a list of places") for i in res.interpretations)


def test_or_is_read_as_a_list_and_says_so():
    res = resolve_geo("Dallas or Houston")
    assert "'or' is read like 'and'" in res.interpretations[0]["why"]


def test_plural_counties_are_one_county_each():
    res = resolve_geo("Los Angeles and Orange counties, CA")
    assert labels(res) == ["Los Angeles County, CA", "Orange County, CA"] and res.method == "county"


def test_one_container_does_not_swallow_the_next_place_in_the_list():
    res = resolve_geo("Plano in Collin County and Houston in Harris County")
    assert labels(res) == ["Collin County, TX", "Harris County, TX"] and not res.unmatched_tokens
    assert understood(res)["Plano in Collin County"] == "Collin County"


def test_cities_in_a_county_is_the_county_not_a_place_called_cities():
    res = resolve_geo("cities in Orange County, CA")
    assert labels(res) == ["Orange County, CA"] and res.interpretations[0]["why"] == "dropped the introductory words"


def test_an_overlap_offer_lists_every_spelling_typed_for_the_same_place():
    res = resolve_geo("Othello, WA; Othello WA; Adams County, WA")
    offer = next(s for s in res.suggestions if s["kind"] == "overlap")
    assert offer["drop_inputs"] == ["Othello, WA", "Othello WA"]


def test_an_overlap_offer_is_worded_as_a_possibility_for_places_that_are_not_in_the_county():
    """Fremont and Berkeley share a market with Santa Clara County but are in Alameda County: the offer never says otherwise."""
    res = resolve_geo("Santa Clara County, CA; Fremont, CA; Berkeley, CA")
    offer = next(s for s in res.suggestions if s["kind"] == "overlap")
    assert "may be inside" in offer["label"] and "can't prove" in offer["why"] and "only" not in offer["label"].lower().split("drop")[0]


def test_the_ambiguity_of_a_container_names_the_whole_typed_phrase_so_picking_replaces_it():
    text = "Dallas; Kansas City, MO and surrounding areas in Johnson County; Austin"
    res = resolve_geo(text)
    entry = res.ambiguous[0]
    assert entry["input"] == "Kansas City, MO and surrounding areas in Johnson County" and entry["input"] in text
    assert [c["label"] for c in entry["candidates"]] == ["Johnson County, KS", "Johnson County, MO"]
    assert gt.prepare("Los Angeles and Orange counties, CA").origins == {}                    # several places from one phrase: none stands for the text
    assert gt.prepare("Plano in Collin County and Houston in Harris County").origins == {
        "Collin County": "Plano in Collin County", "Harris County": "Houston in Harris County"}
