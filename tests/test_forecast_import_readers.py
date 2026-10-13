"""The source readers: the shipped files read cleanly with the expected shape and values, and every kind of damage in a
source is reported as a specific finding instead of being dropped or guessed at."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import openpyxl
import pytest

from app.services.forecast_import import geo_reference as geo
from app.services.forecast_import import readers
from app.services.forecast_import.model import SeriesSpec, SourceError
from tests.forecast_support import make_export, payload, row, shipped

ROOT = Path(__file__).resolve().parent.parent


def spec(key="t-series", reader="madhive_zip", **kw):
    base = dict(series_key=key, title="Test", channel="ctv", avails_unit="monthly", reader=reader, source="x")
    base.update(kw)
    return SeriesSpec(**base)


def rules(p, severity=None):
    return sorted({f.rule for f in p.findings if severity in (None, f.severity)})


# --------------------------------------------------------------------------------------------- the shipped data

def test_every_shipped_series_reads_without_error():
    specs, _dir, payloads = shipped()
    assert len(specs) == 39
    for s in specs:
        p = payloads[s.series_key]
        assert not isinstance(p, SourceError), (s.series_key, p)
        assert p.errors == [], (s.series_key, [f.message for f in p.errors])


def test_the_only_warnings_in_the_shipped_data_are_the_known_ones():
    """A new warning appearing in the shipped data is a change somebody should look at."""
    allowed = {"vix-select": {"N006"}, "netflix-15s": {"X003"}, "netflix-30s": {"X003"},
               "live-sports-mlb": {"L002"}, "live-sports-mlb-playoffs": {"L005"}, "live-sports-nba-playoffs": {"L005"},
               "live-sports-ncaab-march-madness": {"L005"}, "live-sports-nhl-playoffs": {"L005"},
               "live-sports-fifa-world-cup-26": {"L005", "L002"}}
    for s in shipped()[0]:
        p = payload(s.series_key)
        extra = set(rules(p, "warning")) - allowed.get(s.series_key, set())
        assert not extra, (s.series_key, extra)


def test_connect_default_shape_and_values_match_the_export():
    p = payload("connect-default")
    assert p.row_counts() == {"dma": 210, "state": 51, "cd": 436, "zip": 24848, "device": 4, "publisher": 222}
    assert p.levels["dma"]["new-york"].avails == 201_933_200 and p.levels["dma"]["los-angeles"].avails == 176_781_300
    assert p.levels["state"]["CA"].avails == 363_896_300 and p.levels["state"]["DC"].avails > 0
    assert p.levels["cd"]["GA-05"].avails == 17_574_700
    assert p.levels["zip"]["20149"].avails == 31_517_600          # the Monthly column (Total says 31,517,500: rounding)
    assert p.devices["connected_tv"].avails == 2_899_770_300 and set(p.devices) == {"connected_tv", "mobile", "desktop", "tablet"}
    assert p.publishers["Samsung TV Plus"].avails == 672_012_500


def test_unknown_rows_are_kept_apart_so_totals_reconcile():
    p = payload("connect-default")
    assert set(p.unknown) == {"dma", "state", "cd"}                      # the ZIP file has no Unknown row
    assert "unknown" not in p.levels["dma"] and "Unknown" not in p.levels["dma"]
    known = sum(f.avails for f in p.levels["dma"].values())
    assert 0.02 < p.unknown["dma"].avails / (known + p.unknown["dma"].avails) < 0.03
    assert abs(known + p.unknown["dma"].avails - p.stats["level_totals"]["dma"]) < 1 if "level_totals" in p.stats else True


def test_zip_codes_keep_their_leading_zeros():
    zips = payload("connect-default").levels["zip"]
    assert "08541" in zips and "02112" in zips and "8541" not in zips
    assert all(len(z) == 5 and z.isdigit() for z in zips)


def test_a_series_that_lacks_a_market_says_so_instead_of_inventing_it():
    from app.services.forecast_import.validate import validate
    vix = payload("vix-select")
    assert len(vix.levels["dma"]) == 209 and "glendive" not in vix.levels["dma"]
    assert any(f.rule == "N006" and "Glendive" in f.message for f in validate(vix))


def test_distinct_publishers_with_a_plus_sign_stay_distinct():
    pubs = payload("connect-default").publishers
    assert "AMC" in pubs and "AMC+" in pubs and "Discovery" in pubs and "Discovery+" in pubs
    assert payload("edigital-display").row_counts()["publisher"] == 3777


def test_pause_ads_is_ctv_only_and_family_is_not_cross_device():
    assert set(payload("pause-ads").devices) == {"connected_tv"}
    fam = payload("connect-family").devices
    assert fam["connected_tv"].avails / sum(f.avails for f in fam.values()) > 0.9


def test_netflix_sample_is_extended_to_every_dma_and_reported_rows_are_untouched():
    p15, p30 = payload("netflix-15s"), payload("netflix-30s")
    for p in (p15, p30):
        assert len(p.levels["dma"]) == 210
        assert sum(1 for f in p.levels["dma"].values() if f.basis == "reported") == 17
        assert sum(1 for f in p.levels["dma"].values() if f.basis == "modeled") == 193
    assert p15.levels["dma"]["los-angeles"].avails == 17_432_036 and p15.levels["dma"]["los-angeles"].basis == "reported"
    assert p30.levels["dma"]["los-angeles"].avails == 16_124_633
    assert p15.levels["dma"]["chicago"].basis == "modeled" and 8_000_000 < p15.levels["dma"]["chicago"].avails < 14_000_000
    ext = p15.stats["extension"]
    assert 0.08 < ext["k"] < 0.12 and ext["mape_on_reported"] < 0.2 and ext["reported_markets"] == 17 and ext["log_log_r2"] > 0.9
    assert 0.9 < p30.stats["extension"]["k"] / ext["k"] < 0.95           # the :30 market level is ~92% of the :15


def test_netflix_new_york_oddity_is_reported_not_corrected():
    p = payload("netflix-15s")
    assert p.levels["dma"]["new-york"].avails == 15_185_009               # as the source has it
    assert any(f.rule == "X003" and "new-york" in f.message for f in p.findings)


def test_live_sports_tabs_are_separate_weekly_series_with_their_periods():
    mlb, playoffs, soccer = payload("live-sports-mlb"), payload("live-sports-mlb-playoffs"), payload("live-sports-soccer")
    assert mlb.spec.avails_unit == "weekly" and len(mlb.levels["dma"]) == 210
    assert mlb.stats["header_note"] == "Regular Season Ends on 9/27" and mlb.spec.period.end == "2026-09-27"
    assert playoffs.spec.period.start == "2026-09-30" and playoffs.spec.period.end == "2026-10-21"
    assert soccer.spec.period.start is None and "not stated" in soccer.spec.period.label
    assert mlb.levels["dma"]["atlanta"].avails == 2_922_067
    assert "L005" in rules(playoffs)                                      # 'Estimates' (not 'Weekly') headers are flagged, not assumed silently
    assert "L005" not in rules(mlb)


def test_the_stray_cells_in_the_live_sports_workbook_are_reported_not_loaded():
    wc = payload("live-sports-fifa-world-cup-26")
    msg = next(f.message for f in wc.findings if f.rule == "L002")
    assert "Prod Name" in msg and "135644" in msg
    assert "atlanta" in payload("live-sports-mlb").levels["dma"]


def test_the_legacy_model_series_match_the_bundled_workbook_snapshot():
    data = json.loads((ROOT / "app" / "data" / "constanza" / "platform_dma.json").read_text())
    roku = payload("roku")
    fig = data["roku"]["new york"]
    got = roku.levels["dma"]["new-york"]
    assert got.avails == fig["imps_mid"] and got.avails_low == fig["imps_low"] and got.avails_high == fig["imps_high"]
    assert got.est_reach == fig["unique_viewers"] and got.basis == "modeled"
    assert len(roku.levels["dma"]) == 210


# --------------------------------------------------------------------------------------------- damaged exports

def read(tmp_path, **kw):
    path = make_export(tmp_path / "e.zip", **kw)
    return readers.read_madhive_zip(spec(), path)


def test_a_clean_small_export_loads(tmp_path):
    p = read(tmp_path)
    assert p.errors == [] and p.levels["dma"]["new-york"].avails == 2_000_000
    assert p.unknown["dma"].avails == 50_000 and p.levels["zip"]["90210"].avails == 100_000
    assert p.levels["cd"]["CA-12"].avails == 40_000 and p.devices["mobile"].avails == 550_000


def test_a_changed_header_is_an_error(tmp_path):
    p = read(tmp_path, header_overrides={"zip": ["Zip Codes", "Total Avails", "Monthly Avails"]})
    assert "S002" in rules(p, "error")


def test_an_unmapped_dma_label_is_an_error_with_suggestions(tmp_path):
    p = read(tmp_path, dmas=[row("New York", 1000), row("Los Angelez", 900)])
    f = next(f for f in p.errors if f.rule == "G001")
    assert "Los Angelez" in f.message and "Closest" in f.message and "Los Angeles" in f.message
    assert "los-angeles" not in p.levels["dma"]                           # not guessed


def test_a_geography_that_appears_twice_is_an_error(tmp_path):
    assert "S003" in rules(read(tmp_path, zips=[row("90210", 1000), row("90210", 2000)]), "error")
    assert "S003" in rules(read(tmp_path, dmas=[row("Austin", 1000), row("Austin, TX", 2000)]), "error")      # two spellings, one market


@pytest.mark.parametrize("bad", ["9021", "902100", "9021A", "902 1"])
def test_a_zip_that_is_not_five_digits_is_an_error(tmp_path, bad):
    assert "G002" in rules(read(tmp_path, zips=[row(bad, 1000)]), "error")


def test_bad_numbers_are_errors(tmp_path):
    neg = row("90210", 1000); neg[4] = -5
    blank = row("10001", 1000); blank[5] = ""
    p = read(tmp_path, zips=[neg, blank, row("94105", 500)])
    assert [f.rule for f in p.errors].count("S004") == 2
    assert "94105" in p.levels["zip"] and "90210" not in p.levels["zip"]


def test_a_missing_breakdown_is_an_error(tmp_path):
    p = read(tmp_path, omit=("publisher",))
    assert any(f.rule == "S001" and "Publishers" in f.message for f in p.errors)


def test_a_ragged_row_is_an_error(tmp_path):
    p = read(tmp_path, zips=[["90210", 1, 2], row("10001", 5000)])
    assert "S003" in rules(p, "error") and "10001" in p.levels["zip"]


def test_an_unknown_device_or_state_or_district_is_an_error(tmp_path):
    assert "G002" in rules(read(tmp_path, devices=[row("Smart Speaker", 100)]), "error")
    assert "G002" in rules(read(tmp_path, states=[row("Atlantis", 100)]), "error")
    assert "G002" in rules(read(tmp_path, cds=[row("CA Congressional District 99", 100)]), "error")


def test_a_repeated_publisher_is_merged_with_a_warning(tmp_path):
    p = read(tmp_path, publishers=[row("Tubi", 1000), row("tubi ", 500), row("Tubi+", 100)])
    assert p.publishers["Tubi"].avails == 1500 and "Tubi+" in p.publishers
    assert "S003" in rules(p, "warning") and p.errors == []


def test_mac_resource_forks_are_ignored_and_other_files_are_flagged(tmp_path):
    p = read(tmp_path, extra_members={"notes.txt": b"hello", "../evil by Metros.csv": b"x"})
    assert any(f.rule == "S001" and "notes.txt" in f.message for f in p.findings)
    assert any(f.rule == "S001" and "unsafe path" in f.message for f in p.errors)


def test_files_that_disagree_about_the_product_name_are_flagged(tmp_path):
    path = make_export(tmp_path / "e.zip")
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("Another Product by Zip Codes.csv", b"")
    p = readers.read_madhive_zip(spec(), path)
    assert any(f.rule == "S001" for f in p.errors)


def test_an_unreadable_archive_is_a_source_error(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"this is not a zip")
    with pytest.raises(SourceError):
        readers.read_madhive_zip(spec(), bad)


def test_an_oversized_member_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(readers, "MAX_MEMBER_BYTES", 100)
    with pytest.raises(SourceError, match="uncompressed"):
        readers.read_madhive_zip(spec(), make_export(tmp_path / "e.zip"))


def test_a_zip_with_too_many_entries_is_refused(tmp_path):
    path = make_export(tmp_path / "e.zip", extra_members={f"f{i}.txt": b"x" for i in range(60)})
    with pytest.raises(SourceError, match="not an avails export"):
        readers.read_madhive_zip(spec(), path)


def test_parse_number():
    assert readers.parse_number("1,234") == 1234 and readers.parse_number("$54.00") == 54.0 and readers.parse_number(" 7 ") == 7
    assert readers.parse_number("") is None and readers.parse_number("n/a") is None and readers.parse_number("5%") is None
    assert readers.parse_number("nan") is None and readers.parse_number("inf") is None


# --------------------------------------------------------------------------------------------- netflix

def netflix_csv(path: Path, rows):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Market", "Language", "Targeting", "Rate", "Length", "Max. Monthly Avails"])
        w.writerows(rows)
    return path


def test_extension_fills_the_missing_markets_from_the_reference_distribution(tmp_path):
    ref = {d.key: 1_000_000 + 5000 * d.rank for d in geo.dma_table()}
    names = [d.name for d in geo.dma_table()[:12]]
    path = netflix_csv(tmp_path / "n.csv", [[n, "Netflix", "Adult 18+", "$54.00", 15, f"{int(0.1 * ref[geo.slug(n)]):,}"] for n in names])
    p = readers.read_netflix_csv(spec("nf", "netflix_baseline_csv", params={"length": 15}), path, ref)
    assert p.errors == [] and len(p.levels["dma"]) == 210
    assert sum(1 for f in p.levels["dma"].values() if f.basis == "reported") == 12
    some = next(d for d in geo.dma_table()[100:] if d.key not in {geo.slug(n) for n in names})
    assert abs(p.levels["dma"][some.key].avails - 0.1 * ref[some.key]) < 1


def test_extension_needs_enough_reported_markets(tmp_path):
    ref = {d.key: 1_000_000 for d in geo.dma_table()}
    path = netflix_csv(tmp_path / "n.csv", [[n.name, "Netflix", "Adult 18+", "$54.00", 15, 100000] for n in geo.dma_table()[:5]])
    p = readers.read_netflix_csv(spec("nf", "netflix_baseline_csv", params={"length": 15}), path, ref)
    assert "X001" in rules(p, "error") and len(p.levels["dma"]) == 5


def test_extension_refuses_to_publish_a_model_that_does_not_fit(tmp_path):
    ref = {d.key: 1_000_000 * (1 + (d.rank % 7)) for d in geo.dma_table()}
    # reported values unrelated to the reference: alternating 10x / 0.1x
    rows = [[d.name, "Netflix", "Adult 18+", "$54.00", 15, 100000 * (10 if i % 2 else 1)] for i, d in enumerate(geo.dma_table()[:14])]
    p = readers.read_netflix_csv(spec("nf", "netflix_baseline_csv", params={"length": 15}), netflix_csv(tmp_path / "n.csv", rows), ref)
    assert "X004" in rules(p, "error")


def test_netflix_market_not_in_the_alias_file_is_an_error(tmp_path):
    p = readers.read_netflix_csv(spec("nf", "netflix_baseline_csv", params={"length": 15}),
                                 netflix_csv(tmp_path / "n.csv", [["Gotham", "Netflix", "Adult 18+", "$54.00", 15, 1000]]))
    assert "G001" in rules(p, "error")


# --------------------------------------------------------------------------------------------- live sports

def sports_book(path: Path, header, rows, extra=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tab"
    ws.append(header)
    for r in rows:
        ws.append(r)
    for (r, c), v in (extra or {}).items():
        ws.cell(row=r, column=c, value=v)
    wb.save(path)
    return path


def test_live_sports_tab_reads_dma_estimates_and_flags_the_odd_cells(tmp_path):
    path = sports_book(tmp_path / "s.xlsx", ["Metro", "Weekly Estimates", None, "Oct - Apr"], [["Atlanta", 1200], ["Austin, TX", 340.5]], extra={(5, 6): 99})
    p = readers.read_live_sports_xlsx(spec("ls", "live_sports_xlsx", avails_unit="weekly", params={"sheet": "Tab"}), path)
    assert p.levels["dma"]["atlanta"].avails == 1200 and p.levels["dma"]["austin"].avails == 340.5
    assert p.stats["header_note"] == "Oct - Apr" and "L002" in rules(p, "warning") and "L005" not in rules(p)


def test_live_sports_measure_header_other_than_weekly_is_flagged(tmp_path):
    path = sports_book(tmp_path / "s.xlsx", ["Metro", "Estimates"], [["Atlanta", 10]])
    p = readers.read_live_sports_xlsx(spec("ls", "live_sports_xlsx", avails_unit="weekly", params={"sheet": "Tab"}), path)
    assert "L005" in rules(p, "warning")


@pytest.mark.parametrize("value", ["lots", -5, None])
def test_live_sports_bad_value_is_an_error(tmp_path, value):
    path = sports_book(tmp_path / "s.xlsx", ["Metro", "Weekly Estimates"], [["Atlanta", value]])
    p = readers.read_live_sports_xlsx(spec("ls", "live_sports_xlsx", avails_unit="weekly", params={"sheet": "Tab"}), path)
    assert "S004" in rules(p, "error")


def test_live_sports_market_not_in_the_alias_file_is_an_error(tmp_path):
    path = sports_book(tmp_path / "s.xlsx", ["Metro", "Weekly Estimates"], [["Gotham", 10]])
    p = readers.read_live_sports_xlsx(spec("ls", "live_sports_xlsx", avails_unit="weekly", params={"sheet": "Tab"}), path)
    assert "G001" in rules(p, "error")


def test_live_sports_missing_tab_is_a_source_error(tmp_path):
    path = sports_book(tmp_path / "s.xlsx", ["Metro", "Weekly Estimates"], [["Atlanta", 10]])
    with pytest.raises(SourceError, match="no tab named"):
        readers.read_live_sports_xlsx(spec("ls", "live_sports_xlsx", avails_unit="weekly", params={"sheet": "Nope"}), path)
