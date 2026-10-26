"""
A thin, read-only wrapper around the TikTok Marketing API (TikTok API for Business, v1.3) - Constanza's first live data
connection. It exists to answer one question the way TikTok Ads Manager answers it when you set up an ad group: "how many
people match this targeting?" (POST /ad/audience_size/estimate/), plus the lookups Ads Manager's audience panel needs to
build that targeting: locations (DMAs, states, ZIP codes), languages, and the interest / behavior categories.

Nothing here writes to TikTok. Every call is a read: audience-size estimation and targeting catalogs.

Setup (the app runs fine without it, exactly like Notion / Drive / OpenAI - every caller checks `is_configured()` first):
    TIKTOK_ACCESS_TOKEN   a long-term access token of a TikTok for Business developer app that the ad account's owner
                          authorized (https://business-api.tiktok.com/portal/docs?id=1738373164380162)
    TIKTOK_ADVERTISER_ID  the numeric ID of the ad account the estimates are asked through
  optional:
    CONSTANZA_AUDIENCE_X_CONSERVATIVE / _MID / _UPPER   the planning multipliers that turn the lower end of an audience into monthly
                                    impressions (defaults 4.3 / 5.5 / 8.6; shared with Meta, see platform_scenarios.py)
    TIKTOK_OPTIMIZATION_GOAL        replaces the optimization goal of a Reach estimate (default REACH; the diagnostics say when CLICK, the
                                    pair in TikTok's own documentation example, is the one an account accepts)
    TIKTOK_OBJECTIVE_TYPE           the objective used for the location look-ups (default REACH)
    TIKTOK_API_BASE                 point the client somewhere else (the offline mock used by the tests, a sandbox)

Design rules (the ones that bit the previous integrations):
  * the token is only ever sent in the `Access-Token` header, never logged, never put in an exception message, never returned
    by any API of this app;
  * every failure becomes a TikTokAPIError with a `kind` (not_configured / config / auth / permission / rate_limit / invalid /
    unavailable / unexpected) and a plain-language `public_message()`, so a route handler turns it into a clean 4xx / 5xx
    for the dock instead of a stack trace;
  * read-only calls are retried a couple of times on timeouts, 5xx and TikTok's rate-limit codes, with a small back-off;
  * answers that cannot change within a session (languages, category lists, DMA names) are cached in memory, and an identical
    audience estimate is cached for ten minutes - Ads Manager's own estimator is not hammered by a planner re-pulling.

`python -m app.services.tiktok_diagnose` checks a real account end to end (see that module).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

import requests

log = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://business-api.tiktok.com/open_api/v1.3"
DEFAULT_OBJECTIVE = "REACH"
DEFAULT_OPTIMIZATION_GOAL = "REACH"

CONNECT_TIMEOUT = 5          # seconds to open the connection
READ_TIMEOUT = 25            # seconds to wait for the answer (an audience estimate can take a few seconds)
MAX_RETRIES = 2              # extra attempts after the first for a retryable failure
NETWORK_BACKOFF = (0.6, 1.5)
RATE_LIMIT_BACKOFF = (1.0, 2.5)
MIN_INTERVAL = 0.12          # seconds between calls leaving this process (TikTok limits calls per second per app)
MAX_CONCURRENT = 6           # calls in flight at once: a slow TikTok must not hold every worker thread of the app
MAX_QUEUE_WAIT = 5.0         # a call that would have to wait longer than this for its turn is refused instead of queued
CALL_DEADLINE = 45.0         # seconds one call may take in all, retries and the wait for a turn included (the timeouts are per connect / per
                             # read, so a server that drips its answer a few bytes at a time could still run past it)

BATCH_LIMIT = 1000         # keywords per location search (TikTok's own limit for the batch search types)

TTL_CATALOG = 12 * 3600      # interest / behavior / language lists
TTL_LOCATION = 24 * 3600     # a market's TikTok ID
TTL_SEARCH = 3600            # a keyword search
TTL_ESTIMATE = 600           # an identical audience estimate

# What a planner is told for the situations that need an admin (kept here so the wording is in one place).
SETUP_HINT = "an admin needs to add TIKTOK_ACCESS_TOKEN and TIKTOK_ADVERTISER_ID to the app's secrets"

# --- TikTok's return codes (Appendix - Return codes), grouped by what the caller should do about them --------------------
_AUTH_CODES = {40101, 40102, 40103, 40104, 40105, 40106, 40107, 40108, 40109, 40110, 40113, 40115, 40116}
_PERMISSION_CODES = {40001, 40117, 40118, 40119, 40124, 40125, 41000}      # 41000: the client IP is on the banned-country list
_UNAVAILABLE_CODES = {41001, 60001}                                          # the interface is offline / the system is in maintenance
_RATE_LIMIT_CODES = {40016, 40100, 40132, 40133}
_ADVERTISER_CODES = {40300, 40301}
_INVALID_CODES = {40000, 40002, 40006, 40007, 40008, 40010, 40011, 40051, 40065, 40700}


# ------------------------------------------------------------------------------------------------------------- config

def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def access_token() -> str:
    return _env("TIKTOK_ACCESS_TOKEN")


def advertiser_id() -> str:
    return _env("TIKTOK_ADVERTISER_ID")


def api_base() -> str:
    return (_env("TIKTOK_API_BASE") or DEFAULT_API_BASE).rstrip("/")


def objective_type() -> str:
    return (_env("TIKTOK_OBJECTIVE_TYPE") or DEFAULT_OBJECTIVE).upper()


def optimization_goal() -> str:
    return (_env("TIKTOK_OPTIMIZATION_GOAL") or DEFAULT_OPTIMIZATION_GOAL).upper()


def optimization_goal_override() -> str:
    """TIKTOK_OPTIMIZATION_GOAL, when set, replaces the optimization goal sent for a Reach estimate (TikTok's own documentation example
    pairs the Reach objective with CLICK; if an ad account rejects REACH / REACH, this is the way to change it without a release)."""
    return _env("TIKTOK_OPTIMIZATION_GOAL").upper()



_ADVERTISER_RE = re.compile(r"^\d{5,25}$", re.ASCII)
_TOKEN_RE = re.compile(r"^[\x21-\x7e]+$")          # printable ASCII, no space: all a header can carry (a pasted zero-width space is not)
_LOCAL_BASE_RE = re.compile(r"^http://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?(?:/|$)")


def missing_config() -> list[str]:
    """The settings that are absent or unusable, by name (never their values)."""
    problems = []
    token = access_token()
    if not token:
        problems.append("TIKTOK_ACCESS_TOKEN")
    elif not _TOKEN_RE.match(token):
        problems.append("TIKTOK_ACCESS_TOKEN (it has a space or a character a token cannot hold — copy it again)")
    base = _env("TIKTOK_API_BASE")
    if base and not (base.lower().startswith("https://") or _LOCAL_BASE_RE.match(base.lower())):
        problems.append("TIKTOK_API_BASE (it must be an https address)")
    adv = advertiser_id()
    if not adv:
        problems.append("TIKTOK_ADVERTISER_ID")
    elif not _ADVERTISER_RE.match(adv):
        problems.append("TIKTOK_ADVERTISER_ID (it must be the numeric ad account ID)")
    return problems


def is_configured() -> bool:
    return not missing_config()


def advertiser_hint() -> str:
    """The ad account ID with all but its last four digits hidden - enough for an admin to recognise it, never the whole ID."""
    adv = advertiser_id()
    return ("…" + adv[-4:]) if len(adv) >= 8 else ("…" if adv else "")


# ------------------------------------------------------------------------------------------------------------- errors

class TikTokAPIError(Exception):
    """A failed TikTok call, already classified. `kind` says what to do about it; `public_message()` is what a planner reads."""

    def __init__(self, message: str, *, kind: str = "unexpected", code: Optional[int] = None, http_status: Optional[int] = None,
                 request_id: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.code = code
        self.http_status = http_status
        self.request_id = request_id

    @property
    def status_code(self) -> int:
        """The HTTP status this app answers with."""
        return {"not_configured": 503, "config": 503, "rate_limit": 429, "invalid": 422}.get(self.kind, 502)

    def public_message(self) -> str:
        # a message that came in TikTok's own answer is quoted as TikTok's; one this client made up (no code) is only a detail
        said = (f" (TikTok said: {self.message})" if self.code is not None else f" ({self.message})") if self.message else ""
        if self.kind == "not_configured":
            return f"TikTok isn't connected yet — {SETUP_HINT}."
        if self.kind == "config":
            return f"TikTok doesn't recognise the ad account this app was given — an admin needs to check TIKTOK_ADVERTISER_ID{said}."
        if self.kind == "auth":
            return (f"TikTok rejected the app's access token — it may have expired or been revoked, or not be allowed for this ad account, "
                    f"so an admin needs to check TIKTOK_ACCESS_TOKEN{said}.")
        if self.kind == "permission":
            return f"TikTok says this app or ad account isn't allowed to do that{said}."
        if self.kind == "rate_limit":
            return "TikTok is limiting how fast this app can ask — wait a few seconds and pull again."
        if self.kind == "invalid":
            return f"TikTok couldn't use these settings{said}."
        if self.kind == "unavailable":
            return f"TikTok didn't answer properly — try again in a moment{said}."
        return f"TikTok sent an answer Constanza couldn't read{said}."


def not_configured_error() -> TikTokAPIError:
    return TikTokAPIError("; ".join(missing_config()) + " missing", kind="not_configured")


def _classify(code: int) -> str:
    if code in _AUTH_CODES:
        return "auth"
    if code in _PERMISSION_CODES:
        return "permission"
    if code in _RATE_LIMIT_CODES:
        return "rate_limit"
    if code in _ADVERTISER_CODES:
        return "config"
    if code in _UNAVAILABLE_CODES:
        return "unavailable"
    if code in _INVALID_CODES:
        return "invalid"
    if 50000 <= code < 60000:
        return "unavailable"
    return "invalid" if 40000 <= code < 50000 else "unexpected"


# ------------------------------------------------------------------------------------------------------------- helpers

class _TTLCache:
    """A small thread-safe LRU cache whose entries expire."""

    def __init__(self, max_items: int = 400, clock: Callable[[], float] = time.monotonic):
        self._items: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_items
        self._clock = clock

    def get(self, key: str):
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                return False, None
            if hit[0] <= self._clock():
                del self._items[key]
                return False, None
            self._items.move_to_end(key)
            return True, hit[1]

    def put(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._items[key] = (self._clock() + ttl, value)
            self._items.move_to_end(key)
            while len(self._items) > self._max:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class _Throttle:
    """Spaces calls at least `interval` seconds apart. A slot is reserved under the lock and slept for outside it."""

    def __init__(self, interval: float, clock: Callable[[], float], sleep: Callable[[float], None]):
        self.interval, self._clock, self._sleep = interval, clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self, max_wait: Optional[float] = None) -> None:
        with self._lock:
            now = self._clock()
            start = max(now, self._next)
            if max_wait is not None and start - now > max_wait:
                raise TikTokAPIError("too many requests are waiting for their turn", kind="rate_limit")
            self._next = start + self.interval
        if start > now:
            self._sleep(start - now)


_LONG_DIGITS = re.compile(r"(?<!\d)\d{15,}(?!\d)")   # an ad account / business center ID (letters or an underscore may touch it); a place ID (7-13 digits) is not one


def _scrub(message: str, token: str) -> str:
    """TikTok's own message with the token and any ad account ID taken out: it is shown to every planner."""
    if token and len(token) >= 6:
        message = message.replace(token, "…")
    return _LONG_DIGITS.sub(lambda m: "…" + m.group(0)[-4:], message)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class AudienceSize:
    """TikTok's answer to "how many people match this targeting?": a range, and its own verdict on how narrow that is."""
    lower: float
    upper: float
    stage: Optional[int]            # 1 Too narrow, 2 Narrow, 3 Balanced, 4 Fairly broad (None if TikTok did not say)
    request_id: Optional[str] = None
    cached: bool = False

    @property
    def mid(self) -> float:
        return (self.lower + self.upper) / 2.0


STAGE_LABELS = {1: "Too narrow", 2: "Narrow", 3: "Balanced", 4: "Fairly broad"}
STAGE_NOTES = {
    1: "under 10,000 people and under 20% of the possible audience in these locations",
    2: "10,000 people or more, but under 20% of the possible audience in these locations",
    3: "20% to 80% of the possible audience in these locations",
    4: "80% or more of the possible audience in these locations",
}

# `data` keys of /targeting/search/ -> the kind names this app uses
TARGETING_KINDS = {
    "general_interest": "general_interest",
    "additional_interest": "additional_interest",
    "purchase_intention": "purchase_intention",
    "video_interaction": "video_interaction",
    "creator_interaction": "creator_interaction",
    "hashtag_interaction": "hashtag_interaction",
}
_SUB_TYPE = {
    "general_interest": "GENERAL_INTEREST", "additional_interest": "ADDITIONAL_INTEREST", "purchase_intention": "PURCHASE_INTENTION",
    "video_interaction": "VIDEO_INTERACTION", "creator_interaction": "CREATOR_INTERACTION", "hashtag_interaction": "HASHTAG_INTERACTION",
}
LIST_KINDS = ("general_interest", "purchase_intention", "video_interaction", "creator_interaction", "hashtag_interaction")


# ------------------------------------------------------------------------------------------------------------- the client

class TikTokClient:
    def __init__(self, *, token: str, advertiser: str, base: str = DEFAULT_API_BASE, session: Optional[requests.Session] = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 min_interval: float = MIN_INTERVAL, max_retries: int = MAX_RETRIES):
        self._token = token
        self.advertiser = advertiser
        self.base = base.rstrip("/")
        self._session = session or requests.Session()
        self._clock, self._sleep = clock, sleep
        self._cache = _TTLCache(clock=clock)
        self._throttle = _Throttle(min_interval, clock, sleep)
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT)
        self.max_retries = max_retries
        self.adult_ages_needed = False       # set once TikTok has asked (code 40065) for the adult age groups to be spelled out

    def clear_cache(self) -> None:
        """Forget every answer held in memory (the diagnostics want TikTok's real answer, not yesterday's)."""
        self._cache.clear()

    def __repr__(self) -> str:                                    # never show the token, nor the whole ad account ID
        tail = self.advertiser[-4:] if len(self.advertiser) > 4 else ""
        return f"TikTokClient(advertiser='…{tail}', base={self.base!r})"

    # ---- the one place a request is made --------------------------------------------------------------------------------
    def call(self, method: str, path: str, *, params: Optional[dict] = None, body: Optional[dict] = None,
             cache_ttl: Optional[float] = None, cache_if: Optional[Callable[[dict], bool]] = None) -> tuple[dict, Optional[str], bool]:
        """(data, request_id, from_cache). Raises TikTokAPIError for every failure. `cache_if` decides whether an answer is worth remembering:
        a catalog that came back EMPTY is not (it would be served for hours), the next call asks TikTok again."""
        key = None
        if cache_ttl:
            key = f"{method} {path} {_canonical(params)} {_canonical(body)}"
            hit, value = self._cache.get(key)
            if hit:
                return value[0], value[1], True
        data, request_id = self._send(method, path, params, body)
        if key is not None and (cache_if is None or cache_if(data)):
            self._cache.put(key, (data, request_id), cache_ttl)
        return data, request_id, False

    def _send(self, method: str, path: str, params: Optional[dict], body: Optional[dict]) -> tuple[dict, Optional[str]]:
        # at most MAX_CONCURRENT calls in flight, and one that cannot get a slot soon is refused: a slow TikTok must not pin every worker
        began = self._clock()
        if not self._slots.acquire(timeout=MAX_QUEUE_WAIT):
            raise TikTokAPIError("too many requests are waiting for TikTok", kind="rate_limit")
        try:
            return self._send_locked(method, path, params, body, began)
        finally:
            self._slots.release()

    def _send_locked(self, method: str, path: str, params: Optional[dict], body: Optional[dict], began: float) -> tuple[dict, Optional[str]]:
        url = f"{self.base}{path}"
        headers = {"Access-Token": self._token, "Accept": "application/json", "User-Agent": "constanza-proposal-builder/1"}
        # allow_redirects is off: requests would carry our custom Access-Token header to whatever host a redirect names
        kwargs: dict[str, Any] = {"headers": headers, "timeout": (CONNECT_TIMEOUT, READ_TIMEOUT), "allow_redirects": False}
        if params:
            # TikTok's GET endpoints take arrays and objects as JSON in the query string (placements=["PLACEMENT_TIKTOK"])
            kwargs["params"] = {k: (json.dumps(v, separators=(",", ":")) if isinstance(v, (list, dict)) else v) for k, v in params.items() if v is not None}
        if body is not None:
            headers["Content-Type"] = "application/json"
            kwargs["json"] = body

        attempt = 0

        def can_retry(delay: float) -> bool:
            # another attempt may need the whole connect and read time: start one only if it can finish inside the deadline
            return attempt < self.max_retries and (self._clock() - began) + delay + CONNECT_TIMEOUT + READ_TIMEOUT <= CALL_DEADLINE

        while True:
            self._throttle.wait(MAX_QUEUE_WAIT)
            started = self._clock()
            try:
                resp = self._session.request(method, url, **kwargs)
            except UnicodeError:
                raise TikTokAPIError("the access token has a character that cannot be sent in a header", kind="config") from None
            except (requests.Timeout, requests.ConnectionError) as exc:
                error = TikTokAPIError(f"could not reach TikTok ({type(exc).__name__})", kind="unavailable")
                delay = NETWORK_BACKOFF[min(attempt, len(NETWORK_BACKOFF) - 1)]
                if can_retry(delay):
                    self._pause(NETWORK_BACKOFF, attempt)
                    attempt += 1
                    continue
                raise error from None
            except requests.RequestException as exc:
                raise TikTokAPIError(f"the request failed ({type(exc).__name__})", kind="unavailable") from None

            elapsed = self._clock() - started
            if 300 <= resp.status_code < 400:
                raise TikTokAPIError(f"TikTok answered with a redirect (HTTP {resp.status_code}), which is not followed", kind="unexpected", http_status=resp.status_code)
            try:
                payload = resp.json()
            except ValueError:
                payload = None
            code = payload.get("code") if isinstance(payload, dict) else None
            request_id = payload.get("request_id") if isinstance(payload, dict) else None
            log.info("tiktok %s %s -> http %s code %s in %.2fs request_id=%s", method, path, resp.status_code, code, elapsed, request_id)

            # a rate limit, a 5xx or a gateway answer that is not TikTok's envelope: retry a little, then say so
            limited = resp.status_code == 429 or (isinstance(code, int) and code in _RATE_LIMIT_CODES)
            retryable_http = resp.status_code == 429 or resp.status_code >= 500
            if retryable_http or limited or (isinstance(code, int) and 50000 <= code < 60000):
                schedule = RATE_LIMIT_BACKOFF if limited else NETWORK_BACKOFF
                if can_retry(schedule[min(attempt, len(schedule) - 1)]):
                    self._pause(schedule, attempt)
                    attempt += 1
                    continue

            if not isinstance(payload, dict) or not isinstance(code, int):
                kind = "rate_limit" if resp.status_code == 429 else ("unavailable" if retryable_http else "unexpected")
                raise TikTokAPIError(f"HTTP {resp.status_code} with an unreadable body", kind=kind, http_status=resp.status_code)
            if code == 0:
                data = payload.get("data")
                return (data if isinstance(data, dict) else {}), (str(request_id) if request_id else None)
            message = _scrub(str(payload.get("message") or "").strip(), self._token)
            log.warning("tiktok %s %s failed: code %s: %s (request_id=%s)", method, path, code, message[:300], request_id)
            raise TikTokAPIError(message[:400], kind=_classify(code), code=code, http_status=resp.status_code,
                                 request_id=str(request_id) if request_id else None)

    def _pause(self, schedule: tuple, attempt: int) -> None:
        self._sleep(schedule[min(attempt, len(schedule) - 1)])

    # ---- the targeting lookups ---------------------------------------------------------------------------------------------
    def languages(self) -> list[dict]:
        """[{code, name}] - every language TikTok can target (/tool/language/)."""
        data, _, _ = self.call("GET", "/tool/language/", params={"advertiser_id": self.advertiser}, cache_ttl=TTL_CATALOG,
                               cache_if=lambda d: bool(d.get("languages")))
        out = []
        for item in data.get("languages") or []:
            if isinstance(item, dict) and item.get("code"):
                out.append({"code": str(item["code"]), "name": str(item.get("name") or item["code"])})
        return out

    @staticmethod
    def _items(block: Any, keyword: Optional[str]) -> list[dict]:
        """The categories of one kind in a /targeting/search/ answer: the full list, or what a keyword found."""
        if not isinstance(block, dict):
            return []
        raw: list = []
        if keyword is None:
            raw = block.get("list_result") or []
        else:
            found = block.get("search_result") or {}
            if isinstance(found, dict):
                for hits in found.values():
                    raw.extend(hits or [])
        out, seen = [], set()
        for item in raw:
            if not isinstance(item, dict) or item.get("id") in (None, "") or str(item["id"]) in seen:
                continue
            seen.add(str(item["id"]))
            out.append({
                "id": str(item["id"]), "name": str(item.get("name") or item["id"]),
                "level": item.get("level") if isinstance(item.get("level"), int) else None,
                "children_ids": [str(c) for c in (item.get("children_ids") or [])],
                "hashtag_type": item.get("hashtag_type"),
            })
        return out

    def targeting_list(self, kinds: Iterable[str] = LIST_KINDS) -> dict[str, list[dict]]:
        """{kind: [{id, name, level, children_ids}]} - every interest / behavior category TikTok offers, for browsing."""
        kinds = [k for k in kinds if k in LIST_KINDS]
        params: dict[str, Any] = {"advertiser_id": self.advertiser, "targeting_type": "INTEREST_AND_BEHAVIOR"}
        if set(kinds) != set(LIST_KINDS):        # every kind is what TikTok sends when the field is left out: ask for that, the plainest request
            params["sub_targeting_types"] = [_SUB_TYPE[k] for k in kinds]
        data, _, _ = self.call("GET", "/targeting/search/", params=params, cache_ttl=TTL_CATALOG,
                               cache_if=lambda d: any(isinstance(d.get(k), dict) and d[k].get("list_result") for k in TARGETING_KINDS))
        return {k: self._items(data.get(k), None) for k in kinds}

    def targeting_search(self, keyword: str, kinds: Iterable[str] = tuple(TARGETING_KINDS)) -> dict[str, list[dict]]:
        """{kind: [...]} - the categories, additional interests and hashtags that match ONE keyword."""
        keyword = (keyword or "").strip()[:100]
        if not keyword:
            return {}
        kinds = [k for k in kinds if k in TARGETING_KINDS]
        params = {"advertiser_id": self.advertiser, "targeting_type": "INTEREST_AND_BEHAVIOR", "search_keywords": [keyword]}
        if set(kinds) != set(TARGETING_KINDS):   # a single keyword searches every kind unless told otherwise
            params["sub_targeting_types"] = [_SUB_TYPE[k] for k in kinds]
        data, _, _ = self.call("GET", "/targeting/search/", params=params, cache_ttl=TTL_SEARCH)
        return {k: self._items(data.get(k), keyword) for k in kinds}

    # ---- locations ---------------------------------------------------------------------------------------------------------
    def list_regions(self, level_range: str = "TO_PROVINCE") -> list[dict]:
        """Every location TikTok can target down to the level asked (/tool/region/): countries, states and - at state level - TV
        markets (`area_type` METROPOLITAN_OR_DMA). [{id, name, parent_id, region_code, area_type, level}]. One call, cached for hours."""
        params = {"advertiser_id": self.advertiser, "placements": ["PLACEMENT_TIKTOK"], "objective_type": objective_type(), "level_range": level_range}
        data, _, _ = self.call("GET", "/tool/region/", params=params, cache_ttl=TTL_CATALOG, cache_if=lambda d: bool(d.get("region_info")))
        out = []
        for r in data.get("region_info") or []:
            if not isinstance(r, dict) or not r.get("location_id"):
                continue
            out.append({"id": str(r["location_id"]), "name": str(r.get("name") or ""), "parent_id": str(r.get("parent_id") or ""),
                        "region_code": str(r.get("region_code") or ""), "area_type": str(r.get("area_type") or ""), "level": str(r.get("level") or "")})
        return out

    def search_locations(self, keywords: list[str], *, geo_types: Optional[list[str]] = None, region_codes: Optional[list[str]] = ("US",),
                         search_type: str = "BATCH_REGION_SEARCH") -> list[dict]:
        """Location tags for the keywords (/tool/targeting/search/): [{keyword, name, geo_id, geo_type, parent_id, region_code,
        description, enabled, ancestors}]. `ancestors` is the chain of parents the answer carried in `parent_tags` (nearest first:
        a city's county, then its state, then its country), each {geo_id, geo_type, name, description}; it is empty when TikTok did not
        send the parents, and a caller must then treat where a place sits as UNKNOWN, not as a match. BATCH_REGION_SEARCH takes up to
        1,000 keywords and returns the five best per keyword; BATCH_ZIPCODE_SEARCH takes exact ZIP codes; FUZZY_SEARCH takes one
        keyword and returns up to 100."""
        keywords = [k for k in keywords if k]
        if not keywords:
            return []
        if search_type == "FUZZY_SEARCH" and len(keywords) != 1:
            raise ValueError("a fuzzy search takes exactly one keyword")
        out: list[dict] = []
        for start in range(0, len(keywords), BATCH_LIMIT):                 # TikTok takes at most 1,000 keywords a call: the rest go in the next
            body: dict[str, Any] = {
                "advertiser_id": self.advertiser, "objective_type": objective_type(), "placements": ["PLACEMENT_TIKTOK"],
                "search_type": search_type, "keywords": keywords[start:start + BATCH_LIMIT],
            }
            if geo_types:
                body["geo_types"] = list(geo_types)
            if region_codes:
                body["region_codes"] = list(region_codes)
            data, _, _ = self.call("POST", "/tool/targeting/search/", body=body, cache_ttl=TTL_LOCATION,
                                   cache_if=lambda d: bool(d.get("targeting_tag_list")))
            parents: dict[str, dict] = {}
            for tag in data.get("parent_tags") or []:
                pg = (tag or {}).get("geo") or {}
                if pg.get("geo_id"):
                    parents[str(pg["geo_id"])] = {"geo_id": str(pg["geo_id"]), "geo_type": str(pg.get("geo_type") or ""), "name": str((tag or {}).get("name") or ""),
                                                  "description": str(pg.get("description") or ""), "parent_id": str(pg.get("parent_id") or "")}
            for tag in data.get("targeting_tag_list") or []:
                geo = (tag or {}).get("geo") or {}
                if not geo.get("geo_id"):
                    continue
                status = ((tag or {}).get("status_info") or {}).get("status")
                chain, cur = [], str(geo.get("parent_id") or "")
                while cur in parents and len(chain) < 8 and not any(c["geo_id"] == cur for c in chain):
                    chain.append({k: parents[cur][k] for k in ("geo_id", "geo_type", "name", "description")})
                    cur = parents[cur]["parent_id"]
                out.append({
                    "keyword": str(tag.get("keyword") or ""), "name": str(tag.get("name") or ""), "geo_id": str(geo["geo_id"]),
                    "geo_type": str(geo.get("geo_type") or ""), "parent_id": str(geo.get("parent_id") or ""),
                    "region_code": str(geo.get("region_code") or ""), "description": str(geo.get("description") or ""),
                    "enabled": status in (None, "ENABLED"), "ancestors": chain,
                })
        return out

    # ---- the audience-size estimate ----------------------------------------------------------------------------------------
    def estimate_audience(self, body: dict) -> AudienceSize:
        """POST /ad/audience_size/estimate/ - `body` is the targeting (see constanza_tiktok.build_estimate_body); the advertiser
        is filled in here."""
        payload = dict(body)
        payload["advertiser_id"] = self.advertiser
        data, request_id, cached = self.call("POST", "/ad/audience_size/estimate/", body=payload, cache_ttl=TTL_ESTIMATE)
        return parse_audience_size(data, request_id, cached)


def parse_audience_size(data: dict, request_id: Optional[str] = None, cached: bool = False) -> AudienceSize:
    """The estimate in TikTok's answer. v1.3 sends data.user_count.{lower_end, upper_end} and data.user_count_stage; the numbers
    are read leniently (strings, the v1.2 keys "1" / "2", a single number) but an answer with none is an error, never a zero."""
    uc = data.get("user_count") if isinstance(data, dict) else None
    lower = upper = None
    if isinstance(uc, dict):
        lower = _number(uc.get("lower_end", uc.get("1")))
        upper = _number(uc.get("upper_end", uc.get("2")))
    else:
        lower = upper = _number(uc)
    if lower is None and upper is None:
        keys = ", ".join(sorted(map(str, data.keys())))[:120] if isinstance(data, dict) else type(data).__name__
        raise TikTokAPIError(f"the answer had no audience size (it held: {keys or 'nothing'})", kind="unexpected", request_id=request_id)
    lower = upper if lower is None else lower
    upper = lower if upper is None else upper
    if lower < 0 or upper < 0:
        raise TikTokAPIError("the audience size was negative", kind="unexpected", request_id=request_id)
    lo, hi = min(lower, upper), max(lower, upper)
    stage = data.get("user_count_stage")
    stage = int(stage) if isinstance(stage, (int, float)) and not isinstance(stage, bool) and int(stage) in STAGE_LABELS else None
    return AudienceSize(lower=lo, upper=hi, stage=stage, request_id=request_id, cached=cached)


# ------------------------------------------------------------------------------------------------------------- the shared client

_client_lock = threading.Lock()
_client: Optional[TikTokClient] = None
_client_key: Optional[str] = None


def get_client() -> TikTokClient:
    """The process-wide client (so its caches and rate spacing are shared by every request). TikTokAPIError("not_configured")
    when the secrets are missing; a client is rebuilt if they change."""
    global _client, _client_key
    if not is_configured():
        raise not_configured_error()
    key = hashlib.sha256(f"{access_token()}|{advertiser_id()}|{api_base()}".encode()).hexdigest()
    with _client_lock:
        if _client is None or _client_key != key:
            _client = TikTokClient(token=access_token(), advertiser=advertiser_id(), base=api_base())
            _client_key = key
        return _client


def reset_client() -> None:
    """Forget the shared client and everything it cached (tests; a changed secret does the same by itself)."""
    global _client, _client_key
    with _client_lock:
        _client, _client_key = None, None
