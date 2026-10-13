"""
How the freeform product list and the YouTube split reach the rest of the app: the paste parser, the recommender,
reopening a saved proposal, and the blurb rules.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import pytest

from app import catalog
from app.services.notion_parser import _match_product

NAMES = ["YouTube Ads - Skippable", "YouTube Ads - Non-Skippable", "Search - SEM"]


# --- the paste parser ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("YouTube Ads", "YouTube Ads - Skippable"),
    ("YouTube Ads - non skippable", "YouTube Ads - Non-Skippable"),
    ("YouTube Ads Non-Skippable", "YouTube Ads - Non-Skippable"),
    ("bumper ads on YouTube", "YouTube Ads - Non-Skippable"),
    ("youtube bumper", "YouTube Ads - Non-Skippable"),
    ("YouTube unskippable", "YouTube Ads - Non-Skippable"),
    ("youtube skippable", "YouTube Ads - Skippable"),
    ("TrueView", "YouTube Ads - Skippable"),
])
def test_youtube_phrasings_land_on_the_right_half(text, expected):
    assert _match_product(text, NAMES) == expected


def test_an_alias_whose_target_was_renamed_follows_the_rename():
    renamed = ["YT Skippable", "YouTube Ads - Non-Skippable"]
    assert _match_product("YouTube Ads", renamed, {"YouTube Ads - Skippable": "YT Skippable"}) == "YT Skippable"


# --- the recommender ---------------------------------------------------------------------------------------------------------

def _request(products=()):
    from app.services.notion_parser import ProposalRequest
    return ProposalRequest(products_selected=list(products), campaign_goal="awareness")


def test_a_deleted_product_is_never_recommended_and_a_renamed_one_appears_once_under_its_new_name(monkeypatch):
    from app.services import recommender
    rows = [{"id": i, "is_deleted": deleted, "product": p} for i, (p, deleted) in enumerate([
        (catalog.by_name("Search - SEM"), False), (catalog.by_name("Spotify"), True),
    ])]
    renamed = catalog.replace(catalog.by_name("Search - SEM"), name="Paid Search")
    rows[0]["product"] = renamed
    monkeypatch.setattr(catalog, "load_product_rows", lambda: rows)
    monkeypatch.setattr(catalog, "_db_aliases", lambda: {"Search - SEM": "Paid Search"})
    items = recommender.recommend_line_items(_request(["Search - SEM", "Paid Search", "Spotify"]), 20000)
    names = [li.product_name for li in items]
    assert names.count("Paid Search") == 1 and "Spotify" not in names and "Search - SEM" not in names


# --- reopening a saved proposal --------------------------------------------------------------------------------------------------

def test_reopening_follows_a_renamed_or_split_product_everywhere_a_name_is_stored():
    import app.main as m
    state = {
        "request": {"products_selected": ["YouTube Ads", "YouTube Ads - Skippable", "Search - SEM"]},
        "line_items": [{"product_name": "YouTube Ads", "monthly_budget": 500}],
        "avails_data": {"YouTube Ads": {"max_impressions": 1}, "Search - SEM": {"max_impressions": 2}},
        "addons": [{"product_name": "YouTube Ads", "amount": 100}],
        "tiers": [{"label": "A", "line_items": [{"product_name": "YouTube Ads"}], "avails_data": {"YouTube Ads": {"max_impressions": 9}}}],
        "roadblocks": {"product_roadblocks": [{"product_name": "YouTube Ads", "risk_level": "low"}]},
        "enrichment": {"product_blurbs": [{"product_name": "YouTube Ads", "blurb": "x"}]},
    }
    fixed = m._with_current_product_names(state)
    skippable = "YouTube Ads - Skippable"
    assert fixed["request"]["products_selected"] == [skippable, "Search - SEM"]           # renamed duplicates collapse
    assert fixed["line_items"][0]["product_name"] == skippable
    assert set(fixed["avails_data"]) == {skippable, "Search - SEM"}
    assert fixed["addons"][0]["product_name"] == skippable
    assert fixed["tiers"][0]["line_items"][0]["product_name"] == skippable and set(fixed["tiers"][0]["avails_data"]) == {skippable}
    assert fixed["roadblocks"]["product_roadblocks"][0]["product_name"] == skippable
    assert fixed["enrichment"]["product_blurbs"][0]["product_name"] == skippable


def test_an_entry_already_under_the_current_name_wins_over_a_renamed_duplicate():
    import app.main as m
    state = {"avails_data": {"YouTube Ads": {"max_impressions": 1}, "YouTube Ads - Skippable": {"max_impressions": 7}}}
    assert m._with_current_product_names(state)["avails_data"] == {"YouTube Ads - Skippable": {"max_impressions": 7}}


# --- blurbs ----------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("sentence,name,kept", [
    ("Skippable in-stream ads reach viewers.", "YouTube Ads - Skippable", True),
    ("Skippable in-stream ads reach viewers.", "YouTube Ads - Non-Skippable", False),
    ("Non-skippable ads reach viewers.", "YouTube Ads - Non-Skippable", True),
    ("Non-skippable ads reach viewers.", "YouTube Ads - Skippable", False),
    ("Skippable and non-skippable formats are available.", "YouTube Ads - Skippable", False),
    ("Skippable and non-skippable formats are available.", "YouTube Ads - Non-Skippable", False),
    ("Skippable ads reach viewers.", "Search - SEM", False),
    ("Search ads reach buyers.", "Search - SEM", True),
])
def test_a_blurb_may_name_only_the_format_its_own_product_is(sentence, name, kept):
    from app.services.ai_enricher import _blurb_sentence_ok
    assert _blurb_sentence_ok(sentence, name) is kept
