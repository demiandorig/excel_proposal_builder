"""
DOOH Deliverables — the DOOH Screenlist table and the pre-made DOOH Summary pivot table.

The "DOOH Deliverables" export is two worksheets that are always built together:

  DOOH Screenlist  The inventory the planner pastes in (the platform export's 40 columns, in a fixed order, plus two
                   calculated helper columns) as an Excel TABLE named DOOHScreenlist, shipped with ONE sample row.
  DOOH Summary     A real, pre-made PIVOT TABLE over that table: Venue Type > Network, with Number of Screens, Max.
                   Avails and Max. Spend. It refreshes when the workbook opens, so pasted rows simply appear in it.

openpyxl can write the table but cannot write a pivot table, so this module also contains `add_dooh_pivot()`, a
post-processor that injects the pivot parts (pivotCacheDefinition / pivotCacheRecords / pivotTable, their rels, the
workbook.xml <pivotCaches> entry, the content types and a custom pivot style) into an already-saved .xlsx. The shape of
those parts was studied from a pivot built by Excel itself through COM automation (same layout, same attributes), and
the output was checked by opening it in Excel, refreshing it and adding rows.

Whatever saves the workbook must call `add_dooh_pivot(path)` AFTER its last openpyxl save (an openpyxl load + save
would drop the pivot). It is idempotent and never touches a workbook that has no DOOH sheets.

This module has no dependency on excel_template (which imports it for the constants), so the whole thing can be
unit-tested without building a proposal.
"""
from __future__ import annotations

import logging
import os
import posixpath
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from html import unescape as _html_unescape
from pathlib import Path
from typing import Optional, Union
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

DOOH_SUMMARY_SHEET = "DOOH Summary"
DOOH_SCREENLIST_SHEET = "DOOH Screenlist"
DOOH_TABLE_NAME = "DOOHScreenlist"            # the Excel table (ListObject) the pivot reads — also its displayName

# The single user-facing tab key for the DOOH export ("DOOH Deliverables" = both sheets above). The two keys it replaced
# are still accepted everywhere a tab dict comes in (see notion_parser.normalize_tab_keys).
DOOH_TAB_KEY = "dooh"
DOOH_TAB_LABEL = "DOOH Deliverables"
LEGACY_DOOH_TAB_KEYS = ("dooh_summary", "dooh_screenlist")

# ---------------------------------------------------------------------------
# The Screenlist: 40 inventory columns (always these, always this order) + 2 calculated helper columns
# ---------------------------------------------------------------------------

DOOH_SCREENLIST_COLUMNS: list[str] = [
    "id", "adunit_name", "asset_name", "adunit_id", "status", "integration_type", "publisher", "network", "media_type",
    "venue_type", "venue_name", "city", "postal_code", "county", "state", "dma", "country", "latitude", "longitude",
    "address", "cpm", "currency", "four_week_impressions", "impression_multiplier", "bidstream_impressions",
    "bidstream_inferred_impressions", "screen_count", "min_duration", "max_duration", "supports_video",
    "supports_banner", "supports_audio", "supports_dynamic_creative", "slot_dimensions", "preferred_ad_format",
    "ad_formats", "restrictions", "languages", "open_ooh_venue_category_id", "asset_image_url",
]

# Calculated helper columns, to the RIGHT of the 40 so the inventory columns stay untouched/pasteable. They exist
# because a pivot "calculated field" multiplies the SUMS of its fields, which is wrong for any group whose rows have
# different CPMs; a per-row column summed by the pivot is right for any number of rows.
MAX_AVAILS_COLUMN = "max_avails"
MAX_SPEND_COLUMN = "max_spend"
DOOH_HELPER_COLUMNS: list[str] = [MAX_AVAILS_COLUMN, MAX_SPEND_COLUMN]
DOOH_ALL_COLUMNS: list[str] = DOOH_SCREENLIST_COLUMNS + DOOH_HELPER_COLUMNS

# Max. Avails = four_week_impressions * DOOH_MAX_AVAILS_SHARE (the part of a screen's four-week impressions we count
# as available to a campaign).
DOOH_MAX_AVAILS_SHARE = 0.4

# Max. Spend = Max. Avails * cpm / DOOH_SPEND_DIVISOR.
# This is EXACTLY the formula the planner asked for: ((four_week_impressions * 0.4) * cpm / 100). CPM is normally a
# price per THOUSAND impressions, which would make the divisor 1000 — the 100 is kept as written until the planner
# confirms it, and this constant is the one place to change it (the table's calculated-column formula, the cell
# formulas and the cached sample values are all generated from it).
DOOH_SPEND_DIVISOR = 100

# The sample row (same order as DOOH_SCREENLIST_COLUMNS). Numbers are numbers, TRUE/FALSE real booleans, "" an empty
# cell. postal_code and the identifier columns (id, *_id, names that are hex codes) stay TEXT so leading zeros and
# long codes are never altered by Excel — which is also what a platform export pasted as text gives.
DOOH_SAMPLE_ROW: list = [
    "1348310", "61e86cc70b573d05e4183fe0", "DVG288", "550eceb3-b6fa-4daf-a9e7-456bfb47846f", "live", "API", "GSTV",
    "GSTV Fast", "Point of Sale", "Retail|Gas Stations|Pump", "61e86cc70b573d05e4183fe0", "DENVER", "80216",
    "Denver County - CO", "CO", "Denver, CO", "USA", 39.774694, -104.924262, "4140 Grape Street, Denver, CO", 32.5,
    "USD", 2353, 1.483757764, 1675, 1486, 8, 3, 61, True, True, True, False, "1920x1080", "1920x1080", "1920x1080",
    "", "", "20101",
    "https://static1.squarespace.com/static/64d54fd8cd03f240928c1cf0/t/657372a51e23d159d4ce9520/1702064835985/"
    "Programmatic_Inventory",
]

assert len(DOOH_SCREENLIST_COLUMNS) == 40 and len(DOOH_SAMPLE_ROW) == 40

_SAMPLE_BY_COLUMN = dict(zip(DOOH_SCREENLIST_COLUMNS, DOOH_SAMPLE_ROW))


def max_avails_for(four_week_impressions) -> float:
    """The max_avails column's value for one row."""
    return float(four_week_impressions) * DOOH_MAX_AVAILS_SHARE


def max_spend_for(four_week_impressions, cpm) -> float:
    """The max_spend column's value for one row."""
    return (float(four_week_impressions) * DOOH_MAX_AVAILS_SHARE) * float(cpm) / DOOH_SPEND_DIVISOR


# The sample's helper values (also written as the cached values of the helper formulas, so a viewer that doesn't
# recalculate — Protected View, a preview pane — still shows the right numbers). Rounded to kill float noise.
SAMPLE_MAX_AVAILS = round(max_avails_for(_SAMPLE_BY_COLUMN["four_week_impressions"]), 6)
SAMPLE_MAX_SPEND = round(max_spend_for(_SAMPLE_BY_COLUMN["four_week_impressions"], _SAMPLE_BY_COLUMN["cpm"]), 6)


def table_formula(column: str) -> str:
    """The calculated-column formula (no leading '=') for a helper column, in the structured-reference form Excel
    itself stores in a table. Wrapped in IFERROR so one pasted row with a non-numeric impressions/CPM cell counts as
    0 instead of turning every pivot total it touches into #VALUE!."""
    imps = f"{DOOH_TABLE_NAME}[[#This Row],[four_week_impressions]]"
    cpm = f"{DOOH_TABLE_NAME}[[#This Row],[cpm]]"
    if column == MAX_AVAILS_COLUMN:
        return f"IFERROR({imps}*{DOOH_MAX_AVAILS_SHARE:g},0)"
    if column == MAX_SPEND_COLUMN:
        return f"IFERROR(({imps}*{DOOH_MAX_AVAILS_SHARE:g})*{cpm}/{DOOH_SPEND_DIVISOR:g},0)"
    raise KeyError(column)


# ---------------------------------------------------------------------------
# The Summary pivot: what it is, where it sits, and the cells it renders for the sample row
# ---------------------------------------------------------------------------

PIVOT_NAME = "DOOHSummaryPivot"
PIVOT_STYLE_NAME = "Entravision DOOH Pivot"          # custom pivot style, added to styles.xml by add_dooh_pivot
PIVOT_FIRST_ROW = 6          # sheet row of the pivot's header line (rows 1-5 are the title and the instruction)
PIVOT_FIRST_COL = 2          # column B
SUMMARY_ROW_HEIGHT = 20      # pt — one height for every row of the sheet, so a grown pivot stays evenly spaced
SUMMARY_PRESIZED_ROWS = 150  # rows from the pivot's first line that get that height explicitly (Excel autofits otherwise)
SUMMARY_COLUMN_WIDTHS = (36, 24, 20, 20, 20)     # B..F: Venue Type, Network, Number of Screens, Max. Avails, Max. Spend

# Row fields (outer first): (source column, caption shown on the pivot).
# The Network caption carries a trailing space ON PURPOSE: Excel refuses a pivot caption that equals the name of a
# source column (case-insensitive) and the Screenlist has a column named "network" (publisher is the one we group by).
# The space is invisible on screen and keeps the caption the planner asked for.
PIVOT_ROW_FIELDS = (("venue_type", "Venue Type"), ("publisher", "Network "))
# Data fields: (source column, aggregation, caption, number format code)
_COUNT_FORMAT = "#,##0"
_MONEY_FORMAT = '"$"#,##0.00'
PIVOT_DATA_FIELDS = (
    ("id", "count", "Number of Screens", _COUNT_FORMAT),
    (MAX_AVAILS_COLUMN, "sum", "Max. Avails", _COUNT_FORMAT),
    (MAX_SPEND_COLUMN, "sum", "Max. Spend", _MONEY_FORMAT),
)
PIVOT_SHOW_SUBTOTALS = True       # a "<venue type> Total" line under each venue type's networks


def pivot_preview_rows() -> list[tuple[str, tuple]]:
    """The lines (kind, (B..F values)) a pivot over the one sample row renders to, in order — the header, the sample
    screen, the venue type's subtotal and the grand total. Written into the Summary sheet as plain cells so the sheet is
    right even where the pivot is never refreshed; refreshing it in Excel replaces them with the real thing."""
    venue_type = _SAMPLE_BY_COLUMN["venue_type"]
    network = _SAMPLE_BY_COLUMN["publisher"]
    measures = (1, SAMPLE_MAX_AVAILS, SAMPLE_MAX_SPEND)
    rows: list[tuple[str, tuple]] = [
        ("header", (PIVOT_ROW_FIELDS[0][1], PIVOT_ROW_FIELDS[1][1]) + tuple(f[2] for f in PIVOT_DATA_FIELDS)),
        ("data", (venue_type, network) + measures),
    ]
    if PIVOT_SHOW_SUBTOTALS:
        rows.append(("subtotal", (f"{venue_type} Total", None) + measures))
    rows.append(("grand", ("Grand Total", None) + measures))
    return rows


def pivot_ref() -> str:
    """The A1 range the pivot occupies for the sample row (B6:F9 with subtotals)."""
    last_row = PIVOT_FIRST_ROW + len(pivot_preview_rows()) - 1
    first_col = _col_letter(PIVOT_FIRST_COL)
    last_col = _col_letter(PIVOT_FIRST_COL + 4)
    return f"{first_col}{PIVOT_FIRST_ROW}:{last_col}{last_row}"


def _col_letter(n: int) -> str:
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


# ---------------------------------------------------------------------------
# XML builders (shapes copied from a pivot Excel wrote itself)
# ---------------------------------------------------------------------------

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL_CACHE_DEF = _NS_R + "/pivotCacheDefinition"
_REL_CACHE_REC = _NS_R + "/pivotCacheRecords"
_REL_PIVOT_TABLE = _NS_R + "/pivotTable"
_REL_TABLE = _NS_R + "/table"
_CT_CACHE_DEF = "application/vnd.openxmlformats-officedocument.spreadsheetml.pivotCacheDefinition+xml"
_CT_CACHE_REC = "application/vnd.openxmlformats-officedocument.spreadsheetml.pivotCacheRecords+xml"
_CT_PIVOT_TABLE = "application/vnd.openxmlformats-officedocument.spreadsheetml.pivotTable+xml"
_XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_SUBTOTAL_ATTR = {"count": ' subtotal="count"'}     # a dataField's aggregation; absent = Sum


def _q(value) -> str:
    """A double-quoted XML attribute value."""
    return '"' + _xml_escape(str(value), {'"': "&quot;"}) + '"'


def _num(value: float) -> str:
    """A number as the cache stores it (shortest exact repr, no float noise from the formulas)."""
    value = round(float(value), 10)
    return repr(int(value)) if value == int(value) and abs(value) < 1e15 else repr(value)


def _all_sample_values() -> list:
    return list(DOOH_SAMPLE_ROW) + [SAMPLE_MAX_AVAILS, SAMPLE_MAX_SPEND]


def _kind(value) -> str:
    if value is None or value == "":
        return "blank"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "num"
    return "str"


def _row_field_columns() -> list[str]:
    return [f[0] for f in PIVOT_ROW_FIELDS]


def _field_numfmt(column: str, money_fmt_id: int) -> int:
    """numFmtId of a cache/pivot field: only the fields the pivot shows need one (3 = the built-in #,##0)."""
    return {MAX_AVAILS_COLUMN: 3, MAX_SPEND_COLUMN: money_fmt_id}.get(column, 0)


def build_cache_definition_xml(money_fmt_id: int, records_rid: str = "rId1", refreshed_by: str = "Proposal Builder") -> str:
    """xl/pivotCache/pivotCacheDefinition1.xml — the cache describes the table's 42 columns, with the sample row's
    values (so the pivot is complete even before its first refresh) and refreshOnLoad so Excel rebuilds it from the
    table every time the workbook is opened."""
    refreshed_date = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime(1899, 12, 30)).total_seconds() / 86400
    fields = []
    row_cols = _row_field_columns()
    for name, value in zip(DOOH_ALL_COLUMNS, _all_sample_values()):
        numfmt = _field_numfmt(name, money_fmt_id)
        kind = _kind(value)
        if name in row_cols:
            shared = f'<sharedItems count="1"><s v={_q(value)}/></sharedItems>'
        elif kind == "num":
            integer = ' containsInteger="1"' if float(value) == int(float(value)) else ""
            shared = (f'<sharedItems containsSemiMixedTypes="0" containsString="0" containsNumber="1"{integer} '
                      f'minValue="{_num(value)}" maxValue="{_num(value)}"/>')
        elif kind == "blank":
            shared = '<sharedItems containsNonDate="0" containsString="0" containsBlank="1"/>'
        else:                                   # text and booleans: no shared-item list unless the field is on an axis
            shared = "<sharedItems/>"
        fields.append(f'<cacheField name={_q(name)} numFmtId="{numfmt}">{shared}</cacheField>')
    return (
        _XML_DECL
        + f'<pivotCacheDefinition xmlns="{_NS_MAIN}" xmlns:r="{_NS_R}" r:id={_q(records_rid)} refreshOnLoad="1" '
          f'refreshedBy={_q(refreshed_by)} refreshedDate="{refreshed_date:.9f}" createdVersion="8" refreshedVersion="8" '
          f'minRefreshableVersion="3" recordCount="1">'
          f'<cacheSource type="worksheet"><worksheetSource name={_q(DOOH_TABLE_NAME)}/></cacheSource>'
          f'<cacheFields count="{len(fields)}">{"".join(fields)}</cacheFields>'
          '<extLst><ext uri="{725AE2AE-9491-48be-B2B4-4EB974FC3084}" '
          'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"><x14:pivotCacheDefinition/></ext></extLst>'
          "</pivotCacheDefinition>"
    )


def build_cache_records_xml() -> str:
    """xl/pivotCache/pivotCacheRecords1.xml — the one sample row."""
    row_cols = _row_field_columns()
    cells = []
    for name, value in zip(DOOH_ALL_COLUMNS, _all_sample_values()):
        kind = _kind(value)
        if name in row_cols:
            cells.append('<x v="0"/>')            # index into that field's shared items
        elif kind == "blank":
            cells.append("<m/>")
        elif kind == "bool":
            cells.append(f'<b v="{int(value)}"/>')
        elif kind == "num":
            cells.append(f'<n v="{_num(value)}"/>')
        else:
            cells.append(f"<s v={_q(value)}/>")
    return (_XML_DECL + f'<pivotCacheRecords xmlns="{_NS_MAIN}" xmlns:r="{_NS_R}" count="1"><r>{"".join(cells)}</r></pivotCacheRecords>')


def build_pivot_table_xml(cache_id: int, money_fmt_id: int) -> str:
    """xl/pivotTables/pivotTable1.xml — Venue Type > Network in tabular form, three value fields, grand totals on."""
    index = {name: i for i, name in enumerate(DOOH_ALL_COLUMNS)}
    row_captions = {col: caption for col, caption in PIVOT_ROW_FIELDS}
    data_cols = {f[0] for f in PIVOT_DATA_FIELDS}
    subtotals = PIVOT_SHOW_SUBTOTALS

    no_subtotal_attr = "" if subtotals else ' defaultSubtotal="0"'
    items = ('<items count="2"><item x="0"/><item t="default"/></items>' if subtotals
             else '<items count="1"><item x="0"/></items>')
    pivot_fields = []
    for name in DOOH_ALL_COLUMNS:
        numfmt = _field_numfmt(name, money_fmt_id)
        if name in row_captions:
            pivot_fields.append(
                f'<pivotField name={_q(row_captions[name])} axis="axisRow" compact="0" outline="0" showAll="0"'
                f'{no_subtotal_attr}>{items}</pivotField>')
        else:
            data_attr = ' dataField="1"' if name in data_cols else ""
            fmt = f' numFmtId="{numfmt}"' if numfmt else ""
            pivot_fields.append(f'<pivotField{data_attr} compact="0"{fmt} outline="0" showAll="0"/>')

    row_fields = "".join(f'<field x="{index[col]}"/>' for col, _ in PIVOT_ROW_FIELDS)
    row_items = '<i><x/><x/></i>' + ('<i t="default"><x/></i>' if subtotals else "") + '<i t="grand"><x/></i>'
    n_row_items = 3 if subtotals else 2
    col_items = '<i><x/></i>' + "".join(f'<i i="{k}"><x v="{k}"/></i>' for k in range(1, len(PIVOT_DATA_FIELDS)))
    formats = {_COUNT_FORMAT: 3, _MONEY_FORMAT: money_fmt_id}
    data_fields = "".join(
        f'<dataField name={_q(caption)} fld="{index[col]}"{_SUBTOTAL_ATTR.get(agg, "")} '
        f'baseField="0" baseItem="0" numFmtId="{formats[fmt]}"/>'
        for col, agg, caption, fmt in PIVOT_DATA_FIELDS)

    return (
        _XML_DECL
        + f'<pivotTableDefinition xmlns="{_NS_MAIN}" name={_q(PIVOT_NAME)} cacheId="{cache_id}" '
          'applyNumberFormats="0" applyBorderFormats="0" applyFontFormats="0" applyPatternFormats="0" '
          'applyAlignmentFormats="0" applyWidthHeightFormats="1" dataCaption="Values" updatedVersion="8" '
          'minRefreshableVersion="3" itemPrintTitles="1" createdVersion="8" indent="0" compact="0" compactData="0" '
          'multipleFieldFilters="0">'
          f'<location ref={_q(pivot_ref())} firstHeaderRow="0" firstDataRow="1" firstDataCol="{len(PIVOT_ROW_FIELDS)}"/>'
          f'<pivotFields count="{len(pivot_fields)}">{"".join(pivot_fields)}</pivotFields>'
          f'<rowFields count="{len(PIVOT_ROW_FIELDS)}">{row_fields}</rowFields>'
          f'<rowItems count="{n_row_items}">{row_items}</rowItems>'
          '<colFields count="1"><field x="-2"/></colFields>'
          f'<colItems count="{len(PIVOT_DATA_FIELDS)}">{col_items}</colItems>'
          f'<dataFields count="{len(PIVOT_DATA_FIELDS)}">{data_fields}</dataFields>'
          f'<pivotTableStyleInfo name={_q(PIVOT_STYLE_NAME)} showRowHeaders="1" showColHeaders="1" showRowStripes="0" '
          'showColStripes="0" showLastColumn="1"/>'
          '<extLst><ext uri="{962EF5D1-5CA2-4c93-8EF4-DBF5C05439D2}" '
          'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"><x14:pivotTableDefinition '
          'hideValuesRow="1" xmlns:xm="http://schemas.microsoft.com/office/excel/2006/main"/></ext></extLst>'
          "</pivotTableDefinition>"
    )


# The custom pivot style: grey header (the Screenlist's header colour), light purple venue-type subtotals, light green
# grand total — the same palette the proposal sheets use — with thin grey cell borders. Each entry is one <dxf>.
_THIN_GREY = '<color rgb="FFBBBBBB"/>'
_STYLE_DXFS = {
    "wholeTable": ("<dxf><border>" + "".join(f'<{s} style="thin">{_THIN_GREY}</{s}>' for s in ("left", "right", "top", "bottom"))
                   + f'<vertical style="thin">{_THIN_GREY}</vertical><horizontal style="thin">{_THIN_GREY}</horizontal></border></dxf>'),
    "headerRow": '<dxf><font><b/><i val="0"/><color rgb="FFFFFFFF"/></font><fill><patternFill><bgColor rgb="FF808080"/></patternFill></fill></dxf>',
    "firstSubtotalRow": '<dxf><font><b/><i val="0"/></font><fill><patternFill><bgColor rgb="FFEFE0EF"/></patternFill></fill></dxf>',
    "totalRow": ('<dxf><font><b/><i val="0"/></font><fill><patternFill><bgColor rgb="FFE8F2DD"/></patternFill></fill>'
                 '<border><top style="medium"><color rgb="FF000000"/></top></border></dxf>'),
}


# ---------------------------------------------------------------------------
# Injection into a saved .xlsx
# ---------------------------------------------------------------------------

class DoohPivotError(Exception):
    """The workbook has the DOOH sheets but not in the shape add_dooh_pivot expects (it is left untouched)."""


def _read_rels(parts: dict, rels_path: str) -> list[dict]:
    if rels_path not in parts:
        return []
    root = ET.fromstring(parts[rels_path])
    return [{"id": r.get("Id"), "type": r.get("Type"), "target": r.get("Target")} for r in root]


def _resolve(base_dir: str, target: str) -> str:
    """A relationship Target as a package path (absolute '/xl/..' or relative to base_dir)."""
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(base_dir, target))


def _rels_path_for(part: str) -> str:
    return posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")


def _next_free(parts: dict, template: str) -> int:
    n = 1
    while template.format(n) in parts:
        n += 1
    return n


def _unique_rid(existing_ids) -> str:
    nums = [int(m.group(1)) for i in existing_ids if (m := re.fullmatch(r"rId(\d+)", i or ""))]
    return f"rId{max(nums, default=0) + 1}"


def _add_rel(rels_xml: Optional[str], rel_type: str, target: str) -> tuple[str, str]:
    """Append a relationship to a .rels document (creating it if absent). Returns (new xml, the new Id)."""
    ids = re.findall(r'\bId="([^"]*)"', rels_xml or "")
    rid = _unique_rid(ids)
    rel = f'<Relationship Id="{rid}" Type="{rel_type}" Target="{target}"/>'
    if not rels_xml:
        return _XML_DECL + f'<Relationships xmlns="{_NS_PKG_REL}">{rel}</Relationships>', rid
    if rels_xml.rstrip().endswith("/>") and "</Relationships>" not in rels_xml:   # an empty <Relationships .../>
        return re.sub(r"\s*/>\s*$", f">{rel}</Relationships>", rels_xml.rstrip()), rid
    return rels_xml.replace("</Relationships>", rel + "</Relationships>"), rid


def _money_format_id(styles_xml: str) -> tuple[str, int]:
    """The numFmtId of the money format in styles.xml, adding it if the workbook does not have it yet."""
    for m in re.finditer(r'<numFmt\s+numFmtId="(\d+)"\s+formatCode="([^"]*)"\s*/>', styles_xml):
        if _html_unescape(m.group(2)) == _MONEY_FORMAT:
            return styles_xml, int(m.group(1))
    ids = [int(x) for x in re.findall(r'<numFmt\s+numFmtId="(\d+)"', styles_xml)]
    new_id = max([163] + ids) + 1
    new_fmt = f'<numFmt numFmtId="{new_id}" formatCode={_q(_MONEY_FORMAT)}/>'
    m = re.search(r'<numFmts\s+count="(\d+)"\s*>', styles_xml)
    if m:
        styles_xml = styles_xml.replace(m.group(0), f'<numFmts count="{int(m.group(1)) + 1}">', 1)
        styles_xml = styles_xml.replace("</numFmts>", new_fmt + "</numFmts>", 1)
    elif re.search(r"<numFmts\s*/>", styles_xml):
        styles_xml = re.sub(r"<numFmts\s*/>", f'<numFmts count="1">{new_fmt}</numFmts>', styles_xml, count=1)
    else:                                         # no <numFmts> at all: it goes first inside <styleSheet>
        m = re.search(r"<styleSheet\b[^>]*>", styles_xml)
        if not m:
            raise DoohPivotError("styles.xml has no <styleSheet>")
        styles_xml = styles_xml[:m.end()] + f'<numFmts count="1">{new_fmt}</numFmts>' + styles_xml[m.end():]
    return styles_xml, new_id


def _add_pivot_style(styles_xml: str) -> str:
    """Append the custom pivot style (its dxf entries and the <tableStyle>) to styles.xml."""
    if f'name="{PIVOT_STYLE_NAME}"' in styles_xml:
        return styles_xml
    # --- dxfs (differential formats the style elements point at)
    m = re.search(r'<dxfs\s+count="(\d+)"\s*>', styles_xml)
    dxf_xml = "".join(_STYLE_DXFS.values())
    if m:
        base = int(m.group(1))
        styles_xml = styles_xml.replace(m.group(0), f'<dxfs count="{base + len(_STYLE_DXFS)}">', 1)
        styles_xml = styles_xml.replace("</dxfs>", dxf_xml + "</dxfs>", 1)
    else:
        base = 0
        empty = re.search(r"<dxfs(\s+count=\"0\")?\s*/>", styles_xml)
        block = f'<dxfs count="{len(_STYLE_DXFS)}">{dxf_xml}</dxfs>'
        if empty:
            styles_xml = styles_xml.replace(empty.group(0), block, 1)
        elif "<tableStyles" in styles_xml:
            styles_xml = styles_xml.replace("<tableStyles", block + "<tableStyles", 1)
        else:
            m2 = re.search(r"<colors\b|<extLst\b|</styleSheet>", styles_xml)
            if not m2:
                raise DoohPivotError("styles.xml has no <styleSheet> end")
            styles_xml = styles_xml[:m2.start()] + block + styles_xml[m2.start():]
    # --- the table style itself
    elements = "".join(f'<tableStyleElement type="{t}" dxfId="{base + i}"/>' for i, t in enumerate(_STYLE_DXFS))
    style = f'<tableStyle name={_q(PIVOT_STYLE_NAME)} table="0" count="{len(_STYLE_DXFS)}">{elements}</tableStyle>'
    m = re.search(r'<tableStyles\b([^>]*?)(/?)>', styles_xml)
    if m and m.group(2) == "/":                   # <tableStyles count="0" .../>
        attrs = re.sub(r'\bcount="\d+"', 'count="1"', m.group(1))
        styles_xml = styles_xml.replace(m.group(0), f"<tableStyles{attrs}>{style}</tableStyles>", 1)
    elif m:
        count = re.search(r'\bcount="(\d+)"', m.group(1))
        new_open = m.group(0)
        if count:
            new_open = new_open.replace(count.group(0), f'count="{int(count.group(1)) + 1}"', 1)
        styles_xml = styles_xml.replace(m.group(0), new_open, 1).replace("</tableStyles>", style + "</tableStyles>", 1)
    else:
        block = f'<tableStyles count="1" defaultTableStyle="TableStyleMedium9" defaultPivotStyle="PivotStyleLight16">{style}</tableStyles>'
        m2 = re.search(r"<colors\b|<extLst\b|</styleSheet>", styles_xml)
        if not m2:
            raise DoohPivotError("styles.xml has no <styleSheet> end")
        styles_xml = styles_xml[:m2.start()] + block + styles_xml[m2.start():]
    return styles_xml


def _set_cached_value(sheet_xml: str, ref: str, value: float) -> str:
    """Fill the (empty) cached value of the formula cell `ref` — openpyxl writes formulas without one."""
    pattern = re.compile(r'(<c r="%s"[^>]*>(?:(?!</c>).)*?</f>)(?:<v></v>|<v\s*/>)?(</c>)' % re.escape(ref), re.S)
    return pattern.sub(lambda m: f"{m.group(1)}<v>{_num(value)}</v>{m.group(2)}", sheet_xml, count=1)


def _add_ignored_errors(sheet_xml: str, sqref: str) -> str:
    """Hide the 'number stored as text' triangles on the Screenlist (postal codes and ids are deliberately text)."""
    if "<ignoredErrors" in sheet_xml:
        return sheet_xml
    block = f'<ignoredErrors><ignoredError sqref={_q(sqref)} numberStoredAsText="1"/></ignoredErrors>'
    m = re.search(r"<smartTags\b|<drawing\b|<legacyDrawing\b|<legacyDrawingHF\b|<picture\b|<oleObjects\b|<controls\b|"
                  r"<webPublishItems\b|<tableParts\b|<extLst\b|</worksheet>", sheet_xml)
    if not m:
        raise DoohPivotError("worksheet has no end tag")
    return sheet_xml[:m.start()] + block + sheet_xml[m.start():]


def _sheet_parts(parts: dict) -> dict:
    """{sheet name: worksheet part path} from workbook.xml + its rels."""
    if "xl/workbook.xml" not in parts:
        raise DoohPivotError("not an xlsx workbook (no xl/workbook.xml)")
    root = ET.fromstring(parts["xl/workbook.xml"])
    rels = {r["id"]: r for r in _read_rels(parts, "xl/_rels/workbook.xml.rels")}
    out = {}
    for sheet in root.iter(f"{{{_NS_MAIN}}}sheet"):
        rid = sheet.get(f"{{{_NS_R}}}id")
        if rid in rels:
            out[sheet.get("name")] = _resolve("xl", rels[rid]["target"])
    return out


def _find_screenlist_table(parts: dict, sheet_part: str) -> str:
    for rel in _read_rels(parts, _rels_path_for(sheet_part)):
        if rel["type"] == _REL_TABLE:
            path = _resolve(posixpath.dirname(sheet_part), rel["target"])
            root = ET.fromstring(parts[path])
            if root.get("displayName") == DOOH_TABLE_NAME:
                names = [c.get("name") for c in root.iter(f"{{{_NS_MAIN}}}tableColumn")]
                if names != DOOH_ALL_COLUMNS:
                    raise DoohPivotError(f"table {DOOH_TABLE_NAME} does not have the expected columns: {names}")
                return path
    raise DoohPivotError(f"{DOOH_SCREENLIST_SHEET} has no table named {DOOH_TABLE_NAME}")


def add_dooh_pivot(path: Union[str, Path]) -> bool:
    """Add the DOOH Summary pivot table to the saved workbook at `path`.

    Returns True when the pivot was added; False when there was nothing to do (the workbook has no DOOH Summary +
    DOOH Screenlist pair, or the pivot is already there — safe to call twice). Raises DoohPivotError when the DOOH sheets
    are there but are not the ones dooh_pivot's builders create (no table, other columns): the file is left untouched,
    so the caller can keep the workbook as-is and warn. The file is replaced atomically.
    """
    path = Path(path)
    with zipfile.ZipFile(path) as zin:
        infos = zin.infolist()
        parts = {i.filename: zin.read(i.filename) for i in infos}
        compress = {i.filename: i.compress_type for i in infos}

    sheets = _sheet_parts(parts)
    if DOOH_SUMMARY_SHEET not in sheets or DOOH_SCREENLIST_SHEET not in sheets:
        return False
    summary_part, list_part = sheets[DOOH_SUMMARY_SHEET], sheets[DOOH_SCREENLIST_SHEET]
    summary_rels_path = _rels_path_for(summary_part)
    if any(r["type"] == _REL_PIVOT_TABLE for r in _read_rels(parts, summary_rels_path)):
        return False
    _find_screenlist_table(parts, list_part)                  # validates the source table before anything is written

    for needed in ("[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml"):
        if needed not in parts:
            raise DoohPivotError(f"workbook has no {needed}")

    new_parts: dict[str, bytes] = {}

    # --- styles: the money number format (look it up / add it) and the custom pivot style
    styles_xml = parts["xl/styles.xml"].decode("utf-8")
    styles_xml, money_id = _money_format_id(styles_xml)
    styles_xml = _add_pivot_style(styles_xml)
    new_parts["xl/styles.xml"] = styles_xml.encode("utf-8")

    # --- part names (never collide with a cache / pivot another tool may have put in the workbook)
    cache_n = _next_free(parts, "xl/pivotCache/pivotCacheDefinition{}.xml")
    pivot_n = _next_free(parts, "xl/pivotTables/pivotTable{}.xml")
    cache_def = f"xl/pivotCache/pivotCacheDefinition{cache_n}.xml"
    cache_rec = f"xl/pivotCache/pivotCacheRecords{cache_n}.xml"
    pivot_tbl = f"xl/pivotTables/pivotTable{pivot_n}.xml"

    wb_xml = parts["xl/workbook.xml"].decode("utf-8")
    cache_ids = [int(x) for x in re.findall(r'<pivotCache\b[^>]*\bcacheId="(\d+)"', wb_xml)]
    cache_id = max(cache_ids, default=0) + 1

    # --- the three pivot parts and their rels
    rec_rels, rec_rid = _add_rel(None, _REL_CACHE_REC, posixpath.basename(cache_rec))
    new_parts[cache_def] = build_cache_definition_xml(money_id, records_rid=rec_rid).encode("utf-8")
    new_parts[_rels_path_for(cache_def)] = rec_rels.encode("utf-8")
    new_parts[cache_rec] = build_cache_records_xml().encode("utf-8")
    new_parts[pivot_tbl] = build_pivot_table_xml(cache_id, money_id).encode("utf-8")
    pt_rels, _ = _add_rel(None, _REL_CACHE_DEF, "../pivotCache/" + posixpath.basename(cache_def))
    new_parts[_rels_path_for(pivot_tbl)] = pt_rels.encode("utf-8")

    # --- the Summary sheet points at the pivot table
    sheet_rels, _ = _add_rel(parts.get(summary_rels_path, b"").decode("utf-8") or None, _REL_PIVOT_TABLE,
                             "../pivotTables/" + posixpath.basename(pivot_tbl))
    new_parts[summary_rels_path] = sheet_rels.encode("utf-8")

    # --- the workbook points at the cache
    wb_rels, wb_rid = _add_rel(parts["xl/_rels/workbook.xml.rels"].decode("utf-8"), _REL_CACHE_DEF,
                               "pivotCache/" + posixpath.basename(cache_def))
    new_parts["xl/_rels/workbook.xml.rels"] = wb_rels.encode("utf-8")
    entry = f'<pivotCache cacheId="{cache_id}" xmlns:r="{_NS_R}" r:id="{wb_rid}"/>'
    if "</pivotCaches>" in wb_xml:
        wb_xml = wb_xml.replace("</pivotCaches>", entry + "</pivotCaches>", 1)
    else:
        block = f"<pivotCaches>{entry}</pivotCaches>"
        # CT_Workbook order: ... definedNames, calcPr, oleSize, customWorkbookViews, pivotCaches, ... extLst
        m = (re.search(r"<calcPr\b[^>]*?/>|<calcPr\b[^>]*>.*?</calcPr>", wb_xml, re.S)
             or re.search(r"<definedNames\b[^>]*?/>|<definedNames\b.*?</definedNames>", wb_xml, re.S)
             or re.search(r"</sheets>", wb_xml))
        if not m:
            raise DoohPivotError("workbook.xml has no <sheets>")
        wb_xml = wb_xml[:m.end()] + block + wb_xml[m.end():]
    new_parts["xl/workbook.xml"] = wb_xml.encode("utf-8")

    # --- content types
    ct = parts["[Content_Types].xml"].decode("utf-8")
    overrides = "".join(f'<Override PartName="/{p}" ContentType="{t}"/>' for p, t in (
        (cache_def, _CT_CACHE_DEF), (cache_rec, _CT_CACHE_REC), (pivot_tbl, _CT_PIVOT_TABLE)))
    new_parts["[Content_Types].xml"] = ct.replace("</Types>", overrides + "</Types>", 1).encode("utf-8")

    # --- Screenlist sheet: cached values for the two helper formulas + no "number stored as text" triangles
    list_xml = parts[list_part].decode("utf-8")
    for column, value in ((MAX_AVAILS_COLUMN, SAMPLE_MAX_AVAILS), (MAX_SPEND_COLUMN, SAMPLE_MAX_SPEND)):
        list_xml = _set_cached_value(list_xml, f"{_col_letter(DOOH_ALL_COLUMNS.index(column) + 1)}2", value)
    list_xml = _add_ignored_errors(list_xml, f"A2:{_col_letter(len(DOOH_ALL_COLUMNS))}1048576")
    new_parts[list_part] = list_xml.encode("utf-8")

    # --- write the new package next to the old one, then swap it in
    fd, tmp_name = tempfile.mkstemp(suffix=".xlsx.tmp", dir=str(path.parent))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp_name, "w") as zout:
            for info in infos:                                   # original order ([Content_Types].xml first)
                data = new_parts.get(info.filename, parts[info.filename])
                zout.writestr(info.filename, data, compress_type=compress.get(info.filename, zipfile.ZIP_DEFLATED))
            for name, data in new_parts.items():
                if name not in parts:
                    zout.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return True
