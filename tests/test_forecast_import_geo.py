"""Canonical geography for the forecast tables: states, districts, DMAs and the alias file that is the authority for
what a source's label means. These pin the data a reviewer approves in dma_aliases.csv."""
from __future__ import annotations

import csv

import pytest

from app.services.forecast_import import geo_reference as geo


def test_states_and_districts_are_complete():
    assert len(geo.STATES) == 51                                         # 50 states + DC
    assert len({c for c, _, _ in geo.STATES}) == 51 and len({f for _, _, f in geo.STATES}) == 51
    assert sum(geo.CD_COUNTS.values()) == 436                            # 435 voting districts + DC's delegate
    keys = geo.all_cd_keys()
    assert len(keys) == 436 == len(set(keys))
    assert {"AK-AL", "DC-AL", "CA-52", "TX-38", "NY-26", "WY-AL"} <= set(keys) and "CA-53" not in keys and "AK-01" not in keys


@pytest.mark.parametrize("label,key", [
    ("GA Congressional District 05", "GA-05"), ("CA Congressional District 52", "CA-52"),
    ("AK Congressional District (at Large)", "AK-AL"), ("DC Delegate District (at Large)", "DC-AL"),
    ("ak congressional district (at large)", "AK-AL"),
])
def test_district_labels_become_canonical_keys(label, key):
    assert geo.parse_cd_label(label) == key


@pytest.mark.parametrize("label", ["Unknown", "XX Congressional District 01", "GA District 05", "", "Congressional District 05"])
def test_a_label_that_is_not_a_district_is_rejected(label):
    assert geo.parse_cd_label(label) is None


def test_the_census_geoid_the_resolver_uses_maps_to_the_same_key():
    assert geo.cd_key_from_geoid("1305") == "GA-05"
    assert geo.cd_key_from_geoid("0200") == "AK-AL"                       # '00' = at large
    assert geo.cd_key_from_geoid("1198") == "DC-AL"                       # '98' = the delegate
    assert geo.cd_key_from_geoid("9905") is None and geo.cd_key_from_geoid("13") is None


@pytest.mark.parametrize("label,code", [("California", "CA"), ("Washington, D.C.", "DC"), ("district of columbia", "DC"), ("CA", "CA"), ("new york", "NY")])
def test_state_labels(label, code):
    assert geo.state_code(label) == code


def test_not_a_state():
    assert geo.state_code("Unknown") is None and geo.state_code("Puerto Rico") is None and geo.state_code("") is None


def test_every_canonical_dma_resolves_to_itself_and_keys_are_unique():
    table = geo.dma_table()
    assert len(table) == 210 and len({d.key for d in table}) == 210 and len({d.rank for d in table}) == 210
    for d in table:
        assert geo.dma_key_for_label(d.name) == d.key, d.name
    assert geo.dma_key_for_label("new  york") == "new-york"             # case and spacing do not matter


def test_every_alias_in_the_file_points_at_a_real_dma_and_resolves_to_it():
    by_name = geo.dma_by_name()
    rows = geo.alias_rows()
    assert len(rows) >= 200
    for r in rows:
        assert r["dma"] in by_name, r
        assert geo.dma_key_for_label(r["alias"]) == by_name[r["dma"]].key, r


def test_aliases_from_every_source_style_are_present():
    # the three spellings of the same market seen in the Madhive export, the Live Sports workbook and the Netflix file
    assert geo.dma_key_for_label("Tampa - Saint Petersburg (Sarasota)") == geo.dma_key_for_label("Tampa-St. Pete (Sarasota)")
    assert geo.dma_key_for_label("Sacramnto-Stkton-Modesto") == "sacramento-stockton-modesto"
    assert geo.dma_key_for_label("Washington DC (Hagrstwn)") == geo.dma_key_for_label("Washington, DC (Hagerstown)")
    assert geo.dma_key_for_label("Austin, TX") == "austin" and geo.dma_key_for_label("Columbus, GA") == geo.dma_key_for_label("Columbus, GA (Opelika, AL)")
    # the fuzzy matcher got these two wrong; the curated rows are what is trusted
    assert geo.dma_key_for_label("El Paso") == geo.dma_key_for_label("El Paso (Las Cruces)")
    assert geo.dma_key_for_label("Santa Barbara - Santa Maria - San Luis Obispo") == geo.dma_key_for_label("Santa Barbra-Sntmra-Slobs")


def test_similar_names_are_never_confused():
    assert geo.dma_key_for_label("Charlotte") != geo.dma_key_for_label("Charlottesville")
    assert geo.dma_key_for_label("Portland, OR") != geo.dma_key_for_label("Portland-Auburn")
    assert geo.dma_key_for_label("Springfield, MO") != geo.dma_key_for_label("Springfield-Holyoke")


def test_an_unknown_label_is_not_mapped_but_gets_suggestions():
    assert geo.dma_key_for_label("Nowhere") is None
    names = [n for n, _ in geo.suggest_dma("Sacramento - Stockton - Modesto, CA")]
    assert "Sacramento-Stockton-Modesto" in names
    assert len(geo.suggest_dma("zzz", limit=2)) == 2


def test_the_alias_file_has_no_conflicts(tmp_path):
    seen: dict[str, str] = {}
    with open(geo.ALIAS_CSV, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            norm = geo.normalize_label(r["alias"])
            assert seen.setdefault(norm, r["dma"]) == r["dma"], f"{r['alias']!r} is claimed by two DMAs"


@pytest.mark.parametrize("a,b,same", [
    ("AMC", "AMC+", False), ("Discovery", "Discovery+", False), ("Tubi", "  TUBI ", True), ("The Roku Channel", "The  Roku Channel", True),
    ("Solitaire: Classic Card Game", "Solitaire - Classic Card Game", False),
])
def test_publisher_identity_keeps_symbols(a, b, same):
    assert (geo.publisher_key(a) == geo.publisher_key(b)) is same


def test_normalize_label():
    assert geo.normalize_label("Fort Smith - Fayetteville (Rogers)") == "fort smith fayetteville rogers"
    assert geo.normalize_label("Champaign & Springfield") == "champaign and springfield"
    assert geo.normalize_label("Café") == "cafe"
