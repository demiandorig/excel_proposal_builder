"""
Checks the TikTok connection end to end, against the real account (or a mock, with TIKTOK_API_BASE): is the token good, can it
read the targeting catalogs, are the TV markets findable by name, and does an audience-size estimate come back.

    python -m app.services.tiktok_diagnose              # every check, and all 210 TV markets (about 15 calls)
    python -m app.services.tiktok_diagnose --sample     # a sample of about 40 markets
    python -m app.services.tiktok_diagnose --json

The same checks run from Admin -> Forecast ("Test the connection"). Nothing is written to TikTok and no secret is printed. When
something fails, the line says what to do about it; the markets line says which names did not match, which is the one thing
that could not be verified before an ad account existed (see tiktok_geo.py).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional

from app.data.constanza import load_dma_reference
from app.services import constanza_tiktok as ct
from app.services import tiktok_client, tiktok_geo
from app.services.constanza_geo import GeoResolution, ResolvedArea
from app.services.tiktok_client import TikTokAPIError, TikTokClient

SAMPLE_SIZE = 40                    # markets tried when only a sample is asked for
@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    warn: bool = False                # worked, but something deserves a look
    seconds: float = 0.0

    def to_public(self) -> dict:
        return {"name": self.name, "ok": self.ok, "warn": self.warn and self.ok, "detail": self.detail, "seconds": round(self.seconds, 2)}


def _area(dma) -> ResolvedArea:
    return ResolvedArea(dma=dma.dma, area_population=dma.population, dma_share=1.0, source_type="dma", apportionment="exact",
                        state=dma.state, label=dma.dma, geo_id=dma.dma)


def _zip_area(z: str) -> ResolvedArea:
    return ResolvedArea(dma=None, area_population=1.0, dma_share=1.0, source_type="zip", apportionment="exact", state=None, label=f"ZIP {z}", geo_id=z)


def _resolution(areas: list[ResolvedArea], method: str) -> GeoResolution:
    return GeoResolution(input_text="diagnose", method=method, areas=areas, geo_confidence="high")


def _timed(name: str, fn: Callable[[], tuple[str, bool]]) -> Check:
    started = time.monotonic()
    try:
        detail, warn = fn()
        return Check(name, True, detail, warn=warn, seconds=time.monotonic() - started)
    except TikTokAPIError as exc:
        return Check(name, False, exc.public_message() + (f" [TikTok code {exc.code}, request {exc.request_id}]" if exc.code else ""),
                     seconds=time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 - a diagnostic must report, not crash
        return Check(name, False, f"{type(exc).__name__}: {exc}", seconds=time.monotonic() - started)


def sample_markets(dmas: list) -> list:
    """The markets worth trying when not all 210 are: the biggest ten, every market whose first city another market shares (the ones a
    name match can confuse), the abbreviated names (St. / Ft. / Mt.) and the ones with an ampersand."""
    chosen = list(dmas[:10])
    by_lead: dict[str, list] = {}
    for d in dmas:
        by_lead.setdefault(tiktok_geo._primary(d.dma).lower(), []).append(d)
    for group in by_lead.values():
        if len(group) > 1:
            chosen.extend(group)
    chosen.extend([d for d in dmas if re.search(r"\b(St|Ft|Mt)\b\.?", d.dma)][:6])
    chosen.extend([d for d in dmas if "&" in d.dma][:3])
    out, seen = [], set()
    for d in chosen:
        if d.dma not in seen:
            seen.add(d.dma)
            out.append(d)
    return out[:SAMPLE_SIZE]


def lookalike_families(dmas: list) -> list[list]:
    """Groups of two or more markets whose first city is the same word, or one written short for the other: the three Columbuses, the two
    Portlands, Charlotte and Charlottesville. These are the ones a name match could swap, so the line that lists them is the one to read by eye."""
    leads = [tiktok_geo._lead(d.dma, True) for d in dmas]
    parent = list(range(len(dmas)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(dmas)):
        for j in range(i + 1, len(dmas)):
            if tiktok_geo._same_lead(leads[i], leads[j]):
                parent[find(j)] = find(i)
    groups: dict[int, list] = {}
    for i, d in enumerate(dmas):
        groups.setdefault(find(i), []).append(d)
    return [g for g in groups.values() if len(g) > 1]


def run_checks(client: Optional[TikTokClient] = None, *, all_dmas: bool = True) -> list[Check]:
    """The checks, in order; a failure that every later check would share (a bad token) stops the run there. All 210 TV markets are
    tried by default: against TikTok's market list that is one call, and the line says which markets did not match."""
    if client is None:
        missing = tiktok_client.missing_config()
        if missing:
            return [Check("Settings", False, "Missing: " + ", ".join(missing) + f" — {tiktok_client.SETUP_HINT}.")]
        client = tiktok_client.get_client()
    client.clear_cache()
    client.adult_ages_needed = False          # let the "interests, no ages" check find out what this account says
    tiktok_geo.forget_everything()
    checks: list[Check] = []
    placed: dict[str, tiktok_geo.Mapping] = {}

    def add(check: Check) -> bool:
        checks.append(check)
        return check.ok

    def languages():
        langs = client.languages()
        if not langs:
            raise TikTokAPIError("TikTok returned no languages", kind="unexpected")
        return f"{len(langs)} languages (for example {', '.join(l['name'] for l in langs[:4])})", False

    # a bad token or ad account fails everything after it the same way: stop at the first such failure
    if not add(_timed("Access token and ad account", languages)):
        return checks

    def catalogs():
        lists = client.targeting_list()
        parts = [f"{len(items)} {kind.replace('_', ' ')}" for kind, items in lists.items()]
        empty = [kind for kind, items in lists.items() if not items]
        return ", ".join(parts), bool(empty)

    add(_timed("Interest and behavior categories", catalogs))

    def search():
        found = client.targeting_search("fashion")
        return ", ".join(f"{len(v)} {k.replace('_', ' ')}" for k, v in found.items() if v) or "nothing found for “fashion”", not any(found.values())

    add(_timed("Keyword search", search))

    dmas = list(load_dma_reference())
    by_name = {d.dma: d for d in dmas}
    wanted = dmas if all_dmas else sample_markets(dmas)

    def markets():
        out = tiktok_geo.map_resolution(_resolution([_area(d) for d in wanted], "exact_dma"), client)
        placed["markets"] = out
        shown = "; ".join(f"{l.source} → “{l.name}”" for l in out.locations[:4])
        missed = out.unmapped
        how = {"list": "from TikTok's own market list", "search": "by searching: TikTok's market list was not available"}.get(out.market_source, "")
        detail = f"{len(out.locations)} of {len(wanted)} TV markets placed {how} ({shown}{'…' if len(out.locations) > 4 else ''})"
        if missed:
            detail += ". NOT placed: " + "; ".join(f"{m['input']} — {m['reason']}" for m in missed[:12]) + (f" and {len(missed) - 12} more" if len(missed) > 12 else "")
        return detail, bool(missed)

    add(_timed("TV markets by name", markets))

    def lookalikes():
        out = placed.get("markets")
        if out is None:
            return "skipped: the market line above did not run", True
        got = {l.source: l for l in out.locations}
        tried = {_a for _a in (d.dma for d in wanted)}
        parts, unplaced = [], 0
        for family in lookalike_families(dmas):
            cells = []
            for d in family:
                if d.dma in got:
                    cells.append(f"{d.dma} → “{got[d.dma].name}”")
                elif d.dma in tried:
                    cells.append(f"{d.dma} → not placed")
                    unplaced += 1
                else:
                    cells.append(f"{d.dma} → not tried")
            parts.append("; ".join(cells))
        text = " | ".join(parts)
        if len(text) > 1800:
            text = text[:1800] + "…"
        return (f"{len(parts)} groups of markets that share a first city - each line must show DIFFERENT TikTok markets, the right ones: {text}"), bool(unplaced)

    add(_timed("Look-alike TV markets", lookalikes))

    def zips():
        out = tiktok_geo.map_resolution(_resolution([_zip_area("90210"), _zip_area("10001")], "zip"), client)
        return f"{len(out.locations)} of 2 ZIP codes placed" + (f"; NOT placed: {out.unmapped}" if out.unmapped else ""), bool(out.unmapped)

    add(_timed("ZIP codes", zips))

    def states():
        probe = tiktok_geo.Mapping()
        tiktok_geo._map_states(["TX"], client, probe)
        if not probe.locations:
            raise TikTokAPIError("no state called Texas was found", kind="unexpected")
        return f"Texas → “{probe.locations[0].name}” ({probe.locations[0].tiktok_id})", False

    add(_timed("States", states))

    def national():
        res = GeoResolution(input_text="National", method="national", areas=[], geo_confidence="high")
        try:
            out, hint = ct.estimate(res, ct.Targeting(), 1, client=client), ""
        except TikTokAPIError as refused:
            if refused.kind != "invalid" or tiktok_client.optimization_goal_override():
                raise
            # TikTok's own documentation example pairs the Reach objective with CLICK: if that is what this account takes, say so
            out = ct.estimate(res, ct.Targeting(), 1, client=client, goal_override="CLICK")
            hint = (f" — but only as REACH / CLICK: TikTok refused REACH / REACH ({refused.message}). Set TIKTOK_OPTIMIZATION_GOAL=CLICK "
                    "in the app's secrets to use it everywhere.")
        s = out.size
        return (f"{s.lower:,.0f} – {s.upper:,.0f} accounts (TikTok's verdict: {tiktok_client.STAGE_LABELS.get(s.stage, 'not given')}); "
                f"lower end ×{out.factor:g} = {out.monthly['mid']:,.0f} impressions a month (mid){hint}"), bool(hint)

    add(_timed("Audience size · United States", national))

    def narrow():
        interests = client.targeting_list(["general_interest"]).get("general_interest") or []
        pick = next((i for i in interests if (i.get("level") or 1) > 1), interests[0] if interests else None)
        t = ct.Targeting.from_dict({"gender": "female", "age_groups": ["AGE_25_34"], "interest_category_ids": [pick["id"]] if pick else []})
        la = by_name.get("Los Angeles")
        national = GeoResolution(input_text="National", method="national", areas=[], geo_confidence="high")
        where, fallback = "Los Angeles", False
        try:
            if la is None:
                raise ValueError("no Los Angeles in the market list")
            out = ct.estimate(_resolution([_area(la)], "exact_dma"), t, 1, client=client)
        except ValueError:                       # the market could not be placed (the line above says so): still try the targeting
            out, where, fallback = ct.estimate(national, t, 1, client=client), "the whole U.S.", True
        s = out.size
        what = f"women 25–34{', ' + pick['name'] if pick else ''} in {where}"
        return (f"{what}: {s.lower:,.0f} – {s.upper:,.0f} accounts; lower end ×{out.factor:g} = {out.monthly['mid']:,.0f} impressions a month (mid)"
                + (" (Los Angeles could not be placed, see the markets line)" if fallback else "")), fallback

    add(_timed("Audience size · a narrow audience", narrow))

    def interests_without_ages():
        interests = client.targeting_list(["general_interest"]).get("general_interest") or []
        pick = next((i for i in interests if (i.get("level") or 1) > 1), interests[0] if interests else None)
        if pick is None:
            return "TikTok listed no interest category to try", True
        national = GeoResolution(input_text="National", method="national", areas=[], geo_confidence="high")
        out = ct.estimate(national, ct.Targeting.from_dict({"interest_category_ids": [pick["id"]]}), 1, client=client)
        s = out.size
        how = ("TikTok asked for the age groups to be spelled out (code 40065), so the five adult groups were sent - the app does that by itself"
               if out.adult_ages_sent else "TikTok took it with no age groups")
        return f"{pick['name']}, no ages chosen: {s.lower:,.0f} – {s.upper:,.0f} people; {how}", False

    add(_timed("Audience size · interests, no ages chosen", interests_without_ages))
    return checks


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check the TikTok connection end to end.")
    parser.add_argument("--sample", action="store_true", help="try a sample of the TV markets (about 40) instead of all 210")
    parser.add_argument("--json", action="store_true", help="print the checks as JSON")
    args = parser.parse_args(argv)
    checks = run_checks(all_dmas=not args.sample)
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
