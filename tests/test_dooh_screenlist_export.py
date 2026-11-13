"""The export with an uploaded DOOH screen list: the DOOH Screenlist table holds every screen (typed like the sample row, helper formulas
with cached values), and the DOOH Summary pivot is pre-populated - its cache holds the real records and its rendered cells show the grouped
Venue Type > Network lines, the subtotals and the grand total - while it still refreshes on open.

There is no Excel here, so the workbook is checked the way a strict reader would: every part well formed, the zip sound, every relationship and
count consistent (cache record count, shared-item counts and indexes, table ref, pivot location), the pivot's own row items decoded back into
labels and compared with the cells written under it, and every number compared across the table, the cache, the pivot and the totals the wizard
shows. What cannot be checked without Excel is said in the build report."""
from __future__ import annotations

import math
import os
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import openpyxl
import pytest
from openpyxl import Workbook

from app import excel_template as et
from app.services import dooh_pivot as dp
from app.services import dooh_screenlist as ds
from app.services.notion_parser import ProposalRequest
from app.services.proposal_generator import LineItem, generate_proposal
from tests import dooh_support as sup

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
COL = {name: i for i, name in enumerate(dp.DOOH_ALL_COLUMNS)}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr("app.market_config.load_market_config", lambda: {})


def _req():
    return ProposalRequest(client_name="Acme", request_type="New Business", start_date="2026-10-01", end_date="2026-12-31", total_months=3)


def export(tmp_path, rows, name="out.xlsx") -> Path:
    """The workbook through the real generator, as /api/generate builds it."""
    out = tmp_path / name
    summary = generate_proposal(_req(), [LineItem(product_name="Digital Out of Home", monthly_budget=2000.0, months=3, id="d")], out, dooh_screenlist=rows)
    assert not summary["warnings"], summary["warnings"]
    return out


def parts_of(path) -> dict:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def sheet_part(parts: dict, sheet: str) -> str:
    return dp._sheet_parts(parts)[sheet]


def tricky_rows() -> list[list]:
    """Three groups of screens plus what a real export holds that breaks naive writers: a venue spelled in another case, empty networks and
    venue types, markup characters, quotes, line breaks, tabs, accents, a very long text, an empty id, empty optional cells."""
    rows = sup.screens(36, seed=11)
    ix = {c: i for i, c in enumerate(dp.DOOH_SCREENLIST_COLUMNS)}
    rows[1][ix["venue_type"]] = rows[1][ix["venue_type"]].lower()                       # the same venue type in another case: one pivot item
    rows[2][ix["publisher"]] = None                                                     # no network
    rows[3][ix["venue_type"]] = None                                                    # no venue type
    rows[4][ix["address"]] = 'Suite "5" <b>&amp;</b>\nSecond line\tTabbed, Cañón City 日本'
    rows[5][ix["asset_image_url"]] = "https://static.example.com/" + "x" * 400          # longer than 255
    rows[6][ix["id"]] = None                                                            # not counted by Number of Screens
    rows[7][ix["supports_video"]] = None
    rows[8][ix["latitude"]] = None
    rows[9][ix["venue_name"]] = "  padded name  ".strip()
    rows[10][ix["postal_code"]] = "00501"                                               # leading zeros stay
    rows[11][ix["publisher"]] = "Ünïcode Network & Co <x>"
    return rows


# --- independent expectations ----------------------------------------------------------------------------------------------

def expected_pivot(rows):
    """[(kind, venue label, network label, count, avails, spend)] as a pivot over these rows must show: A-Z (empty last, case-insensitive),
    a subtotal line after each venue type, then the grand total. Computed here from the rows and the per-row math only."""
    ix = {c: i for i, c in enumerate(dp.DOOH_SCREENLIST_COLUMNS)}
    key = lambda v: None if v in (None, "") else v.casefold()
    label = {}
    groups = defaultdict(lambda: defaultdict(lambda: [0, [], []]))
    for r in rows:
        v, n = key(r[ix["venue_type"]]), key(r[ix["publisher"]])
        label.setdefault(("v", v), r[ix["venue_type"]] or "(blank)")
        label.setdefault(("n", n), r[ix["publisher"]] or "(blank)")
        g = groups[v][n]
        g[0] += 0 if r[ix["id"]] in (None, "") else 1
        g[1].append(dp.max_avails_for(r[ix["four_week_impressions"]]))
        g[2].append(dp.max_spend_for(r[ix["four_week_impressions"]], r[ix["cpm"]]))
    order = lambda k: (k is None, (k or ""))
    out, total = [], [0, [], []]
    for v in sorted(groups, key=order):
        sub = [0, [], []]
        for n in sorted(groups[v], key=order):
            c, a, s = groups[v][n]
            out.append(("data", label[("v", v)], label[("n", n)], c, math.fsum(a), math.fsum(s)))
            sub[0] += c; sub[1] += a; sub[2] += s
        out.append(("subtotal", f"{label[('v', v)]} Total", None, sub[0], math.fsum(sub[1]), math.fsum(sub[2])))
        total[0] += sub[0]; total[1] += sub[1]; total[2] += sub[2]
    out.append(("grand", "Grand Total", None, total[0], math.fsum(total[1]), math.fsum(total[2])))
    return out


def rendered_pivot(ws, last_row):
    """What the Summary sheet shows under the pivot's header: [(kind, B, C, D, E, F)] to the grand total."""
    lines = []
    for r in range(dp.PIVOT_FIRST_ROW + 1, last_row + 1):
        b, c, d, e, f = (ws.cell(row=r, column=col).value for col in range(2, 7))
        kind = "grand" if b == "Grand Total" else "subtotal" if (b or "").endswith(" Total") and c is None else "data"
        lines.append((kind, b, c, d, e, f))
    return lines


def decode_row_items(pivot, cache):
    """The pivot definition's row items turned back into labels, the way Excel reads them: each item is a POSITION in its pivot field's item
    list, whose <item x> is an index into the cache field's shared items."""
    fields = pivot.findall("m:pivotFields/m:pivotField", NS)
    cache_fields = cache.findall("m:cacheFields/m:cacheField", NS)
    row_fields = [int(f.get("x")) for f in pivot.findall("m:rowFields/m:field", NS)]

    def label(field, position):
        item = fields[field].findall("m:items/m:item", NS)[position]
        assert item.get("t") is None
        shared = cache_fields[field].find("m:sharedItems", NS)[int(item.get("x"))]
        return "(blank)" if shared.tag == M + "m" else shared.get("v")

    out, current = [], []
    for i in pivot.findall("m:rowItems/m:i", NS):
        kind, repeated = i.get("t") or "data", int(i.get("r") or 0)
        positions = [int(x.get("v") or 0) for x in i.findall("m:x", NS)]
        if kind == "data":
            current = current[:repeated] + [label(row_fields[repeated + k], p) for k, p in enumerate(positions)]
            assert len(current) == len(row_fields)
            out.append(("data", tuple(current)))
        elif kind == "default":
            out.append(("subtotal", (label(row_fields[0], positions[0]) + " Total",)))
        else:
            out.append(("grand", ("Grand Total",)))
    return out


# --- the checks every workbook with a list must pass --------------------------------------------------------------------------

def check_workbook(path: Path, rows: list[list], totals=None):
    n = len(rows)
    last_col = dp._col_letter(len(dp.DOOH_ALL_COLUMNS))
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        names = z.namelist()
    assert len(names) == len(set(names))                                       # no part written twice
    parts = parts_of(path)
    for name, data in parts.items():
        if name.endswith((".xml", ".rels")):
            ET.fromstring(data)                                                # well formed
    # every relationship points at a part that exists; every part of the pivot is registered
    for name, data in parts.items():
        if name.endswith(".rels"):
            base = os.path.dirname(os.path.dirname(name))
            for rel in ET.fromstring(data):
                target = rel.get("Target")
                if rel.get("TargetMode") == "External":                          # (the FAQ tab's hyperlinks)
                    continue
                resolved = target.lstrip("/") if target.startswith("/") else os.path.normpath(os.path.join(base, target)).replace("\\", "/")
                assert resolved in parts, (name, target)
    overrides = {o.get("PartName") for o in ET.fromstring(parts["[Content_Types].xml"]) if o.tag.endswith("Override")}
    for part in ("pivotCache/pivotCacheDefinition1.xml", "pivotCache/pivotCacheRecords1.xml", "pivotTables/pivotTable1.xml"):
        assert f"/xl/{part}" in overrides

    # --- the Screenlist: header + every screen, typed like the sample row
    wb = openpyxl.load_workbook(path)
    ws = wb["DOOH Screenlist"]
    assert ws.max_row == n + 1
    assert [c.value for c in ws[1]] == dp.DOOH_ALL_COLUMNS
    table = ws.tables["DOOHScreenlist"]
    assert table.ref == f"A1:{last_col}{n + 1}" and table.autoFilter.ref == table.ref
    assert [c.name for c in table.tableColumns] == dp.DOOH_ALL_COLUMNS
    for k, row in enumerate(rows, start=2):
        cells = [c.value for c in ws[k]]
        assert cells[:40] == row, k
        for name, value in zip(dp.DOOH_SCREENLIST_COLUMNS, row):
            if isinstance(value, bool):
                assert ws.cell(row=k, column=COL[name] + 1).value is value
        assert cells[COL["max_avails"]] == "=" + dp.table_formula("max_avails") and cells[COL["max_spend"]] == "=" + dp.table_formula("max_spend")
        assert isinstance(cells[COL["id"]], (str, type(None))) and isinstance(cells[COL["postal_code"]], (str, type(None)))
    assert ws.cell(row=2, column=COL["four_week_impressions"] + 1).number_format == "#,##0"
    assert ws.cell(row=n + 1, column=COL["four_week_impressions"] + 1).number_format == "#,##0"          # the last row has the format too
    assert ws.cell(row=n + 1, column=COL["max_spend"] + 1).number_format == '"$"#,##0.00'
    cached = openpyxl.load_workbook(path, data_only=True)["DOOH Screenlist"]
    sum_avails = sum_spend = 0.0
    helper = [dp.helper_values(r) for r in rows]
    for k, (avails, spend) in enumerate(helper, start=2):
        assert cached.cell(row=k, column=COL["max_avails"] + 1).value == pytest.approx(avails, abs=1e-9)        # cached, so a viewer that never recalculates shows it
        assert cached.cell(row=k, column=COL["max_spend"] + 1).value == pytest.approx(spend, abs=1e-9)
        sum_avails += cached.cell(row=k, column=COL["max_avails"] + 1).value
        sum_spend += cached.cell(row=k, column=COL["max_spend"] + 1).value
    sheet_xml = parts[sheet_part(parts, "DOOH Screenlist")].decode("utf-8")
    assert sheet_xml.count("<f>") == 2 * n and f'<dimension ref="A1:{last_col}{n + 1}"/>' in sheet_xml
    assert f'<ignoredError sqref="A2:{last_col}1048576" numberStoredAsText="1"/>' in sheet_xml

    # --- the pivot cache: the real records, counted and indexed consistently
    cache = ET.fromstring(parts["xl/pivotCache/pivotCacheDefinition1.xml"])
    records = ET.fromstring(parts["xl/pivotCache/pivotCacheRecords1.xml"])
    assert cache.get("refreshOnLoad") == "1" and cache.find("m:cacheSource/m:worksheetSource", NS).get("name") == "DOOHScreenlist"
    assert cache.get("recordCount") == records.get("count") == str(n) and len(records) == n
    fields = cache.findall("m:cacheFields/m:cacheField", NS)
    assert [f.get("name") for f in fields] == dp.DOOH_ALL_COLUMNS and cache.find("m:cacheFields", NS).get("count") == "42"
    shared_items = {}
    for name in ("venue_type", "publisher"):
        shared = fields[COL[name]].find("m:sharedItems", NS)
        assert shared.get("count") == str(len(shared)) and len(shared) >= 1
        assert (shared.get("containsBlank") == "1") == any(c.tag == M + "m" for c in shared)
        labels = [None if c.tag == M + "m" else c.get("v") for c in shared]
        assert len({(v or "").casefold() if v is not None else None for v in labels}) == len(labels)       # a pivot item is one case-insensitive value
        shared_items[name] = labels
    for k, (record, row) in enumerate(zip(records, rows)):
        assert len(record) == 42, k
        for name in ("venue_type", "publisher"):
            cell = record[COL[name]]
            assert cell.tag == M + "x" and 0 <= int(cell.get("v")) < len(shared_items[name])
            want = row[dp.DOOH_SCREENLIST_COLUMNS.index(name)]
            got = shared_items[name][int(cell.get("v"))]
            assert (got or "").casefold() == (want or "").casefold()
        for name in dp.DOOH_ALL_COLUMNS:
            if name in ("venue_type", "publisher"):
                continue
            cell, value = record[COL[name]], (row[COL[name]] if COL[name] < 40 else helper[k][COL[name] - 40])
            if value in (None, ""):
                assert cell.tag == M + "m", (k, name)
            elif isinstance(value, bool):
                assert cell.tag == M + "b" and cell.get("v") == str(int(value))
            elif isinstance(value, (int, float)):
                assert cell.tag == M + "n" and float(cell.get("v")) == pytest.approx(value, abs=1e-9)
            else:
                assert cell.tag == M + "s" and cell.get("v") == value, (k, name)                     # newlines, tabs, quotes and markup come back exactly
    # the declared kinds of every non-axis field are exactly what its records contain
    for name in dp.DOOH_ALL_COLUMNS:
        if name in ("venue_type", "publisher"):
            continue
        shared = fields[COL[name]].find("m:sharedItems", NS)
        kinds = {c[COL[name]].tag.replace(M, "") for c in records}
        assert (shared.get("containsBlank") == "1" or shared.get("containsNonDate") == "0") == ("m" in kinds), name
        if kinds <= {"n", "m"} and "n" in kinds:
            values = [float(c[COL[name]].get("v")) for c in records if c[COL[name]].tag == M + "n"]
            assert shared.get("containsNumber") == "1" and shared.get("containsString") == "0"
            assert float(shared.get("minValue")) == pytest.approx(min(values)) and float(shared.get("maxValue")) == pytest.approx(max(values))
            assert (shared.get("containsInteger") == "1") == all(v == int(v) for v in values)
        if "s" in kinds:
            assert shared.get("containsNumber") is None and shared.get("containsString") in (None, "1")
            assert (shared.get("longText") == "1") == any(len(c[COL[name]].get("v")) > 255 for c in records if c[COL[name]].tag == M + "s")

    # --- the pivot definition and the cells written under it
    pivot = ET.fromstring(parts["xl/pivotTables/pivotTable1.xml"])
    workbook_cache = ET.fromstring(parts["xl/workbook.xml"]).find("m:pivotCaches/m:pivotCache", NS)
    assert pivot.get("cacheId") == workbook_cache.get("cacheId")
    assert len(pivot.findall("m:pivotFields/m:pivotField", NS)) == 42
    items = pivot.findall("m:rowItems/m:i", NS)
    assert pivot.find("m:rowItems", NS).get("count") == str(len(items))
    for axis in ("venue_type", "publisher"):
        field = pivot.findall("m:pivotFields/m:pivotField", NS)[COL[axis]]
        listed = field.findall("m:items/m:item", NS)
        assert field.find("m:items", NS).get("count") == str(len(listed))
        real = [int(i.get("x")) for i in listed if i.get("t") is None]
        assert sorted(real) == list(range(len(shared_items[axis])))                      # every shared item is listed once
        assert len(listed) == len(real) + (1 if dp.PIVOT_SHOW_SUBTOTALS else 0)
    summary = wb["DOOH Summary"]
    expected = expected_pivot(rows)
    first_row = dp.PIVOT_FIRST_ROW
    last_row = first_row + len(expected)
    assert pivot.find("m:location", NS).get("ref") == f"B{first_row}:F{last_row}" == dp.pivot_ref(rows)
    assert [summary.cell(row=first_row, column=c).value for c in range(2, 7)] == ["Venue Type", "Network ", "Number of Screens", "Max. Avails", "Max. Spend"]
    shown = rendered_pivot(summary, last_row)
    assert len(shown) == len(expected) and summary.cell(row=last_row + 1, column=2).value is None
    carried = None
    for (kind, b, c, d, e, f), (xkind, venue, network, count, avails, spend) in zip(shown, expected):
        assert kind == xkind
        if kind == "data":
            assert b in (None, venue) and c == network                                   # the venue type is named on its first line only
            carried = venue if b is not None else carried
            assert (b or carried) == venue
        else:
            assert b == venue and c is None
        assert d == count and e == pytest.approx(avails, abs=1e-5) and f == pytest.approx(spend, abs=1e-5)
    # the definition's own items decode to exactly the lines written under it
    decoded = decode_row_items(pivot, cache)
    previous, flat = None, []
    for kind, labels in decoded:
        if kind == "data":
            flat.append(("data", labels[0], labels[1]))
        else:
            flat.append((kind, labels[0], None))
    assert flat == [(k, v, nw) for k, v, nw, *_ in expected]
    # the numbers agree everywhere: table (cached helper cells), pivot grand total, totals the wizard shows
    grand = shown[-1]
    assert grand[2] is None and grand[3] == sum(1 for r in rows if r[0] not in (None, ""))
    assert grand[4] == pytest.approx(sum_avails, abs=1e-4) and grand[5] == pytest.approx(sum_spend, abs=1e-4)
    assert sum(r[3] for r in shown if r[0] == "data") == grand[3] and sum(r[3] for r in shown if r[0] == "subtotal") == grand[3]
    assert sum(r[4] for r in shown if r[0] == "data") == pytest.approx(grand[4], abs=1e-4)
    assert sum(r[5] for r in shown if r[0] == "data") == pytest.approx(grand[5], abs=1e-4)
    cache_avails = sum(float(c[COL["max_avails"]].get("v")) for c in records)
    cache_spend = sum(float(c[COL["max_spend"]].get("v")) for c in records)
    assert cache_avails == pytest.approx(grand[4], abs=1e-4) and cache_spend == pytest.approx(grand[5], abs=1e-4)
    if totals:
        assert totals == (pytest.approx(grand[4], abs=1e-4), pytest.approx(grand[5], abs=1e-4))
    return wb


# --- tests ---------------------------------------------------------------------------------------------------------------------

def test_three_groups_of_screens_come_out_as_a_complete_table_and_a_prepopulated_pivot(tmp_path):
    rows = sup.screens(90, seed=21)
    parsed = ds.parse_upload("x.csv", sup.csv_bytes(rows))
    path = export(tmp_path, parsed.rows)
    check_workbook(path, rows, totals=(parsed.total_impressions, parsed.total_spend))
    ws = openpyxl.load_workbook(path)["DOOH Summary"]
    assert "90 screens" in ws["B4"].value and "Minimum of 20" in ws["B4"].value and str(dp.DOOH_MINIMUM_SCREENS) in ws["B4"].value
    shown = [r for r in rendered_pivot(ws, dp.PIVOT_FIRST_ROW + len(expected_pivot(rows)))]
    assert [r[1] for r in shown if r[0] == "subtotal"] == ["Out of Home|Billboards|Digital Total", "Retail|Gas Stations|Pump Total", "Transit|Airports|Concourse Total"]


def test_a_messy_real_world_list_keeps_every_value_and_every_count_consistent(tmp_path):
    rows = tricky_rows()
    path = export(tmp_path, rows)
    check_workbook(path, rows)
    cache = ET.fromstring(parts_of(path)["xl/pivotCache/pivotCacheDefinition1.xml"])
    venue = [c for c in cache.findall("m:cacheFields/m:cacheField", NS) if c.get("name") == "venue_type"][0].find("m:sharedItems", NS)
    assert venue.get("containsBlank") == "1"                                           # the screen with no venue type is its own "(blank)" item
    assert sum(1 for c in venue if c.tag == M + "s") == 3                              # the lower-case spelling did not make a fourth
    image_field = [c for c in cache.findall("m:cacheFields/m:cacheField", NS) if c.get("name") == "asset_image_url"][0].find("m:sharedItems", NS)
    assert image_field.get("longText") == "1"
    ws = openpyxl.load_workbook(path)["DOOH Summary"]
    labels = {ws.cell(row=r, column=2).value for r in range(7, 40)}
    assert "(blank) Total" in labels                                                    # the empty venue type, as a pivot labels it


def test_a_large_list_with_many_distinct_groups_stays_consistent_when_the_sorted_order_differs_from_the_file_order(tmp_path):
    import random
    rnd = random.Random(4)
    rows = sup.screens(1200, seed=8)
    ix = {c: i for i, c in enumerate(dp.DOOH_SCREENLIST_COLUMNS)}
    venues = [f"{rnd.choice('ZzYyAaBbMm')}{rnd.randint(0, 99):02d} venue {rnd.choice(['Pump', 'Gate', 'Mall'])}" for _ in range(45)]
    networks = [f"{rnd.choice('qQwWeErRtT')}-net {rnd.randint(0, 99)}" for _ in range(70)]
    for r in rows:
        r[ix["venue_type"]] = rnd.choice(venues)
        r[ix["publisher"]] = rnd.choice(networks)
    path = export(tmp_path, rows)
    check_workbook(path, rows)
    parts = parts_of(path)
    assert len(parts["xl/pivotCache/pivotCacheRecords1.xml"]) > 500_000          # the records are really there


def test_without_an_uploaded_list_the_export_is_the_one_sample_row_exactly_as_before(tmp_path):
    plain = export(tmp_path, None, "plain.xlsx")
    empty = export(tmp_path, [], "empty.xlsx")
    ws = openpyxl.load_workbook(plain)
    assert ws["DOOH Screenlist"].tables["DOOHScreenlist"].ref == "A1:AP2" and ws["DOOH Screenlist"].max_row == 2
    assert ws["DOOH Summary"]["B4"].value == et.DOOH_SUMMARY_INSTRUCTION
    assert openpyxl.load_workbook(empty)["DOOH Screenlist"].max_row == 2                 # an empty list is no list
    # the sample row given explicitly is the same Screenlist sheet and the same pivot parts as giving nothing
    sample = [[None if v == "" else v for v in dp.DOOH_SAMPLE_ROW]]
    one = export(tmp_path, sample, "one.xlsx")
    a, b = parts_of(plain), parts_of(one)
    for part in (sheet_part(a, "DOOH Screenlist"), "xl/tables/table1.xml", "xl/pivotCache/pivotCacheRecords1.xml", "xl/pivotTables/pivotTable1.xml"):
        assert a[part] == b[part], part
    norm = lambda x: re.sub(rb'refreshedDate="[^"]*"', b"", x)
    assert norm(a["xl/pivotCache/pivotCacheDefinition1.xml"]) == norm(b["xl/pivotCache/pivotCacheDefinition1.xml"])
    check_workbook(one, sample)


def test_the_pivot_stays_consistent_with_the_subtotals_switched_off(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "PIVOT_SHOW_SUBTOTALS", False)
    rows = sup.screens(40, seed=2)
    path = export(tmp_path, rows)
    ws = openpyxl.load_workbook(path)["DOOH Summary"]
    kinds = [r[0] for r in rendered_pivot(ws, dp.PIVOT_FIRST_ROW + 1 + 7)]
    assert "subtotal" not in kinds and kinds[-1] == "grand"
    pivot = ET.fromstring(parts_of(path)["xl/pivotTables/pivotTable1.xml"])
    assert pivot.find("m:location", NS).get("ref") == "B6:F14" and not [i for i in pivot.findall("m:rowItems/m:i", NS) if i.get("t") == "default"]
    for f in pivot.findall("m:pivotFields/m:pivotField", NS):
        if f.get("axis") == "axisRow":
            assert f.get("defaultSubtotal") == "0" and not [i for i in f.findall("m:items/m:item", NS) if i.get("t") == "default"]


def test_the_summary_sheet_rows_are_pre_sized_to_the_pivots_length(tmp_path):
    rows = sup.screens(600, seed=3)
    ix = {c: i for i, c in enumerate(dp.DOOH_SCREENLIST_COLUMNS)}
    for k, r in enumerate(rows):
        r[ix["venue_type"]] = f"Venue type {k % 120:03d}"
    path = export(tmp_path, rows)
    ws = openpyxl.load_workbook(path)["DOOH Summary"]
    last = dp.PIVOT_FIRST_ROW + len(expected_pivot(rows))
    assert last > dp.PIVOT_FIRST_ROW + dp.SUMMARY_PRESIZED_ROWS                         # more lines than the template pre-sizes
    assert {ws.row_dimensions[r].height for r in range(dp.PIVOT_FIRST_ROW, last + 1)} == {dp.SUMMARY_ROW_HEIGHT}


# --- guards -----------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("break_row, message", [
    (lambda r: r[:-1], "39 values"),
    (lambda r: [*r[:22], "lots", *r[23:]], "four_week_impressions"),
    (lambda r: [*r[:22], True, *r[23:]], "four_week_impressions"),
    (lambda r: [*r[:22], float("nan"), *r[23:]], "four_week_impressions"),
    (lambda r: [*r[:29], "TRUE", *r[30:]], "supports_video"),
    (lambda r: [*r[:12], 80216, *r[13:]], "postal_code"),
])
def test_a_row_the_table_cannot_hold_is_refused_before_anything_is_written(break_row, message):
    rows = sup.screens(3)
    rows[1] = break_row(rows[1])
    with pytest.raises(dp.DoohRowError, match=message):
        dp.PivotModel(rows)
    with pytest.raises(dp.DoohRowError):
        dp.pivot_preview_rows(rows)


def test_the_pivot_is_refused_when_the_table_does_not_cover_exactly_the_rows_it_is_given(tmp_path):
    rows = sup.screens(5)
    wb = Workbook()
    wb.remove(wb.active)
    et.build_dooh_summary(wb, rows=rows)
    et.build_dooh_screenlist(wb, rows=rows)
    path = tmp_path / "mismatch.xlsx"
    wb.save(path)
    before = path.read_bytes()
    with pytest.raises(dp.DoohPivotError, match="covers A1:AP6"):
        dp.add_dooh_pivot(path, rows=sup.screens(6))
    assert path.read_bytes() == before and [p.name for p in tmp_path.iterdir()] == ["mismatch.xlsx"]
    assert dp.add_dooh_pivot(path, rows=rows) is True
    assert dp.add_dooh_pivot(path, rows=rows) is False                                   # still idempotent


def test_a_failing_pivot_injection_with_a_list_keeps_the_complete_screenlist_and_warns(tmp_path, monkeypatch):
    def boom(_path, rows=None):
        raise dp.DoohPivotError("simulated")
    monkeypatch.setattr("app.services.dooh_pivot.add_dooh_pivot", boom)
    rows = sup.screens(25)
    out = tmp_path / "warn.xlsx"
    summary = generate_proposal(_req(), [LineItem(product_name="Digital Out of Home", monthly_budget=1000.0, months=3, id="a")], out, dooh_screenlist=rows)
    assert any("pivot table could not be added" in w for w in summary["warnings"])
    wb = openpyxl.load_workbook(out)
    assert wb["DOOH Screenlist"].max_row == 26 and not wb["DOOH Summary"]._pivots
    assert wb["DOOH Summary"].cell(row=dp.PIVOT_FIRST_ROW, column=2).value == "Venue Type"      # the grouped cells are still there


def test_attribute_values_keep_line_breaks_and_tabs_that_an_xml_parser_would_turn_into_spaces():
    assert dp._q("a\nb\tc\rd") == '"a&#10;b&#9;c&#13;d"' and ET.fromstring(f"<x v={dp._q('a\nb\tc')}/>").get("v") == "a\nb\tc"
    assert dp._q('He said "hi" & <left>') == '"He said &quot;hi&quot; &amp; &lt;left&gt;"'


def test_the_summarys_minimum_screens_wording_matches_the_one_constant():
    assert str(dp.DOOH_MINIMUM_SCREENS) in et.DOOH_SUMMARY_INSTRUCTION and str(dp.DOOH_MINIMUM_SCREENS) in et.DOOH_SUMMARY_INSTRUCTION_WITH_LIST
