"""The DOOH screen list upload: reading the file (CSV / Excel, tolerant headers, bad rows counted), the totals (the export's own max_avails /
max_spend math), the limits, the small reference the proposal keeps, the route (login, size, errors), the store, and how /api/generate hands
the rows to the export. The workbook the rows produce is checked in test_dooh_screenlist_export.py."""
from __future__ import annotations

import gzip
import io
import json
import math
import os
import zipfile
from types import SimpleNamespace
from urllib.parse import quote

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import pytest

from app import main
from app.services import dooh_pivot as dp
from app.services import dooh_screenlist as ds
from tests import dooh_support as sup


def parse(data, name="list.csv"):
    return ds.parse_upload(name, data if isinstance(data, bytes) else data.encode("utf-8"))


def expected_totals(rows):
    i, c = dp.DOOH_SCREENLIST_COLUMNS.index("four_week_impressions"), dp.DOOH_SCREENLIST_COLUMNS.index("cpm")
    return (math.fsum(dp.max_avails_for(r[i]) for r in rows), math.fsum(dp.max_spend_for(r[i], r[c]) for r in rows))


# --- reading --------------------------------------------------------------------------------------------------------

def test_a_csv_with_a_bom_is_read_into_typed_rows_exactly_like_the_sample_row():
    rows = sup.screens(30)
    parsed = parse(sup.csv_bytes(rows))
    assert parsed.rows == rows                                   # ids / postal codes text (leading zeros kept), numbers numbers, real booleans, None for empty
    assert parsed.skipped == 0 and parsed.screens == 30
    first = dict(zip(dp.DOOH_SCREENLIST_COLUMNS, parsed.rows[0]))
    assert isinstance(first["postal_code"], str) and isinstance(first["id"], str) and first["supports_video"] in (True, False)
    # and the platform's own sample row (the one in the template) survives the round trip through a file
    sample = [[None if v == "" else v for v in dp.DOOH_SAMPLE_ROW]]
    assert parse(sup.csv_bytes(sample)).rows == sample


def test_the_totals_are_the_sums_of_the_exports_own_max_avails_and_max_spend_math():
    rows = sup.screens(200, seed=7)
    parsed = parse(sup.csv_bytes(rows))
    avails, spend = expected_totals(rows)
    assert parsed.total_impressions == pytest.approx(avails, abs=1e-5) and parsed.total_spend == pytest.approx(spend, abs=1e-5)
    # the same numbers the helper columns of the exported table cache, row by row
    assert parsed.total_impressions == pytest.approx(math.fsum(dp.helper_values(r)[0] for r in parsed.rows), abs=1e-6)
    sample = parse(sup.csv_bytes([[None if v == "" else v for v in dp.DOOH_SAMPLE_ROW]]))
    assert sample.total_impressions == pytest.approx(941.2) and sample.total_spend == pytest.approx(30.589)


@pytest.mark.parametrize("delimiter", [";", "\t", "|"])
def test_semicolon_tab_and_pipe_separated_files_are_read_too(delimiter):
    rows = sup.screens(12)
    assert parse(sup.csv_bytes(rows, delimiter=delimiter)).rows == rows


def test_a_semicolon_file_may_use_decimal_commas_but_a_comma_file_never_turns_32_5_into_325():
    rows = sup.screens(3)
    text = sup.csv_text(rows, delimiter=";").replace(str(rows[0][20]), "32,5", 1)
    parsed = parse(text)
    assert parsed.rows[0][20] == 32.5
    comma = sup.csv_text(rows).replace(f",{rows[0][20]},", ',"32,5",', 1)         # a quoted 32,5 in a comma file: not a thousands separator
    parsed = parse(comma)
    assert parsed.screens == 2 and parsed.skipped == 1 and "cpm is not a number" in parsed.skipped_examples[0]
    thousands = sup.csv_text(rows).replace(f",{rows[0][22]},", f',"{rows[0][22]:,}",', 1)   # "12,345" IS one
    assert parse(thousands).rows[0][22] == rows[0][22]


def test_headers_match_without_regard_to_case_spaces_underscores_dashes_or_a_bom():
    rows = sup.screens(5)
    variants = {"four_week_impressions": "Four Week Impressions", "cpm": "CPM", "venue_type": "Venue-Type", "publisher": "  PUBLISHER ", "id": "ID"}
    headers = [variants.get(c, c) for c in dp.DOOH_SCREENLIST_COLUMNS]
    assert parse(sup.csv_bytes(rows, headers=headers)).rows == rows
    assert parse(sup.csv_bytes(rows, bom=True)).rows == rows and parse(sup.csv_bytes(rows, bom=False)).rows == rows
    # a file saved as cp1252 (Excel on Windows) with the BOM mangled into "ï»¿" still finds its first column
    mangled = sup.csv_text(rows, bom=False).replace("id,", "ï»¿id,", 1).encode("cp1252")
    assert parse(mangled).rows == rows
    cp1252 = sup.csv_text([[*r[:11], "Cañón City", *r[12:]] for r in rows], bom=False).encode("cp1252")
    assert parse(cp1252).rows[0][11] == "Cañón City"


def test_columns_the_tool_does_not_use_are_left_out_and_never_change_the_totals():
    rows = sup.screens(10)
    plain = parse(sup.csv_bytes(rows))
    extra = parse(sup.csv_bytes(rows, extra=[("max_avails", "999"), ("max_spend", "999"), ("Internal note", "x"), ("", "y")]))
    assert extra.rows == plain.rows and extra.total_impressions == plain.total_impressions and extra.total_spend == plain.total_spend
    assert extra.ignored_columns == ["max_avails", "max_spend", "Internal note"]       # an unnamed column is nothing to report


def test_an_excel_file_gives_the_same_rows_and_prefers_a_sheet_named_screenlist():
    import openpyxl
    rows = sup.screens(25)
    from_csv = parse(sup.csv_bytes(rows))
    from_xlsx = parse(sup.xlsx_bytes(rows), "list.xlsx")
    assert from_xlsx.rows == from_csv.rows and from_xlsx.sheet == "DOOH Screenlist"
    assert from_xlsx.total_impressions == from_csv.total_impressions
    wb = openpyxl.Workbook()
    wb.active.title = "Notes"
    wb.active.append(["not the inventory"])
    ws = wb.create_sheet("Screen list (final)")
    ws.append(dp.DOOH_SCREENLIST_COLUMNS)
    [ws.append(r) for r in rows]
    out = io.BytesIO()
    wb.save(out)
    assert parse(out.getvalue(), "multi.xlsx").sheet == "Screen list (final)"
    # no such sheet: the first one is read
    assert parse(sup.xlsx_bytes(rows, sheet="Export"), "x.xlsx").sheet == "Export"


def test_the_workbook_this_tool_exports_can_be_uploaded_back(tmp_path):
    from openpyxl import Workbook
    from app import excel_template as et
    rows = sup.screens(15)
    wb = Workbook()
    wb.remove(wb.active)
    et.build_dooh_summary(wb, rows=rows)
    et.build_dooh_screenlist(wb, rows=rows)
    path = tmp_path / "export.xlsx"
    wb.save(path)
    dp.add_dooh_pivot(path, rows=rows)
    parsed = parse(path.read_bytes(), "export.xlsx")
    assert parsed.rows == rows and parsed.sheet == "DOOH Screenlist" and parsed.ignored_columns == ["max_avails", "max_spend"]


def test_a_title_line_above_the_header_is_fine_and_row_numbers_are_the_sheets_own():
    rows = sup.screens(6)
    rows[2][22] = "n/a"
    parsed = parse(sup.xlsx_bytes(rows, title_rows=2), "x.xlsx")
    assert parsed.screens == 5 and parsed.skipped_examples == ["row 6: four_week_impressions is not a number (n/a)"]      # 2 title rows + header + 3rd screen = row 6


def test_missing_columns_are_named_in_plain_words_with_what_was_found():
    with pytest.raises(ds.ScreenlistError) as err:
        parse("name,city\nA,B\n")
    text = str(err.value)
    for needed in ("four_week_impressions", "cpm", "id", "venue_type", "publisher"):
        assert needed in text
    assert "name, city" in text and "column names unchanged" in text
    rows = sup.screens(3)
    only_cpm = [c for c in dp.DOOH_SCREENLIST_COLUMNS if c != "cpm"]
    with pytest.raises(ds.ScreenlistError, match="missing columns.*cpm"):
        parse(sup.csv_bytes([[v for c, v in zip(dp.DOOH_SCREENLIST_COLUMNS, r) if c != "cpm"] for r in rows], headers=only_cpm))


def test_rows_that_cannot_be_used_are_skipped_counted_and_the_first_few_named():
    rows = sup.screens(12)
    rows[0][22] = "lots"                     # not a number
    rows[1][20] = None                       # no CPM
    rows[2][22] = -5                         # negative
    rows[3][20] = float("inf")               # not finite
    text = sup.csv_text(rows).splitlines()
    text.insert(5, ",,,,,,")                 # an empty row between screens
    text += ["", ",,,", ""]                  # empty rows at the end are not skipped rows
    parsed = parse("\r\n".join(text))
    assert parsed.screens == 8 and parsed.skipped == 5               # 4 bad + the one empty row in the middle
    assert parsed.skipped_examples[:2] == ["row 2: four_week_impressions is not a number (lots)", "row 3: cpm is empty"]
    assert len(parsed.skipped_examples) <= ds.MAX_EXAMPLES
    assert parsed.total_impressions == pytest.approx(expected_totals(parsed.rows)[0], abs=1e-5)       # skipped rows are in no total
    many = sup.screens(30)
    for r in many[:20]:
        r[22] = "x"
    parsed = parse(sup.csv_bytes(many))
    assert parsed.skipped == 20 and len(parsed.skipped_examples) == ds.MAX_EXAMPLES


def test_a_gap_of_empty_rows_inside_the_data_counts_once_each_and_a_file_of_nothing_usable_says_so():
    rows = sup.screens(4)
    lines = sup.csv_text(rows).splitlines()
    lines[2:2] = ["", "", ""]
    parsed = parse("\r\n".join(lines))
    assert parsed.skipped == 3 and parsed.skipped_examples == ["row 3: 3 empty rows"]
    junk = [r[:20] + ["?"] + r[21:] for r in sup.screens(3)]
    with pytest.raises(ds.ScreenlistError, match="couldn't find a usable screen"):
        parse(sup.csv_bytes(junk))
    with pytest.raises(ds.ScreenlistError, match="empty"):
        parse(b"")
    with pytest.raises(ds.ScreenlistError, match="empty"):
        parse(",,\n,,\n")


def test_other_columns_that_hold_the_wrong_kind_of_value_are_left_empty_and_counted_not_trusted():
    rows = sup.screens(3)
    text = sup.csv_text(rows).replace(f",{rows[0][17]},", ",not-a-latitude,", 1).replace("TRUE", "maybe", 1)
    parsed = parse(text)
    assert parsed.screens == 3 and parsed.blanked_cells == 2
    first = dict(zip(dp.DOOH_SCREENLIST_COLUMNS, parsed.rows[0]))
    assert first["latitude"] is None
    for word, value in (("yes", True), ("false", False), ("T", True), ("0", False), ("1", True), ("N", False)):
        got = parse(sup.csv_text(rows[:1]).replace("TRUE", word, 1)).rows[0][dp.DOOH_SCREENLIST_COLUMNS.index("supports_video")]
        assert got is value or (word == "false" and got is False), (word, got)


def test_text_is_cleaned_numbers_in_text_columns_become_plain_digits_and_long_text_is_cut():
    import openpyxl
    rows = sup.screens(2)
    rows[0][9] = "V" * 400                                      # a venue type: a pivot item may not be longer than 255
    rows[0][19] = "A" * 40000                                   # an address: Excel's cell limit is 32,767
    rows[1][12] = 2134.0                                        # a postal code Excel stored as a number
    rows[1][0] = 1348310                                        # an id Excel stored as a number
    parsed = parse(sup.xlsx_bytes(rows), "x.xlsx")
    assert len(parsed.rows[0][9]) == ds.MAX_AXIS_TEXT and len(parsed.rows[0][19]) == ds.MAX_TEXT
    assert parsed.rows[1][12] == "2134" and parsed.rows[1][0] == "1348310"
    # control characters are not allowed in an Excel cell (or in XML): a stray BEL / vertical tab in a CSV is dropped
    csv_parsed = parse(sup.csv_text(sup.screens(1), bom=False).replace("live", "live\x07\x0b", 1))
    assert csv_parsed.rows[0][4] == "live"


def test_an_old_xls_a_damaged_workbook_and_a_foreign_file_get_a_plain_message():
    with pytest.raises(ds.ScreenlistError, match=r"\.xlsx"):
        parse(b"whatever", "old.xls")
    with pytest.raises(ds.ScreenlistError, match="real .xlsx"):
        parse(b"not a zip at all", "broken.xlsx")
    broken = io.BytesIO()
    with zipfile.ZipFile(broken, "w") as z:
        z.writestr("hello.txt", "x")
    with pytest.raises(ds.ScreenlistError, match="Couldn't open this file as an Excel workbook"):
        parse(broken.getvalue(), "fake.xlsx")
    with pytest.raises(ds.ScreenlistError, match="protected workbook"):
        parse(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"x" * 100, "locked.xlsx")
    with pytest.raises(ds.ScreenlistError, match=r"\.csv or \.xlsx"):
        parse(b"%PDF-1.4 ...", "list.pdf")
    # a CSV named .xlsx / an xlsx named .csv are read for what they are
    rows = sup.screens(4)
    assert parse(sup.xlsx_bytes(rows), "oops.csv").rows == rows


# --- limits -----------------------------------------------------------------------------------------------------------

def test_a_list_over_the_screen_limit_is_refused_with_the_number_in_the_message(monkeypatch):
    monkeypatch.setattr(ds, "MAX_SCREENS", 20)
    monkeypatch.setattr(ds, "MAX_ROWS_SCANNED", 30)
    assert parse(sup.csv_bytes(sup.screens(20))).screens == 20
    with pytest.raises(ds.ScreenlistError, match="more than 20 screens"):
        parse(sup.csv_bytes(sup.screens(21)))
    bad = sup.screens(40)
    for r in bad:
        r[22] = "x"                                                  # junk rows count against the scan limit too
    with pytest.raises(ds.ScreenlistError, match="more than 30 rows"):
        parse(sup.csv_bytes(bad))


def test_an_excel_file_that_unpacks_to_far_too_much_is_refused_before_it_is_read(monkeypatch):
    monkeypatch.setattr(ds, "MAX_UNZIPPED_BYTES", 1000)
    with pytest.raises(ds.ScreenlistError, match="unpacks to far more data"):
        parse(sup.xlsx_bytes(sup.screens(30)), "big.xlsx")


def test_a_sheet_that_claims_a_million_empty_rows_ends_where_the_data_ends(monkeypatch):
    monkeypatch.setattr(ds, "MAX_BLANK_RUN", 50)
    rows = sup.screens(5)
    lines = sup.csv_text(rows).splitlines() + [""] * 200 + [",,,,"] * 10
    parsed = parse("\r\n".join(lines))
    assert parsed.screens == 5 and parsed.skipped == 0


# --- the reference the proposal keeps ------------------------------------------------------------------------------------

def test_the_reference_is_small_has_no_rows_and_survives_the_cleaner_unchanged():
    parsed = parse(sup.csv_bytes(sup.screens(300)))
    ref = ds.reference_for(parsed, ds.content_id(parsed.rows))
    assert set(ref) == {"id", "filename", "screens", "skipped", "skipped_examples", "total_impressions", "total_spend", "minimum_screens"}
    assert ref["screens"] == 300 and ref["minimum_screens"] == dp.DOOH_MINIMUM_SCREENS
    assert len(json.dumps(ref)) < 600                                  # not 300 rows x 40 columns
    assert ds.clean_reference(ref) == ref and ds.clean_reference(json.loads(json.dumps(ref))) == ref


@pytest.mark.parametrize("bad", [None, "x", 5, [], {}, {"id": "abc"}, {"id": "A" * 64}, {"id": "g" * 64}, {"id": "a" * 63}, {"id": "a" * 64 + "; DROP TABLE x"}])
def test_a_reference_without_a_valid_id_is_dropped(bad):
    assert ds.clean_reference(bad) is None


def test_a_tampered_reference_is_rebuilt_field_by_field():
    list_id = "ab" * 32
    ref = ds.clean_reference({"id": list_id, "filename": "<img src=x onerror=1>\x00.csv", "screens": -4, "skipped": "9", "total_impressions": float("nan"),
                              "total_spend": 1e30, "skipped_examples": ["row 2: ok", 7, "x" * 999] + ["more"] * 20, "minimum_screens": 0, "evil": "<script>"})
    assert ref == {"id": list_id, "filename": "<img src=x onerror=1>.csv", "screens": 0, "skipped": 0, "skipped_examples": ["row 2: ok", "x" * 160, "more", "more"][:4],
                   "total_impressions": 0.0, "total_spend": 0.0, "minimum_screens": dp.DOOH_MINIMUM_SCREENS}
    assert len(ref["skipped_examples"]) <= ds.MAX_EXAMPLES


def test_the_content_id_belongs_to_the_rows_not_the_file():
    rows = sup.screens(10)
    a = parse(sup.csv_bytes(rows), "a.csv")
    b = parse(sup.xlsx_bytes(rows), "b.xlsx")
    assert ds.content_id(a.rows) == ds.content_id(b.rows) and len(ds.content_id(a.rows)) == 64
    changed = [list(r) for r in rows]
    changed[3][22] += 1
    assert ds.content_id(changed) != ds.content_id(a.rows)


def test_the_avails_entry_declares_the_reference_cleans_it_and_the_saved_state_stays_small():
    parsed = parse(sup.csv_bytes(sup.screens(300)))
    ref = ds.reference_for(parsed, ds.content_id(parsed.rows))
    entry = main.AvailsEntry(max_imps=1, dooh_screenlist=ref)
    assert entry.model_dump()["dooh_screenlist"] == ref
    assert main.AvailsEntry(dooh_screenlist={"id": "nope"}).dooh_screenlist is None
    assert main.AvailsEntry().dooh_screenlist is None
    state = main._build_reopen_state(
        request_dict={}, line_items=[], tiers=None, avails_data={"L1": entry}, strategy_brief=None, roadblocks=None, force_tabs=None, addons=[],
        raw_notion_text=None, enrichment=None, time_unit="month", monthly_distribution_mode="even", campaign_name_override=None, wizard_step=5)
    assert state["avails_data"]["L1"]["dooh_screenlist"] == ref
    assert len(json.dumps(state)) < 4000                                # a reopen / duplicate carries the reference, never the screens


# --- the route -------------------------------------------------------------------------------------------------------------

@pytest.fixture
def http(monkeypatch):
    """The real app over HTTP (routing, the login middleware, the real handler) with a stubbed session; the store is a dict."""
    try:
        from fastapi.testclient import TestClient
    except (ImportError, RuntimeError) as exc:
        pytest.skip(f"no HTTP test client here: {exc}")
    stored: dict = {}

    def fake_save(parsed, created_by=None):
        list_id = ds.content_id(parsed.rows)
        stored[list_id] = (parsed, created_by)
        return list_id

    monkeypatch.setattr(ds, "save", fake_save)

    def make(logged_in: bool = True):
        monkeypatch.setattr(main.auth_svc, "get_user_by_session", lambda token: {"id": 1, "email": "planner@example.com", "is_admin": False} if logged_in else None)
        monkeypatch.setattr(main.auth_svc, "refresh_session", lambda token: None)
        client = TestClient(main.app, cookies={main.SESSION_COOKIE_NAME: "test-session"}, raise_server_exceptions=False)
        client.stored = stored
        return client
    return make


def post(client, body, name="list.csv"):
    return client.post("/api/dooh/screenlist", content=body, headers={"content-type": "application/octet-stream", "x-filename": quote(name)})


def test_the_upload_route_needs_the_same_login_as_the_other_proposal_routes(http):
    client = http(logged_in=False)
    answer = post(client, sup.csv_bytes(sup.screens(5)))
    assert answer.status_code == 401 and client.stored == {}


def test_a_good_file_answers_with_the_reference_the_totals_and_stores_the_rows_for_the_logged_in_planner(http):
    client = http()
    rows = sup.screens(60, seed=3)
    answer = post(client, sup.csv_bytes(rows), name="Pantallas Houston ñ.csv")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    ref = body["screenlist"]
    assert ref["filename"] == "Pantallas Houston ñ.csv" and ref["screens"] == 60 and ref["skipped"] == 0
    avails, spend = expected_totals(rows)
    assert ref["total_impressions"] == pytest.approx(avails, abs=0.01) and ref["total_spend"] == pytest.approx(spend, abs=0.01)
    assert "rows" not in answer.text and len(answer.content) < 2000
    (parsed, created_by), = client.stored.values()
    assert parsed.rows == rows and created_by == "planner@example.com" and ref["id"] in client.stored
    assert body["ignored_column_count"] == 0 and body["blanked_cells"] == 0


def test_what_is_wrong_with_a_file_comes_back_as_a_400_in_plain_words(http):
    client = http()
    answer = post(client, b"a,b\n1,2\n")
    assert answer.status_code == 400 and "missing columns" in answer.json()["detail"] and client.stored == {}
    old = post(client, b"xxxx", name="inventory.xls")
    assert old.status_code == 400 and ".xlsx" in old.json()["detail"]
    empty = post(client, b"")
    assert empty.status_code == 400 and "empty" in empty.json()["detail"]


def test_a_file_over_the_size_limit_is_refused_by_its_declared_size_and_by_what_actually_arrives(http, monkeypatch):
    monkeypatch.setattr(ds, "MAX_UPLOAD_BYTES", 4096)
    client = http()
    big = sup.csv_bytes(sup.screens(30))
    assert len(big) > 4096
    declared = post(client, big)
    assert declared.status_code == 413 and "larger than" in declared.json()["detail"]
    chunked = client.post("/api/dooh/screenlist", content=iter([big[:3000], big[3000:6000], big[6000:]]),
                          headers={"content-type": "application/octet-stream", "x-filename": "x.csv"})       # no Content-Length: counted as it arrives
    assert chunked.status_code == 413 and client.stored == {}
    small = post(client, sup.csv_bytes(sup.screens(3)))
    assert small.status_code == 200


def test_a_storage_failure_after_a_good_read_is_a_503_that_says_to_try_again(http, monkeypatch):
    client = http()

    def boom(parsed, created_by=None):
        raise RuntimeError("connection to server failed: password=hunter2")
    monkeypatch.setattr(ds, "save", boom)
    answer = post(client, sup.csv_bytes(sup.screens(5)))
    assert answer.status_code == 503 and "try again" in answer.json()["detail"].lower() and "hunter2" not in answer.text


# --- the store ---------------------------------------------------------------------------------------------------------------

class _Conn:
    """The one table, in memory: INSERT ... ON CONFLICT DO NOTHING and SELECT by id."""
    def __init__(self, table):
        self.table, self.executed = table, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        flat = " ".join(sql.split())
        self.executed.append(flat)
        if flat.startswith("INSERT INTO dooh_screenlists"):
            self.table.setdefault(params[0], dict(zip(("id", "filename", "screens", "content", "size_bytes", "created_by"), params)))
            return SimpleNamespace(fetchone=lambda: None)
        assert flat.startswith("SELECT content FROM dooh_screenlists WHERE id = %s")
        return SimpleNamespace(fetchone=lambda: self.table.get(params[0]))


def test_the_store_round_trips_the_rows_never_changes_a_stored_list_and_dedupes_by_content(monkeypatch):
    table: dict = {}
    conn = _Conn(table)
    monkeypatch.setattr(ds, "get_connection", lambda: conn)
    rows = sup.screens(80)
    first = parse(sup.csv_bytes(rows), "first.csv")
    list_id = ds.save(first, "a@x.com")
    again = ds.save(parse(sup.xlsx_bytes(rows), "second.xlsx"), "b@x.com")
    assert again == list_id and len(table) == 1                          # the same screens are one row, whoever uploaded them
    assert table[list_id]["filename"] == "first.csv" and table[list_id]["created_by"] == "a@x.com"
    assert all("ON CONFLICT (id) DO NOTHING" in s for s in conn.executed if s.startswith("INSERT")) and not any(s.startswith("UPDATE") for s in conn.executed)
    assert ds.load_rows(list_id) == rows
    assert gzip.decompress(table[list_id]["content"]).startswith(b"[[")
    assert ds.load_rows("0" * 64) is None and ds.load_rows("not-an-id") is None and ds.load_rows(None) is None


def test_a_missing_table_reads_as_no_list_but_an_unreachable_database_is_not_swallowed(monkeypatch):
    import psycopg

    class Missing:
        def __enter__(self):
            raise psycopg.errors.UndefinedTable("relation \"dooh_screenlists\" does not exist")

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(ds, "get_connection", lambda: Missing())
    assert ds.load_rows("a" * 64) is None

    class Down:
        def __enter__(self):
            raise psycopg.OperationalError("could not connect")

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(ds, "get_connection", lambda: Down())
    with pytest.raises(psycopg.OperationalError):
        ds.load_rows("a" * 64)


def test_the_schema_and_the_migration_say_the_same_thing_and_use_no_trigger():
    from pathlib import Path
    from app.services import migrations
    from app.services.schema_bootstrap import idempotent_statements, split_statements
    root = Path(__file__).resolve().parents[1]
    schema = (root / "schema.sql").read_text(encoding="utf-8")
    mine = [s for s in idempotent_statements(schema) if s["target"] == "dooh_screenlists"]
    assert [s["kind"] for s in mine] == ["table"]                      # picked up by the startup bootstrap and Publish's schema diff
    found = migrations.discover()
    migration = next(m for m in found if m.name == "dooh_screenlists")
    assert [m.version for m in found] == [f"{n:04d}" for n in range(1, len(found) + 1)]
    statements = split_statements(migration.sql)
    assert len(statements) == 1 and " ".join(statements[0].split()) == " ".join(mine[0]["sql"].split())    # one source of truth, written twice
    assert "TRIGGER" not in statements[0].upper() and "TRIGGER" not in mine[0]["sql"].upper()      # (published deployments take tables from the schema diff, not triggers)


@pytest.mark.real_db
def test_the_store_against_a_real_postgres_round_trips_and_is_idempotent():
    import psycopg
    from pathlib import Path
    from app.db import get_connection
    from app.services.schema_bootstrap import idempotent_statements
    url = os.environ.get("DATABASE_URL", "")
    if "fake" in url or not url:
        pytest.skip("no real database")
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except Exception:
        pytest.skip("no reachable database")
    schema = (Path(__file__).resolve().parents[1] / "schema.sql").read_text(encoding="utf-8")
    with get_connection() as conn:                                 # the table's own statement (what the startup bootstrap and the migration run)
        for item in idempotent_statements(schema):
            if item["target"] == "dooh_screenlists":
                conn.execute(item["sql"])
    rows = sup.screens(120, seed=99)
    parsed = parse(sup.csv_bytes(rows), "zz-test.csv")
    list_id = ds.content_id(parsed.rows)
    try:
        assert ds.save(parsed, "zz-test") == list_id
        assert ds.save(parsed, "zz-test") == list_id                 # again: no error, still one row
        with get_connection() as conn:
            count = conn.execute("SELECT count(*) AS n FROM dooh_screenlists WHERE id = %s", (list_id,)).fetchone()["n"]
        assert count == 1
        assert ds.load_rows(list_id) == rows
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM dooh_screenlists WHERE created_by = 'zz-test'")


# --- /api/generate hands the rows to the export -----------------------------------------------------------------------------

DOOH = "Digital Out of Home"


def _tiers(*option_specs):
    """[(label, [(line id, product name, reference or None)])] as the dicts /api/generate builds."""
    from app.services.proposal_generator import LineItem
    return [{"label": label, "line_items": [LineItem(product_name=name, monthly_budget=1000.0, months=3, id=lid) for lid, name, _ in lines],
             "avails_data": {lid: {"dooh_screenlist": ref} for lid, _, ref in lines if ref}} for label, lines in option_specs]


def _ref(rows, name="a.csv"):
    parsed = parse(sup.csv_bytes(rows), name)
    return ds.reference_for(parsed, ds.content_id(parsed.rows))


def test_the_rows_of_the_list_on_the_dooh_line_are_loaded_by_id(monkeypatch):
    rows = sup.screens(20)
    ref = _ref(rows)
    asked = []
    monkeypatch.setattr(ds, "load_rows", lambda list_id: asked.append(list_id) or rows)
    got, warnings = main._dooh_screenlist_for(_tiers(("A", [("l1", "Search - SEM", None), ("l2", DOOH, ref)])))
    assert got == rows and warnings == [] and asked == [ref["id"]]
    assert main._dooh_screenlist_for(_tiers(("A", [("l1", DOOH, None)]))) == (None, [])           # no list: the sample row, as before
    assert asked == [ref["id"]]


def test_a_list_left_on_a_line_that_is_no_longer_dooh_is_ignored(monkeypatch):
    monkeypatch.setattr(ds, "load_rows", lambda list_id: pytest.fail("must not be loaded"))
    assert main._dooh_screenlist_for(_tiers(("A", [("l1", "Search - SEM", _ref(sup.screens(20)))]))) == (None, [])


def test_two_different_lists_use_the_first_options_and_say_so(monkeypatch):
    a, b = sup.screens(20, seed=1), sup.screens(25, seed=2)
    ref_a, ref_b = _ref(a, "optionA.csv"), _ref(b, "optionB.csv")
    stored = {ref_a["id"]: a, ref_b["id"]: b}
    monkeypatch.setattr(ds, "load_rows", lambda list_id: stored[list_id])
    got, warnings = main._dooh_screenlist_for(_tiers(("A", [("l1", DOOH, ref_a)]), ("B", [("l2", DOOH, ref_b)])))
    assert got == a and len(warnings) == 1 and "Option A" in warnings[0] and "optionA.csv" in warnings[0]
    same, none = main._dooh_screenlist_for(_tiers(("A", [("l1", DOOH, ref_a)]), ("B", [("l2", DOOH, ref_a)])))      # the same file on both options: nothing to say
    assert same == a and none == []


@pytest.mark.parametrize("failure", ["missing", "down"])
def test_a_list_that_cannot_be_loaded_never_fails_generate_it_falls_back_to_the_sample_and_warns(monkeypatch, failure):
    def load(list_id):
        if failure == "down":
            raise RuntimeError("database is down")
        return None
    monkeypatch.setattr(ds, "load_rows", load)
    got, warnings = main._dooh_screenlist_for(_tiers(("A", [("l1", DOOH, _ref(sup.screens(20), "Houston.csv"))])))
    assert got is None and len(warnings) == 1 and "Houston.csv" in warnings[0] and "sample row" in warnings[0]


def _run_generate(monkeypatch, tmp_path, body):
    from tests.test_full_flight_api import _run_generate as run
    return run(monkeypatch, tmp_path, body)


def test_generate_builds_the_dooh_tabs_from_the_stored_list_and_keeps_the_reference_not_the_rows(monkeypatch, tmp_path):
    import openpyxl
    rows = sup.screens(45, seed=5)
    ref = _ref(rows)
    monkeypatch.setattr(ds, "load_rows", lambda list_id: rows if list_id == ref["id"] else None)
    monkeypatch.setattr("app.catalog.load_rate_overrides", lambda: {})
    monkeypatch.setattr("app.catalog.load_custom_products", lambda: [])
    monkeypatch.setattr("app.catalog.load_deleted_builtin_names", lambda: set())
    monkeypatch.setattr("app.market_config.load_market_config", lambda: {})
    body = main.GenerateRequest(
        request={"client_name": "Acme", "start_date": "2026-11-24", "end_date": "2026-12-31", "total_months": 2, "request_type": "New Business"},
        line_items=[main.LineItemModel(product_name=DOOH, monthly_budget=2000.0, months=2, id="d1")],
        avails_data={"d1": main.AvailsEntry(max_imps=round(ref["total_impressions"]), max_spend=round(ref["total_spend"]), dooh_screenlist=ref)},
        time_unit="month")
    result, saved = _run_generate(monkeypatch, tmp_path, body)
    assert not result["summary"]["warnings"]
    wb = openpyxl.load_workbook(tmp_path / result["filename"])
    assert wb["DOOH Screenlist"].tables["DOOHScreenlist"].ref == f"A1:AP{len(rows) + 1}"
    assert wb["DOOH Screenlist"].max_row == len(rows) + 1
    assert len(wb["DOOH Summary"]._pivots) == 1
    # what is saved for reopen is the reference, not the screens
    assert saved["reopen_state"]["avails_data"]["d1"]["dooh_screenlist"] == ref
    assert len(json.dumps(saved["reopen_state"])) < 6000
    # and a list that has gone missing is a warning on the generate, not a failure
    monkeypatch.setattr(ds, "load_rows", lambda list_id: None)
    result, _ = _run_generate(monkeypatch, tmp_path, body)
    assert any("sample row" in w for w in result["summary"]["warnings"])
    assert openpyxl.load_workbook(tmp_path / result["filename"])["DOOH Screenlist"].max_row == 2
