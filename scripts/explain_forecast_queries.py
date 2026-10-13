"""
Measure the forecast API's database queries with EXPLAIN (ANALYZE, BUFFERS).

The queries are the exact strings app/services/forecast_store.py runs; the parameters are taken from the data in the
database you point it at (the biggest current series, real ZIPs / districts / states). Run it on Replit after an import,
and any time before adding an index: an index is added only when a measurement here shows it is needed
(docs/forecast_data_platform.md, "Indexes").

    python scripts/explain_forecast_queries.py                 # the plans and timings
    python scripts/explain_forecast_queries.py --check         # exit 1 if a hot-path query stops using its index
    python scripts/explain_forecast_queries.py --repeat 20     # more timing runs (reports the median)
    python scripts/explain_forecast_queries.py --sizes         # table and index sizes

Needs DATABASE_URL. Read-only (EXPLAIN ANALYZE runs the SELECTs for real, which change nothing).
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import get_connection  # noqa: E402
from app.services import forecast_store as fs  # noqa: E402

TIME = re.compile(r"Execution Time: ([0-9.]+) ms")
BUFFERS = re.compile(r"Buffers: shared (.*)")
SEQ_SCAN_PAGE_LIMIT = 64          # a sequential scan of a few pages (a 40-row table) is what the planner SHOULD choose


def pages_touched(plan: str) -> int:
    m = BUFFERS.search(plan)
    return sum(int(n) for n in re.findall(r"(?:hit|read)=(\d+)", m.group(1))) if m else 0


def pick_parameters(conn) -> dict:
    row = conn.execute(
        "SELECT v.series_id, v.series_key FROM forecast_series_version v "
        "JOIN forecast_avail_zip z ON z.series_id = v.series_id WHERE v.status = 'current' "
        "GROUP BY v.series_id, v.series_key ORDER BY count(*) DESC LIMIT 1").fetchone()
    if row is None:
        raise SystemExit("No current series with ZIP rows - import the data first (python -m app.services.forecast_import import).")
    sid = row["series_id"]
    zips = [r["zip"] for r in conn.execute("SELECT zip FROM forecast_avail_zip WHERE series_id = %s ORDER BY random() LIMIT 500", (sid,)).fetchall()]
    cds = [r["cd_key"] for r in conn.execute("SELECT cd_key FROM forecast_avail_cd WHERE series_id = %s LIMIT 3", (sid,)).fetchall()]
    states = [r["state_code"] for r in conn.execute("SELECT state_code FROM forecast_avail_state WHERE series_id = %s LIMIT 3", (sid,)).fetchall()]
    return {"series_id": sid, "series_key": row["series_key"], "zips": zips, "cds": cds, "states": states}


def queries(p: dict) -> list[dict]:
    """name, sql, params, and whether a Seq Scan on a value table would be a regression (hot = on the request path)."""
    return [
        {"name": "current series (catalog metadata, cached 60 s)", "sql": fs.SQL_CURRENT_SERIES, "params": (), "hot": False},
        {"name": "product -> series links (cached 60 s)", "sql": fs.SQL_PRODUCT_LINKS, "params": (), "hot": False},
        {"name": "DMA table of one series (cached per series)", "sql": fs.SQL_DMA_TABLE, "params": (p["series_id"],), "hot": True},
        {"name": "ZIP lookup, 3 ZIPs", "sql": fs.sql_rows("zip"), "params": (p["series_id"], p["zips"][:3]), "hot": True},
        {"name": "ZIP lookup, 50 ZIPs", "sql": fs.sql_rows("zip"), "params": (p["series_id"], p["zips"][:50]), "hot": True},
        {"name": "ZIP lookup, 500 ZIPs", "sql": fs.sql_rows("zip"), "params": (p["series_id"], p["zips"][:500]), "hot": True},
        {"name": "congressional-district lookup", "sql": fs.sql_rows("cd"), "params": (p["series_id"], p["cds"]), "hot": True},
        {"name": "state lookup", "sql": fs.sql_rows("state"), "params": (p["series_id"], p["states"]), "hot": True},
        {"name": "unplaced ('Unknown') share", "sql": fs.SQL_UNKNOWN, "params": (p["series_id"],), "hot": True},
    ]


def explain(conn, sql: str, params: tuple) -> str:
    rows = conn.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params).fetchall()
    return "\n".join(r["QUERY PLAN"] for r in rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeat", type=int, default=7)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--sizes", action="store_true")
    ap.add_argument("--plans", action="store_true", help="print the full plan of every query (default: only the scan lines)")
    args = ap.parse_args()
    failures = 0
    with get_connection() as conn:
        params = pick_parameters(conn)
        counts = {t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in
                  ("forecast_series_version", "forecast_avail_dma", "forecast_avail_state", "forecast_avail_cd", "forecast_avail_zip")}
        print(f"series measured: {params['series_key']} (series_id {params['series_id']})   rows: " + ", ".join(f"{k.replace('forecast_', '')}={v:,}" for k, v in counts.items()))
        print(f"{'query':<50} {'median ms':>9} {'rows':>6} {'buffers':>22}  plan")
        for q in queries(params):
            times, plan = [], ""
            for _ in range(max(args.repeat, 1)):
                plan = explain(conn, q["sql"], q["params"])
                m = TIME.search(plan)
                times.append(float(m.group(1)) if m else float("nan"))
            scans = [ln.strip() for ln in plan.splitlines() if re.search(r"(Seq Scan|Index Scan|Index Only Scan|Bitmap Heap Scan|Bitmap Index Scan) ", ln)]
            first = re.sub(r"\s*\(cost=.*", "", scans[0]) if scans else "?"
            rows = re.search(r"rows=(\d+) loops=\d+\)\s*$", plan.splitlines()[0])
            buf = BUFFERS.search(plan)
            print(f"{q['name']:<50} {statistics.median(times):>9.3f} {rows.group(1) if rows else '?':>6} {(buf.group(1) if buf else '')[:22]:>22}  {first}")
            if args.plans:
                print("    " + plan.replace("\n", "\n    "))
            if q["hot"] and re.search(r"Seq Scan on forecast_avail", plan) and pages_touched(plan) > SEQ_SCAN_PAGE_LIMIT:
                print(f"    !! {q['name']}: sequential scan of a value table ({pages_touched(plan)} pages)")
                failures += 1
            if q["hot"] and statistics.median(times) > 50:
                print(f"    !! {q['name']}: median {statistics.median(times):.1f} ms (budget 50 ms)")
                failures += 1
        if args.sizes:
            print("\nsizes (table + indexes):")
            for r in conn.execute(
                "SELECT c.relname AS name, pg_size_pretty(pg_total_relation_size(c.oid)) AS total, pg_size_pretty(pg_relation_size(c.oid)) AS heap, "
                "pg_size_pretty(pg_indexes_size(c.oid)) AS indexes, c.reltuples::bigint AS est_rows "
                "FROM pg_class c WHERE c.relkind = 'r' AND c.relname LIKE 'forecast\\_%' ORDER BY pg_total_relation_size(c.oid) DESC").fetchall():
                print(f"  {r['name']:<28} total {r['total']:>9}  heap {r['heap']:>9}  indexes {r['indexes']:>9}  ~{r['est_rows']:,} rows")
    if args.check and failures:
        print(f"\n{failures} query check(s) failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
