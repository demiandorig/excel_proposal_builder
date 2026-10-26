"""
Checks the Meta connection end to end, against the real ad account: is the token good and can it read the account, can geographies be
searched, does a reach estimate come back (a country, a TV market with age and gender, interests), and can the interest / behavior /
demographic lists be read.

    python -m app.services.meta_diagnose
    python -m app.services.meta_diagnose --json

Read-only: nothing is created in the ad account and no secret is printed (the ad account shows as its last four digits). When a check
fails the line says what to do about it. This is the one thing that could not be verified before an ad account existed: the request
shapes were built from Meta's published Marketing API reference.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional

from app.services import constanza_meta as meta
from app.services import platform_scenarios


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    warn: bool = False
    seconds: float = 0.0

    def to_public(self) -> dict:
        return {"name": self.name, "ok": self.ok, "warn": self.warn and self.ok, "detail": self.detail, "seconds": round(self.seconds, 2)}


def _timed(name: str, fn: Callable[[], tuple[str, bool]]) -> Check:
    started = time.monotonic()
    try:
        detail, warn = fn()
        return Check(name, True, detail, warn=warn, seconds=time.monotonic() - started)
    except meta.MetaAPIError as exc:
        return Check(name, False, str(exc), seconds=time.monotonic() - started)
    except ValueError as exc:
        return Check(name, False, f"this app could not form the request: {exc}", seconds=time.monotonic() - started)


def run_checks() -> list[Check]:
    checks: list[Check] = []

    def add(check: Check) -> Check:
        checks.append(check)
        return check

    def settings():
        missing = meta.missing_config()
        if missing:
            raise meta.MetaAPIError(f"Not configured: add {' and '.join(missing)} in the app's secrets. {meta.SETUP_HINT}")
        return f"ad account {meta.account_hint()}, Graph API {meta.graph_version()}, scenarios {platform_scenarios.describe()}", False

    if not add(_timed("Settings", settings)).ok:
        return checks

    def account():
        row = meta._graph_get(f"act_{meta._account()}", {"fields": "name,account_status,currency"})
        status = {1: "active", 2: "disabled", 3: "unsettled", 7: "pending review", 9: "in grace period", 101: "closed"}.get(row.get("account_status"), "status not given")
        return f"“{row.get('name', '?')}” ({status}, {row.get('currency', '?')})", row.get("account_status") not in (None, 1)

    if not add(_timed("Token and ad account (ads_read)", account)).ok:
        return checks

    state = {"market": None}

    def geography():
        found = meta.search_geography("Los Angeles", "geo_market")
        if not found:
            raise meta.MetaAPIError("Meta's geography search found no TV market called Los Angeles: pick geographies by hand in the panel.")
        state["market"] = found[0]
        return f"{len(found)} match(es); first: {found[0]['name']} (key {found[0]['key']}, {found[0]['type']})", False

    add(_timed("Geography search · TV market", geography))

    def country():
        lower, upper = meta.fetch_reach_estimate(geo_type="country", geo_key="US")
        return f"U.S.: {lower:,} – {upper:,} accounts; lower end x{platform_scenarios.multipliers()['mid']:g} = {lower * platform_scenarios.multipliers()['mid']:,.0f} impressions a month (mid)", False

    add(_timed("Reach estimate · United States", country))

    def market():
        if not state["market"]:
            raise meta.MetaAPIError("no TV market to ask about (the geography search above failed)")
        lower, upper = meta.fetch_reach_estimate(geo_type="geo_market", geo_key=state["market"]["key"], age_min=25, age_max=44, gender="female")
        return f"women 25–44 in {state['market']['name']}: {lower:,} – {upper:,} accounts", False

    add(_timed("Reach estimate · a TV market with age and gender", market))

    def interests():
        found = meta.search_targeting("soccer")
        first = (found["interests"] or [None])[0]
        if not first:
            raise meta.MetaAPIError("Meta's interest search returned nothing for “soccer”: the interest list may not be available to this token.")
        spec = {"geo_type": "country", "geo_key": "US", "targeting": [first]}
        lower, upper = meta.fetch_reach_estimate(**spec)
        return (f"“soccer” found {len(found['interests'])} interest(s), {len(found['behaviors'])} behavior(s), {len(found['demographics'])} demographic(s); "
                f"{first['name']} in the U.S.: {lower:,} – {upper:,} accounts"), False

    add(_timed("Interest search + a reach estimate with an interest", interests))

    for kind in ("interests", "behaviors", "demographics"):
        def listing(kind=kind):
            items = meta.targeting_catalog(kind)
            if not items:
                raise meta.MetaAPIError(f"Meta returned an empty {kind} list. Demographic options also depend on the token's home country.")
            return f"{len(items):,} options; first: {items[0]['name']}", len(items) < 5
        add(_timed(f"Targeting list · {kind}", listing))
    return checks


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check the Meta connection end to end.")
    parser.add_argument("--json", action="store_true", help="print the checks as JSON")
    args = parser.parse_args(argv)
    checks = run_checks()
    if args.json:
        print(json.dumps([c.to_public() for c in checks], indent=2))
    else:
        for c in checks:
            mark = "✓" if c.ok and not c.warn else ("!" if c.ok else "✗")
            print(f"{mark} {c.name} ({c.seconds:.1f}s)\n    {c.detail}")
        print("\n" + ("All checks passed." if all(c.ok for c in checks) and not any(c.warn for c in checks)
                      else "Some checks need a look (lines marked ! or ✗)."))
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
