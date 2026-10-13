"""Command line for the forecast data import.

    python -m app.services.forecast_import plan [--only KEY ...] [--source-dir DIR]
        Read and validate every source. No database needed; nothing changes. Exit 1 if any series has an error.
    python -m app.services.forecast_import import [--only KEY ...] [--no-publish] [--accept-large-change] [--by NAME]
        Stage, validate and publish. Idempotent: an unchanged source is skipped. A series with errors is reported and
        skipped while the others still load.
    python -m app.services.forecast_import status
    python -m app.services.forecast_import rollback SERIES_KEY [--to VERSION]
    python -m app.services.forecast_import prune [--keep N]
    python -m app.services.forecast_import seed-products
    python -m app.services.forecast_import seed-audience
        Load app/data/constanza/audience_taxonomy.json into the audience tables (idempotent; admin-edited rows are kept).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.services.forecast_import import service


def _print_outcomes(outcomes, verbose: bool) -> int:
    worst = 0
    width = max((len(o.series_key) for o in outcomes), default=10)
    for o in outcomes:
        counts = ", ".join(f"{k} {v:,}" for k, v in o.row_counts.items())
        version = f" v{o.version}" if o.version else ""
        print(f"{o.series_key:<{width}}  {o.status:<9}{version:<4}  errors={o.errors} warnings={o.warnings}  {counts}")
        if o.note:
            print(f"{'':<{width}}  note: {o.note}")
        shown = [f for f in o.findings if f.severity == "error" or (verbose and f.severity != "info") or f.severity == "warning"]
        for f in shown[: (40 if verbose else 6)]:
            print(f"{'':<{width}}  [{f.severity[:4]} {f.rule}] {f.message}" + (f"  ({f.where})" if f.where else ""))
        if verbose:
            for f in o.findings:
                if f.severity == "info":
                    print(f"{'':<{width}}  [info {f.rule}] {f.message}")
        if o.status in ("invalid", "error"):
            worst = 1
    return worst


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="forecast_import", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "import"):
        p = sub.add_parser(name)
        p.add_argument("--only", nargs="*", help="series keys to process (default: every series in the manifest)")
        p.add_argument("--source-dir", type=Path)
        p.add_argument("-v", "--verbose", action="store_true")
        if name == "import":
            p.add_argument("--no-publish", action="store_true", help="stage the version but leave the current one in place")
            p.add_argument("--accept-large-change", action="store_true", help="publish even if the national total moved more than 40%%")
            p.add_argument("--by", default=None, help="who is importing (recorded with the version)")
    sub.add_parser("status")
    rb = sub.add_parser("rollback"); rb.add_argument("series_key"); rb.add_argument("--to", type=int)
    pr = sub.add_parser("prune"); pr.add_argument("--keep", type=int, default=2)
    sub.add_parser("seed-products")
    sub.add_parser("seed-audience")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if args.command == "plan":
        return _print_outcomes(service.plan(args.source_dir, args.only), args.verbose)
    if args.command == "import":
        return _print_outcomes(service.run_import(args.source_dir, args.only, publish=not args.no_publish, requested_by=args.by,
                                                  accept_large_change=args.accept_large_change), args.verbose)
    if args.command == "status":
        rows = service.status()
        for r in rows:
            cur = f"v{r['version']} {r['status']}" if r["version"] else "- not imported -"
            period = f"  [{r['period_label']}]" if r["period_label"] else ""
            print(f"{r['series_key']:<34} {r['channel']:<14} {r['avails_unit']:<8} {cur:<18} {r['data_basis'] or '':<18}{period}")
        return 0
    if args.command == "rollback":
        print(f"{args.series_key} is now at v{service.rollback_series(args.series_key, args.to)}")
        return 0
    if args.command == "prune":
        print(f"deleted {service.prune_versions(args.keep)} old version(s)")
        return 0
    if args.command == "seed-products":
        from app.db import get_connection
        from app.services.forecast_import import product_seed
        with get_connection() as conn:
            print(f"inserted {product_seed.apply_to_db(conn)} product(s)")
        return 0
    if args.command == "seed-audience":
        from app.db import get_connection
        from app.services import audience_taxonomy
        with get_connection() as conn:
            report = audience_taxonomy.seed_to_db(conn)
            conn.commit()
        print(f"{report['groups']} groups, {report['segments']} segments seeded ({report['deactivated']} deactivated)")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
