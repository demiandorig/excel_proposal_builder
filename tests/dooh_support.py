"""Shared builders for the DOOH screen-list tests: typed screen rows of the platform export's shape, and the same screens as the
.csv / .xlsx files a planner would upload."""
from __future__ import annotations

import csv
import io
import random

from app.services import dooh_pivot as dp

# three venue types, each with its own networks (the "publisher" column, shown as Network in the pivot)
VENUES = [("Retail|Gas Stations|Pump", ["GSTV", "Outfront Fuel"]),
          ("Transit|Airports|Concourse", ["Clear Channel", "Place Exchange"]),
          ("Out of Home|Billboards|Digital", ["Lamar", "Outfront", "Clear Channel"])]


def screens(n: int, seed: int = 1) -> list[list]:
    """n typed screen rows (the 40 inventory columns, in order): ids and postal codes as text (leading zeros kept), numbers as numbers,
    the four supports_* flags as real booleans, an empty cell as None. Venue types and networks cycle, so every group has screens."""
    rnd = random.Random(seed)
    rows = []
    for k in range(n):
        venue, networks = VENUES[k % len(VENUES)]
        network = networks[(k // len(VENUES)) % len(networks)]
        impressions = rnd.randint(500, 90000)
        base = {c: (None if v == "" else v) for c, v in zip(dp.DOOH_SCREENLIST_COLUMNS, dp.DOOH_SAMPLE_ROW)}
        base.update(
            id=str(1000000 + k), adunit_name=f"DVG{k}", asset_name=f"AS{k}", adunit_id=f"{rnd.getrandbits(128):032x}",
            publisher=network, network=f"{network} Fast", venue_type=venue, venue_name=f"Venue {k}",
            city=rnd.choice(["DENVER", "AUSTIN", "MIAMI", "PHOENIX"]), postal_code=f"{rnd.randint(1000, 99999):05d}",
            latitude=round(rnd.uniform(25, 48), 6), longitude=round(rnd.uniform(-120, -70), 6),
            address=f"{k} Grape Street, Denver, CO", cpm=round(rnd.uniform(4, 40), 2), four_week_impressions=impressions,
            impression_multiplier=round(rnd.uniform(1, 2), 9), bidstream_impressions=impressions - 100,
            bidstream_inferred_impressions=impressions - 200, screen_count=rnd.randint(1, 12),
            supports_video=rnd.random() < 0.7, supports_dynamic_creative=rnd.random() < 0.3,
            asset_image_url=f"https://static1.example.com/static/{k}/Programmatic_Inventory")
        rows.append([base[c] for c in dp.DOOH_SCREENLIST_COLUMNS])
    return rows


def _cell(value):
    return "" if value is None else ("TRUE" if value is True else "FALSE" if value is False else value)


def csv_text(rows, delimiter: str = ",", bom: bool = True, headers=None, extra=None) -> str:
    """The rows as the platform's CSV: header line, then one line per screen. `extra` = [(header, value)] appended columns."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(list(headers or dp.DOOH_SCREENLIST_COLUMNS) + [h for h, _ in (extra or [])])
    for row in rows:
        writer.writerow([_cell(v) for v in row] + [v for _, v in (extra or [])])
    return ("﻿" if bom else "") + out.getvalue()


def csv_bytes(rows, **kw) -> bytes:
    return csv_text(rows, **kw).encode("utf-8")


def xlsx_bytes(rows, sheet: str = "DOOH Screenlist", headers=None, title_rows: int = 0) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for i in range(title_rows):
        ws.append([f"Inventory export, generated line {i + 1}"])
    ws.append(list(headers or dp.DOOH_SCREENLIST_COLUMNS))
    for row in rows:
        ws.append(list(row))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
