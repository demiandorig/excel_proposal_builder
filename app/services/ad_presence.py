"""
Ad Presence Intelligence for Entravision Proposal Builder.

Checks whether a prospective client is CURRENTLY running paid ads on Meta
(Facebook/Instagram), Google, and/or TikTok — and what language(s) those ads
are in. Powers the "Digital Ad Presence" section of Step 03's Strategy Brief:
the planner runs it on demand (its own endpoint, after the brief exists), and
the plain-text `summary` is what gets spliced into the LLM prompt when the
brief is regenerated. That gives the brief a real, current competitive-intel
signal instead of the model guessing from static training data, e.g. "client
already runs English-only Meta ads with no Spanish creative — clear
Entravision opening" is a far sharper, more specific insight than anything
web search can reliably surface on its own.

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
deliberately kept small: up to 3 name variants for Meta + 1 domain lookup
for Google = 4 page loads for a US lookup. TikTok adds up to 3 more ONLY
for lookups in a region its library actually covers (EU/EEA, UK,
Switzerland) — 7 page loads is the hard worst case, and a US lookup never
touches TikTok at all (see "TIKTOK" below).

Those few page loads share ONE headless Chromium per lookup and run a few at
a time (`_MAX_CONCURRENT_PAGES`) instead of strictly one after another. That
changes nothing about the volume above — same handful of pages a person
would open in a few browser tabs — it just stops each one from paying for
its own browser launch and a network-idle wait. Images, video and fonts are
never downloaded (the text is all we read).

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
advertising; querying "Nike" returns mostly retailers (Foot Locker, JD
Sports...) whose ad copy mentions Nike. Every candidate ad is therefore
tagged with a `confidence`:
  - "high"   — the ad's own Page/advertiser name shares a distinctive word
               with the client's name/domain, OR (Meta) the ad's
               destination domain IS the client's website domain;
  - "medium" — the client only shows up in the ad's text;
  - "low"    — neither (an unrelated advertiser the platform's own fuzzy
               search happened to surface).
Only high-confidence hits count toward `status: "active"`; everything else
is still returned, clearly labeled, for a human to eyeball rather than
silently dropped or silently trusted.

OUTPUT CONTRACT (what `check_ad_presence` returns — the UI/export rely on it)
---------------------------------------------------------------------------
{"meta": P, "google": P, "tiktok": P, "summary": str,
 "elapsed_seconds": float, "country": str}

Every platform result P has: platform, status, checked, active, note,
search_urls, library_url, languages. `status` is one of:
  - "active"      confirmed ads found (Meta/TikTok: >=1 high-confidence ad;
                  Google: a verified advertiser or a non-zero ad count)
  - "not_found"   the library loaded fine and nothing confirmed was found
  - "unsupported" can't be verified for this region (TikTok outside
                  EU/EEA/UK/CH — including every US lookup)
  - "error"       page didn't load / no browser / unexpected failure
  - "not_checked" no usable input (no name / no website)
`checked` is True only when the library was actually loaded and read;
`active` is exactly `status == "active"`. Platform-specific keys: see
`_meta_result` / `_google_result` / `_tiktok_result`. One platform failing
never fails the others, and no exception ever escapes `check_ad_presence`.

REQUIRES (one-time — see requirements.txt / scripts/post-merge.sh):
    pip install langdetect
    playwright install chromium      # browser binary; playwright itself
                                      # is already in requirements.txt

KNOWN LIMITATIONS (flagged inline too):
  - Meta: ad-text extraction is regex-over-rendered-text, not the DOM
    (Meta's class names are obfuscated/hashed and churn often — parsing the
    human-visible text is more resilient than chasing CSS selectors, but
    still needs a periodic spot-check against the live page). Each card is
    parsed on its own (split on "Library ID"); the Page name is the line
    right before "Sponsored", then body copy, then the link section
    (destination domain in caps, headline, CTA button). Validated against
    real Nike / Barringer Law Firm result pages (Sep 2026): single-image,
    video, multi-version, carousel, and EU-transparency cards. Headline
    detection for a card with no destination-domain line is heuristic.
    Blocking images makes Meta show a harmless "Turn off ad blocker" modal
    after the results; it doesn't affect them today, but it's the first
    thing to check if Meta ever starts withholding results.
  - Google: the domain-summary page gives verified-advertiser identity and
    a rough total ad count, not per-ad creative text — so `languages` is
    always empty for Google. Getting real ad copy means walking into each
    advertiser's own page, which is a slower, heavier scrape; left as a
    deliberate follow-up rather than guessed at.
  - TIKTOK: its public Commercial Content Library only lists ads shown in
    the EU/EEA, UK and Switzerland (a US search silently comes back with an
    empty country and "Total ads: 0" every time). So a US lookup returns
    "unsupported" with ZERO page loads rather than a meaningless negative.
    Where it is covered, the advertiser-name search (`adv_name=`) also
    matches other advertisers' ads that mention the name (Nike -> "THE SOLE
    SUPPLIER LIMITED", "Otto"), and the listing shows no ad copy — so
    confidence is scored on the advertiser name only, `languages` stays
    empty, and sample ads carry advertiser/dates/reach only. That layout
    was validated against real GB/DE result pages (Sep 2026) only.
"""
from __future__ import annotations

import asyncio
import contextvars
import datetime as _dt
import logging
import os
import re
import shutil
import sys
import time
import urllib.parse
from typing import Optional

try:
    from playwright.async_api import async_playwright
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
_LANG_LABELS = {"en": "English", "es": "Spanish"}

# ---------------------------------------------------------------------------
# Result contract
# ---------------------------------------------------------------------------

STATUS_ACTIVE = "active"
STATUS_NOT_FOUND = "not_found"
STATUS_UNSUPPORTED = "unsupported"
STATUS_ERROR = "error"
STATUS_NOT_CHECKED = "not_checked"
STATUSES = (STATUS_ACTIVE, STATUS_NOT_FOUND, STATUS_UNSUPPORTED, STATUS_ERROR, STATUS_NOT_CHECKED)

META_LIBRARY_URL = "https://www.facebook.com/ads/library/"
GOOGLE_LIBRARY_URL = "https://adstransparency.google.com/"
TIKTOK_LIBRARY_URL = "https://library.tiktok.com/ads"

_PLATFORM_LABELS = {
    "meta": "Meta Ad Library",
    "google": "Google Ads Transparency Center",
    "tiktok": "TikTok Commercial Content Library",
}

_SAMPLE_ADS_MAX = 12
_BODY_MAX_CHARS = 600
_ADVERTISERS_MAX = 10

_PLAYWRIGHT_MISSING_NOTE = "playwright not installed — run: pip install playwright && playwright install chromium"

# ---------------------------------------------------------------------------
# Browser / timing knobs
# ---------------------------------------------------------------------------

# goto(..., wait_until="domcontentloaded") budget. We never wait for
# "networkidle": Meta keeps long-lived connections open, so that wait mostly
# just burned the whole timeout before the old code gave up on it.
_NAV_TIMEOUT_MS = 15000
# Pages open at once within one lookup (the lookup's total volume is fixed by
# the name variants — this only controls how many overlap).
_MAX_CONCURRENT_PAGES = 3
# Hard ceiling for a whole lookup; anything still running is stopped and
# reported as an error for that platform only.
_OVERALL_TIMEOUT_S = 60.0
# After a page's results marker shows up, wait until its text stops changing
# for this long (capped) so a half-rendered list isn't read.
_SETTLE_QUIET_MS = 350
_SETTLE_MAX_MS = 1500
_BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})

# Meta/Google/TikTok all pad their rendered text with invisible characters
# (zero-width space/joiner, BOM, word-joiner — presumably layout/analytics
# artifacts) that do nothing but break naive regexes and — on Windows,
# whose console defaults to cp1252 — crash a plain `print()`. Built from
# raw codepoints (not literal/escaped characters in this source file) so
# there's no ambiguity about what's actually in the character class.
_INVISIBLE_CODEPOINTS = (0x200B, 0x200C, 0x200D, 0xFEFF, 0x2060)
_INVISIBLE_CHARS_RE = re.compile("[" + "".join(chr(c) for c in _INVISIBLE_CODEPOINTS) + "]")


def _browser_env() -> dict[str, str]:
    """Give Playwright's child Chromium access to Replit's Nix libraries."""
    env = os.environ.copy()
    nix_ldflags = env.get("NIX_LDFLAGS", "")
    nix_library_paths = re.findall(r"(?:^|\s)-L(\S+)", nix_ldflags)
    if nix_library_paths:
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = ":".join(
            dict.fromkeys(nix_library_paths + ([existing] if existing else []))
        )
    return env


def _chromium_executable() -> Optional[str]:
    """Return a usable Chromium path before Playwright's cache fallback.

    The interactive Replit workspace exposes managed Chromium at
    ``/repl/tools/bin/chromium``. Published runtimes may not put that wrapper
    on PATH, so deployment can also provide an explicit path through
    ``CHROMIUM_EXECUTABLE_PATH``. If none of these are present, returning None
    deliberately lets Playwright use the browser installed by the deployment
    build.
    """
    candidates = [
        os.environ.get("CHROMIUM_EXECUTABLE_PATH"),
        "/repl/tools/bin/chromium",
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
    ]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _clean_text(text: str) -> str:
    return _INVISIBLE_CHARS_RE.sub("", text)


def _page_lines(text: str) -> list[str]:
    """Rendered page text -> stripped, non-empty lines (invisible padding and
    whitespace-only lines — e.g. the NBSP spacers between carousel cards —
    removed). Every parser below works on this."""
    lines = (line.strip() for line in _clean_text(text or "").splitlines())
    return [line for line in lines if line]


def _norm_line(line: str) -> str:
    """Case/whitespace/apostrophe-insensitive form of a line, for comparing
    against the fixed UI strings each library renders."""
    return re.sub(r"\s+", " ", (line or "").replace("’", "'")).strip().lower()


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _unique(items) -> list:
    out = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---------------------------------------------------------------------------
# Shared browser (one per lookup) + the page-fetch layer
# ---------------------------------------------------------------------------

_READY_JS = """([source, flags]) => {
  const body = document.body;
  return !!body && new RegExp(source, flags).test(body.innerText || "");
}"""

_SETTLE_JS = """(quietMs) => {
  const body = document.body;
  const len = body ? (body.innerText || "").length : 0;
  const now = Date.now();
  if (window.__adPresenceLen !== len) {
    window.__adPresenceLen = len;
    window.__adPresenceSince = now;
    return false;
  }
  return now - window.__adPresenceSince >= quietMs;
}"""

_INNER_TEXT_JS = "() => (document.body ? document.body.innerText : '') || ''"


async def _block_heavy_resources(route) -> None:
    """Route handler: never download images/video/fonts — only the rendered
    text is read, and on Meta's image-heavy result grid this is most of the
    bytes. (CSS is deliberately kept: without it, hidden UI would leak into
    innerText.)"""
    try:
        if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()
    except Exception:
        pass  # page/context already closing — nothing left to route


class _BrowserSession:
    """ONE headless Chromium shared by every page load of a single
    `check_ad_presence()` call, launched lazily on the first real fetch (so
    tests that monkeypatch the fetch layer never start a browser) and
    closed when the lookup ends. Each page load gets its own fresh context
    (no cookies shared between searches) and at most `max_pages` load at
    once."""

    def __init__(self, max_pages: int = _MAX_CONCURRENT_PAGES):
        self._slots = asyncio.Semaphore(max(1, max_pages))
        self._launch_lock = asyncio.Lock()
        self._playwright = None
        self._browser = None
        self._launch_failed = False
        self.page_loads = 0

    async def _get_browser(self):
        async with self._launch_lock:
            if self._browser is not None or self._launch_failed:
                return self._browser
            if not _HAS_PLAYWRIGHT:
                self._launch_failed = True
                return None
            launch_options = {"headless": True, "env": _browser_env()}
            executable = _chromium_executable()
            if executable:
                launch_options["executable_path"] = executable
                _logger.info("ad_presence: using Chromium executable %s", executable)
            else:
                _logger.info("ad_presence: using Playwright-managed Chromium")
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(**launch_options)
            except Exception as e:
                _logger.warning("ad_presence: Chromium failed to launch: %s: %s", type(e).__name__, e)
                self._launch_failed = True
                await self._stop_playwright()
            return self._browser

    async def fetch(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """(rendered_visible_text, fail_reason) — see `_fetch_rendered_text`.
        Never raises."""
        ready_pattern, ready_timeout_ms = _ready_marker_for(url)
        async with self._slots:
            browser = await self._get_browser()
            if browser is None:
                return None, "no_browser"
            self.page_loads += 1
            context = None
            try:
                context = await browser.new_context(user_agent=_UA, locale="en-US", service_workers="block")
                await context.route("**/*", _block_heavy_resources)
                page = await context.new_page()
                page.set_default_navigation_timeout(_NAV_TIMEOUT_MS)
                page.set_default_timeout(_NAV_TIMEOUT_MS)
                try:
                    await page.goto(url, wait_until="domcontentloaded")
                except Exception as e:
                    _logger.warning("ad_presence: page load failed for %s: %s: %s", url, type(e).__name__, e)
                    return None, "navigation"
                if ready_pattern:
                    # Wait for the platform's own "results are in" text (not a
                    # fixed sleep, not network idle). On timeout the text is
                    # still read — each parser checks for a recognizable
                    # results/empty-state layout and reports anything else as
                    # an error rather than a false "no ads".
                    try:
                        await page.wait_for_function(_READY_JS, arg=[ready_pattern, "i"],
                                                     polling=200, timeout=ready_timeout_ms)
                    except Exception:
                        _logger.info("ad_presence: no results marker within %dms for %s", ready_timeout_ms, url)
                    else:
                        try:
                            await page.wait_for_function(_SETTLE_JS, arg=_SETTLE_QUIET_MS,
                                                         polling=120, timeout=_SETTLE_MAX_MS)
                        except Exception:
                            pass  # still changing — read what's there
                text = await page.evaluate(_INNER_TEXT_JS)
                return _clean_text(text or ""), None
            except Exception as e:
                # Browser launched fine (past "no_browser"), so anything
                # unexpected this far in is a navigation-class failure.
                _logger.warning("ad_presence: unexpected failure for %s: %s: %s", url, type(e).__name__, e)
                return None, "navigation"
            finally:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        pass

    async def close(self) -> None:
        """Never raises."""
        browser, self._browser = self._browser, None
        if browser is not None:
            try:
                await asyncio.wait_for(browser.close(), timeout=10)
            except Exception as e:
                _logger.warning("ad_presence: closing Chromium failed: %s: %s", type(e).__name__, e)
        await self._stop_playwright()

    async def _stop_playwright(self) -> None:
        pw, self._playwright = self._playwright, None
        if pw is not None:
            try:
                await asyncio.wait_for(pw.stop(), timeout=10)
            except Exception as e:
                _logger.warning("ad_presence: stopping Playwright failed: %s: %s", type(e).__name__, e)


# The lookup-wide browser, visible to every platform check (and the tasks
# they fan out into — asyncio copies the context into each new task) without
# threading a session argument through every function signature. Unset
# when a check_* function is called on its own; it then gets a one-shot
# browser for that call.
_ACTIVE_SESSION: contextvars.ContextVar[Optional[_BrowserSession]] = contextvars.ContextVar(
    "ad_presence_browser_session", default=None)


async def _fetch_rendered_text(url: str) -> tuple[Optional[str], Optional[str]]:
    """
    THE page-fetch layer (tests monkeypatch exactly this). Loads `url` in the
    lookup's shared headless Chromium and returns
    (rendered_visible_text, fail_reason). fail_reason is None on success,
    else:
      - "no_browser" — Chromium itself never launched (almost always a
        missing/incomplete `playwright install chromium` on THIS server —
        nothing to do with Meta/Google/TikTok at all).
      - "navigation" — the browser launched fine but the page itself
        didn't load in time (a real network issue, a slow page, or the
        site declining the request).

    This distinction used to be collapsed into one indistinguishable "did
    not load — timeout, network error, or missing browser binary" message,
    which is exactly what let a real missing-browser-binary setup gap hide
    until someone happened to check server logs for it (see
    scripts/post-merge.sh). Surfacing it here means the very next failed
    result already says which of the two very different problems it is,
    without needing log access.
    """
    session = _ACTIVE_SESSION.get()
    if session is not None:
        return await session.fetch(url)
    session = _BrowserSession(max_pages=1)
    try:
        return await session.fetch(url)
    finally:
        await session.close()


async def _fetch_page(url: str) -> tuple[Optional[str], Optional[str]]:
    """Calls the fetch layer and turns anything it raises into an
    "unexpected" failure for just this page — the fail-soft boundary every
    platform check goes through."""
    try:
        text, reason = await _fetch_rendered_text(url)
    except Exception as e:
        _logger.warning("ad_presence: fetch failed for %s: %s: %s", url, type(e).__name__, e)
        return None, "unexpected"
    if text is None and not reason:
        reason = "navigation"
    return text, (None if text is not None else reason)


def _was_opened(fail_reason: Optional[str]) -> bool:
    """Did this lookup actually request the page from the site? (Counted in
    `search_urls` even when the load then failed; not when there was no
    browser to request it with.)"""
    return fail_reason in (None, "navigation")


_FAIL_REASON_PRIORITY = ("no_browser", "not_ready", "navigation", "unexpected")


def _worst_reason(searches: list[dict]) -> Optional[str]:
    reasons = {s.get("fail_reason") for s in searches}
    return next((r for r in _FAIL_REASON_PRIORITY if r in reasons), "navigation")


def _load_failure_note(label: str, reason: Optional[str]) -> str:
    if reason == "no_browser":
        return (f"{label} check couldn't run: no Chromium browser is installed on this "
                "server. This is a server setup gap, not a Meta/Google/TikTok problem — "
                "`playwright install chromium` needs to run as part of THIS server's own "
                "deploy/build step (installing the `playwright` pip package alone does not "
                "download the actual browser).")
    if reason == "not_ready":
        return (f"{label} loaded, but its results never appeared (slow page, or the site "
                "showed a login/verification page instead) — open the library link to check by hand.")
    if reason == "unexpected":
        return f"{label} check failed with an unexpected error — open the library link to check by hand."
    return f"{label} did not load (timeout or network error)."


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------

def _normalize_country(country: Optional[str]) -> str:
    """ISO-3166 alpha-2 (upper-cased; "UK" -> "GB"), or "ALL". Anything
    unrecognizable falls back to "US" — the app's default market."""
    code = (country or "").strip().upper()
    if code == "UK":
        code = "GB"
    if code == "ALL" or re.fullmatch(r"[A-Z]{2}", code):
        return code
    return "US"


def _bare_host(website: str) -> str:
    """'https://WWW.Nike.com:443/us/?x#y' -> 'nike.com'. Strips scheme,
    userinfo, port, path/query/fragment, a trailing dot and a leading
    'www.'/'www2.' (the Google Ads Transparency Center returns ZERO ads for
    'www.barringerlawfirm.com' but 8 for 'barringerlawfirm.com')."""
    host = (website or "").strip().lower()
    host = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", host)
    host = re.split(r"[/?#]", host, maxsplit=1)[0]
    host = host.rsplit("@", 1)[-1].split(":", 1)[0].strip(".")
    return re.sub(r"^www\d*\.", "", host)


def _normalize_domain(website: str) -> str:
    """`_bare_host`, but only if the result actually looks like a domain."""
    host = _bare_host(website)
    if not host or "." not in host or re.search(r"\s", host):
        return ""
    return host


# A "website" that's really a social/link-in-bio page says nothing about the
# client's own ads: a Google domain lookup for facebook.com would come back
# ACTIVE for Meta Platforms itself, and "Facebook" would become a Meta search
# variant/match token. Treated as "no website" instead.
_PLATFORM_DOMAINS = frozenset({
    "facebook.com", "fb.com", "fb.me", "m.me", "instagram.com", "tiktok.com",
    "youtube.com", "youtu.be", "google.com", "g.page", "linkedin.com",
    "twitter.com", "x.com", "linktr.ee", "yelp.com", "wa.me", "whatsapp.com",
})


def _is_platform_domain(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in _PLATFORM_DOMAINS)


def _client_domain(website: str) -> str:
    """The client's own domain for domain-based checks, '' if none usable."""
    domain = _normalize_domain(website)
    return "" if (not domain or _is_platform_domain(domain)) else domain


def _domain_matches(ad_domain: Optional[str], client_domain: str) -> bool:
    """An ad's displayed destination ('NIKE.COM', 'WWW.NIKE.COM',
    'SHOP.NIKE.COM') points at the client's own website domain."""
    if not ad_domain or not client_domain:
        return False
    domain = _normalize_domain(ad_domain)
    return bool(domain) and (domain == client_domain or domain.endswith("." + client_domain))


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

# Words too generic to identify a business on their own: without this, any
# "... Law Firm" Page matched "Barringer Law Firm LLC" by sharing "law"/
# "firm"/"llc" and counted as a HIGH-confidence (=> ACTIVE) hit. Dropped from
# the client's reference tokens unless nothing else is left.
_GENERIC_NAME_TOKENS = frozenset({
    "inc", "llc", "pllc", "llp", "corp", "corporation", "company", "ltd", "limited",
    "group", "holdings", "enterprises", "incorporated",
    "the", "and", "law", "firm", "office", "offices", "services", "service",
    "center", "centre", "associates", "partners", "agency", "solutions",
})


def _domain_brand_token(website: str) -> str:
    """Best-effort brand-name guess from a domain/URL, e.g. 'nike.com' ->
    'Nike', 'sub.my-brand.com.mx' -> 'My Brand'. Good enough for the common
    cases; not a substitute for a real public-suffix list. '' for a social/
    platform URL (see `_PLATFORM_DOMAINS`)."""
    domain = _bare_host(website)
    if not domain or _is_platform_domain(domain):
        return ""
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
    return (tokens - _GENERIC_NAME_TOKENS) or tokens


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


_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


def _parse_display_date(value: Optional[str], formats: tuple[str, ...]) -> Optional[_dt.date]:
    for fmt in formats:
        try:
            return _dt.datetime.strptime((value or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def _recency_key(date: Optional[_dt.date]) -> tuple[int, int]:
    """Sort key: most recent first, unknown dates last."""
    return (0, -date.toordinal()) if date else (1, 0)


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def _detect_language(text: str) -> Optional[str]:
    """langdetect code for one snippet ('en', 'es', ...), None if too short
    to be reliable, undetectable, or langdetect isn't installed."""
    if not _HAS_LANGDETECT:
        return None
    text = (text or "").strip()
    if len(text) < 12:  # too short for langdetect to be reliable
        return None
    try:
        return detect_langs(text)[0].lang
    except Exception:
        return None


def _language_mix(codes: list[Optional[str]]) -> dict:
    codes = [c for c in codes if c]
    if not codes:
        return {}
    counts: dict[str, int] = {}
    for code in codes:
        counts[code] = counts.get(code, 0) + 1
    return {code: round(n / len(codes), 2) for code, n in counts.items()}


def summarize_languages(snippets: list[str]) -> dict:
    """
    Given raw ad-copy snippets, returns e.g. {"en": 0.67, "es": 0.33} —
    only languages actually detected, {} if nothing usable came through.
    """
    return _language_mix([_detect_language(s) for s in snippets])


def _lang_label(code: str) -> str:
    return _LANG_LABELS.get(code, code)


# ---------------------------------------------------------------------------
# Platform result skeletons (every key of the contract, always present)
# ---------------------------------------------------------------------------

def _base_result(platform: str, library_url: str) -> dict:
    return {
        "platform": platform,
        "status": STATUS_NOT_CHECKED,
        "checked": False,
        "active": False,
        "note": "",
        "search_urls": [],
        "library_url": library_url,
        "languages": {},
    }


def _meta_result() -> dict:
    result = _base_result("meta", META_LIBRARY_URL)
    result.update({"queries_tried": [], "high_confidence_count": 0,
                   "result_count_estimate": None, "sample_ads": []})
    return result


def _google_result() -> dict:
    result = _base_result("google", GOOGLE_LIBRARY_URL)
    result.update({"ad_count_estimate_display": None, "advertisers": [], "search_url": ""})
    return result


def _tiktok_result() -> dict:
    result = _base_result("tiktok", TIKTOK_LIBRARY_URL)
    result.update({"queries_tried": [], "sample_ads": [],
                   # Extras (not in the minimum contract): same meaning as Meta's.
                   "high_confidence_count": 0, "result_count_estimate": None})
    return result


_RESULT_FACTORIES = {"meta": _meta_result, "google": _google_result, "tiktok": _tiktok_result}


def _finish(result: dict, status: str, note: str) -> dict:
    result["status"] = status
    result["active"] = status == STATUS_ACTIVE
    if status in (STATUS_ERROR, STATUS_NOT_CHECKED, STATUS_UNSUPPORTED):
        result["checked"] = False
    result["note"] = note
    return result


def _crashed_result(platform: str, exc: BaseException) -> dict:
    return _finish(_RESULT_FACTORIES[platform](), STATUS_ERROR,
                   f"{_PLATFORM_LABELS[platform]} check failed unexpectedly ({type(exc).__name__}) — "
                   "open the library link to check by hand.")


def _timed_out_result(platform: str) -> dict:
    return _finish(_RESULT_FACTORIES[platform](), STATUS_ERROR,
                   f"{_PLATFORM_LABELS[platform]} check took longer than {int(_OVERALL_TIMEOUT_S)}s "
                   "and was stopped — open the library link to check by hand.")


# ---------------------------------------------------------------------------
# Meta Ad Library (public web UI — no login required for "All ads")
# ---------------------------------------------------------------------------

_META_READY_MARKER = r"Library ID:|No ads match"
# Meta is the slowest page by far (results render after its own JS boots and
# a search XHR returns — ~2-6s after DOMContentLoaded on a desktop, measured
# Sep 2026). Only matters when it's slow: a normal load returns as soon as the
# marker shows up.
_META_READY_TIMEOUT_MS = 10000

_META_LIBRARY_ID_RE = re.compile(r"Library ID:\s*(\d+)")
_META_STARTED_RE = re.compile(r"Started running on\s+([A-Z][a-z]{2,9}\.? \d{1,2}, \d{4})")
_META_RESULT_COUNT_RE = re.compile(r"^~?\s*\d[\d,.]*\s*[KMB]?\s+results?$", re.IGNORECASE)
_META_EMPTY_RE = re.compile(r"No ads match", re.IGNORECASE)
_META_VIDEO_TIME_RE = re.compile(r"^(?:\d{1,2}:)?\d{1,2}:\d{2}\s*/\s*(?:\d{1,2}:)?\d{1,2}:\d{2}$")
# The destination-domain line of an ad's link section is always rendered in
# caps: NIKE.COM, WWW.INSTAGRAM.COM, PLAY.GOOGLE.COM.
_META_DOMAIN_LINE_RE = re.compile(r"^(?:[A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?\.)+[A-Z]{2,24}$")
_META_STATUS_LINES = frozenset({"Active", "Inactive"})
# What a video renders as when its media can't play — always, here, since
# media downloads are blocked. The player's own "Learn more" help link
# follows it; that is NOT the ad's CTA.
_META_VIDEO_ERROR_LINES = frozenset({"sorry, we're having trouble playing this video."})

_META_JUNK_EXACT = frozenset({
    "platforms", "menu", "sponsored", "see ad details", "see summary details",
    "learn more", "active", "inactive", "open dropdown", "open drop-down",
    "this ad has multiple versions", "eu transparency",
}) | _META_VIDEO_ERROR_LINES
_META_JUNK_PATTERNS = (
    re.compile(r"^Started running on ", re.IGNORECASE),
    re.compile(r"^\d+\s+ads?\s+use this creative", re.IGNORECASE),
    _META_VIDEO_TIME_RE,
)

# Meta's call-to-action button labels (compared case-insensitively).
_META_CTA_LABELS = frozenset({
    "apply now", "book now", "book travel", "buy now", "buy tickets", "call now",
    "check availability", "contact us", "donate now", "download", "get access",
    "get directions", "get offer", "get promotions", "get quote", "get showtimes",
    "get started", "get tickets", "get updates", "inquire now", "install app",
    "install now", "interested", "learn more", "like page", "listen now",
    "open link", "order now", "play game", "pre-order now", "preorder now",
    "pre-register", "remind me", "request time", "save", "see details", "see menu",
    "send instagram message", "send message", "send whatsapp message", "shop now",
    "sign up", "start order", "subscribe", "try now", "use app", "view instagram profile",
    "visit instagram profile", "vote now", "watch more", "watch now", "whatsapp",
})


def _is_meta_junk_line(line: str) -> bool:
    if _norm_line(line) in _META_JUNK_EXACT:
        return True
    return any(p.match(line.strip()) for p in _META_JUNK_PATTERNS)


def _is_meta_video_line(line: str) -> bool:
    return bool(_META_VIDEO_TIME_RE.match(line.strip())) or _norm_line(line) in _META_VIDEO_ERROR_LINES


def _is_meta_cta(line: str) -> bool:
    return _norm_line(line) in _META_CTA_LABELS


def _is_meta_domain_line(line: str) -> bool:
    return bool(_META_DOMAIN_LINE_RE.match(line.strip()))


def _looks_like_page_name(line: str) -> bool:
    return 0 < len(line) <= 60 and not line.endswith((".", "!", "?"))


def _looks_like_headline(line: str) -> bool:
    return len(line) <= 60 and len(line.split()) <= 8 and not line.endswith((".", "!", "?", ":", ";", ",", "…"))


def _meta_search_url(query: str, country: str) -> str:
    return ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
            f"&country={country}&q={urllib.parse.quote(query)}")


def _meta_ad_url(library_id: str) -> str:
    return f"https://www.facebook.com/ads/library/?id={library_id}"


def _split_meta_ad_content(content: list[str]) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
    """Lines after "Sponsored" -> (body, headline, cta, destination_domain).

    Layout (observed): body copy, then an optional video line (a "0:03 /
    0:15" timestamp, or the "Sorry, we're having trouble playing this
    video." + "Learn more" pair when media is blocked), then the link
    section: DESTINATION.COM / headline / link description / CTA button. A
    carousel repeats [domain] / headline / CTA per card; the first one is
    reported."""
    body: list[str] = []
    i = 0
    while i < len(content) and not (_is_meta_video_line(content[i]) or _is_meta_domain_line(content[i])
                                    or _is_meta_cta(content[i])):
        body.append(content[i])
        i += 1
    rest = content[i:]
    body_ended_on_cta = bool(rest) and _is_meta_cta(rest[0])

    tail: list[str] = []
    skip_player_help_link = False
    for line in rest:
        if _is_meta_video_line(line):
            skip_player_help_link = _norm_line(line) in _META_VIDEO_ERROR_LINES
            continue
        if skip_player_help_link and _norm_line(line) == "learn more":
            skip_player_help_link = False
            continue
        skip_player_help_link = False
        tail.append(line)

    domain = next((line for line in tail if _is_meta_domain_line(line)), None)
    after_domain = tail[tail.index(domain) + 1:] if domain else tail
    headline = None
    if after_domain and not _is_meta_cta(after_domain[0]) and not _is_meta_domain_line(after_domain[0]):
        headline = after_domain[0]
    cta = next((line for line in tail if _is_meta_cta(line)), None)
    # No domain line and the copy runs straight into a CTA (a carousel with
    # no link domain, or a catalog card): the short line right before the
    # CTA is the headline, not the last line of the body.
    if body_ended_on_cta and domain is None and headline is None and len(body) >= 2 \
            and _looks_like_headline(body[-1]):
        headline = body.pop()
    return _truncate(" ".join(body), _BODY_MAX_CHARS), headline, cta, domain


def _parse_meta_card(library_id: str, lines: list[str]) -> dict:
    """One ad card's lines (everything after its "Library ID" line)."""
    started = None
    for line in lines:
        match = _META_STARTED_RE.search(line)
        if match:
            started = match.group(1)
            break
    has_versions = any(_norm_line(line) == "this ad has multiple versions" for line in lines)

    page_name = ""
    sponsored_at = next((i for i, line in enumerate(lines) if _norm_line(line) == "sponsored"), None)
    if sponsored_at is not None:
        # The Page name is reliably the line right before "Sponsored" —
        # "This ad has multiple versions", "EU transparency", "N ads use this
        # creative and text", "See ad details" etc. all come BEFORE it.
        candidate = lines[sponsored_at - 1] if sponsored_at > 0 else ""
        if candidate and not _is_meta_junk_line(candidate) and len(candidate) <= 100:
            page_name = candidate
        content = lines[sponsored_at + 1:]
    else:
        # No "Sponsored" marker (layout change?): fall back to the first
        # non-junk line, if it plausibly is a name rather than ad copy.
        rest = [line for line in lines if not _is_meta_junk_line(line)]
        if rest and _looks_like_page_name(rest[0]):
            page_name, content = rest[0], rest[1:]
        else:
            content = rest

    body, headline, cta, domain = _split_meta_ad_content(content)
    return {
        "library_id": library_id,
        "page_name": page_name,
        "started_running_on": started,
        "body": body,
        "headline": headline,
        "cta": cta,
        "destination_domain": domain,
        "has_versions": has_versions,
    }


def _parse_meta_page(text: str) -> dict:
    """Rendered Meta Ad Library search page -> {recognized, empty,
    result_count, cards}. `recognized` is False when neither ad cards nor
    Meta's explicit empty state are on the page (still loading, login wall,
    block page) — callers report that as an error, never as "no ads"."""
    lines = _page_lines(text)
    result_count = next((re.sub(r"\s+", " ", line) for line in lines if _META_RESULT_COUNT_RE.match(line)), None)
    empty = any(_META_EMPTY_RE.search(line) for line in lines)

    id_lines = []
    for i, line in enumerate(lines):
        match = _META_LIBRARY_ID_RE.search(line)
        if match:
            id_lines.append((i, match.group(1)))

    cards = []
    for n, (idx, library_id) in enumerate(id_lines):
        is_last = n + 1 == len(id_lines)
        if is_last:
            # Page footer ("System status", then the ad-blocker modal) ends
            # the last card.
            end = next((j for j in range(idx + 1, len(lines)) if _norm_line(lines[j]) == "system status"), len(lines))
        else:
            end = id_lines[n + 1][0]
        card_lines = lines[idx + 1:end]
        while card_lines and card_lines[-1] in _META_STATUS_LINES:
            card_lines.pop()  # the NEXT card's "Active" badge
        if is_last:
            while card_lines and _norm_line(card_lines[-1]) == "see more":
                card_lines.pop()  # the list's own "load more" button
        cards.append(_parse_meta_card(library_id, card_lines))

    return {"recognized": bool(cards) or empty, "empty": empty and not cards,
            "result_count": result_count, "cards": cards}


def _meta_ad_confidence(card: dict, ref_tokens: set[str], client_domain: str) -> str:
    if _domain_matches(card.get("destination_domain"), client_domain):
        return "high"  # the ad links to the client's own website
    evidence = " ".join(filter(None, (card.get("body"), card.get("headline"), card.get("destination_domain"))))
    confidence = _match_confidence(card.get("page_name") or "", evidence, ref_tokens)
    return confidence if confidence in _CONFIDENCE_RANK else "low"


async def _search_meta_once(query: str, country: str) -> dict:
    """One Meta Ad Library search for a single query string. Returns the
    parsed cards — no confidence scoring yet (that needs the caller's
    client-name/domain context). Never raises."""
    url = _meta_search_url(query, country)
    out = {"query": query, "url": url, "opened": False, "fail_reason": None,
           "recognized": False, "result_count": None, "cards": []}
    text, fail_reason = await _fetch_page(url)
    out["opened"] = _was_opened(fail_reason)
    if text is None:
        out["fail_reason"] = fail_reason
        return out
    try:
        parsed = _parse_meta_page(text)
    except Exception as e:
        _logger.warning("ad_presence: couldn't parse Meta page for %r: %s: %s", query, type(e).__name__, e)
        out["fail_reason"] = "unexpected"
        return out
    if not parsed["recognized"]:
        out["fail_reason"] = "not_ready"
        return out
    out.update(recognized=True, result_count=parsed["result_count"], cards=parsed["cards"])
    return out


def _meta_sort_key(ad: dict) -> tuple:
    date = _parse_display_date(ad.get("started_running_on"), ("%b %d, %Y", "%B %d, %Y", "%b. %d, %Y"))
    return (_CONFIDENCE_RANK.get(ad.get("confidence"), 3),) + _recency_key(date)


async def check_meta(client_name: str, client_website: str = "", country: str = "US") -> dict:
    """Live-checks the public Meta Ad Library across a few name variants
    derived from `client_name`/`client_website` (searched concurrently),
    then confidence-scores every ad card found against the client's actual
    name/domain. Never raises."""
    result = _meta_result()
    try:
        return await _check_meta(result, client_name, client_website, _normalize_country(country))
    except Exception as e:
        _logger.exception("ad_presence: Meta check failed unexpectedly")
        return _finish(result, STATUS_ERROR, f"Meta Ad Library check failed unexpectedly ({type(e).__name__}) — "
                                             "open the library link to check by hand.")


async def _check_meta(result: dict, client_name: str, client_website: str, country: str) -> dict:
    variants = name_variants(client_name, client_website)
    if not variants:
        return _finish(result, STATUS_NOT_CHECKED,
                       "No client name or website provided, so Meta's Ad Library wasn't searched.")
    result["queries_tried"] = list(variants)
    result["library_url"] = _meta_search_url(variants[0], country)
    if not _HAS_PLAYWRIGHT:
        return _finish(result, STATUS_ERROR, _PLAYWRIGHT_MISSING_NOTE)

    searches = await asyncio.gather(*(_search_meta_once(q, country) for q in variants))
    result["search_urls"] = [s["url"] for s in searches if s["opened"]]
    loaded = [s for s in searches if s["recognized"]]
    if not loaded:
        return _finish(result, STATUS_ERROR, _load_failure_note("Meta Ad Library", _worst_reason(searches)))
    result["checked"] = True

    ref_tokens = _reference_tokens(client_name, client_website)
    client_domain = _client_domain(client_website)
    ads: list[dict] = []
    seen_ids: set[str] = set()
    best, best_key = None, None
    for search in loaded:  # variant order, so an ad keeps the first query that found it
        high_here = 0
        for card in search["cards"]:
            confidence = _meta_ad_confidence(card, ref_tokens, client_domain)
            high_here += confidence == "high"
            if card["library_id"] in seen_ids:
                continue
            seen_ids.add(card["library_id"])
            ads.append({
                "library_id": card["library_id"],
                "page_name": card["page_name"],
                "started_running_on": card["started_running_on"],
                "body": card["body"],
                "headline": card["headline"],
                "cta": card["cta"],
                "destination_domain": card["destination_domain"],
                "language": None,
                "confidence": confidence,
                "matched_query": search["query"],
                "library_url": _meta_ad_url(card["library_id"]),
                "has_versions": card["has_versions"],
            })
        key = (high_here, len(search["cards"]))
        if best_key is None or key > best_key:
            best, best_key = search, key

    ads.sort(key=_meta_sort_key)
    high = [a for a in ads if a["confidence"] == "high"]
    medium = [a for a in ads if a["confidence"] == "medium"]
    low = [a for a in ads if a["confidence"] == "low"]
    sample = ads[:_SAMPLE_ADS_MAX]
    for ad in {id(a): a for a in sample + high}.values():
        ad["language"] = _detect_language(ad["body"] if len(ad["body"] or "") >= 12
                                          else " ".join(filter(None, (ad["body"], ad["headline"]))))

    result["sample_ads"] = sample
    result["high_confidence_count"] = len(high)
    result["result_count_estimate"] = best["result_count"] if best else None
    if best and best["cards"]:
        result["library_url"] = best["url"]  # the search a human would want to open
    # The client's OWN ad copy only — other advertisers mentioning the client
    # (medium/low) say nothing about which languages the client runs.
    result["languages"] = _language_mix([a["language"] for a in high])

    if high:
        pages = _unique([a["page_name"] for a in high])[:3]
        status = STATUS_ACTIVE
        note = (f"Found {_plural(len(high), 'active ad')} in Meta's Ad Library that match the client by "
                "Page name or destination website" + (f" (Page: {', '.join(pages)})" if pages else "") + ".")
    elif medium:
        status = STATUS_NOT_FOUND
        note = (f"No ad matched the client by Page name or website, but {_plural(len(medium), 'ad')} from other "
                "Pages mention the client's name in the ad copy — worth a manual look.")
    elif low:
        status = STATUS_NOT_FOUND
        note = (f"Found {_plural(len(low), 'ad')} under these name variants, but none matched the client by Page "
                "name, website or ad copy — likely unrelated advertisers picked up by Meta's own fuzzy search.")
    else:
        status = STATUS_NOT_FOUND
        note = (f"No active ads found in Meta's Ad Library for this client under "
                f"{_plural(len(variants), 'name variant')}.")
    failed = len(searches) - len(loaded)
    if failed:
        note += f" ({failed} of {len(searches)} name searches didn't load, so this may be incomplete.)"
    return _finish(result, status, note)


# ---------------------------------------------------------------------------
# Google Ads Transparency Center (public web UI — no official API exists)
# ---------------------------------------------------------------------------

_GOOGLE_READY_MARKER = r"\d\s*[KMB]?\s+ads?\b|No ads found"
_GOOGLE_READY_TIMEOUT_MS = 8000
_GOOGLE_COUNT_RE = re.compile(r"^~?\s*(\d[\d,.]*\s*[KMB]?)\s+ads?$", re.IGNORECASE)
_GOOGLE_EMPTY_RE = re.compile(r"No ads found", re.IGNORECASE)


def _google_search_url(domain: str, region: str) -> str:
    region_param = "anywhere" if region == "ALL" else region
    return f"https://adstransparency.google.com/?region={region_param}&domain={urllib.parse.quote(domain)}"


def _parse_compact_number(display: Optional[str]) -> Optional[int]:
    """'9K' -> 9000, '1.2M' -> 1200000, '8' -> 8, '12,345' -> 12345."""
    match = re.fullmatch(r"(\d[\d,]*(?:\.\d+)?)\s*([KMB]?)", (display or "").strip(), re.IGNORECASE)
    if not match:
        return None
    scale = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[match.group(2).upper()]
    return int(float(match.group(1).replace(",", "")) * scale)


def _parse_google_page(text: str) -> dict:
    """Rendered Transparency Center domain page -> {recognized, empty,
    count_display, count_value, advertisers}. Layout: "~9K ads" line, then
    one "<advertiser name>" / "Verified" pair per advertiser card."""
    lines = _page_lines(text)
    count_display = None
    for line in lines:
        match = _GOOGLE_COUNT_RE.match(line)
        if match:
            count_display = re.sub(r"\s+", "", match.group(1))
            break
    empty = any(_GOOGLE_EMPTY_RE.search(line) for line in lines)
    advertisers: list[str] = []
    for i, line in enumerate(lines):
        if i and _norm_line(line) == "verified":
            name = lines[i - 1]
            if _norm_line(name) != "verified" and len(name) <= 120 and name not in advertisers:
                advertisers.append(name)
    return {"recognized": bool(count_display) or empty or bool(advertisers), "empty": empty,
            "count_display": count_display, "count_value": _parse_compact_number(count_display),
            "advertisers": advertisers}


async def check_google(domain: str, region: str = "US") -> dict:
    """Live-checks Google's Ads Transparency Center for `domain` (a bare
    domain or a full website URL — normalized here, including dropping a
    leading "www."). Domain search is already precise (no name-variant
    fuzziness needed the way Meta/TikTok keyword search does) — returns
    verified-advertiser identity + a rough ad count; does NOT return per-ad
    creative text/language — see module docstring. Never raises."""
    result = _google_result()
    try:
        return await _check_google(result, domain, _normalize_country(region))
    except Exception as e:
        _logger.exception("ad_presence: Google check failed unexpectedly")
        return _finish(result, STATUS_ERROR, f"Google Ads Transparency Center check failed unexpectedly "
                                             f"({type(e).__name__}) — open the library link to check by hand.")


async def _check_google(result: dict, domain: str, region: str) -> dict:
    host = _normalize_domain(domain)
    if host and _is_platform_domain(host):
        return _finish(result, STATUS_NOT_CHECKED,
                       f"The client website given is a {host} page rather than the client's own domain, so "
                       "Google's Ads Transparency Center (searched by domain) wasn't checked.")
    if not host:
        return _finish(result, STATUS_NOT_CHECKED,
                       "No usable client website provided, so Google's Ads Transparency Center "
                       "(searched by domain) wasn't checked.")
    url = _google_search_url(host, region)
    result["search_url"] = url
    result["library_url"] = url
    if not _HAS_PLAYWRIGHT:
        return _finish(result, STATUS_ERROR, _PLAYWRIGHT_MISSING_NOTE)

    text, fail_reason = await _fetch_page(url)
    if _was_opened(fail_reason):
        result["search_urls"] = [url]
    if text is None:
        return _finish(result, STATUS_ERROR, _load_failure_note("Google Ads Transparency Center", fail_reason))
    parsed = _parse_google_page(text)
    if not parsed["recognized"]:
        return _finish(result, STATUS_ERROR, _load_failure_note("Google Ads Transparency Center", "not_ready"))

    result["checked"] = True
    result["ad_count_estimate_display"] = parsed["count_display"]
    result["advertisers"] = parsed["advertisers"][:_ADVERTISERS_MAX]
    count_value = parsed["count_value"] or 0
    advertisers = result["advertisers"]
    if advertisers or count_value > 0:
        count_part = f"~{parsed['count_display']} ads" if count_value > 0 else "ads"
        who = (f" from verified advertiser{'s' if len(advertisers) != 1 else ''} {'; '.join(advertisers[:3])}"
               if advertisers else "")
        note = f"Google's Ads Transparency Center shows {count_part} pointing to {host}{who}"
        return _finish(result, STATUS_ACTIVE, note.rstrip(".") + ".")  # "Nike, Inc." already ends in one
    if parsed["empty"] or parsed["count_value"] == 0:
        return _finish(result, STATUS_NOT_FOUND, f"No ads found for {host} in Google's Ads Transparency Center.")
    return _finish(result, STATUS_NOT_FOUND,
                   f"Google's Ads Transparency Center page for {host} loaded but showed no verified advertiser "
                   "or ad count — worth a manual check.")


# ---------------------------------------------------------------------------
# TikTok Commercial Content Library (public web UI)
# ---------------------------------------------------------------------------

# Regions the public library actually lists ads for: EU-27 + the rest of the
# EEA + UK + Switzerland ("all" = all of those). Anywhere else — the US
# included — a search just returns an empty country and "Total ads: 0".
_TIKTOK_LIBRARY_REGIONS = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "IS", "LI", "NO", "GB", "CH",
})
_TIKTOK_READY_MARKER = r"First shown:|No ads found"
_TIKTOK_READY_TIMEOUT_MS = 8000
_TIKTOK_TOTAL_RE = re.compile(r"Total ads:\s*(\d[\d,]*)", re.IGNORECASE)
_TIKTOK_EMPTY_RE = re.compile(r"No ads found", re.IGNORECASE)
# One result card of the advertiser-name (adv_name=) search layout.
_TIKTOK_CARD_RE = re.compile(
    r"^Ad\n(?P<advertiser>[^\n]+)\nFirst shown:\s*(?P<first>[^\n]*)\nLast shown:\s*(?P<last>[^\n]*)"
    r"(?:\nUnique users seen:\s*(?P<users>[^\n]*))?",
    re.MULTILINE,
)


def _tiktok_region(country: str) -> Optional[str]:
    """TikTok library `region=` value for `country`, None if the library
    doesn't cover it."""
    if country == "ALL":
        return "all"
    return country if country in _TIKTOK_LIBRARY_REGIONS else None


def _tiktok_unsupported_note(country: str) -> str:
    return ("TikTok's public ad library only lists ads shown in the EU/EEA, UK and Switzerland, so "
            f"{country} campaigns can't be verified automatically — open the library to search by hand if needed.")


def _tiktok_search_url(query: str, region: str, lookback_days: int) -> str:
    # `query=` is silently ignored by the current UI; the advertiser-name
    # search is `adv_name=<name>&query_type=1`.
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - lookback_days * 86400 * 1000
    return ("https://library.tiktok.com/ads?"
            f"region={region}&start_time={start_ms}&end_time={now_ms}"
            f"&adv_name={urllib.parse.quote(query)}&adv_biz_ids=&query_type=1"
            "&sort_type=last_shown_date,desc")


def _parse_tiktok_page(text: str) -> dict:
    """Rendered TikTok library search page -> {recognized, total,
    region_label, cards}. `total` parses "5,000" as 5000 (the old
    `(\\d+)` read it as 5). `region_label` is '' when the page shows no
    target country — i.e. the library doesn't cover the requested region."""
    lines = _page_lines(text)
    joined = "\n".join(lines)
    total_match = _TIKTOK_TOTAL_RE.search(joined)
    total = int(total_match.group(1).replace(",", "")) if total_match else None
    empty = bool(_TIKTOK_EMPTY_RE.search(joined))
    region_label = None
    for i, line in enumerate(lines):
        if _norm_line(line) == "ad target country":
            following = lines[i + 1] if i + 1 < len(lines) else ""
            region_label = "" if _norm_line(following) in ("", "last shown date") else following
            break
    cards = [{
        "advertiser": m.group("advertiser").strip(),
        "first_shown": m.group("first").strip() or None,
        "last_shown": m.group("last").strip() or None,
        "unique_users_seen": (m.group("users") or "").strip() or None,
    } for m in _TIKTOK_CARD_RE.finditer(joined)]
    return {"recognized": total is not None or empty or bool(cards), "total": total,
            "region_label": region_label, "cards": cards}


async def _search_tiktok_once(query: str, region: str, lookback_days: int) -> dict:
    """One TikTok library advertiser-name search. Never raises."""
    url = _tiktok_search_url(query, region, lookback_days)
    out = {"query": query, "url": url, "opened": False, "fail_reason": None, "recognized": False,
           "total": None, "region_label": None, "cards": []}
    text, fail_reason = await _fetch_page(url)
    out["opened"] = _was_opened(fail_reason)
    if text is None:
        out["fail_reason"] = fail_reason
        return out
    try:
        parsed = _parse_tiktok_page(text)
    except Exception as e:
        _logger.warning("ad_presence: couldn't parse TikTok page for %r: %s: %s", query, type(e).__name__, e)
        out["fail_reason"] = "unexpected"
        return out
    if not parsed["recognized"]:
        out["fail_reason"] = "not_ready"
        return out
    out.update(recognized=True, total=parsed["total"], region_label=parsed["region_label"], cards=parsed["cards"])
    return out


def _tiktok_sort_key(ad: dict) -> tuple:
    date = _parse_display_date(ad.get("last_shown"), ("%m/%d/%Y",))
    return (_CONFIDENCE_RANK.get(ad.get("confidence"), 3),) + _recency_key(date)


async def check_tiktok(client_name: str, client_website: str = "", country: str = "US", lookback_days: int = 90) -> dict:
    """Live-checks TikTok's Commercial Content Library across the same name
    variants as Meta — but only where that library has any coverage
    (EU/EEA/UK/CH). Everywhere else, the US included, it returns
    "unsupported" without loading a single page. Never raises."""
    result = _tiktok_result()
    try:
        return await _check_tiktok(result, client_name, client_website, _normalize_country(country), lookback_days)
    except Exception as e:
        _logger.exception("ad_presence: TikTok check failed unexpectedly")
        return _finish(result, STATUS_ERROR, f"TikTok Commercial Content Library check failed unexpectedly "
                                             f"({type(e).__name__}) — open the library link to check by hand.")


async def _check_tiktok(result: dict, client_name: str, client_website: str, country: str, lookback_days: int) -> dict:
    region = _tiktok_region(country)
    if region is None:
        return _finish(result, STATUS_UNSUPPORTED, _tiktok_unsupported_note(country))
    variants = name_variants(client_name, client_website)
    if not variants:
        return _finish(result, STATUS_NOT_CHECKED,
                       "No client name or website provided, so TikTok's library wasn't searched.")
    result["queries_tried"] = list(variants)
    if not _HAS_PLAYWRIGHT:
        return _finish(result, STATUS_ERROR, _PLAYWRIGHT_MISSING_NOTE)

    searches = await asyncio.gather(*(_search_tiktok_once(q, region, lookback_days) for q in variants))
    result["search_urls"] = [s["url"] for s in searches if s["opened"]]
    loaded = [s for s in searches if s["recognized"]]
    if not loaded:
        return _finish(result, STATUS_ERROR,
                       _load_failure_note("TikTok Commercial Content Library", _worst_reason(searches)))
    if any(s["region_label"] == "" for s in loaded):
        return _finish(result, STATUS_UNSUPPORTED, _tiktok_unsupported_note(country))
    result["checked"] = True

    ref_tokens = _reference_tokens(client_name, client_website)
    best, best_key = None, None
    for search in loaded:
        for card in search["cards"]:
            # The listing shows no ad copy, so only the advertiser name can
            # confirm it's the client (there is no "medium" here).
            card["confidence"] = "high" if _match_confidence(card["advertiser"], "", ref_tokens) == "high" else "low"
            card["matched_query"] = search["query"]
        high_here = sum(card["confidence"] == "high" for card in search["cards"])
        key = (high_here, search["total"] or 0, len(search["cards"]))
        if best_key is None or key > best_key:
            best, best_key = search, key
        search["high"] = high_here

    # Same ad can come back for several variants (and the listing has no ad
    # IDs to dedupe on), so report the single most informative search.
    cards = sorted(best["cards"], key=_tiktok_sort_key)
    high = [card for card in cards if card["confidence"] == "high"]
    total = best["total"] or 0
    result["sample_ads"] = cards[:_SAMPLE_ADS_MAX]
    result["high_confidence_count"] = len(high)
    result["result_count_estimate"] = f"{best['total']:,} ads" if best["total"] is not None else None

    if high:
        names = _unique([card["advertiser"] for card in high])[:3]
        status = STATUS_ACTIVE
        note = (f"Found {_plural(len(high), 'ad')} from an advertiser matching the client ({', '.join(names)}) "
                f"in TikTok's Commercial Content Library for {country}.")
    elif total > 0:
        status = STATUS_NOT_FOUND
        note = (f"TikTok's library lists {total:,} ads for this name search in {country}, but none of the "
                f"{len(cards)} shown were run by an advertiser matching the client — likely other brands or "
                "resellers mentioning it; worth a manual look.")
    else:
        status = STATUS_NOT_FOUND
        note = (f"No ads found in TikTok's Commercial Content Library for {country} in the last {lookback_days} "
                f"days under {_plural(len(variants), 'name variant')}.")
    failed = len(searches) - len(loaded)
    if failed:
        note += f" ({failed} of {len(searches)} name searches didn't load, so this may be incomplete.)"
    return _finish(result, status, note)


# Host -> (JS-compatible regex marking "results are rendered", cap in ms).
_READY_MARKERS = {
    "facebook.com": (_META_READY_MARKER, _META_READY_TIMEOUT_MS),
    "adstransparency.google.com": (_GOOGLE_READY_MARKER, _GOOGLE_READY_TIMEOUT_MS),
    "library.tiktok.com": (_TIKTOK_READY_MARKER, _TIKTOK_READY_TIMEOUT_MS),
}


def _ready_marker_for(url: str) -> tuple[Optional[str], int]:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    for suffix, marker in _READY_MARKERS.items():
        if host == suffix or host.endswith("." + suffix):
            return marker
    return None, 0


# ---------------------------------------------------------------------------
# Orchestrator — the entry point the Strategy Brief endpoint (or anything
# else) calls
# ---------------------------------------------------------------------------

async def check_ad_presence(client_name: str, client_website: str = "", country: str = "US") -> dict:
    """
    Runs all three platform checks for one client — concurrently, sharing
    one headless browser — and returns
    {"meta", "google", "tiktok", "summary", "elapsed_seconds", "country"}
    (see OUTPUT CONTRACT in the module docstring). `summary` is plain text
    ready to splice into an LLM prompt. Never raises: a platform that fails
    comes back with status "error" and a note saying why, and the others are
    unaffected.
    """
    started = time.monotonic()
    country_code = _normalize_country(country)
    try:
        if _loop_can_spawn_subprocesses():
            results = await _check_all_platforms(client_name, client_website, country_code)
        else:
            results = await asyncio.to_thread(_run_on_private_loop, client_name, client_website, country_code)
    except Exception as e:
        _logger.exception("ad_presence: lookup failed unexpectedly")
        results = {platform: _crashed_result(platform, e) for platform in ("meta", "google", "tiktok")}

    meta, google, tiktok = results["meta"], results["google"], results["tiktok"]
    try:
        summary = _build_summary(meta, google, tiktok, country_code)
    except Exception:
        _logger.exception("ad_presence: couldn't build summary")
        summary = ""
    return {
        "meta": meta,
        "google": google,
        "tiktok": tiktok,
        "summary": summary,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "country": country_code,
    }


async def _check_all_platforms(client_name: str, client_website: str, country: str) -> dict:
    started = time.monotonic()
    session = _BrowserSession()
    token = _ACTIVE_SESSION.set(session)
    try:
        # Google first: its single page is the fastest, so its slot frees up
        # soonest for a queued Meta variant.
        jobs = {
            "google": check_google(client_website, region=country),
            "meta": check_meta(client_name, client_website, country=country),
            "tiktok": check_tiktok(client_name, client_website, country=country),
        }
        tasks = {platform: asyncio.ensure_future(job) for platform, job in jobs.items()}
        done, pending = await asyncio.wait(tasks.values(), timeout=_OVERALL_TIMEOUT_S)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=5)
        results = {}
        for platform, task in tasks.items():
            if task not in done or task.cancelled():
                _logger.warning("ad_presence: %s check timed out after %.0fs", platform, _OVERALL_TIMEOUT_S)
                results[platform] = _timed_out_result(platform)
            elif task.exception() is not None:
                results[platform] = _crashed_result(platform, task.exception())
            else:
                results[platform] = task.result()
        _logger.info("ad_presence: %r (%s) meta=%s google=%s tiktok=%s — %d page load(s) in %.1fs",
                     client_name, country, results["meta"]["status"], results["google"]["status"],
                     results["tiktok"]["status"], session.page_loads, time.monotonic() - started)
        return results
    finally:
        _ACTIVE_SESSION.reset(token)
        await session.close()


def _loop_can_spawn_subprocesses() -> bool:
    """False on Windows when the running loop is a SelectorEventLoop — which
    is exactly what `uvicorn --reload` (run.ps1) runs there. Playwright
    starts its driver/Chromium as a subprocess, which that loop type can't
    do (NotImplementedError — it would surface as a misleading "no Chromium
    installed"). Linux/macOS loops, and Windows' default Proactor loop, can."""
    if sys.platform != "win32":
        return True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return True
    proactor = getattr(asyncio, "ProactorEventLoop", None)
    return proactor is None or isinstance(loop, proactor)


def _run_on_private_loop(client_name: str, client_website: str, country: str) -> dict:
    """Runs the whole lookup on a fresh subprocess-capable event loop in this
    (worker) thread — see `_loop_can_spawn_subprocesses`."""
    loop_factory = getattr(asyncio, "ProactorEventLoop", None) or asyncio.new_event_loop
    loop = loop_factory()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_check_all_platforms(client_name, client_website, country))
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        asyncio.set_event_loop(None)
        loop.close()


def _build_summary(meta: dict, google: dict, tiktok: dict, country: str = "US") -> str:
    lines = [
        _platform_line("Meta (Facebook/Instagram)", meta, country),
        _platform_line("Google Ads", google, country),
        _platform_line("TikTok", tiktok, country),
    ]
    return " ".join(line for line in lines if line)


def _status_of(r: dict) -> str:
    status = r.get("status")
    if status in STATUSES:
        return status
    if r.get("active"):
        return STATUS_ACTIVE
    return STATUS_NOT_FOUND if r.get("checked") else STATUS_NOT_CHECKED


def _who_clause(r: dict) -> str:
    if r.get("platform") == "google":
        names = (r.get("advertisers") or [])[:3]
        return f" (verified advertiser{'s' if len(names) != 1 else ''}: {'; '.join(names)})" if names else ""
    names = _unique([(ad.get("page_name") or ad.get("advertiser") or "")
                     for ad in (r.get("sample_ads") or []) if ad.get("confidence") == "high"])[:3]
    if not names:
        return ""
    noun = "Page" if r.get("platform") == "meta" else "advertiser"
    return f" ({noun}{'s' if len(names) != 1 else ''}: {', '.join(names)})"


def active_count_phrase(r: dict) -> str:
    """Meta/TikTok only count the client's own ads among the results that
    rendered on the first page, so that number is a floor, not a total.
    Google's number is the Transparency Center's own estimate for the domain."""
    confirmed = r.get("high_confidence_count") or 0
    if confirmed:
        return f"at least {_plural(confirmed, 'ad')} running"
    estimate = r.get("ad_count_estimate_display") or r.get("ad_count_estimate")
    if estimate and (_parse_compact_number(str(estimate)) or 0) > 0:
        return f"~{estimate} ads running"
    return "ads running"


def _platform_line(label: str, r: dict, country: str = "US") -> str:
    r = r or {}
    status = _status_of(r)
    note = (r.get("note") or "").strip()
    if status == STATUS_ACTIVE:
        langs = r.get("languages") or {}
        lang_str = ""
        if langs:
            parts = [f"{round(p * 100)}% {_lang_label(c)}" for c, p in sorted(langs.items(), key=lambda kv: -kv[1])]
            lang_str = f" — ad copy sampled as {', '.join(parts)}"
        return f"{label}: ACTIVE, {active_count_phrase(r)}{_who_clause(r)}{lang_str}."
    if status == STATUS_UNSUPPORTED:
        if r.get("platform") == "tiktok":
            return f"{label}: not verifiable for {country} campaigns (public library covers EU/UK only)."
        return f"{label}: not verifiable for this region. {note}".strip()
    if status == STATUS_NOT_FOUND:
        return f"{label}: no confirmed active ads found. {note}".strip()
    if status == STATUS_ERROR:
        return f"{label}: could not be checked ({note.rstrip('.') or 'unknown error'})."
    return f"{label}: not checked ({note.rstrip('.') or 'skipped'})."


if __name__ == "__main__":
    # Quick manual smoke test:
    #   python -m app.services.ad_presence "Business Name" "example.com" [COUNTRY]
    import json as _json

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    except Exception:
        pass

    name = sys.argv[1] if len(sys.argv) > 1 else "Nike"
    site = sys.argv[2] if len(sys.argv) > 2 else "nike.com"
    region_arg = sys.argv[3] if len(sys.argv) > 3 else "US"
    print(_json.dumps(asyncio.run(check_ad_presence(name, site, region_arg)), indent=2, ensure_ascii=False))
