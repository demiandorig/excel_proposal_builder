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
library.tiktok.com/ads). This module drives THAT website with Playwright
(already a project dependency — see requirements.txt), one client at a
time, on demand, the same way a human researcher would for a single
prospect at proposal time — deliberately NOT a scheduled/bulk crawl across
the whole client list. That distinction matters for ToS exposure:
occasional, single-lookup browser automation at proposal-creation time
reads very differently, risk-wise, than an unattended scraper hammering
these endpoints. Treat this as "internal research aid," not as a data
product to resell or run at scale. Request volume per client is
deliberately kept small: up to 3 name variants each for Meta/TikTok + 1
domain lookup for Google = 7 page loads, worst case.

WHY NAME VARIANTS + A CONFIDENCE SCORE (not just one keyword search)
---------------------------------------------------------------------
The name typed into Proposal Builder is often NOT the exact Page/account
name the client (or its agency) actually advertises under — legal-entity
suffixes, DBAs, abbreviations, etc. So `check_meta`/`check_tiktok` search
several derived variants (see `name_variants`), not just the literal
client_name.

That broader search comes with a real accuracy cost, confirmed hands-on:
querying Meta's public library for "Entravision" returns ads that merely
route through Entravision-owned ad tech/domains (e.g. a school district's
ad served via an Entravision O&O property) — NOT Entravision itself
advertising. Every candidate ad is therefore tagged with a `confidence`
("high"/"medium"/"low") based on whether the client's name/domain tokens
actually appear in the ad's own Page name (high) or its body text (medium),
or neither (low — probably an unrelated advertiser incidentally matched by
Meta's own fuzzy search). Only high-confidence hits count toward the
headline `active` flag; everything else is still returned, clearly labeled,
for a human to eyeball rather than silently dropped or silently trusted.

REQUIRES (one-time, not yet in requirements.txt — see note in that file):
    pip install langdetect
    playwright install chromium      # browser binary; playwright itself
                                      # is already in requirements.txt

KNOWN v0 LIMITATIONS (flagged inline too):
  - Meta: ad-text extraction is regex-over-rendered-text, not the DOM
    (Meta's class names are obfuscated/hashed and churn often — parsing
    the human-visible text is more resilient than chasing CSS selectors,
    but still needs a periodic spot-check against the live page). The
    "first non-junk line is the Page name" heuristic is derived from 3
    real observed ad cards — a materially different card layout (e.g. a
    carousel/collection ad) could break it; low-confidence results should
    still get a human glance before being trusted as a true negative.
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

import logging
import re
import time
import urllib.parse
from typing import Optional

try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False

_logger = logging.getLogger(__name__)

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
    except Exception as e:
        # Deliberately still returns None to the caller (a live ad-library
        # check failing shouldn't break proposal generation) — but the
        # caller's own error surfaces this as one generic, indistinguishable
        # "timeout, network error, or missing browser binary" message with
        # no way to tell which. Logging the real exception here is the only
        # way to actually tell those apart afterward (this exact ambiguity
        # is what hid a real missing-browser-binary setup gap — see
        # scripts/post-merge.sh — until someone happened to check server
        # logs for it).
        _logger.warning("ad_presence: page load failed for %s: %s: %s", url, type(e).__name__, e)
        return None


# ---------------------------------------------------------------------------
# Name variants + match confidence
# ---------------------------------------------------------------------------

_LEGAL_SUFFIXES_RE = re.compile(
    r",?\s*\b(inc|l\.?l\.?c|corp(?:oration)?|co|company|ltd|group|holdings|enterprises)\b\.?\s*$",
    re.IGNORECASE,
)

# Not a full public-suffix-list implementation — just the compound TLDs
# likely to actually show up given Entravision's US/Hispanic-market focus.
_COMPOUND_TLDS = {"co.uk", "org.uk", "com.mx", "com.br", "com.co", "com.ar", "com.au", "com.pe"}


def _domain_brand_token(website: str) -> str:
    """Best-effort brand-name guess from a domain/URL, e.g. 'nike.com' ->
    'Nike', 'sub.my-brand.com.mx' -> 'My Brand'. Good enough for the common
    cases; not a substitute for a real public-suffix list."""
    domain = re.sub(r"^https?://", "", (website or "").strip()).split("/")[0].split(":")[0]
    domain = re.sub(r"^www\.", "", domain, flags=re.IGNORECASE)
    labels = [p for p in domain.split(".") if p]
    if not labels:
        return ""
    if len(labels) == 1:
        root = labels[0]
    elif ".".join(labels[-2:]).lower() in _COMPOUND_TLDS and len(labels) >= 3:
        root = labels[-3]
    else:
        root = labels[-2]
    words = re.split(r"[-_]+", root)
    return " ".join(w.capitalize() for w in words if w)


def name_variants(client_name: str, client_website: str = "", limit: int = 3) -> list[str]:
    """
    Short list of name variants worth searching, because the name typed
    into Proposal Builder often isn't the exact Page/account name the
    client (or its agency) actually advertises under. Capped at `limit` to
    keep request volume low — this runs per-platform, so keep it tight.
    """
    variants: list[str] = []

    def _add(v: str):
        v = (v or "").strip()
        if v and v.lower() not in [x.lower() for x in variants]:
            variants.append(v)

    if client_name:
        _add(client_name)
        _add(_LEGAL_SUFFIXES_RE.sub("", client_name).strip(" ,.-"))

    _add(_domain_brand_token(client_website))

    return variants[:limit]


def _normalize_tokens(text: str) -> set[str]:
    text = re.sub(r"[^\w\s]", " ", (text or ""), flags=re.UNICODE).lower()
    return {t for t in text.split() if len(t) >= 3}


def _reference_tokens(client_name: str, client_website: str) -> set[str]:
    tokens = _normalize_tokens(client_name)
    tokens |= _normalize_tokens(_domain_brand_token(client_website))
    return tokens


def _match_confidence(name_guess: str, raw_text: str, reference_tokens: set[str]) -> str:
    """
    'high'    — the candidate ad's own Page/advertiser name shares a
                significant word with the client's name/domain.
    'medium'  — no match in the name, but the client's name/domain shows up
                somewhere in the ad's body text (e.g. a "sponsored by" line).
    'low'     — neither — most likely an unrelated advertiser that Meta/
                TikTok's own fuzzy search happened to surface.
    'unknown' — not enough information to judge either way.
    """
    if not reference_tokens:
        return "unknown"
    if _normalize_tokens(name_guess) & reference_tokens:
        return "high"
    if _normalize_tokens(raw_text) & reference_tokens:
        return "medium"
    return "low"


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

_META_JUNK_EXACT = {
    "Platforms", "Menu", "Sponsored", "See ad details", "See summary details",
    "Learn more", "Learn More", "Active", "Inactive", "Open Dropdown", "Open dropdown",
}
_META_JUNK_PATTERNS = (
    re.compile(r"^Started running on ", re.IGNORECASE),
    re.compile(r"^\d+\s+ads?\s+use this creative", re.IGNORECASE),
)


def _is_meta_junk_line(line: str) -> bool:
    if line in _META_JUNK_EXACT:
        return True
    return any(p.match(line) for p in _META_JUNK_PATTERNS)


def _search_meta_once(query: str, country: str) -> dict:
    """One Meta Ad Library search for a single query string. Returns raw
    candidates — no confidence scoring yet (that needs the caller's
    client-name/domain context)."""
    out = {"checked": False, "ad_count_estimate": 0, "candidates": [], "note": ""}
    url = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
           f"&country={country}&q={urllib.parse.quote(query)}")
    text = _fetch_rendered_text(url)
    if text is None:
        out["note"] = "Meta Ad Library did not load (timeout, network error, or missing browser binary)."
        return out

    out["checked"] = True
    count_match = re.search(r"~?([\d,]+)\s+results?", text, re.IGNORECASE)
    if count_match:
        out["ad_count_estimate"] = int(count_match.group(1).replace(",", ""))

    ids = re.findall(r"Library ID:\s*(\d+)", text)
    starts = re.findall(r"Started running on ([A-Za-z]+ \d{1,2}, \d{4})", text)
    blocks = re.split(r"Library ID:\s*\d+", text)[1:]  # [0] is page chrome before the first ad

    for i, block in enumerate(blocks[:8]):  # cap for speed/relevance
        lines = [l.strip() for l in block.strip().split("\n") if l.strip() and not _is_meta_junk_line(l.strip())]
        name_guess = lines[0] if lines else ""
        if len(name_guess) > 60 or name_guess.endswith((".", "!", "?")):
            name_guess = ""  # looks like ad copy, not a Page name — don't trust it
        raw_text = " ".join(lines)[:600]
        out["candidates"].append({
            "library_id": ids[i] if i < len(ids) else None,
            "started_running_on": starts[i] if i < len(starts) else None,
            "page_name_guess": name_guess,
            "raw_text": raw_text,
        })
    return out


def check_meta(client_name: str, client_website: str = "", country: str = "US") -> dict:
    """Live-checks the public Meta Ad Library across a few name variants
    derived from `client_name`/`client_website`, then confidence-scores
    every candidate ad found against the client's actual name/domain."""
    result = {"platform": "meta", "checked": False, "active": False,
              "queries_tried": [], "sample_ads": [], "languages": {}, "note": ""}
    if not _HAS_PLAYWRIGHT:
        result["note"] = "playwright not installed — run: pip install playwright && playwright install chromium"
        return result

    variants = name_variants(client_name, client_website)
    if not variants:
        result["note"] = "No client name or website provided."
        return result
    result["queries_tried"] = variants

    ref_tokens = _reference_tokens(client_name, client_website)
    seen_ids: set[str] = set()
    samples: list[dict] = []
    any_checked = False

    for q in variants:
        r = _search_meta_once(q, country)
        if not r["checked"]:
            continue
        any_checked = True
        for cand in r["candidates"]:
            lib_id = cand.get("library_id")
            if lib_id and lib_id in seen_ids:
                continue
            if lib_id:
                seen_ids.add(lib_id)
            cand["confidence"] = _match_confidence(cand["page_name_guess"], cand["raw_text"], ref_tokens)
            cand["matched_query"] = q
            samples.append(cand)

    if not any_checked:
        result["note"] = "Meta Ad Library did not load for any name variant (timeout, network error, or missing browser binary)."
        return result

    result["checked"] = True
    result["sample_ads"] = samples[:12]

    high_conf = [s for s in samples if s["confidence"] == "high"]
    med_conf = [s for s in samples if s["confidence"] == "medium"]
    low_conf = [s for s in samples if s["confidence"] == "low"]

    result["active"] = bool(high_conf)
    result["high_confidence_count"] = len(high_conf)
    result["languages"] = summarize_languages([s["raw_text"] for s in (high_conf + med_conf)])

    if high_conf:
        result["note"] = ""
    elif med_conf:
        result["note"] = (f"No ad matched the client by Page name, but {len(med_conf)} ad(s) mention "
                           "the client's name/domain in the body text — worth a manual look.")
    elif low_conf:
        result["note"] = (f"Found {len(low_conf)} ad(s) under these name variants, but none matched the "
                           "client by Page identity — likely unrelated advertisers picked up by Meta's own "
                           "fuzzy search, not this client advertising. Treat as inconclusive, not a positive.")
    else:
        result["note"] = "No ads found for this client under any tried name variant in Meta's public Ad Library."

    return result


# ---------------------------------------------------------------------------
# Google Ads Transparency Center (public web UI — no official API exists)
# ---------------------------------------------------------------------------

def check_google(domain: str, region: str = "US") -> dict:
    """Live-checks Google's Ads Transparency Center for `domain`. Domain
    search is already precise (no name-variant fuzziness needed the way
    Meta/TikTok keyword search does) — returns verified-advertiser identity
    + a rough ad count; does NOT return per-ad creative text/language in v0
    — see module docstring."""
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

def _search_tiktok_once(query: str, country: str, lookback_days: int) -> dict:
    out = {"checked": False, "ad_count_estimate": 0, "candidates": [], "note": ""}
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - lookback_days * 86400 * 1000
    url = ("https://library.tiktok.com/ads?"
           f"region={country}&query={urllib.parse.quote(query)}"
           f"&start_time={start_ms}&end_time={now_ms}")
    text = _fetch_rendered_text(url)
    if text is None:
        out["note"] = "TikTok Commercial Content Library did not load (timeout, network error, or missing browser binary)."
        return out

    out["checked"] = True
    count_match = re.search(r"Total ads:\s*\n?\s*(\d+)", text)
    if count_match:
        out["ad_count_estimate"] = int(count_match.group(1))
    if out["ad_count_estimate"] > 0:
        # Best-effort — unvalidated against a real positive result (every
        # test query during development returned zero matches in-window).
        tail = text.split("Publication date", 1)[-1]
        raw = re.sub(r"\s+", " ", tail).strip()[:600]
        if raw:
            out["candidates"].append({"page_name_guess": "", "raw_text": raw})
    return out


def check_tiktok(client_name: str, client_website: str = "", country: str = "US", lookback_days: int = 90) -> dict:
    """Live-checks TikTok's Commercial Content Library across the same name
    variants as Meta. Coverage here is the thinnest of the three — see
    module docstring before trusting a negative result."""
    result = {"platform": "tiktok", "checked": False, "active": False,
              "queries_tried": [], "sample_ads": [], "languages": {}, "note": ""}
    if not _HAS_PLAYWRIGHT:
        result["note"] = "playwright not installed — run: pip install playwright && playwright install chromium"
        return result

    variants = name_variants(client_name, client_website)
    if not variants:
        result["note"] = "No client name or website provided."
        return result
    result["queries_tried"] = variants

    ref_tokens = _reference_tokens(client_name, client_website)
    samples: list[dict] = []
    any_checked = False
    total_ads = 0

    for q in variants:
        r = _search_tiktok_once(q, country, lookback_days)
        if not r["checked"]:
            continue
        any_checked = True
        total_ads += r["ad_count_estimate"]
        for cand in r["candidates"]:
            cand["confidence"] = _match_confidence(cand["page_name_guess"], cand["raw_text"], ref_tokens)
            cand["matched_query"] = q
            samples.append(cand)

    if not any_checked:
        result["note"] = "TikTok Commercial Content Library did not load for any name variant."
        return result

    result["checked"] = True
    result["sample_ads"] = samples
    high_or_med = [s for s in samples if s["confidence"] in ("high", "medium")]
    result["active"] = total_ads > 0
    result["languages"] = summarize_languages([s["raw_text"] for s in high_or_med])

    if not result["active"]:
        result["note"] = (
            f"No ads found in TikTok's public Commercial Content Library in the last "
            f"{lookback_days} days across {len(variants)} name variant(s) — this library's "
            "commercial-ad coverage is thin, so treat this as inconclusive, not as "
            "confirmation the client isn't on TikTok."
        )
    else:
        result["note"] = "Ad-text extraction for TikTok is unverified in v0 — spot-check against the live library."

    return result


# ---------------------------------------------------------------------------
# Orchestrator — the entry point Strategy Brief (or anything else) calls
# ---------------------------------------------------------------------------

def check_ad_presence(client_name: str, client_website: str = "", country: str = "US") -> dict:
    """
    Runs all three platform checks for one client and returns a combined
    result plus a `summary` string ready to splice into an LLM prompt (see
    strategy_brief.py's `_build_prompt` for where this plugs in).
    """
    started = time.time()
    domain = re.sub(r"^https?://", "", (client_website or "").strip()).split("/")[0]

    meta = check_meta(client_name, client_website, country=country) if (client_name or domain) else _skipped("meta", "no client name or website provided")
    google = check_google(domain, region=country) if domain else _skipped("google", "no client website provided")
    tiktok = check_tiktok(client_name, client_website, country=country) if (client_name or domain) else _skipped("tiktok", "no client name or website provided")

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
        return f"{label}: no confirmed active ads found. {r.get('note', '')}".strip()
    langs = r.get("languages") or {}
    lang_str = ""
    if langs:
        parts = [f"{round(p * 100)}% {_lang_label(c)}" for c, p in sorted(langs.items(), key=lambda kv: -kv[1])]
        lang_str = f" — ad copy sampled as {', '.join(parts)}"
    count = r.get("high_confidence_count") or r.get("ad_count_estimate") or r.get("ad_count_estimate_display") or "some"
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
