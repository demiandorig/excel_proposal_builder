"""
The DOOH screen list the planner uploads on the DOOH line (Step 05): reading the file, the numbers it gives the line, and
the server-side store it lives in.

WHAT IT IS. The platform's inventory export (a .csv or .xlsx with the 40 columns of dooh_pivot.DOOH_SCREENLIST_COLUMNS).
Reading it gives the typed rows the export's DOOH Screenlist table is built from (dooh_pivot / excel_template), and two
totals for the line's Max Recommended Monthly Imps / Spend: the SUM of every screen's max_avails and max_spend, from
dooh_pivot.max_avails_for / max_spend_for — the very functions that write the cached values of the export's helper
columns, so the wizard, the table and the pivot can never disagree. The totals are the four-week figures as they are:
they are not scaled to the flight length.

WHERE IT LIVES. Not in the proposal's JSON: a list is thousands of rows x 40 columns, and the wizard autosaves that JSON.
The parsed rows are stored once in the `dooh_screenlists` table under an id that is the SHA-256 of the rows themselves, so
a stored list can never change (the same file uploaded twice is one row, and a duplicated proposal can share the id). The
proposal keeps only a small reference (id, file name, counts, totals) in the line's avails entry — see clean_reference — and
/api/generate loads the rows by that id.

READING IS TOLERANT, never guessing: headers match without regard to case, spaces, underscores, dashes or a BOM; columns
it doesn't know are left out; the header may sit a few rows down; a row whose impressions or CPM is not a number is skipped
and counted (with the first few row numbers), never silently kept. A comma is read as a thousands separator only where it
looks like one ("1,234"), so "32,5" is never turned into 325.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import itertools
import json
import logging
import math
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Iterator, Optional

import psycopg

from app.db import get_connection
from app.services import dooh_pivot as dp

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limits. Measured on this code with synthetic exports of the real shape (40 columns, ~400 bytes a row): 15,000 screens is a
# 6.3 MB .csv / 3.3 MB .xlsx, read in 0.5 s (.csv) / 3.5 s (.xlsx); the workbook for it builds in about 8 s using ~0.4 GB and is
# 5.5 MB (the DOOH Screenlist sheet ~39 MB of XML, the pivot cache ~11 MB, both zip ~10:1). 25,000 screens take 12 s and ~0.75 GB, which
# is where one Autoscale instance serving other planners at the same time gets uncomfortable, so the cap is below that. A bigger list
# is a market-filtered export away; the message says so.
# ---------------------------------------------------------------------------
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_SCREENS = 15_000
MAX_UNZIPPED_BYTES = 250 * 1024 * 1024        # an .xlsx is a zip: refuse one that would unpack to something absurd
MAX_ROWS_SCANNED = MAX_SCREENS + 10_000       # rows looked at, good or bad (a sheet of junk rows must not run for ever)
MAX_BLANK_RUN = 5_000                         # this many empty rows in a row = the data is over (Excel sheets often claim a million rows)
HEADER_SEARCH_ROWS = 25                       # the header row is looked for in the first few rows (a title line above it is fine)
MAX_EXAMPLES = 5                              # skipped rows named to the planner
MAX_TEXT = 32_767                             # Excel's limit for the text of one cell
MAX_AXIS_TEXT = 255                           # ... and for a pivot item (Venue Type / Network)
MAX_FILENAME = 120

# Columns the file must have. publisher is what the DOOH Summary shows as "Network" (the sample row: publisher GSTV, network
# GSTV Fast); id is what its Number of Screens counts.
REQUIRED_COLUMNS = ("four_week_impressions", "cpm", "id", "venue_type", "publisher")
_REQUIRED_LABEL = {"publisher": "publisher (the DOOH Summary's Network)", "id": "id (what Number of Screens counts)"}


class ScreenlistError(ValueError):
    """The file can't be used; the message is written for the planner."""


# ---------------------------------------------------------------------------
# Header matching
# ---------------------------------------------------------------------------

def _norm(header) -> str:
    """'Four Week-Impressions' / 'four_week_impressions' / '﻿four week impressions' -> 'fourweekimpressions'."""
    return re.sub(r"[^a-z0-9]", "", str(header if header is not None else "").lower())


_CANON = {_norm(c): c for c in dp.DOOH_SCREENLIST_COLUMNS}
assert len(_CANON) == len(dp.DOOH_SCREENLIST_COLUMNS)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")   # not allowed in an Excel cell (or in XML)


def _empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _blank_row(row) -> bool:
    return all(_empty(c) for c in row)


# ---------------------------------------------------------------------------
# Cell values
# ---------------------------------------------------------------------------

class _BadCell(ValueError):
    pass


_NUMBER = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_US_THOUSANDS = re.compile(r"[+-]?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?")
_EU_THOUSANDS = re.compile(r"[+-]?[0-9]{1,3}(?:\.[0-9]{3})+,[0-9]+")
_EU_DECIMAL = re.compile(r"[+-]?[0-9]+,[0-9]+")


def _number(value, decimal_comma: bool = False):
    """A cell as a number (an int when it is a whole number), None when empty; _BadCell when it is not a number."""
    if _empty(value):
        return None
    if isinstance(value, bool):
        raise _BadCell
    if isinstance(value, (int, float)):
        number = value
    elif isinstance(value, str):
        text = value.strip().replace(" ", "").replace(" ", "")
        if text.startswith("$"):
            text = text[1:]
        elif text[:2] in ("-$", "+$"):
            text = text[0] + text[2:]
        if "," in text:
            if decimal_comma and _EU_THOUSANDS.fullmatch(text):
                text = text.replace(".", "").replace(",", ".")
            elif decimal_comma and _EU_DECIMAL.fullmatch(text):
                text = text.replace(",", ".")
            elif _US_THOUSANDS.fullmatch(text):
                text = text.replace(",", "")
            else:
                raise _BadCell
        if not _NUMBER.fullmatch(text):
            raise _BadCell
        number = float(text)
    else:
        raise _BadCell
    if not math.isfinite(number):
        raise _BadCell
    return int(number) if float(number) == int(number) and abs(number) < 1e15 else float(number)


_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0"}


def _boolean(value):
    if _empty(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE:
            return True
        if word in _FALSE:
            return False
    raise _BadCell


def _text(value, limit: int = MAX_TEXT):
    """A cell as text (None when empty). A number in an Excel cell becomes its plain digits (80216.0 -> '80216')."""
    if _empty(value):
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if math.isfinite(value) and value == int(value) and abs(value) < 1e15 else repr(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = _CONTROL.sub("", str(value)).strip()
    return text[:limit] or None


def _shown(value) -> str:
    """A cell as it is quoted back to the planner in a message: short, one line, no markup characters mattering (the page
    escapes it anyway)."""
    text = _CONTROL.sub("", " ".join(str(value).split()))
    return text if len(text) <= 24 else text[:23] + "…"


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------

@dataclass
class ParsedScreenlist:
    filename: str
    rows: list                                   # typed 40-value screen rows (dooh_pivot.DOOH_SCREENLIST_COLUMNS order)
    skipped: int = 0
    skipped_examples: list = field(default_factory=list)       # ["row 12: cpm is empty", ...] (the first few)
    blanked_cells: int = 0                       # cells of OPTIONAL columns that weren't what the column holds, left empty
    ignored_columns: list = field(default_factory=list)        # header names this tool doesn't use
    sheet: Optional[str] = None                  # the worksheet read (.xlsx)
    total_impressions: float = 0.0               # SUM of max_avails over the screens
    total_spend: float = 0.0                     # SUM of max_spend over the screens

    @property
    def screens(self) -> int:
        return len(self.rows)


def _decode_csv(raw: bytes) -> str:
    """Excel on Windows saves "CSV" as cp1252 unless asked for UTF-8; accept both (a BOM is tolerated)."""
    text = raw.replace(b"\x00", b"")
    try:
        return text.decode("utf-8-sig")
    except UnicodeDecodeError:
        return text.decode("cp1252", errors="replace")


def _header_score(row) -> int:
    return sum(1 for cell in row if _norm(cell) in _CANON)


def _sniff_delimiter(text: str) -> str:
    """The delimiter whose reading of the first rows looks most like the platform's header (comma, semicolon, tab or pipe);
    a file with no recognizable header gets the one most used on its first line."""
    best, best_score = ",", 0
    lines = [ln for ln in text[:200_000].splitlines() if ln.strip()][:HEADER_SEARCH_ROWS]
    for delimiter in (",", ";", "\t", "|"):
        score = 0
        for row in csv.reader(lines, delimiter=delimiter):
            score = max(score, _header_score(row))
        if score > best_score:
            best, best_score = delimiter, score
    if best_score == 0 and lines:
        counts = {d: lines[0].count(d) for d in (",", ";", "\t", "|")}
        best = max(counts, key=counts.get) if max(counts.values()) else ","
    return best


def _csv_rows(data: bytes) -> tuple[Iterator[list], bool]:
    text = _decode_csv(data)
    delimiter = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)

    def rows() -> Iterator[list]:
        try:
            yield from reader
        except csv.Error as exc:
            raise ScreenlistError(f"Couldn't read this file as a CSV (near row {reader.line_num}): {exc}. "
                                  "Save it from the platform or Excel as 'CSV UTF-8' and try again.") from exc

    return rows(), delimiter == ";"


_SCREENLIST_SHEET = re.compile(r"screen\s*_?\s*list", re.IGNORECASE)


def _xlsx_rows(data: bytes) -> tuple[Iterator[list], str]:
    import openpyxl
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        if sum(i.file_size for i in archive.infolist()) > MAX_UNZIPPED_BYTES:
            raise ScreenlistError("This Excel file unpacks to far more data than a screen list should hold. "
                                  "Save just the screens as a new .xlsx or .csv and try again.")
        archive.close()
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except ScreenlistError:
        raise
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types for a damaged / protected / not-an-xlsx file
        log.info("screen list: could not open the workbook (%s)", type(exc).__name__)
        raise ScreenlistError("Couldn't open this file as an Excel workbook. It may be damaged or password-protected. "
                              "Open it in Excel, save it as a regular .xlsx (or as a CSV) and try again.") from exc
    sheets = workbook.worksheets
    if not sheets:
        raise ScreenlistError("This Excel file has no worksheets.")
    sheet = next((s for s in sheets if _SCREENLIST_SHEET.search(s.title)), sheets[0])

    def rows() -> Iterator[list]:
        try:
            for row in sheet.iter_rows(values_only=True):
                yield list(row)
        except Exception as exc:  # noqa: BLE001
            raise ScreenlistError(f"Couldn't read the sheet '{sheet.title}' of this Excel file. "
                                  "Save it as a new .xlsx (or a CSV) and try again.") from exc
        finally:
            workbook.close()

    return rows(), sheet.title


def _kind_of(filename: str, data: bytes) -> str:
    """'xlsx' | 'csv'; raises ScreenlistError for what can't be read (.xls, other formats)."""
    name = (filename or "").lower()
    if name.endswith(".xls") or data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ScreenlistError("That is an old Excel file (.xls) or a protected workbook. Open it in Excel and use "
                              "File > Save As > Excel Workbook (.xlsx) or CSV UTF-8 (.csv), then upload the new file.")
    if data[:2] == b"PK":
        return "xlsx"
    if name.endswith((".csv", ".txt", ".tsv")):
        return "csv"
    if name.endswith((".xlsx", ".xlsm")):
        raise ScreenlistError("This file doesn't look like a real .xlsx workbook. Open it in Excel, save it again as "
                              ".xlsx (or CSV) and try again.")
    raise ScreenlistError("Upload the platform's inventory export as a .csv or .xlsx file.")


def _clean_filename(filename: str) -> str:
    name = os.path.basename((filename or "").replace("\\", "/"))
    return _CONTROL.sub("", name).strip()[:MAX_FILENAME] or "screen list"


def parse_upload(filename: str, data: bytes) -> ParsedScreenlist:
    """Read an uploaded inventory export. Raises ScreenlistError (plain language) when it can't be used; otherwise returns
    the typed rows, what was skipped and the two totals."""
    if not data:
        raise ScreenlistError("That file is empty.")
    kind = _kind_of(filename, data)
    sheet = None
    if kind == "csv":
        source, decimal_comma = _csv_rows(data)
    else:
        source, sheet = _xlsx_rows(data)
        decimal_comma = False
    parsed = _read(source, decimal_comma)
    parsed.filename, parsed.sheet = _clean_filename(filename), sheet
    return parsed


def _read(source: Iterable[list], decimal_comma: bool) -> ParsedScreenlist:
    numbered = enumerate(source, start=1)

    # --- the header: the best-matching row of the first few (a title line above it is fine)
    head: list[tuple[int, list]] = []
    filled = 0
    for item in numbered:
        head.append(item)
        filled += 0 if _blank_row(item[1]) else 1
        if filled >= HEADER_SEARCH_ROWS:
            break
    candidates = [(i, _header_score(row)) for i, (_, row) in enumerate(head) if not _blank_row(row)]
    if not candidates:
        raise ScreenlistError("That file is empty.")
    best = max(score for _, score in candidates)
    at = next(i for i, score in candidates if score == best)          # the first best-matching row (the first filled row when none match)
    header_number, header = head[at]
    positions: dict[str, int] = {}
    ignored: list[str] = []
    for index, name in enumerate(header):
        canonical = _CANON.get(_norm(name))
        if canonical and canonical not in positions:
            positions[canonical] = index
        elif not _empty(name):
            ignored.append(_shown(name))
    missing = [c for c in REQUIRED_COLUMNS if c not in positions]
    if missing:
        found = ", ".join(_shown(h) for h in header if not _empty(h))[:300]
        raise ScreenlistError(
            "This file is missing columns the DOOH Summary needs: " + ", ".join(_REQUIRED_LABEL.get(c, c) for c in missing)
            + ". Upload the platform's inventory export with its column names unchanged (extra columns are fine)."
            + (f" The columns I found: {found}." if found else ""))

    column_at = [positions.get(c) for c in dp.DOOH_SCREENLIST_COLUMNS]
    imps_i, cpm_i = (dp.DOOH_SCREENLIST_COLUMNS.index(c) for c in ("four_week_impressions", "cpm"))
    axis_i = {dp.DOOH_SCREENLIST_COLUMNS.index(c) for c in ("venue_type", "publisher")}

    out = ParsedScreenlist(filename="", rows=[], ignored_columns=ignored)

    def skip(number: int, why: str, how_many: int = 1) -> None:
        out.skipped += how_many
        if len(out.skipped_examples) < MAX_EXAMPLES:
            out.skipped_examples.append(f"row {number}: {why}")

    gap_first, gap_size = 0, 0       # empty rows since the last screen: a gap inside the data is skipped rows, but empty rows at the end are not
    scanned = 0
    for number, row in itertools.chain(head[at + 1:], numbered):
        scanned += 1
        if scanned > MAX_ROWS_SCANNED:
            raise ScreenlistError(f"This file has more than {MAX_ROWS_SCANNED:,} rows. A DOOH screen list is limited to "
                                  f"{MAX_SCREENS:,} screens; filter it (for example to the markets in this proposal) and upload it again.")
        if _blank_row(row):
            if gap_size == 0:
                gap_first = number
            gap_size += 1
            if gap_size > MAX_BLANK_RUN:        # a sheet that claims a million rows of nothing: the data is over
                break
            continue
        if gap_size:
            skip(gap_first, "empty row" if gap_size == 1 else f"{gap_size} empty rows", gap_size)
            gap_size = 0
        screen: list = []
        problem = None
        for i, column in enumerate(dp.DOOH_SCREENLIST_COLUMNS):
            at_col = column_at[i]
            raw = row[at_col] if at_col is not None and at_col < len(row) else None
            try:
                if column in dp.DOOH_NUMBER_COLUMNS:
                    value = _number(raw, decimal_comma)
                    if value is not None and value < 0 and i in (imps_i, cpm_i):
                        problem = f"{column} is negative ({_shown(raw)})"
                        break
                elif column in dp.DOOH_BOOLEAN_COLUMNS:
                    value = _boolean(raw)
                else:
                    value = _text(raw, MAX_AXIS_TEXT if i in axis_i else MAX_TEXT)
            except _BadCell:
                if i in (imps_i, cpm_i):
                    problem = f"{column} is not a number ({_shown(raw)})"
                    break
                out.blanked_cells += 1
                value = None
            if value is None and i in (imps_i, cpm_i):
                problem = f"{column} is empty"
                break
            screen.append(value)
        if problem:
            skip(number, problem)
            continue
        out.rows.append(screen)
        if len(out.rows) > MAX_SCREENS:
            raise ScreenlistError(f"This file has more than {MAX_SCREENS:,} screens, which is the most a proposal can carry. "
                                  "Filter it (for example to the markets in this proposal) and upload it again.")

    if not out.rows:
        detail = f" Skipped: {'; '.join(out.skipped_examples)}." if out.skipped_examples else ""
        raise ScreenlistError(f"I couldn't find a usable screen in this file. Each screen needs a number in "
                              f"four_week_impressions and cpm.{detail}")

    # the totals: the same per-row math that fills the export's max_avails / max_spend columns
    values = [dp.helper_values(r) for r in out.rows]
    out.total_impressions = round(math.fsum(v[0] for v in values), 6)
    out.total_spend = round(math.fsum(v[1] for v in values), 6)
    return out


# ---------------------------------------------------------------------------
# The reference kept in the proposal, and what the browser is shown
# ---------------------------------------------------------------------------

_ID = re.compile(r"[0-9a-f]{64}")


def content_id(rows: list) -> str:
    """SHA-256 of the rows (canonically serialized): the same screens always have the same id, different screens never do."""
    blob = json.dumps(rows, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def reference_for(parsed: ParsedScreenlist, list_id: str) -> dict:
    """The small, bounded record the proposal keeps in the line's avails entry (never the rows)."""
    return {
        "id": list_id,
        "filename": parsed.filename,
        "screens": parsed.screens,
        "skipped": parsed.skipped,
        "skipped_examples": list(parsed.skipped_examples[:MAX_EXAMPLES]),
        "total_impressions": round(parsed.total_impressions, 2),
        "total_spend": round(parsed.total_spend, 2),
        "minimum_screens": dp.DOOH_MINIMUM_SCREENS,
    }


def clean_reference(value) -> Optional[dict]:
    """A reference as the proposal's JSON may carry it, or None. Whatever arrives from the browser is rebuilt field by field
    (a stored proposal is shown to the planner on reopen and its id reaches the database), so a malformed, oversized or
    tampered value is dropped rather than trusted."""
    if not isinstance(value, dict):
        return None
    list_id = value.get("id")
    if not isinstance(list_id, str) or not _ID.fullmatch(list_id):
        return None

    def count(key: str) -> int:
        n = value.get(key)
        return n if isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 10_000_000 else 0

    def money(key: str) -> float:
        n = value.get(key)
        ok = isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) and 0 <= n <= 1e15
        return round(float(n), 2) if ok else 0.0

    examples = value.get("skipped_examples")
    examples = [_CONTROL.sub("", e)[:160] for e in examples[:MAX_EXAMPLES] if isinstance(e, str)] if isinstance(examples, list) else []
    return {
        "id": list_id,
        "filename": _clean_filename(value.get("filename") if isinstance(value.get("filename"), str) else ""),
        "screens": count("screens"),
        "skipped": count("skipped"),
        "skipped_examples": examples,
        "total_impressions": money("total_impressions"),
        "total_spend": money("total_spend"),
        "minimum_screens": count("minimum_screens") or dp.DOOH_MINIMUM_SCREENS,
    }


def upload_response(parsed: ParsedScreenlist, list_id: str) -> dict:
    """What POST /api/dooh/screenlist answers: the reference to keep, plus what is worth telling the planner once."""
    return {
        "screenlist": reference_for(parsed, list_id),
        "sheet": parsed.sheet,
        "ignored_columns": parsed.ignored_columns[:8],
        "ignored_column_count": len(parsed.ignored_columns),
        "blanked_cells": parsed.blanked_cells,
    }


# ---------------------------------------------------------------------------
# The store (table dooh_screenlists, schema.sql / migrations/0008)
# ---------------------------------------------------------------------------

def save(parsed: ParsedScreenlist, created_by: Optional[str] = None) -> str:
    """Store the rows (gzip-compressed JSON) and return their id. Idempotent: the same rows are one row of the table, and an
    existing row is never changed. Raises on a database problem — the caller tells the planner to try again."""
    rows = parsed.rows
    list_id = content_id(rows)
    blob = gzip.compress(json.dumps(rows, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8"), 6)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO dooh_screenlists (id, filename, screens, content, size_bytes, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (list_id, parsed.filename, parsed.screens, blob, len(blob), created_by),
        )
    return list_id


def load_rows(list_id: str) -> Optional[list]:
    """The typed rows stored under `list_id`, or None when there is no such list (or the table isn't there yet). A database
    that can't be reached raises."""
    if not isinstance(list_id, str) or not _ID.fullmatch(list_id):
        return None
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT content FROM dooh_screenlists WHERE id = %s", (list_id,)).fetchone()
    except psycopg.errors.UndefinedTable:
        log.warning("dooh_screenlists table is missing - apply schema.sql")
        return None
    if not row or row.get("content") is None:
        return None
    rows = json.loads(gzip.decompress(bytes(row["content"])).decode("utf-8"))
    return rows if isinstance(rows, list) else None
