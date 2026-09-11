"""
Ad Presence Intelligence for Entravision Proposal Builder.

Checks whether a prospective client is CURRENTLY running paid ads on Meta
(Facebook/Instagram), Google, and/or TikTok — and what language(s) those ads
are in. Built to feed Step 03's Strategy Brief (`strategy_brief.py`) with a
real, current competitive-intel signal instead of the model guessing from
static training data, e.g. "client already runs English-only Meta ads with
no Spanish creative — clear Entravision opening" is a far sharper, more
specific insight than anything web_search_preview can reliably surface on
its own.

WHY THIS ISN'T A CLEAN API CALL (read before changing the approach)
---------------------------------------------------------------------
None of the three platforms offers a general-purpose "is this business
advertising" API for ordinary COMMERCIAL ads (verified hands-on + via
research, Sep 2026):

  - Meta's Ad Library API (`/ads_archive`) is restricted to EU political /
    social-issue ads on standard developer access. It does NOT return
    ordinary commercial ad data, no matter how the request is verified.
  - Google publishes no API at all for the Ads Transparency Center. Its own
    frontend calls an internal, undocumented RPC
    (`/anji/_/rpc/SearchService/SearchCreatives`) — confirmed by inspecting
    the site's own network traffic. Google's terms explicitly forbid
    automated/programmatic use of that endpoint at any real volume; only
    human browsing of the page is sanctioned.
  - TikTok's Commercial Content API is gated to approved researchers /
    regulators, not marketers or agencies — application-only, and even then
    it's aimed at EU DSA compliance data, not general commercial coverage.

All three DO run a public, no-login-required *website* meant for a human to
browse (facebook.com/ads/library, adstransparency.google.com,
library.tiktok.com/ads) — confirmed live during development: querying
"Entravision" on Meta's public library returned real active ads (English
and Spanish) with no auth wall; searching "nike.com" on Google's
Transparency Center returned ~9K ads across 4 verified advertiser accounts;
TikTok's library returned a real (empty) result set for a date-scoped
query with no login.

This module drives THAT website with Playwright (already a project
dependency — see requirements.txt), one client at a time, on demand, the
same way a human researcher would for a single prospect at proposal time —
deliberately NOT a scheduled/bulk crawl across the whole client list. That
distinction matters for ToS exposure: occasional, single-lookup browser
automation at proposal-creation time reads very differently, risk-wise,
than an unattended scraper hammering these endpoints. Treat this as
"internal research aid," not as a data product to resell or run at scale.

REQUIRES (one-time, not yet in requirements.txt — see note in that file):
    pip install langdetect
    playwright install chromium      # browser binary; playwright itself
                                      # is already in requirements.txt

KNOWN v0 LIMITATIONS (flagged inline too):
  - Meta: ad-text extraction is regex-over-rendered-text, not the DOM
    (Meta's class names are obfuscated/hashed and churn often — parsing
    the human-visible text is more resilient than chasing CSS selectors,
    but still needs a periodic spot-check against the live page).
  - Google: the domain-summary page gives verified-advertiser identity and
    a rough total ad count, not per-ad creative text — so `languages` is
    always empty for Google in v0. Getting real ad copy means walking into
    each advertiser's own page, which is a slower, heavier scrape; left as
    a deliberate follow-up rather than guessed at.
  - TikTok: commercial (non-EU-political) coverage in this library is
    genuinely thin — a "0 ads found" result is common even for brands that
    clearly do run TikTok ads elsewhere. Treat TikTok's `active: False` as
    "not found in this public library," never as proof the client isn't on
    TikTok. The sample-ad text path also hasn't been validated against a
    real positive result yet (every test query during development returned
    zero matches in-window) — spot-check before trusting it.
"""
from __future__ import annotations

import re
import time
import urllib.parse
from typing import Optional

try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False

try:
    from langdetect import detect_langs, DetectorFactory
    DetectorFactory.seed = 0  # deterministic results across runs
    _HAS_LANGDETECT = True
except ImportError:
    _HAS_LANGDETECT = False


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_NAV_TIMEOUT_MS = 20000
_LANG_LABELS = {"en": "English", "es": "Spanish"}

# Meta/Google/TikTok all pad their rendered text with invisible characters
# (zero-width space/joiner, BOM, word-joiner — presumably layout/analytics
# artifacts) that do nothing but break naive regexes and — on Windows,
# whose console defaults to cp1252 — crash a plain `print()`. Built from
# raw codepoints (not literal/escaped characters in this source file) so
# there's no ambiguity about what's actually in the character class.
_INVISIBLE_CODEPOINTS = (0x200B, 0x200C, 0x200D, 0xFEFF, 0x2060)
_INVISIBLE_CHARS_RE = re.compile("[" + "".join(chr(c) for c in _INVISIBLE_CODEPOINTS) + "]")


def _clean_text(text: str) -> str:
    return _INVISIBLE_CHARS_RE.sub("", text)


# ---------------------------------------------------------------------------
# Shared browser helpers
# ---------------------------------------------------------------------------

def _fetch_rendered_text(url: str) -> Optional[str]:
    """
    Loads `url` in a headless Chromium tab and returns the fully-rendered
    page's visible text, or None if the browser/binary isn't available or
    the page failed to load. One-shot: launches and tears down its own
    browser per call (these checks run a handful of times per proposal,
    not in a hot loop, so the launch overhead is not worth pooling yet).
    """
    if not _HAS_PLAYWRIGHT:
        return None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=_UA, locale="en-US")
                page = context.new_page()
                page.set_default_navigation_timeout(_NAV_TIMEOUT_MS)
                page.set_default_timeout(_NAV_TIMEOUT_MS)
                try:
                    page.goto(url, wait_until="networkidle")
                except Exception:
                    page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)  # let client-side results hydrate
                return _clean_text(page.inner_text("body"))
            finally:
                browser.close()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def summarize_languages(snippets: list[str]) -> dict:
    """
    Given raw ad-copy snippets, returns e.g. {"en": 0.67, "es": 0.33} —
    only languages actually detected, {} if nothing usable came through.
    """
    if not _HAS_LANGDETECT:
        return {}
    counts: dict[str, int] = {}
    total = 0
    for text in snippets:
        text = (text or "").strip()
        if len(text) < 12:  # too short for langdetect to be reliable
            continue
        try:
            best = detect_langs(text)[0]
        except Exception:
            continue
        counts[best.lang] = counts.get(best.lang, 0) + 1
        total += 1
    if not total:
        return {}
    return {code: round(n / total, 2) for code, n in counts.items()}


def _lang_label(code: str) -> str:
    return _LANG_LABELS.get(code, code)


# ---------------------------------------------------------------------------
# Meta Ad Library (public web UI — no login required for "All ads")
# ---------------------------------------------------------------------------

def check_meta(query: str, country: str = "US") -> dict:
    """Live-checks the public Meta Ad Library for ads matching `query` (the
    business/Page name)."""
    result = {"platform": "meta", "checked": False, "active": False,
              "ad_count_estimate": 0, "sample_ads": [], "languages": {}, "note": ""}
    if not _HAS_PLAYWRIGHT:
        result["note"] = "playwright not installed — run: pip install playwright && playwright install chromium"
        return result
    if not query:
        result["note"] = "No client name provided."
        return result

    url = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
           f"&country={country}&q={urllib.parse.quote(query)}")
    text = _fetch_rendered_text(url)
    if text is None:
        result["note"] = "Meta Ad Library did not load (timeout, network error, or missing browser binary)."
        return result

    result["checked"] = True

    count_match = re.search(r"~?([\d,]+)\s+results?", text, re.IGNORECASE)
    if count_match:
        result["ad_count_estimate"] = int(count_match.group(1).replace(",", ""))

    ids = re.findall(r"Library ID:\s*(\d+)", text)
    starts = re.findall(r"Started running on ([A-Za-z]+ \d{1,2}, \d{4})", text)
    blocks = re.split(r"Library ID:\s*\d+", text)[1:]  # [0] is page chrome before the first ad

    junk = {"Platforms", "Menu", "Sponsored", "See ad details", "See summary details",
            "Learn more", "Learn More", "Active", "Inactive"}
    samples = []
    for i, block in enumerate(blocks[:8]):  # cap for speed/relevance
        lines = [l.strip() for l in block.strip().split("\n") if l.strip() and l.strip() not in junk]
        raw_text = " ".join(lines[:15])[:600]
        samples.append({
            "library_id": ids[i] if i < len(ids) else None,
            "started_running_on": starts[i] if i < len(starts) else None,
            "raw_text": raw_text,
        })

    result["sample_ads"] = samples
    result["active"] = bool(samples) or result["ad_count_estimate"] > 0
    result["languages"] = summarize_languages([s["raw_text"] for s in samples])
    if not result["active"]:
        result["note"] = "No active ads found for this query in Meta's public Ad Library."
    return result


# ---------------------------------------------------------------------------
# Google Ads Transparency Center (public web UI — no official API exists)
# ---------------------------------------------------------------------------

def check_google(domain: str, region: str = "US") -> dict:
    """Live-checks Google's Ads Transparency Center for `domain`. Returns
    verified-advertiser identity + a rough ad count; does NOT return
    per-ad creative text/language in v0 — see module docstring."""
    result = {"platform": "google", "checked": False, "active": False,
              "ad_count_estimate_display": None, "advertisers": [], "languages": {}, "note": ""}
    if not _HAS_PLAYWRIGHT:
        result["note"] = "playwright not installed — run: pip install playwright && playwright install chromium"
        return result

    domain = re.sub(r"^https?://", "", (domain or "").strip()).split("/")[0]
    if not domain:
        result["note"] = "No usable client website/domain provided."
        return result

    url = f"https://adstransparency.google.com/?region={region}&domain={urllib.parse.quote(domain)}"
    text = _fetch_rendered_text(url)
    if text is None:
        result["note"] = "Google Ads Transparency Center did not load (timeout, network error, or missing browser binary)."
        return result

    result["checked"] = True
    lowered = text.lower()
    if "no results" in lowered or "no ads found" in lowered:
        result["note"] = "No ads found for this domain in the Ads Transparency Center."
        return result

    count_match = re.search(r"~?([\d,.]+\s*[KM]?)\s+ads\b", text)
    if count_match:
        result["ad_count_estimate_display"] = count_match.group(1)

    advertisers: list[str] = []
    for name in re.findall(r"\n([A-Z][^\n]{1,80})\nVerified\n", "\n" + text):
        name = name.strip()
        if name and name not in advertisers:
            advertisers.append(name)
    result["advertisers"] = advertisers[:10]

    result["active"] = bool(advertisers) or bool(count_match)
    if not result["active"]:
        result["note"] = "Domain page loaded but no verified-advertiser or ad-count signal was found — worth a manual check."
    return result


# ---------------------------------------------------------------------------
# TikTok Commercial Content Library (public web UI)
# ---------------------------------------------------------------------------

def check_tiktok(query: str, country: str = "US", lookback_days: int = 90) -> dict:
    """Live-checks TikTok's Commercial Content Library. Coverage here is the
    thinnest of the three — see module docstring before trusting a
    negative result."""
    result = {"platform": "tiktok", "checked": False, "active": False,
              "ad_count_estimate": 0, "sample_ads": [], "languages": {}, "note": ""}
    if not _HAS_PLAYWRIGHT:
        result["note"] = "playwright not installed — run: pip install playwright && playwright install chromium"
        return result
    if not query:
        result["note"] = "No client name provided."
        return result

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - lookback_days * 86400 * 1000
    url = ("https://library.tiktok.com/ads?"
           f"region={country}&query={urllib.parse.quote(query)}"
           f"&start_time={start_ms}&end_time={now_ms}")
    text = _fetch_rendered_text(url)
    if text is None:
        result["note"] = "TikTok Commercial Content Library did not load (timeout, network error, or missing browser binary)."
        return result

    result["checked"] = True
    count_match = re.search(r"Total ads:\s*\n?\s*(\d+)", text)
    if count_match:
        result["ad_count_estimate"] = int(count_match.group(1))
    result["active"] = result["ad_count_estimate"] > 0

    if not result["active"]:
        result["note"] = (
            f"No ads found in TikTok's public Commercial Content Library in the last "
            f"{lookback_days} days — this library's commercial-ad coverage is thin, so "
            "treat this as inconclusive, not as confirmation the client isn't on TikTok."
        )
    else:
        # Best-effort — not yet validated against a real positive result;
        # spot-check before relying on the extracted text/language here.
        tail = text.split("Publication date", 1)[-1]
        raw = re.sub(r"\s+", " ", tail).strip()[:600]
        if raw:
            result["sample_ads"] = [{"raw_text": raw}]
            result["languages"] = summarize_languages([raw])
        result["note"] = "Ad-text extraction for TikTok is unverified in v0 — spot-check against the live library."

    return result


# ---------------------------------------------------------------------------
# Orchestrator — the entry point Strategy Brief (or anything else) calls
# ---------------------------------------------------------------------------

def check_ad_presence(client_name: str, client_website: str = "", country: str = "US") -> dict:
    """
    Runs all three platform checks for one client and returns a combined
    result plus a `summary` string ready to splice into an LLM prompt (see
    strategy_brief.py's `_build_prompt` for where this would plug in).
    """
    started = time.time()
    domain = re.sub(r"^https?://", "", (client_website or "").strip()).split("/")[0]

    meta = check_meta(client_name, country=country) if client_name else _skipped("meta", "no client name provided")
    google = check_google(domain, region=country) if domain else _skipped("google", "no client website provided")
    tiktok = check_tiktok(client_name, country=country) if client_name else _skipped("tiktok", "no client name provided")

    return {
        "meta": meta,
        "google": google,
        "tiktok": tiktok,
        "summary": _build_summary(meta, google, tiktok),
        "elapsed_seconds": round(time.time() - started, 1),
    }


def _skipped(platform: str, reason: str) -> dict:
    return {"platform": platform, "checked": False, "active": False, "note": reason}


def _build_summary(meta: dict, google: dict, tiktok: dict) -> str:
    lines = [
        _platform_line("Meta (Facebook/Instagram)", meta),
        _platform_line("Google Ads", google),
        _platform_line("TikTok", tiktok),
    ]
    return " ".join(l for l in lines if l)


def _platform_line(label: str, r: dict) -> str:
    if not r.get("checked"):
        return f"{label}: not checked ({r.get('note') or 'skipped'})."
    if not r.get("active"):
        return f"{label}: no active ads found. {r.get('note', '')}".strip()
    langs = r.get("languages") or {}
    lang_str = ""
    if langs:
        parts = [f"{round(p * 100)}% {_lang_label(c)}" for c, p in sorted(langs.items(), key=lambda kv: -kv[1])]
        lang_str = f" — ad copy sampled as {', '.join(parts)}"
    count = r.get("ad_count_estimate") or r.get("ad_count_estimate_display") or "some"
    return f"{label}: ACTIVE, ~{count} ads found{lang_str}."


if __name__ == "__main__":
    # Quick manual smoke test:
    #   python -m app.services.ad_presence "Business Name" "example.com"
    import json as _json
    import sys as _sys

    try:
        _sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    except Exception:
        pass

    name = _sys.argv[1] if len(_sys.argv) > 1 else "Nike"
    site = _sys.argv[2] if len(_sys.argv) > 2 else "nike.com"
    print(_json.dumps(check_ad_presence(name, site), indent=2, ensure_ascii=False))
