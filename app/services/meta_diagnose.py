"""
Checks the Meta connection end to end, against the real ad account: is the token good and can it read the account, can geographies be
searched, does a reach estimate come back (a country, a TV market with age and gender, interests, a language), can languages be searched
and listed, and can the interest / behavior / demographic lists be read. Two lines are about what Meta does with `locales`: the Spanish one
is a check (the figure must come out clearly smaller than the plain U.S. one), the English one only REPORTS what Meta did with a language that
is common in the U.S. (Meta's reference describes `locales` as for a language other than the common one for a location) and never fails the run.

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
from typing import Callable, Optional

from app.services import constanza_meta as meta
from app.services import platform_scenarios
from app.services.constanza_methods import Check            # one type for every method's connection test (re-exported here)


# A language only narrows an estimate, so the figure for Spanish must come out clearly below the plain U.S. figure of the same run. Meta answers the
# same spec with the same rounded bounds, so a language it did not apply gives the U.S. figure itself; below this share of it counts as applied.
LANGUAGE_NARROWS_BELOW = 0.9


def _timed(name: str, fn: Callable[[], tuple[str, bool]]) -> Check:
    """Run one check. Every line it returns passes through `meta.redact`: what Meta says (an ad account is often NAMED with its own id) must not
    carry the token, the app secret, the proof or the whole ad account to the Admin page."""
    started = time.monotonic()
    try:
        detail, warn = fn()
        return Check(name, True, meta.redact(detail), warn=warn, seconds=time.monotonic() - started)
    except meta.MetaAPIError as exc:
        return Check(name, False, meta.redact(exc), seconds=time.monotonic() - started)
    except ValueError as exc:
        return Check(name, False, meta.redact(f"this app could not form the request: {exc}"), seconds=time.monotonic() - started)


def run_checks() -> list[Check]:
    meta.reset_cache()           # the web process remembers estimates for ten minutes and the lists for hours: a test must ask Meta, as the TikTok one does
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
        raw = row.get("account_status")
        names = {1: "active", 2: "disabled", 3: "unsettled", 7: "pending risk review", 8: "pending settlement", 9: "in grace period",
                 100: "pending closure", 101: "closed", 201: "any active", 202: "any closed"}
        status = "status not given" if raw is None else names.get(raw, f"status {raw}")
        return f"“{row.get('name', '?')}” ({status}, {row.get('currency', '?')})", raw not in (None, 1)

    if not add(_timed("Token and ad account (ads_read)", account)).ok:
        return checks

    state = {"market": None, "language": None, "us": None}

    def us_baseline():
        """The plain U.S. estimate of this run: the one the check above got, else asked once more (None when Meta will not give it)."""
        if state["us"] is None:
            try:
                state["us"] = meta.fetch_reach_estimate(geo_type="country", geo_key="US")
            except (meta.MetaAPIError, ValueError):
                return None
        return state["us"]

    def against_us(lower):
        """(the plain U.S. range as words, the language's lower end as a share of the plain U.S. lower end) - both None without a usable baseline."""
        baseline = us_baseline()
        if not baseline or baseline[0] <= 0:
            return None, None
        return f"against {baseline[0]:,} – {baseline[1]:,} for the whole U.S.", lower / baseline[0]

    def geography():
        found = meta.search_geography("Los Angeles", "geo_market")
        if not found:
            raise meta.MetaAPIError("Meta's geography search found no TV market called Los Angeles: pick geographies by hand in the panel.")
        state["market"] = found[0]
        return f"{len(found)} match(es); first: {found[0]['name']} (key {found[0]['key']}, {found[0]['type']})", False

    add(_timed("Geography search · TV market", geography))

    def country():
        lower, upper = meta.fetch_reach_estimate(geo_type="country", geo_key="US")
        state["us"] = (lower, upper)
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

    def language_search():
        found = meta.search_locales("Spanish")
        if not found:
            raise meta.MetaAPIError("Meta's language search found nothing for “Spanish”: the token may not be able to use the targeting search "
                                    "(`type=adlocale`).")
        state["language"] = next((f for f in found if f["name"].lower() == "spanish"), found[0])
        return f"{len(found)} match(es); using: {state['language']['name']} (locale {state['language']['id']})", False

    add(_timed("Language search", language_search))

    def language_reach():
        language = state["language"]
        if not language:
            raise meta.MetaAPIError("no language to ask about (the language search above failed)")
        lower, upper = meta.fetch_reach_estimate(geo_type="country", geo_key="US", locales=[language])
        detail = f"U.S. accounts set to {language['name']}: {lower:,} – {upper:,}"
        compared, share = against_us(lower)
        if share is None:                                              # nothing to compare with: say so instead of claiming a result
            return detail + " (no plain U.S. figure to compare with, so whether Meta applied `locales` is not verified)", True
        if share >= LANGUAGE_NARROWS_BELOW:                            # a language only narrows: about the U.S. figure means Meta did not apply it
            return (f"{detail}, {compared} - not clearly smaller than the whole U.S. ({share:.1%} of it), so Meta may not be applying `locales`; "
                    "compare in Ads Manager (Languages field)"), True
        return f"{detail}, {compared} (smaller than the whole U.S.: {share:.1%} of it, so the language narrowed the estimate)", False

    add(_timed("Reach estimate · with a language", language_reach))

    def common_language():
        """For information only: what Meta does with English, the language most common in the U.S. It is reported, never judged, and never fails."""
        try:
            found = meta.search_locales("English")
            language = (next((f for f in found if f["name"].lower() == "english (us)"), None)
                        or next((f for f in found if f["name"].lower().startswith("english")), None))
            if not language:
                return "Meta lists no language called English, so there is nothing to compare (for information only).", False
            lower, upper = meta.fetch_reach_estimate(geo_type="country", geo_key="US", locales=[language])
            detail = f"U.S. accounts set to {language['name']}: {lower:,} – {upper:,}"
            compared, share = against_us(lower)
        except (meta.MetaAPIError, ValueError) as exc:
            return f"Could not ask ({exc}). For information only: this does not change the result of the run.", False
        if share is None:
            return detail + " (no plain U.S. figure to compare with). For information only.", False
        if share < LANGUAGE_NARROWS_BELOW:
            return f"{detail}, {compared} ({share:.1%} of it): Meta narrowed the estimate for English too. For information only.", False
        return (f"{detail}, {compared} ({share:.1%} of it): no clear narrowing, Meta gave about the whole U.S. figure for English. For information only: "
                "Meta's reference describes `locales` as for a language other than the common one for a location, so check what the English and "
                "Spanish + English picks do in Ads Manager."), False

    add(_timed("Reach estimate · English, a common language (information)", common_language))

    for kind in ("interests", "behaviors", "demographics"):
        def listing(kind=kind):
            items = meta.targeting_catalog(kind)
            if not items:
                raise meta.MetaAPIError(f"Meta returned an empty {kind} list. Demographic options also depend on the token's home country.")
            return f"{len(items):,} options; first: {items[0]['name']}", len(items) < 5
        add(_timed(f"Targeting list · {kind}", listing))

    def language_list():
        items = meta.search_locales()
        if not items:
            raise meta.MetaAPIError("Meta returned an empty language list (a blank search with limit=1000); a language can still be searched by name.")
        return f"{len(items):,} languages; first: {items[0]['name']}", len(items) < 5

    add(_timed("Language list", language_list))
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
