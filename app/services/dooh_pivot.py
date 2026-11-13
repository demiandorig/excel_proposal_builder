"""
DOOH Deliverables — the DOOH Screenlist table and the pre-made DOOH Summary pivot table.

The "DOOH Deliverables" export is two worksheets that are always built together:

  DOOH Screenlist  The inventory the planner pastes in (the platform export's 40 columns, in a fixed order, plus two
                   calculated helper columns) as an Excel TABLE named DOOHScreenlist, shipped with ONE sample row, or
                   with every row of the screen list the planner uploaded in the wizard (see dooh_screenlist.py).
  DOOH Summary     A real, pre-made PIVOT TABLE over that table: Venue Type > Network, with Number of Screens, Max.
                   Avails and Max. Spend. It refreshes when the workbook opens, so pasted rows simply appear in it; with
                   an uploaded list its cache and its rendered cells already hold the real grouped numbers.

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
import math
import os
import time
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

# Max. Spend = Max. Avails * cpm / DOOH_SPEND_DIVISOR = ((four_week_impressions * 0.4) * cpm / 1000): CPM is the
# price per THOUSAND impressions. (The planner first wrote /100, then confirmed /1000.) This constant is the one place
# the divisor lives: the table's calculated-column formula, the cell formulas and the cached sample values are all
# generated from it.
DOOH_SPEND_DIVISOR = 1000

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

# What a cell of each column holds (the upload parser produces exactly this, and the sample row follows it): numbers are
# numbers, the four supports_* flags are real booleans, everything else is TEXT (ids and postal codes stay text so leading
# zeros and long codes are never altered), and an empty cell is None.
DOOH_NUMBER_COLUMNS = ("latitude", "longitude", "cpm", "four_week_impressions", "impression_multiplier",
                       "bidstream_impressions", "bidstream_inferred_impressions", "screen_count", "min_duration",
                       "max_duration")
DOOH_BOOLEAN_COLUMNS = ("supports_video", "supports_banner", "supports_audio", "supports_dynamic_creative")
DOOH_TEXT_COLUMNS = tuple(c for c in DOOH_SCREENLIST_COLUMNS if c not in DOOH_NUMBER_COLUMNS + DOOH_BOOLEAN_COLUMNS)

# The Summary sheet's own instruction says a DOOH proposal needs at least this many screens.
DOOH_MINIMUM_SCREENS = 20


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
SUMMARY_COLUMN_WIDTHS = (52, 34, 20, 20, 20)     # B..F: Venue Type, Network, Number of Screens, Max. Avails, Max. Spend (wide enough for real venue paths)

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


class DoohRowError(ValueError):
    """A screen row the Screenlist table cannot hold: the wrong number of values, or a cell of the wrong type."""


_COL_INDEX = {name: i for i, name in enumerate(DOOH_ALL_COLUMNS)}
_NUMBER_SET, _BOOLEAN_SET = frozenset(DOOH_NUMBER_COLUMNS), frozenset(DOOH_BOOLEAN_COLUMNS)
BLANK_LABEL = "(blank)"          # how a pivot labels an empty item
_LONG_TEXT = 255                 # a text longer than this is flagged longText in the cache (Excel's pivot item limit)


def _is_blank(value) -> bool:
    return value is None or value == ""


def helper_values(row) -> tuple[float, float]:
    """(max_avails, max_spend) of one 40-value screen row — what its two calculated cells hold. The same IFERROR-to-0
    the table formulas have, for a row whose impressions or CPM is not a number."""
    try:
        imps, cpm = row[_COL_INDEX["four_week_impressions"]], row[_COL_INDEX["cpm"]]
        return max_avails_for(imps), max_spend_for(imps, cpm)
    except (TypeError, ValueError):
        return 0.0, 0.0


def _check_row(row, number: int) -> None:
    if len(row) != len(DOOH_SCREENLIST_COLUMNS):
        raise DoohRowError(f"screen {number} has {len(row)} values; the Screenlist has {len(DOOH_SCREENLIST_COLUMNS)} columns")
    for name, value in zip(DOOH_SCREENLIST_COLUMNS, row):
        if _is_blank(value):
            continue
        if name in _NUMBER_SET:
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        elif name in _BOOLEAN_SET:
            ok = isinstance(value, bool)
        else:
            ok = isinstance(value, str)
        if not ok:
            raise DoohRowError(f"screen {number}: the {name} cell holds {value!r}, which a {name} column cannot")


class _Axis:
    """One row field of the pivot: its distinct values as a pivot sees them (text compared case-insensitively, empty is
    its own "(blank)" item), in first-seen order — which is the order of the cache's shared items — plus the A-Z display
    order the pivot field lists them in (empty last)."""

    def __init__(self, values: list):
        self.shared: list = []                       # label of each shared item (None = the empty item)
        self.at_row: list[int] = []                  # each row's shared-item index
        seen: dict = {}
        for value in values:
            key = None if _is_blank(value) else value.casefold()
            index = seen.get(key)
            if index is None:
                index = seen[key] = len(self.shared)
                self.shared.append(None if key is None else value)
            self.at_row.append(index)
        self.order = sorted(range(len(self.shared)),
                            key=lambda i: (self.shared[i] is None, (self.shared[i] or "").casefold(), self.shared[i] or ""))
        self.position = {shared_index: pos for pos, shared_index in enumerate(self.order)}

    def label(self, shared_index: int) -> str:
        return BLANK_LABEL if self.shared[shared_index] is None else self.shared[shared_index]


class PivotModel:
    """Everything the pivot parts are written from, worked out once from the screens: the 42-value rows (the 40 inventory
    values + the two helper values), the two axes, the column types the cache describes, and the layout the pivot renders
    (Venue Type > Network lines, each venue type's subtotal, the grand total).

    `rows` is a list of 40-value typed screen rows (see DOOH_NUMBER_COLUMNS...); None is the one sample row the template
    ships with. The sample goes through exactly the same code, so the shipped template and an uploaded list can't drift."""

    def __init__(self, rows=None):
        if rows is None:
            self.rows = [_all_sample_values()]
        else:
            self.rows = []
            for number, row in enumerate(rows, start=1):
                _check_row(row, number)
                self.rows.append(list(row) + list(helper_values(row)))
            if not self.rows:
                raise DoohRowError("a screen list needs at least one screen")
        self.n = len(self.rows)
        self.axes = {col: _Axis([r[_COL_INDEX[col]] for r in self.rows]) for col in _row_field_columns()}
        self.stats = {name: self._column_stats(_COL_INDEX[name]) for name in DOOH_ALL_COLUMNS if name not in self.axes}
        self.layout = self._layout()

    def _column_stats(self, at: int) -> dict:
        kinds, low, high, all_int, long_text = set(), None, None, True, False
        for row in self.rows:
            value = row[at]
            kind = _kind(value)
            kinds.add(kind)
            if kind == "num":
                low = value if low is None or value < low else low
                high = value if high is None or value > high else high
                all_int = all_int and float(value) == int(float(value))
            elif kind == "str" and len(value) > _LONG_TEXT:
                long_text = True
        return {"kinds": kinds, "min": low, "max": high, "all_int": all_int, "long_text": long_text}

    def _layout(self) -> list[tuple]:
        """[(kind, venue shared index, network shared index or None, (count, avails, spend))] in display order, then the
        grand total as ("grand", None, None, measures). kind is "data" or "subtotal"."""
        venue, network = (self.axes[col] for col, _ in PIVOT_ROW_FIELDS)
        at_id, at_avails, at_spend = (_COL_INDEX[f[0]] for f in PIVOT_DATA_FIELDS)
        groups: dict[int, dict[int, list]] = {}
        for i, row in enumerate(self.rows):
            cell = groups.setdefault(venue.at_row[i], {}).setdefault(network.at_row[i], [0, [], []])
            cell[0] += 0 if _is_blank(row[at_id]) else 1      # Count counts the cells that are not empty
            cell[1].append(row[at_avails])
            cell[2].append(row[at_spend])

        def measures(parts):
            return (sum(p[0] for p in parts), round(math.fsum(v for p in parts for v in p[1]), 6),
                    round(math.fsum(v for p in parts for v in p[2]), 6))

        out: list[tuple] = []
        for v in venue.order:
            nets = sorted(groups[v], key=network.position.__getitem__)
            for n in nets:
                out.append(("data", v, n, measures([groups[v][n]])))
            if PIVOT_SHOW_SUBTOTALS:
                out.append(("subtotal", v, None, measures(list(groups[v].values()))))
        out.append(("grand", None, None, measures([c for g in groups.values() for c in g.values()])))
        return out

    def preview_rows(self) -> list[tuple[str, tuple]]:
        venue, network = (self.axes[col] for col, _ in PIVOT_ROW_FIELDS)
        rows: list[tuple[str, tuple]] = [
            ("header", (PIVOT_ROW_FIELDS[0][1], PIVOT_ROW_FIELDS[1][1]) + tuple(f[2] for f in PIVOT_DATA_FIELDS))]
        previous_venue = None
        for kind, v, n, measures in self.layout:
            if kind == "data":
                rows.append(("data", (venue.label(v) if v != previous_venue else None, network.label(n)) + measures))
                previous_venue = v
            elif kind == "subtotal":
                rows.append(("subtotal", (f"{venue.label(v)} Total", None) + measures))
            else:
                rows.append(("grand", ("Grand Total", None) + measures))
        return rows

    @property
    def ref(self) -> str:
        last_row = PIVOT_FIRST_ROW + len(self.layout)          # the header line + one line per layout entry
        return f"{_col_letter(PIVOT_FIRST_COL)}{PIVOT_FIRST_ROW}:{_col_letter(PIVOT_FIRST_COL + 4)}{last_row}"


def _model_for(data) -> PivotModel:
    return data if isinstance(data, PivotModel) else PivotModel(data)


def pivot_preview_rows(rows=None) -> list[tuple[str, tuple]]:
    """The lines (kind, (B..F values)) a pivot over the screens renders to, in order — the header, each Venue Type >
    Network line, each venue type's subtotal and the grand total (for the one sample row: the header, the sample screen,
    its venue type's subtotal and the grand total). Written into the Summary sheet as plain cells so the sheet is right
    even where the pivot is never refreshed; refreshing it in Excel replaces them with the real thing. `rows`: the typed
    screen rows (None = the sample row)."""
    return _model_for(rows).preview_rows()


def pivot_ref(rows=None) -> str:
    """The A1 range the pivot occupies (B6:F9 for the sample row, with subtotals)."""
    return _model_for(rows).ref


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
    """A double-quoted XML attribute value. A line break or tab inside it is written as a character reference: an XML
    parser turns a literal one into a space (attribute-value normalization), which would change an address or note."""
    return '"' + _xml_escape(str(value), {'"': "&quot;", "\n": "&#10;", "\r": "&#13;", "\t": "&#9;"}) + '"'


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


def _shared_items_xml(stats: dict) -> str:
    """The <sharedItems> of a cache field that is not on an axis: no item list, just what its cells contain (Excel
    stores the values themselves in the records). The types are exactly the ones present, so the declared kinds and the
    records can never disagree."""
    kinds = stats["kinds"]
    blank = "blank" in kinds
    types = kinds - {"blank"}
    if not types:
        return '<sharedItems containsNonDate="0" containsString="0" containsBlank="1"/>'
    if types == {"num"}:
        integer = ' containsInteger="1"' if stats["all_int"] else ""
        bounds = f'minValue="{_num(stats["min"])}" maxValue="{_num(stats["max"])}"'
        if blank:
            return f'<sharedItems containsString="0" containsBlank="1" containsNumber="1"{integer} {bounds}/>'
        return f'<sharedItems containsSemiMixedTypes="0" containsString="0" containsNumber="1"{integer} {bounds}/>'
    if len(types) == 1:                                   # text, or booleans: no item list unless the field is on an axis
        attrs = (' containsBlank="1"' if blank else "") + (' longText="1"' if stats["long_text"] else "")
        return f"<sharedItems{attrs}/>"
    raise DoohRowError(f"a column mixes {', '.join(sorted(types))} cells")


def build_cache_definition_xml(money_fmt_id: int, records_rid: str = "rId1", refreshed_by: str = "Proposal Builder",
                               rows=None) -> str:
    """xl/pivotCache/pivotCacheDefinition1.xml — the cache describes the table's 42 columns, with the screens' values (so
    the pivot is complete even before its first refresh; for the template, the one sample row) and refreshOnLoad so Excel
    rebuilds it from the table every time the workbook is opened. `rows`: the typed screen rows (None = the sample row)."""
    model = _model_for(rows)
    refreshed_date = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime(1899, 12, 30)).total_seconds() / 86400
    fields = []
    for name in DOOH_ALL_COLUMNS:
        axis = model.axes.get(name)
        if axis is not None:
            items = "".join("<m/>" if label is None else f"<s v={_q(label)}/>" for label in axis.shared)
            blank = ' containsBlank="1"' if None in axis.shared else ""
            shared = f'<sharedItems{blank} count="{len(axis.shared)}">{items}</sharedItems>'
        else:
            shared = _shared_items_xml(model.stats[name])
        fields.append(f'<cacheField name={_q(name)} numFmtId="{_field_numfmt(name, money_fmt_id)}">{shared}</cacheField>')
    return (
        _XML_DECL
        + f'<pivotCacheDefinition xmlns="{_NS_MAIN}" xmlns:r="{_NS_R}" r:id={_q(records_rid)} refreshOnLoad="1" '
          f'refreshedBy={_q(refreshed_by)} refreshedDate="{refreshed_date:.9f}" createdVersion="8" refreshedVersion="8" '
          f'minRefreshableVersion="3" recordCount="{model.n}">'
          f'<cacheSource type="worksheet"><worksheetSource name={_q(DOOH_TABLE_NAME)}/></cacheSource>'
          f'<cacheFields count="{len(fields)}">{"".join(fields)}</cacheFields>'
          '<extLst><ext uri="{725AE2AE-9491-48be-B2B4-4EB974FC3084}" '
          'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"><x14:pivotCacheDefinition/></ext></extLst>'
          "</pivotCacheDefinition>"
    )


def build_cache_records_xml(rows=None) -> str:
    """xl/pivotCache/pivotCacheRecords1.xml — one record per screen (for the template, the one sample row)."""
    model = _model_for(rows)
    row_cols = set(model.axes)
    records = []
    for i, row in enumerate(model.rows):
        cells = []
        for name, value in zip(DOOH_ALL_COLUMNS, row):
            if name in row_cols:
                cells.append(f'<x v="{model.axes[name].at_row[i]}"/>')     # index into that field's shared items
                continue
            kind = _kind(value)
            if kind == "blank":
                cells.append("<m/>")
            elif kind == "bool":
                cells.append(f'<b v="{int(value)}"/>')
            elif kind == "num":
                cells.append(f'<n v="{_num(value)}"/>')
            else:
                cells.append(f"<s v={_q(value)}/>")
        records.append(f'<r>{"".join(cells)}</r>')
    return (_XML_DECL + f'<pivotCacheRecords xmlns="{_NS_MAIN}" xmlns:r="{_NS_R}" count="{model.n}">{"".join(records)}</pivotCacheRecords>')


def build_pivot_table_xml(cache_id: int, money_fmt_id: int, rows=None) -> str:
    """xl/pivotTables/pivotTable1.xml — Venue Type > Network in tabular form, three value fields, grand totals on. The
    row/column items and the location are those of the screens' layout (see PivotModel.layout), so the definition matches
    the cells written into the Summary sheet and the cache's records."""
    model = _model_for(rows)
    index = _COL_INDEX
    row_captions = {col: caption for col, caption in PIVOT_ROW_FIELDS}
    data_cols = {f[0] for f in PIVOT_DATA_FIELDS}
    subtotals = PIVOT_SHOW_SUBTOTALS

    no_subtotal_attr = "" if subtotals else ' defaultSubtotal="0"'
    pivot_fields = []
    for name in DOOH_ALL_COLUMNS:
        numfmt = _field_numfmt(name, money_fmt_id)
        if name in row_captions:
            order = model.axes[name].order
            items = "".join(f'<item x="{i}"/>' for i in order) + ('<item t="default"/>' if subtotals else "")
            pivot_fields.append(
                f'<pivotField name={_q(row_captions[name])} axis="axisRow" compact="0" outline="0" showAll="0" '
                f'sortType="ascending"{no_subtotal_attr}><items count="{len(order) + (1 if subtotals else 0)}">{items}</items>'
                f'</pivotField>')   # A-Z, not the pasted order
        else:
            data_attr = ' dataField="1"' if name in data_cols else ""
            fmt = f' numFmtId="{numfmt}"' if numfmt else ""
            pivot_fields.append(f'<pivotField{data_attr} compact="0"{fmt} outline="0" showAll="0"/>')

    row_fields = "".join(f'<field x="{index[col]}"/>' for col, _ in PIVOT_ROW_FIELDS)
    venue, network = (model.axes[col] for col, _ in PIVOT_ROW_FIELDS)

    def x(position: int) -> str:                  # <x/> is item 0
        return "<x/>" if position == 0 else f'<x v="{position}"/>'

    items_xml, previous_venue = [], None
    for kind, v, n, _measures in model.layout:
        if kind == "data":
            # the first line of a venue type names both items; the next ones repeat the venue type (r="1": one leading item shared with the line above)
            items_xml.append(f"<i>{x(venue.position[v])}{x(network.position[n])}</i>" if v != previous_venue
                             else f'<i r="1">{x(network.position[n])}</i>')
            previous_venue = v
        elif kind == "subtotal":
            items_xml.append(f'<i t="default">{x(venue.position[v])}</i>')
        else:
            items_xml.append('<i t="grand"><x/></i>')
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
          f'<location ref={_q(model.ref)} firstHeaderRow="0" firstDataRow="1" firstDataCol="{len(PIVOT_ROW_FIELDS)}"/>'
          f'<pivotFields count="{len(pivot_fields)}">{"".join(pivot_fields)}</pivotFields>'
          f'<rowFields count="{len(PIVOT_ROW_FIELDS)}">{row_fields}</rowFields>'
          f'<rowItems count="{len(items_xml)}">{"".join(items_xml)}</rowItems>'
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


def _fill_cached_values(sheet_xml: str, values: dict[str, list[float]]) -> str:
    """Fill the (empty) cached value of every helper formula cell — openpyxl writes formulas without one, so a viewer
    that doesn't recalculate (Protected View, a preview pane) would show nothing. `values` maps a column letter to that
    column's value per data row (row 2 first). One pass over the sheet, whatever the number of rows; raises
    DoohPivotError unless every cell was found (the sheet is not the one the builder wrote)."""
    letters = "|".join(re.escape(c) for c in values)
    pattern = re.compile(r'(<c r="(%s)(\d+)"[^>]*><f>[^<]*</f>)(?:<v></v>|<v\s*/>)?(</c>)' % letters)
    filled = 0

    def put(m):
        nonlocal filled
        column, row = m.group(2), int(m.group(3))
        column_values = values[column]
        if not 2 <= row < 2 + len(column_values):
            return m.group(0)
        filled += 1
        return f"{m.group(1)}<v>{_num(column_values[row - 2])}</v>{m.group(4)}"

    out = pattern.sub(put, sheet_xml)
    if filled != sum(len(v) for v in values.values()):
        raise DoohPivotError(f"{DOOH_SCREENLIST_SHEET} does not hold the {len(next(iter(values.values())))} rows it should")
    return out


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


def _find_screenlist_table(parts: dict, sheet_part: str) -> tuple[str, str]:
    """(part path, ref) of the Screenlist's table, after checking it is the one the builder writes."""
    for rel in _read_rels(parts, _rels_path_for(sheet_part)):
        if rel["type"] == _REL_TABLE:
            path = _resolve(posixpath.dirname(sheet_part), rel["target"])
            root = ET.fromstring(parts[path])
            if root.get("displayName") == DOOH_TABLE_NAME:
                names = [c.get("name") for c in root.iter(f"{{{_NS_MAIN}}}tableColumn")]
                if names != DOOH_ALL_COLUMNS:
                    raise DoohPivotError(f"table {DOOH_TABLE_NAME} does not have the expected columns: {names}")
                return path, root.get("ref")
    raise DoohPivotError(f"{DOOH_SCREENLIST_SHEET} has no table named {DOOH_TABLE_NAME}")


def add_dooh_pivot(path: Union[str, Path], rows=None) -> bool:
    """Add the DOOH Summary pivot table to the saved workbook at `path`.

    `rows` are the typed screen rows the Screenlist sheet was built from (build_dooh_screenlist(wb, rows=...)); None is
    the template's one sample row. The pivot's cache and its row/column items are written from them, and the helper
    columns' cached values are filled for every one of them, so the workbook is complete before Excel refreshes it. The
    sheet's table must cover exactly header + these rows, or DoohPivotError is raised.

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
    _, table_ref = _find_screenlist_table(parts, list_part)   # validates the source table before anything is written
    model = PivotModel(rows)                                   # raises DoohRowError for a row the table could not hold
    expected_ref = f"A1:{_col_letter(len(DOOH_ALL_COLUMNS))}{model.n + 1}"
    if table_ref != expected_ref:
        raise DoohPivotError(f"table {DOOH_TABLE_NAME} covers {table_ref}, not {expected_ref} (header + {model.n} screens)")

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
    new_parts[cache_def] = build_cache_definition_xml(money_id, records_rid=rec_rid, rows=model).encode("utf-8")
    new_parts[_rels_path_for(cache_def)] = rec_rels.encode("utf-8")
    new_parts[cache_rec] = build_cache_records_xml(model).encode("utf-8")
    new_parts[pivot_tbl] = build_pivot_table_xml(cache_id, money_id, model).encode("utf-8")
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
    list_xml = _fill_cached_values(list_xml, {
        _col_letter(DOOH_ALL_COLUMNS.index(column) + 1): [row[DOOH_ALL_COLUMNS.index(column)] for row in model.rows]
        for column in DOOH_HELPER_COLUMNS})
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
        for attempt in range(5):                  # a momentary lock (antivirus, a sync client) shouldn't lose the pivot
            try:
                os.replace(tmp_name, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.25 * (attempt + 1))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return True
