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
    # the place has no state of its own: both Johnson Counties are in its market, so the planner picks
    res = resolve_geo("Kansas City and surrounding areas in Johnson County")
    assert not res.resolved and [c["label"] for c in res.ambiguous[0]["candidates"]] == ["Johnson County, KS", "Johnson County, MO"]
    assert any("same TV market" in n for n in res.notes)
    assert labels(resolve_geo("Johnson County, KS")) == ["Johnson County, KS"]                               # picking one works


def test_a_container_smaller_than_the_place_named_inside_it_is_not_the_one_meant():
    # Kansas City, MO has ten times the people of Johnson County, MO: the county that surrounds it is the Kansas one
    res = resolve_geo("Kansas City, MO and surrounding areas in Johnson County")
    assert labels(res) == ["Johnson County, KS"]
    assert any("big enough to surround" in i["why"] for i in res.interpretations)
    alt = [s for s in res.suggestions if s["kind"] == "did_you_mean"]
    assert [s["label"] for s in alt] == ["Johnson County, MO"] and alt[0]["replaces"] == "Kansas City, MO and surrounding areas in Johnson County"


def test_a_container_follows_the_state_typed_with_the_place_inside_it():
    assert labels(resolve_geo("Overland Park, KS and surrounding areas in Johnson County")) == ["Johnson County, KS"]
    res = resolve_geo("Warrensburg, MO and surrounding areas in Johnson County")                            # small place: the MO county
    assert labels(res) == ["Johnson County, MO"] and any("state typed with Warrensburg, MO" in i["why"] for i in res.interpretations)


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
    text = "Dallas; Kansas City and surrounding areas in Johnson County; Austin"
    res = resolve_geo(text)
    entry = res.ambiguous[0]
    assert entry["input"] == "Kansas City and surrounding areas in Johnson County" and entry["input"] in text
    assert [c["label"] for c in entry["candidates"]] == ["Johnson County, KS", "Johnson County, MO"]
    assert gt.prepare("Los Angeles and Orange counties, CA").origins == {}                    # several places from one phrase: none stands for the text
    assert gt.prepare("Plano in Collin County and Houston in Harris County").origins == {
        "Collin County": "Plano in Collin County", "Harris County": "Houston in Harris County"}


# --------------------------------------------------------------------------------------------- second review
# Regressions found in the fixes themselves.

@pytest.mark.parametrize("text,expected", [
    ("Beaverton, OR and Hillsboro, OR", ["Beaverton, OR", "Hillsboro, OR"]),
    ("Multnomah County, OR and Washington County, OR", ["Multnomah County, OR", "Washington County, OR"]),
    ("Dallas, Houston, or Austin", ["Dallas-Ft. Worth", "Houston", "Austin"]),
    ("Tucson, AZ or Mesa, AZ", ["Tucson (Sierra Vista)", "Mesa, AZ"]),
])
def test_a_state_code_that_spells_a_conjunction_stays_a_state(text, expected):
    """OR is Oregon: a list written '..., OR and ...' lost its state when 'OR' was read as the word."""
    res = resolve_geo(text)
    assert labels(res) == expected and not res.unmatched_tokens and not res.whole_states


@pytest.mark.parametrize("text,market,inside", [
    ("Chicago and Gary, IN", "Chicago", "Gary, IN"), ("Cincinnati and Covington, KY", "Cincinnati", "Covington, KY"),
    ("Philadelphia and Wilmington, DE", "Philadelphia", "Wilmington, DE"),
])
def test_a_state_at_the_end_is_not_given_to_a_market_that_needs_none(text, market, inside):
    """'Chicago and Gary, IN' added every Indiana market: 'Chicago, IN' was read as the two states."""
    res = resolve_geo(text)
    assert labels(res) == [market] and not res.unmatched_tokens and not res.whole_states
    assert any(inside in n and "counted once" in n for n in res.notes)


def test_a_state_at_the_end_is_shared_with_every_place_that_needs_one():
    res = resolve_geo("Mesa and Gilbert, AZ")
    assert labels(res) == ["Mesa, AZ", "Gilbert, AZ"]
    assert "the state at the end (AZ) applies to each place that needs one" in res.interpretations[0]["why"]


@pytest.mark.parametrize("text,expected", [
    ("Lincoln and Springfield, IL", ["Lincoln, IL", "Springfield, IL"]), ("Lincoln and Roseville, CA", ["Lincoln, CA", "Roseville, CA"]),
    ("Birmingham and Troy, MI", ["Birmingham, MI", "Troy, MI"]), ("Alexandria and Washington, VA", ["Alexandria, VA", "Washington, VA"]),
    ("Kansas City and Overland Park, KS", ["Kansas City, KS", "Overland Park, KS"]),
])
def test_the_state_typed_at_the_end_wins_over_a_same_named_market_elsewhere(text, expected):
    """'Lincoln and Springfield, IL' kept 'Lincoln' bare because it names a Nebraska market, and added the whole Nebraska market."""
    res = resolve_geo(text)
    assert labels(res) == expected and not res.unmatched_tokens and not res.whole_states


@pytest.mark.parametrize("text,town", [("Kansas, OK", "Kansas, OK"), ("Nevada, TX", "Nevada, TX"), ("Oregon, IL", "Oregon, IL"), ("Washington, VA", "Washington, VA")])
def test_a_small_town_named_like_a_state_is_still_the_town_with_the_two_states_offered(text, town):
    """A cutoff by town size sent 'Oregon, IL' to two whole states (15M) with no way back; the town is the default at any size
    because the other reading is the one that is silently huge when wrong."""
    res = resolve_geo(text)
    assert res.method == "city" and labels(res) == [town]
    assert any(sg["kind"] == "did_you_mean" and sg["label"].startswith("The states") and sg["replaces"] == text for sg in res.suggestions)


@pytest.mark.parametrize("text", ["Washington, Oregon", "Oklahoma, TX", "Washington, OR, Idaho"])
def test_two_state_names_or_a_state_with_no_such_town_stay_states(text):
    assert resolve_geo(text).method == "state_list"


@pytest.mark.parametrize("text,pair", [
    ("Delaware, OH", "The states Delaware and Ohio"), ("Washington, PA", "The states Washington and Pennsylvania"),
    ("Indiana, PA", "The states Indiana and Pennsylvania"), ("Oregon, WI", "The states Oregon and Wisconsin"),
])
def test_a_state_name_and_a_state_code_is_the_town_with_the_two_states_one_click_away(text, pair):
    res = resolve_geo(text)
    assert res.method == "city" and len(res.areas) == 1
    alt = next(s for s in res.suggestions if s["kind"] == "did_you_mean")
    assert alt["label"] == pair and alt["replaces"] == text
    assert resolve_geo(alt["resolve_hint"]).method == "state_list"                                          # the offer really resolves


def test_a_suggestion_inside_a_list_names_the_item_it_came_from_not_the_whole_text():
    res = resolve_geo("Travus Co., TX and Dallas, TX")
    assert labels(res) == ["Dallas-Ft. Worth"] and res.unmatched_tokens == ["Travus County, TX"]
    sg = next(s for s in res.suggestions if s["kind"] == "did_you_mean")
    assert sg["label"] == "Travis County, TX" and sg["replaces"] == "Travus Co., TX"


def test_a_long_list_of_places_resolves_each_distinct_place_once(monkeypatch):
    from app.services import constanza_geo as geo
    calls = []
    real = geo._resolve_single_uncached
    monkeypatch.setattr(geo, "_resolve_single_uncached", lambda text, hint: (calls.append(text), real(text, hint))[1])
    towns = ["Plano", "Frisco", "Allen", "McKinney", "Garland", "Irving", "Denton", "Mesquite"]
    res = resolve_geo("; ".join(f"{towns[i % 8]}, TX" for i in range(300)))
    assert res.resolved and len(res.areas) == 8
    assert len(calls) <= 2 * len(towns), calls[:20]                                                          # not one pass per repeated item


@pytest.mark.parametrize("text,zip_label", [
    ("Plano, TX 75024", "ZIP 75024"), ("Portland, OR 97201", "ZIP 97201"), ("Dallas, TX 75201", "ZIP 75201"),
    ("Los Angeles, CA 90012", "ZIP 90012"), ("Plano, TX 75024-1234", "ZIP 75024"), ("New York, NY 10001", "ZIP 10001"),
    ("plano, tx 75024", "ZIP 75024"), ("portland, or 97201", "ZIP 97201"), ("bend, or 97701", "ZIP 97701"),     # lowercase: "or" is Oregon
])
def test_a_city_state_and_zip_is_the_zip(text, zip_label):
    """'Dallas, TX 75201' was the Dallas market plus an unmatched 'TX 75201', and 'Plano, TX 75024' matched nothing."""
    res = resolve_geo(text)
    assert res.method == "zip" and labels(res) == [zip_label] and not res.unmatched_tokens
    assert any(f"read '{text}' as {zip_label}" in i["why"] and "were not used" in i["why"] for i in res.interpretations)


def test_the_address_note_names_the_market_of_the_zip_so_a_contradiction_shows():
    """'Dallas, TX 77002' is a Houston ZIP: the ZIP wins (it is the exact place) and the note says which market that is."""
    res = resolve_geo("Dallas, TX 77002")
    assert labels(res) == ["ZIP 77002"]
    assert any("in the Houston market" in i["why"] for i in res.interpretations)


@pytest.mark.parametrize("text,markets", [
    ("Dallas and Houston, TX 77002", {"Dallas-Ft. Worth", "Houston"}), ("Houston, Dallas, Austin, TX 78701", {"Houston", "Dallas-Ft. Worth", "Austin"}),
    ("Dallas, TX, Houston, TX 77002", {"Dallas-Ft. Worth", "Houston"}), ("Miami and Orlando, FL 32801", {"Miami-Ft. Lauderdale", "Orlando-Daytona Bch-Melbrn"}),
    ("Dallas or Houston, TX 77002", {"Dallas-Ft. Worth", "Houston"}),
])
def test_places_typed_before_a_state_and_zip_are_never_dropped_for_the_zip(text, markets):
    """Only ONE place and its state before a ZIP is an address. 'Dallas and Houston, TX 77002' became the ZIP alone, silently."""
    res = resolve_geo(text)
    assert markets <= set(labels(res)), (text, labels(res))
    assert not any("as ZIP" in i["why"] for i in res.interpretations)


def test_an_address_whose_zip_is_not_in_the_data_falls_back_to_the_city_and_says_so():
    res = resolve_geo("Phoenix AZ 85001")                                  # a PO-box ZIP: no residents, no row
    assert labels(res) == ["Phoenix (Prescott)"] and not res.unmatched_tokens
    assert any("not in the data" in i["why"] for i in res.interpretations)


def test_addresses_in_a_list_are_each_their_zip():
    assert labels(resolve_geo("Plano, TX 75024 and Frisco, TX 75034")) == ["ZIP 75024", "ZIP 75034"]
    assert labels(resolve_geo("Plano, TX 75024; Austin, TX")) == ["ZIP 75024", "Austin"]
    res = resolve_geo("Plano, TX 75024; Travus Co., TX")
    sg = next(s for s in res.suggestions if s["kind"] == "did_you_mean")
    assert sg["replaces"] == "Travus Co., TX"


def test_text_that_only_looks_like_an_address_is_left_alone():
    assert labels(resolve_geo("90210 94105")) == ["ZIP 90210", "ZIP 94105"]                        # two ZIPs, not an address
    res = resolve_geo("Plano, TX 75024 Frisco, TX 75034")                                          # two addresses with nothing between
    assert not any("as ZIP" in i["why"] for i in res.interpretations)
    res = resolve_geo("Plano, TX 75024", "city")                                                   # the planner said what it is
    assert not any("as ZIP" in i["why"] for i in res.interpretations)
    assert gt.address_place("Texas 75201") is None and gt.address_place("75024") is None


@pytest.mark.parametrize("text,markets", [
    ("portland, or and seattle", {"Portland, OR", "Seattle-Tacoma"}), ("portland, or & salem, or", {"Portland, OR"}),
    ("portland, or and boise, id", {"Portland, OR", "Boise"}), ("portland, or and vancouver, wa", {"Portland, OR"}),
])
def test_a_lowercase_or_after_a_comma_is_oregon_when_a_conjunction_follows(text, markets):
    """The Oxford-comma rewrite deleted the state of 'portland, or and seattle' and left an ambiguous 'portland'."""
    res = resolve_geo(text)
    assert markets <= set(labels(res)) and not res.ambiguous, (text, labels(res), res.ambiguous)


def test_a_lowercase_or_before_a_zip_is_oregon_and_the_address_is_the_zip():
    res = resolve_geo("bend, or 97701")
    assert labels(res) == ["ZIP 97701"] and not res.ambiguous and not res.unmatched_tokens


@pytest.mark.parametrize("text,expected", [
    ("Portland, OR, or Salem, OR", {"Portland, OR"}), ("Eugene, OR, or Salem, OR", {"Eugene", "Salem, OR"}),
    ("Austin, TX, or Round Rock, TX", {"Austin"}), ("Dallas, Houston, or Austin", {"Dallas-Ft. Worth", "Houston", "Austin"}),
    ("Chicago, Dallas, or Miami", {"Chicago", "Dallas-Ft. Worth", "Miami-Ft. Lauderdale"}),
])
def test_an_oxford_or_separates_the_places_instead_of_gluing_them_into_one_comma_list(text, expected):
    """'A, ST, or B, ST' became 'A, ST, B, ST', a comma list that is not split: the second place came back unmatched."""
    res = resolve_geo(text)
    assert expected <= set(labels(res)) and not res.unmatched_tokens, (text, labels(res), res.unmatched_tokens)
    assert any("read the 'or' before the last place as part of the list" in i["why"] for i in res.interpretations)


def test_an_oxford_or_between_addresses_makes_each_its_zip():
    assert labels(resolve_geo("Bend, OR 97701, or Salem, OR 97301")) == ["ZIP 97701", "ZIP 97301"]
    assert labels(resolve_geo("Plano in Collin County, or Houston in Harris County")) == ["Collin County, TX", "Harris County, TX"]


# --------------------------------------------------------------------------------------------- fourth review

@pytest.mark.parametrize("text,market", [("Dallas or 75201", "Dallas-Ft. Worth"), ("Houston or 77002", "Houston"), ("Los Angeles or 90001", "Los Angeles")])
def test_a_lowercase_or_between_a_city_and_a_zip_is_a_word_not_oregon(text, market):
    """Upper-casing every trailing two-letter word read 'or' as OR: 'Dallas or 75201' became the ZIP alone (the city dropped)."""
    res = resolve_geo(text)
    assert market in labels(res) and not any("as ZIP" in i["why"] for i in res.interpretations), (text, labels(res))


def test_an_address_built_from_a_city_that_does_not_exist_in_that_state_is_left_to_the_other_readings():
    """'Miami, OR 33101' with a ZIP the data lacks fell back to 'Miami, OR' and added every Oregon market."""
    res = resolve_geo("Miami, or 33101")
    assert "Portland, OR" not in labels(res) and "Eugene" not in labels(res)
    assert gt.address_place("Miami, OR 33101") is None


@pytest.mark.parametrize("text,expected", [
    ("portland, or\nseattle, wa", {"Portland, OR", "Seattle-Tacoma"}), ("bend, or\nsalem, or\neugene, or", {"Bend, OR", "Salem, OR", "Eugene"}),
    ("Bend, or\r\nSalem, or", {"Bend, OR", "Salem, OR"}), ("portland, or\n\nseattle, wa", {"Portland, OR", "Seattle-Tacoma"}),
])
def test_a_lowercase_state_code_at_the_end_of_a_line_stays_the_state(text, expected):
    """The Oxford rewrite ran across line breaks: ', or<newline>seattle' lost the state of every line but the last."""
    res = resolve_geo(text)
    assert expected <= set(labels(res)) and not res.ambiguous, (text, labels(res), res.ambiguous)


def test_a_comma_after_a_zip_ends_an_address_when_the_text_holds_several():
    """Only the last address of a comma-separated list became a ZIP (it followed ', or'); the earlier ones stayed one chunk."""
    assert labels(resolve_geo("Chicago, IL 60601, Chicago, IL 60602, or Chicago, IL 60603")) == ["ZIP 60601", "ZIP 60602", "ZIP 60603"]
    assert labels(resolve_geo("Houston, TX 77002, Austin, TX 78701, or San Antonio, TX 78205")) == ["ZIP 77002", "ZIP 78701", "ZIP 78205"]
    assert labels(resolve_geo("Plano, TX 75024, Frisco, TX 75034")) == ["ZIP 75024", "ZIP 75034"]
    res = resolve_geo("Washington, DC 20001, USA")                                           # one ZIP: one address, nothing is split
    assert not any("end of an address" in i["why"] for i in res.interpretations)
    assert labels(resolve_geo("90210, 94105")) == ["ZIP 90210", "ZIP 94105"]                 # plain ZIP lists are untouched


@pytest.mark.parametrize("text,as_typed,market", [
    ("Kansas City and Overland Park, KS", "Kansas City; Overland Park, KS", "Kansas City"),
    ("Dallas and Lostine, OR", "Dallas; Lostine, OR", "Dallas-Ft. Worth"),
    ("Lincoln and Springfield, IL", "Lincoln; Springfield, IL", "Lincoln & Hastings-Krny"),
])
def test_the_places_as_typed_are_one_click_away_when_the_shared_state_turned_a_market_into_a_town(text, as_typed, market):
    """'Dallas and Lostine, OR' is Dallas, OR + Lostine, OR (the state typed once belongs to each place) - and the TV market the
    planner may have meant is offered, not silently lost (8.96M became 17k)."""
    res = resolve_geo(text)
    sg = next(s for s in res.suggestions if s["label"].startswith("Each place as typed"))
    assert sg["resolve_hint"] == as_typed and sg["replaces"] == text and sg["kind"] == "did_you_mean"
    assert market in labels(resolve_geo(sg["resolve_hint"]))                                 # and it really resolves that way


def test_no_alternative_is_offered_when_the_bare_place_names_no_market():
    res = resolve_geo("Mesa and Gilbert, AZ")
    assert not [s for s in res.suggestions if s["label"].startswith("Each place as typed")]

