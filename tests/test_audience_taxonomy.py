"""The audience (interest) taxonomy: the bundled file, search, the "never guess a rate" rule, the interest endpoints and the
database seed.

The offline tests read the bundled app/data/constanza/audience_taxonomy.json, which is what answers when Postgres is down.
The database tests run inside a transaction that is rolled back, so the real rows are never changed."""
from __future__ import annotations

import copy
import json
import os

import psycopg
import pytest

import app.main as main
from app.services import audience_taxonomy as tax
from app.services import migrations
from app.services.constanza_audience import AudienceSpec, estimate_audience, resolve_interest
from app.services.constanza_engine import estimate_avails
from app.services.constanza_geo import resolve_geo

BUNDLED = json.loads(tax.DATA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def taxonomy():
    return tax.parse(BUNDLED)


# ---------------------------------------------------------------------------------------------------------------------
# The bundled file
# ---------------------------------------------------------------------------------------------------------------------

def test_the_bundled_taxonomy_parses_and_covers_every_audience_the_team_listed(taxonomy):
    assert len(taxonomy.segments) == 90
    assert len(taxonomy.groups) >= 12
    assert all(s.label and s.group_key for s in taxonomy.segments.values())


def test_every_rate_is_a_share_of_adults_with_a_named_source_and_evidence(taxonomy):
    rated = [s for s in taxonomy.segments.values() if s.supported]
    assert len(rated) >= 50
    for s in rated:
        assert 0 < s.rate_all <= 1, s.id
        assert s.status in ("sourced", "derived"), s.id
        assert s.source_title and s.source_url and s.source_url.startswith("http"), s.id
        assert s.source_date, s.id
        assert s.evidence or s.derivation, s.id                       # something a reviewer can check the number against
        assert s.confidence in ("high", "moderate", "low"), s.id
        for g in (s.rate_male, s.rate_female):
            assert g is None or 0 < g <= 1, s.id
        if s.status == "derived":
            assert s.derivation, f"{s.id}: a derived rate shows its arithmetic"


def test_a_segment_with_no_public_rate_is_listed_but_carries_no_number(taxonomy):
    blank = [s for s in taxonomy.segments.values() if not s.supported]
    assert blank, "the research honestly found segments with no public rate"
    for s in blank:
        assert s.status == "no_public_rate" and s.rate_all is None and s.rate_male is None and s.rate_female is None, s.id
        assert s.confidence == "none", s.id
        assert s.rate_for("male") is None


def test_the_names_the_sales_team_uses_find_their_own_segment(taxonomy):
    """Typing the team's own wording ("Dental Care Seekers", "Auto Repair Intenders") puts that segment first."""
    missed = []
    for s in taxonomy.segments.values():
        for name in (s.label, *s.company_names):
            hits = taxonomy.search(name, limit=3)
            if not hits or hits[0].segment.id != s.id:
                missed.append((name, hits[0].segment.id if hits else None, s.id))
    assert not missed, missed


@pytest.mark.parametrize("phrase,expected", [
    ("likes sports", "sports_fan"), ("football", "sports_football_nfl"), ("soccer", "sports_soccer"), ("video games", "video_gamer"),
    ("dentist", "health_dental"), ("fitnes gym", "health_fitness"), ("new parents", "fam_new_parents"), ("college alumni", "edu_alumni_students"),
    ("Hispanic music concerts", "ent_latin_concerts"), ("sports betting", "ent_sports_betting"), ("EV", "auto_ev"),
])
def test_everyday_phrases_resolve_to_the_right_segment(phrase, expected):
    assert resolve_interest(phrase).category == expected


@pytest.mark.parametrize("phrase", [
    "pet owners", "dog owners", "truck owners", "home insurance", "urgent care", "health care", "personal injury", "senior living",
    "nursing school", "phone plans", "live music", "hispanic voters", "life insurance", "insurance broker", "caregivers", "election",
])
def test_a_single_shared_word_is_a_suggestion_never_an_applied_rate(taxonomy, phrase):
    """'pet owners' used to apply Small Business Owners' rate because both contain 'owners' (score 55, exactly the apply line)."""
    assert taxonomy.best_match(phrase) is None, phrase
    assert resolve_interest(phrase).supported is False


def test_two_audiences_that_fit_equally_well_are_left_for_the_planner_to_choose(taxonomy):
    assert taxonomy.best_match("insurance") is None                      # auto insurance or health insurance?
    top = {h.segment.id for h in taxonomy.search("insurance", limit=4)}
    assert {"auto_insurance_shoppers", "fin_health_insurance"} <= top    # ... and both are offered
    assert taxonomy.best_match("owners") is None                          # a bare role word says who, not what


@pytest.mark.parametrize("phrase,segment", [
    ("mortgage shoppers", "fin_mortgage"), ("luxury car shoppers", "auto_luxury"), ("luxury car shopper", "auto_luxury"),
    ("furniture shoppers", "home_furniture_decor"), ("beauty shoppers", "retail_beauty"), ("tire shopper", "auto_tire_shoppers"),
    ("car shoppers", "auto_in_market"), ("solar shoppers", "home_solar"), ("wedding shoppers", "fam_wedding_planning"),
    ("organic shoppers", "health_natural_organic"), ("dental shoppers", "health_dental"),
])
def test_a_bare_role_word_beside_a_topic_does_not_change_the_audience(taxonomy, phrase, segment):
    """'shoppers' is a keyword of Retail Shoppers (General) and tied with the topic's own keyword, so these matched nothing."""
    assert taxonomy.best_match(phrase).segment.id == segment


@pytest.mark.parametrize("phrase", ["grocery shoppers", "online shoppers", "holiday shoppers", "coupon shoppers", "fashion shoppers", "women shoppers"])
def test_a_role_word_beside_a_topic_nobody_names_means_the_catch_all_audience(taxonomy, phrase):
    """No segment is about 'online' or 'grocery': Retail Shoppers (General) is the best there is, as it was before the role-word rule
    capped it (the cap had made these match nothing, and dropped the catch-all from the suggestions too)."""
    hit = taxonomy.best_match(phrase)
    assert hit is not None and hit.segment.id == "retail_general", phrase
    assert taxonomy.best_match("shoppers").segment.id == "retail_general"


@pytest.mark.parametrize("phrase,topic", [
    ("sports shoppers", "sports_fan"), ("sports apparel shoppers", "sports_fan"), ("golf shoppers", "sports_golf"),
    ("video game shoppers", "video_gamer"), ("hotel shoppers", "travel_frequent"), ("casino shoppers", "ent_casino"),
    ("fitness shoppers", "health_fitness"), ("college football shoppers", "sports_football_college"),
])
def test_a_role_word_carries_over_only_to_an_in_market_kind_of_audience(taxonomy, phrase, topic):
    """Sports Fans are not 'sports shoppers': a fan or interest audience is offered for the phrase, never applied to it (a real
    Gallup sports-fan rate was being applied to a shopping phrase)."""
    assert taxonomy.best_match(phrase) is None, phrase
    assert topic in [h.segment.id for h in taxonomy.search(phrase, limit=6)], phrase


@pytest.mark.parametrize("phrase", ["nascar shoppers", "college football shoppers", "college football buyers", "home improvement buyers"])
def test_a_held_back_topic_is_not_replaced_by_a_looser_match(taxonomy, phrase):
    """With 'Sports Fans - College Football' held back, 'college' alone matched Higher Education Seekers, and 'car shopper' was
    found inside 'nas-CAR SHOPPER-s'."""
    assert taxonomy.best_match(phrase) is None, phrase


@pytest.mark.parametrize("phrase,segment", [
    ("student loan shoppers", "fin_in_market"), ("contractor shoppers", "home_services"), ("contractor buyers", "home_services"),
    ("masters shoppers", "edu_higher_ed_seekers"),
])
def test_a_segment_that_accounts_for_as_much_of_the_topic_and_is_in_market_is_the_reading_of_a_shopper_phrase(taxonomy, phrase, segment):
    """The wrong-kind segment (College Students, the B2B construction workforce, Masters golf) is held back; an in-market segment
    that covers the same words is what 'shoppers' means. The bare word stays ambiguous where two segments share it."""
    assert taxonomy.best_match(phrase).segment.id == segment, phrase
    assert taxonomy.best_match("masters") is None


@pytest.mark.parametrize("phrase,segment", [
    ("home buyers", "home_buyers_movers"), ("homes buyers", "home_buyers_movers"), ("auto intenders", "auto_in_market"),
    ("college seekers", "edu_higher_ed_seekers"), ("college buyers", "edu_higher_ed_seekers"), ("university seekers", "edu_higher_ed_seekers"),
    ("degree seekers", "edu_higher_ed_seekers"),
])
def test_a_plural_in_market_phrase_is_not_blocked_by_a_one_word_overlap_with_a_wrong_kind_segment(taxonomy, phrase, segment):
    """'home buyers' shares 'home' with Smart Home and Home Improvement, 'auto intenders' shares 'auto' with Auto Racing: those
    one-word overlaps were never going to apply, yet they were 'held back' and blocked the real segment (a blocker)."""
    assert taxonomy.best_match(phrase).segment.id == segment, phrase


@pytest.mark.parametrize("phrase", [
    "spanish speaking", "spanish-speaking", "spanish language", "bilingual households", "speaks spanish", "spanglish speakers",
    "bilingual families", "spanish dominant", "spanish speaking audience", "spanish language radio", "spanish speaking shoppers",
    "spanish speaking parents",
])
def test_a_language_word_with_only_filler_beside_it_is_the_language_audience(taxonomy, phrase):
    """Language words are modifiers only when something else names a topic: 'speaking', 'language' or 'households' do not."""
    assert taxonomy.best_match(phrase).segment.id == "tech_bilingual", phrase


@pytest.mark.parametrize("text,plural", [("contractor", "contractors"), ("dentist", "dentists"), ("plumber", "plumbers"), ("playstation", "playstations")])
def test_a_typed_plural_still_finds_the_keyword_it_is_a_plural_of(taxonomy, text, plural):
    assert taxonomy.best_match(plural).segment.id == taxonomy.best_match(text).segment.id


def test_a_keyword_is_not_found_across_the_boundary_of_two_typed_words(taxonomy):
    """'car shopper' sits inside 'nas-CAR SHOPPER-s'; 'videogames' for 'video games' (closed-up spaces) still works."""
    assert taxonomy.best_match("videogames").segment.id == "video_gamer" and taxonomy.best_match("play station").segment.id == "video_gamer"
    assert "auto_in_market" not in [h.segment.id for h in taxonomy.search("nascar shoppers", limit=3) if h.score >= 55]


def test_a_phrase_that_holds_a_segments_own_full_name_is_that_segment_whatever_the_role_word(taxonomy):
    assert taxonomy.best_match("New Homeowners shoppers").segment.id == "home_new_owners"
    assert taxonomy.best_match("Beauty & Cosmetics Aficionados shoppers").segment.id == "retail_beauty"


@pytest.mark.parametrize("phrase,segment", [
    ("home services in Spanish", "home_services"), ("contractor in Spanish", "home_services"), ("automotive in Spanish", "auto_in_market"),
    ("quiropractico in Spanish", "health_chiropractic"), ("ford in Spanish", "auto_new_domestic"), ("golf fans in Spanish", "sports_golf"),
    ("tax prep in Spanish", "fin_tax"), ("new mom in Spanish", "fam_new_parents"), ("vehicle shopper in Spanish", "auto_in_market"),
])
def test_a_language_word_beside_a_topic_never_competes_with_it(taxonomy, phrase, segment):
    """'<audience> in Spanish' tied with the 'spanish' keyword of Bilingual Speakers and matched nothing (or, for a short keyword,
    matched Bilingual Speakers)."""
    assert taxonomy.best_match(phrase).segment.id == segment, phrase


@pytest.mark.parametrize("phrase", ["spanish", "bilingual", "spanish speakers", "bilingual spanish", "spanglish"])
def test_language_alone_is_still_the_bilingual_audience(taxonomy, phrase):
    assert taxonomy.best_match(phrase).segment.id == "tech_bilingual", phrase


SUFFIXES = (" shoppers", " owners", " buyers", " in Spanish")


def test_adding_a_role_or_language_word_to_any_name_or_keyword_never_picks_another_audience(taxonomy):
    """It may leave the choice to the planner (the role word does not fit the segment); it must never choose a different segment -
    except where another segment has the very same phrase ('masters': golf or higher education), which a shopper / seeker word
    settles in favour of the in-market one."""
    from app.services.audience_taxonomy import _fold
    owners: dict = {}
    for seg in taxonomy.segments.values():
        for phrase in (seg.label, *seg.keywords, *seg.company_names):
            owners.setdefault(_fold(phrase), set()).add(seg.id)
    wrong = []
    for seg in taxonomy.segments.values():
        for phrase in (seg.label, *seg.keywords, *seg.company_names):
            if len(owners[_fold(phrase)]) > 1:
                continue
            for suffix in ("",) + SUFFIXES:
                hit = taxonomy.best_match(phrase + suffix)
                if hit is not None and hit.segment.id != seg.id:
                    wrong.append((phrase + suffix, hit.segment.id))
    assert not wrong, wrong[:10]


def test_an_exact_label_or_keyword_always_finds_its_segment_unless_another_segment_has_the_very_same_phrase(taxonomy):
    from app.services.audience_taxonomy import _fold
    owners: dict = {}
    for seg in taxonomy.segments.values():
        for phrase in (seg.label, *seg.keywords, *seg.company_names):
            owners.setdefault(_fold(phrase), set()).add(seg.id)
    missed = []
    for seg in taxonomy.segments.values():
        for phrase in (seg.label, *seg.keywords, *seg.company_names):
            hit = taxonomy.best_match(phrase)
            if (hit is None or hit.segment.id != seg.id) and owners[_fold(phrase)] == {seg.id}:
                missed.append((phrase, hit.segment.id if hit else None))
    assert not missed, missed[:10]


def test_an_exact_name_still_wins_even_when_another_segment_scores_close(taxonomy):
    assert taxonomy.best_match("Sports Fans").segment.id == "sports_fan"
    assert taxonomy.best_match("Dental Care Seekers").segment.id == "health_dental"


def test_the_ambiguous_single_words_the_review_found_no_longer_route_to_the_wrong_segment(taxonomy):
    assert taxonomy.best_match("contractors").segment.id == "home_services"        # was the B2B construction workforce
    assert taxonomy.best_match("real estate broker").segment.id == "biz_re_agents"
    assert "broker" not in taxonomy.segments["biz_re_agents"].keywords
    assert "caregiver" not in taxonomy.segments["health_professionals"].keywords
    assert "contractor" not in taxonomy.segments["biz_construction"].keywords


def test_a_label_does_not_promise_more_than_the_number_measures(taxonomy):
    """The review found two segments whose label said 'follows politics' / 'religious or spiritual' over a rate that measures
    something much narrower. The label and description now say what the number is."""
    political = taxonomy.segments["civic_political_engaged"]
    assert "highly engaged" in political.label.lower() and "narrow" in political.description
    assert not {"voter", "election", "politics", "political news"} & set(political.keywords)
    religious = taxonomy.segments["civic_religious"]
    assert "attend" in religious.label.lower() and "at least once a month" in religious.description
    assert "Spiritual & Religious Seekers" in religious.company_names              # the team's own wording still finds it
    assert taxonomy.search("Spiritual & Religious Seekers", limit=1)[0].segment.id == "civic_religious"


def test_spelling_mistakes_still_find_the_segment(taxonomy):
    assert taxonomy.search("chiropracter")[0].segment.id == "health_chiropractic"
    assert taxonomy.search("realestate investors")[0].segment.id == "home_re_investors"


def test_a_search_that_matches_nothing_returns_nothing_rather_than_the_closest_guess(taxonomy):
    assert taxonomy.search("zzzzzz qqqq") == []
    assert taxonomy.search("") == []
    assert taxonomy.best_match("zzzzzz qqqq") is None


# ---------------------------------------------------------------------------------------------------------------------
# The "never guess" rule, end to end
# ---------------------------------------------------------------------------------------------------------------------

def test_an_interest_with_no_public_rate_is_reported_and_leaves_the_estimate_unchanged():
    base = estimate_avails("Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)", resolve_geo("Los Angeles"), months=1)
    plain = estimate_audience(base, AudienceSpec(age_min=25, age_max=44))
    blank = estimate_audience(base, AudienceSpec(age_min=25, age_max=44, interest_terms=["quinceañera"]))
    assert blank.interests[0].category == "fam_quinceanera" and not blank.interests[0].supported
    assert blank.imps_mid == pytest.approx(plain.imps_mid) and blank.cuts_applied == plain.cuts_applied
    assert any("no" in n.lower() and "rate" in n.lower() for n in blank.notes + [blank.interests[0].caveats or ""])


def test_a_lower_confidence_rate_widens_the_range_more_than_a_higher_one():
    base = estimate_avails("Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)", resolve_geo("Los Angeles"), months=1)
    high = estimate_audience(base, AudienceSpec(interest_terms=["dentist"]))               # CDC / NCHS, high confidence
    low = estimate_audience(base, AudienceSpec(interest_terms=["makeup"]))                  # a proxy, low confidence
    assert high.interests[0].confidence == "high" and low.interests[0].confidence == "low"
    width = lambda e: (e.imps_high - e.imps_low) / e.imps_mid                                # noqa: E731
    assert width(low) > width(high)


# ---------------------------------------------------------------------------------------------------------------------
# Parsing rules
# ---------------------------------------------------------------------------------------------------------------------

def _with(**changes):
    data = copy.deepcopy(BUNDLED)
    seg = next(s for s in data["segments"] if s["id"] == "health_dental")
    seg["incidence"].update(changes)
    return data


@pytest.mark.parametrize("changes,message", [
    ({"all": 1.4}, "between"),
    ({"all": None}, "exactly when"),
    ({"status": "no_public_rate"}, "exactly when"),
    ({"source": {"title": "x"}}, "name its source"),
    ({"confidence": "certain"}, "confidence"),
])
def test_the_parser_refuses_data_that_breaks_the_rules(changes, message):
    with pytest.raises(tax.TaxonomyError, match=message):
        tax.parse(_with(**changes))


def test_null_text_fields_read_as_empty_strings():
    data = _with(definition=None, denominator=None, time_window=None, caveats=None)
    seg = tax.parse(data).segments["health_dental"]
    assert (seg.definition, seg.denominator, seg.time_window, seg.caveats) == ("", "", "", "")


# ---------------------------------------------------------------------------------------------------------------------
# The endpoints
# ---------------------------------------------------------------------------------------------------------------------

def test_the_taxonomy_endpoint_lists_every_group_and_keeps_the_legacy_categories():
    out = main.constanza_interest_taxonomy()
    assert out["totals"]["segments"] == 90 and out["totals"]["with_rate"] + out["totals"]["no_public_rate"] == 90
    assert sum(len(g["segments"]) for g in out["groups"]) == 90
    assert {c["category"] for c in out["categories"]} <= {s["id"] for g in out["groups"] for s in g["segments"]}
    assert all(c["source"] and c["source_date"] for c in out["categories"])


def test_the_search_endpoint_ranks_segments_and_never_calls_a_model():
    out = main.constanza_interest_search(q="dental", limit=5)
    assert out["results"][0]["id"] == "health_dental" and out["results"][0]["supported"]
    assert out["results"][0]["rate_all"] == pytest.approx(0.667)
    assert main.constanza_interest_search(q="", limit=5)["results"] == []
    assert len(main.constanza_interest_search(q="sports", limit=3)["results"]) <= 3
    assert len(main.constanza_interest_search(q="sports", limit=10 ** 6)["results"]) <= 20       # the cap holds


def test_the_interest_endpoints_do_blocking_work_so_they_are_not_coroutines():
    import asyncio
    for fn in (main.constanza_interest_taxonomy, main.constanza_interest_search):
        assert not asyncio.iscoroutinefunction(fn), fn.__name__


# ---------------------------------------------------------------------------------------------------------------------
# The database seed (rolled back — the real rows are not touched)
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("no DATABASE_URL")
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except Exception:
        pytest.skip("no reachable database")
    from app.db import get_connection
    migrations.apply_pending()
    with get_connection() as c:
        yield c
        c.rollback()


@pytest.mark.real_db
def test_seeding_is_idempotent_and_matches_the_file(conn):
    first = tax.seed_to_db(conn)
    second = tax.seed_to_db(conn)
    assert first == second and first["segments"] == 90 and first["deactivated"] == 0
    row = conn.execute("SELECT s.label, i.rate_all, i.status FROM audience_segment s JOIN audience_incidence i USING (segment_id) "
                       "WHERE s.segment_id = 'health_dental'").fetchone()
    assert row["status"] == "sourced" and float(row["rate_all"]) == pytest.approx(0.667)


@pytest.mark.real_db
def test_a_row_an_admin_edited_survives_the_next_seed(conn):
    tax.seed_to_db(conn)
    conn.execute("UPDATE audience_segment SET label = 'Edited by an admin', origin = 'admin' WHERE segment_id = 'health_dental'")
    conn.execute("UPDATE audience_incidence SET rate_all = 0.5, origin = 'admin' WHERE segment_id = 'health_dental'")
    tax.seed_to_db(conn)
    row = conn.execute("SELECT s.label, i.rate_all FROM audience_segment s JOIN audience_incidence i USING (segment_id) "
                       "WHERE s.segment_id = 'health_dental'").fetchone()
    assert row["label"] == "Edited by an admin" and float(row["rate_all"]) == 0.5


@pytest.mark.real_db
def test_a_segment_dropped_from_the_file_is_deactivated_not_deleted(conn):
    tax.seed_to_db(conn)
    smaller = copy.deepcopy(BUNDLED)
    smaller["segments"] = [s for s in smaller["segments"] if s["id"] != "auto_tire_shoppers"]
    report = tax.seed_to_db(conn, smaller)
    assert report["deactivated"] == 1
    row = conn.execute("SELECT is_active FROM audience_segment WHERE segment_id = 'auto_tire_shoppers'").fetchone()
    assert row is not None and row["is_active"] is False


@pytest.mark.real_db
def test_the_database_refuses_a_rate_that_is_not_a_share(conn):
    tax.seed_to_db(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE audience_incidence SET rate_all = 1.5 WHERE segment_id = 'health_dental'")
