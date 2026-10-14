"""The manifest and the product seed: data a reviewer approves, checked for the mistakes a reviewer would miss."""
from __future__ import annotations

import copy
import json

import pytest

from app import catalog
from app.services import claim_rules
from app.services.forecast_import import manifest as mf
from app.services.text_utils import split_sentences, strip_variant_pricing

DATA = mf.DATA_DIR


def good():
    return json.loads(mf.MANIFEST_PATH.read_text(encoding="utf-8"))


def test_the_shipped_manifest_parses_and_every_source_file_exists():
    specs, source_dir = mf.load_manifest()
    assert len(specs) == 39 and len({s.series_key for s in specs}) == 39
    for s in specs:
        if s.reader != "bundled_platform_json":
            assert (source_dir / s.source).is_file(), s.source
    assert {s.channel for s in specs} == set(mf.CHANNELS)


def test_every_product_a_series_points_at_exists_in_the_catalog():
    names = {p.name for p in catalog.CATALOG}
    specs, _ = mf.load_manifest()
    missing = [(s.series_key, l.name) for s in specs for l in s.products if l.name not in names]
    assert missing == []


def test_a_product_has_one_default_series():
    specs, _ = mf.load_manifest()
    seen: dict[str, str] = {}
    for s in specs:
        for l in s.products:
            assert l.name not in seen, f"{l.name!r} is linked to both {seen[l.name]} and {s.series_key}"
            seen[l.name] = s.series_key


def test_every_new_product_has_a_series_and_the_legacy_ten_keep_theirs():
    from app.services.constanza_forecast_map import DEFAULT_PLATFORM_PRODUCTS
    specs, _ = mf.load_manifest()
    linked = {l.name: (s.series_key, l.relation) for s in specs for l in s.products}
    for rec in json.loads((DATA / "products.json").read_text())["products"]:
        assert rec["name"] in linked and linked[rec["name"]][1] == "primary", rec["name"]
    for name, platform in DEFAULT_PLATFORM_PRODUCTS.items():
        assert linked[name] == (platform, "primary"), name


def test_the_thirty_new_products_are_forty_dollar_placeholders():
    products = json.loads((DATA / "products.json").read_text())["products"]
    assert len(products) == 30 and len({p["name"] for p in products}) == 30
    live = [p for p in products if "Live Sports" in p["name"]]
    assert len(live) == 17
    for p in products:
        assert p["buying_model"] == "CPM" and p["base_rate"] == 40.0, p["name"]
        assert "PLACEHOLDER" in p["notes"] and p["proposal_description"].strip(), p["name"]
    for p in live:
        assert "weekly estimates by DMA for this window" in p["notes"], p["name"]
    assert {p["family"] for p in products} == {"Entravision Plus", "Display", "Online Video"}
    assert {p["name"] for p in products if p["family"] != "Entravision Plus"} == {"eDigital Display", "eDigital OLV"}


def test_each_zip_the_team_sent_became_a_product_named_after_it():
    names = {p["name"] for p in json.loads((DATA / "products.json").read_text())["products"]}
    for expected in ["Entravision+ Connect - (Default)", "Entravision+ Connect - Reach", "Entravision+ Connect - Family", "Entravision+ Connect - Premier",
                     "Entravision+ Hispanic Connect - (Default)", "Entravision+ Hispanic Connect - Reach", "Entravision+ Hispanic Connect - Family",
                     "Entravision+ Hispanic Connect - Premier", "Entravision+ Hispanic Connect - Spanish Only", "Entravision+ Pause Ads",
                     "eDigital OLV", "eDigital Display", "Entravision Plus - VIX Select"]:
        assert expected in names


def test_descriptions_make_no_statistical_claims_and_pass_the_proposal_text_validator():
    for p in json.loads((DATA / "products.json").read_text())["products"]:
        for seg in split_sentences(strip_variant_pricing(p["proposal_description"])):
            assert not claim_rules.is_unsupported_claim(seg), (p["name"], seg)
        assert "%" not in p["proposal_description"], p["name"]


def test_descriptions_reflect_each_products_real_publisher_mix():
    d = {p["name"]: p["proposal_description"] for p in json.loads((DATA / "products.json").read_text())["products"]}
    assert "Samsung TV Plus" in d["Entravision+ Connect - (Default)"] and "Hulu" in d["Entravision+ Connect - Premier"]
    assert "HappyKids" in d["Entravision+ Connect - Family"] and "Blippi" in d["Entravision+ Hispanic Connect - Family"]
    assert "Estrella TV" in d["Entravision+ Hispanic Connect - Spanish Only"] and "Spanish Content Only" in d["Entravision+ Hispanic Connect - Spanish Only"]
    assert d["Entravision+ Hispanic Connect - (Default)"].startswith("Hispanic Audience Targeting included.")
    assert "Tubi" in d["Entravision+ Pause Ads"] and "pause" in d["Entravision+ Pause Ads"].lower()
    assert "Yahoo" in d["eDigital Display"] and "Yahoo" in d["eDigital OLV"]
    assert "ViX" in d["Entravision Plus - VIX Select"]


def test_the_seed_products_are_part_of_the_code_catalog_in_the_right_places():
    names = [p.name for p in catalog.CATALOG]
    assert len(names) == len(set(names)) == 90                      # the rate card's 62 minus the 2 the seed replaces, plus 30 seed products
    assert names.index("Entravision+ Connect - (Default)") == names.index("Netflix - Spanish Content :30s Ads") + 1
    assert names.index("eDigital Display") == names.index("YouTube Ads - Non-Skippable") + 1          # first of the Display family
    assert names.index("eDigital OLV") == names.index("Search - SEM") + 1                                # first of Online Video
    assert "eDigital Network Display - Standard IAB" not in names and "Video - Pre-roll (OLV)" not in names   # replaced, not kept beside


def test_a_missing_or_broken_seed_file_leaves_the_catalog_as_before(monkeypatch, tmp_path):
    monkeypatch.setattr(catalog, "_FORECAST_SEED_PATH", tmp_path / "nope.json")
    assert catalog._load_forecast_seed_products() == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setattr(catalog, "_FORECAST_SEED_PATH", bad)
    assert catalog._load_forecast_seed_products() == []


# --------------------------------------------------------------------------------------------- manifest validation

def test_manifest_errors_are_reported_before_anything_runs():
    def broken(mutate, message):
        m = copy.deepcopy(good())
        mutate(m)
        with pytest.raises(mf.ManifestError, match=message):
            mf.parse_manifest(m)
    broken(lambda m: m.update(manifest_version=2), "manifest_version")
    broken(lambda m: m["series"][1].update(series_key=m["series"][0]["series_key"]), "used twice")
    broken(lambda m: m["series"][0].update(series_key="Bad Key"), "series_key")
    broken(lambda m: m["series"][0].update(channel="radio"), "channel")
    broken(lambda m: m["series"][0].update(reader="magic"), "reader")
    broken(lambda m: m["series"][0].update(avails_unit="daily"), "avails_unit")
    broken(lambda m: m["series"][0].update(data_basis="guessed"), "data_basis")
    broken(lambda m: m["series"][0].update(band_low=1.5), "planning band")
    broken(lambda m: m["series"][0].update(exported_on="13/10/2026"), "ISO date")
    broken(lambda m: m["series"][0].pop("source"), "source file is required")
    broken(lambda m: m["series"][0].update(products=[{"name": "X", "relation": "friend"}]), "relation")
    broken(lambda m: m["series"][0].update(products=[{"relation": "primary"}]), "no name")
    broken(lambda m: next(s for s in m["series"] if s["reader"] == "live_sports_xlsx").update(period={"label": "x", "start": "2026-05-01", "end": "2026-04-01"}), "starts after")
    broken(lambda m: next(s for s in m["series"] if s["series_key"] == "netflix-15s")["params"].update(extrapolate_from="nope"), "not in the manifest")


def test_a_missing_manifest_file_is_a_clear_error(tmp_path):
    with pytest.raises(mf.ManifestError, match="cannot read manifest"):
        mf.load_manifest(tmp_path / "missing.json")


# --------------------------------------------------------------------------------------------- eDigital Display / OLV replace the older products

OLD_DISPLAY, OLD_OLV = "eDigital Network Display - Standard IAB", "Video - Pre-roll (OLV)"


def test_the_new_edigital_products_say_which_older_product_they_replace():
    assert catalog.forecast_seed_replacements() == {"eDigital Display": OLD_DISPLAY, "eDigital OLV": OLD_OLV}
    # the field is metadata about the seed: it is not a Product field, so the products still load
    assert {p.name for p, _ in catalog._load_forecast_seed_products()} >= {"eDigital Display", "eDigital OLV"}


def test_a_missing_seed_file_replaces_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(catalog, "_FORECAST_SEED_PATH", tmp_path / "nope.json")
    assert catalog.forecast_seed_replacements() == {}


def test_the_older_products_are_gone_from_the_code_catalog_and_the_series_links():
    assert OLD_DISPLAY not in {p.name for p in catalog.CATALOG} and OLD_OLV not in {p.name for p in catalog.CATALOG}
    specs, _ = mf.load_manifest()
    assert not [(s.series_key, l.name) for s in specs for l in s.products if l.name in (OLD_DISPLAY, OLD_OLV)]
    linked = {l.name: l.relation for s in specs if s.series_key in ("edigital-display", "edigital-olv") for l in s.products}
    assert linked == {"eDigital Display": "primary", "eDigital OLV": "primary"}


def test_a_pasted_request_for_display_or_pre_roll_names_the_new_products():
    from app.services.notion_parser import PRODUCT_ALIASES
    for phrase in ("display", "edigital display", "edigital network display", "display banner", "programmatic display"):
        assert PRODUCT_ALIASES[phrase] == "eDigital Display", phrase
    for phrase in ("olv", "edigital olv", "online video", "pre-roll", "preroll", "video pre-roll"):
        assert PRODUCT_ALIASES[phrase] == "eDigital OLV", phrase


def test_the_recommender_suggests_the_new_products():
    from app.services.recommender import GOAL_PRIORITIES
    suggested = {name for names in GOAL_PRIORITIES.values() for name in names}
    assert {"eDigital Display", "eDigital OLV"} <= suggested and not ({OLD_DISPLAY, OLD_OLV} & suggested)


def test_the_restrictions_sheet_entries_for_display_and_olv_map_to_the_new_products_alone():
    from app.services import restrictions as rs
    assert rs.match_catalog_products("eDigital Display") == ["eDigital Display"]
    assert rs.match_catalog_products("eDigital OLV") == ["eDigital OLV"]

