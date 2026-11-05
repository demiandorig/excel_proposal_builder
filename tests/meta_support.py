"""Shared Meta test double: a Graph API shaped like Meta's reference, and the response it returns. Used by the Meta backend tests, the
registry tests and the browser tests (the same way tests/tiktok_support.py serves TikTok). The ids below are INVENTED for the fake."""
from __future__ import annotations

import json

AWARENESS = "Facebook & Instagram Ads | Awareness"
TOKEN = "EAAB-test-private-token-0123456789"
ACCOUNT = "act_7000000000012345"


class FakeGraph:
    """A Graph API shaped like Meta's reference: /search (adgeolocation, adinterest, adTargetingCategory, adlocale) and act_<id>/reachestimate."""

    def __init__(self):
        self.calls: list[dict] = []
        self.reach = (900, 1100)
        self.error: dict | None = None
        self.http_status = 200
        self.estimate_ready = True
        self.empty = False             # the lists answer with no rows (a transient empty answer must not stick)
        self.narrow_by_language = True   # a language narrows the reach estimate (False: the fake "ignores" locales, like a Meta that did not apply them)
        self.refuse_locales = False      # reachestimate refuses a spec that carries locales (Meta's "Invalid parameter")
        self.language_jitter = 0         # a spec with locales answers this much BELOW the same spec without them (0 = no change; use with narrow_by_language=False)
        self.plain_us_failures = 0       # the next N asks for the plain U.S. estimate (a country alone) fail with a transient error
        self.extra_demographics: list[dict] = []     # more rows in the demographics list (e.g. a long type or name)
        self.account_name = f"Entravision {ACCOUNT} ({ACCOUNT.removeprefix('act_')}) Test Account"       # agencies often name an ad account with its own id (the whole value the contract's C2 looks for, and its digits): the connection test must not show it
        self.shape = "object"          # how reachestimate wraps its node: Meta's reference says "a single node" = one object ("list", "bare" also read)
        # adlocale rows: Meta's reference shows `key` (a number) and `name`; invented ids
        self.locales = [{"key": 6, "name": "English (US)"}, {"key": 24, "name": "English (UK)"}, {"key": 1001, "name": "Spanish"},
                        {"key": 1002, "name": "Spanish (Mexico)"}, {"key": 1003, "name": "Spanish (Spain)"}, {"key": 1004, "name": "Portuguese (Brazil)"}]

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {}), "timeout": timeout})
        if self.error is not None:
            return Resp({"error": self.error}, self.http_status)
        path = url.split("graph.facebook.com/", 1)[1]
        version, rest = path.split("/", 1)
        params = params or {}
        if rest == "search":
            kind = params.get("type")
            if kind == "adgeolocation":
                return Resp({"data": [{"key": "803", "name": "Los Angeles", "type": "geo_market", "country_code": "US", "region": "California"},
                                      {"key": "804", "name": "Los Angeles-Long Beach", "type": "geo_market", "country_code": "US"}]})
            if kind == "adinterest":
                return Resp({"data": [{"id": "6003107902433", "name": "Soccer", "audience_size_lower_bound": 100_000_000, "audience_size_upper_bound": 120_000_000,
                                       "path": ["Interests", "Sports and outdoors", "Soccer"]},
                                      {"id": "6003012345678", "name": "Soccer balls", "audience_size": 4_000_000}]})
            if kind == "adlocale":
                q = (params.get("q") or "").lower()
                rows = [r for r in self.locales if not isinstance(r, dict) or str(r.get("name") or "").lower().startswith(q)]       # autocomplete: a prefix
                return Resp({"data": [] if self.empty else rows[:int(params.get("limit", 8))]})                                      # Meta's default limit is 8
            if kind == "adTargetingCategory" and self.empty:
                return Resp({"data": []})
            if kind == "adTargetingCategory":
                cls = params.get("class")
                rows = {"behaviors": [{"id": "6002714895372", "name": "Frequent travelers", "type": "behaviors", "audience_size_lower_bound": 2_000_000, "audience_size_upper_bound": 2_400_000,
                                       "path": ["Behaviors", "Travel", "Frequent travelers"]},
                                      {"id": "6004386044572", "name": "Small business owners", "type": "behaviors", "path": ["Behaviors", "Business", "Small business owners"]}],
                        "interests": [{"id": "6003107902433", "name": "Soccer", "type": "interests"}, {"id": "6003020834693", "name": "Cooking", "type": "interests"}],
                        "demographics": [{"id": "6002714398172", "name": "Newlyweds (1 year)", "type": "life_events", "path": ["Demographics", "Life events", "Newlywed"]},
                                         {"id": "6015559470583", "name": "Likely to engage with political content (US)", "type": "politics", "path": ["Demographics", "Politics"]},
                                         {"id": "6004854404172", "name": "iPhone 15", "type": "user_device", "path": ["Demographics", "Mobile Device User", "iPhone"]},
                                         {"id": "6006", "name": "No type at all"}]}[cls]
                return Resp({"data": rows + (self.extra_demographics if cls == "demographics" else [])})
        if rest.endswith("/reachestimate"):
            lo, hi = self.reach
            spec = json.loads(params["targeting_spec"])
            if spec == {"geo_locations": {"countries": ["US"]}} and self.plain_us_failures > 0:
                self.plain_us_failures -= 1
                return Resp({"error": {"message": "transient", "code": 1}}, 500)
            if spec.get("locales"):
                if self.refuse_locales:
                    return Resp({"error": {"message": "Invalid parameter", "code": 100}}, 400)
                if self.narrow_by_language:                                        # a language only narrows the audience
                    lo, hi = lo // 3, hi // 3
                lo, hi = lo - self.language_jitter, hi - self.language_jitter
            node = {"users_lower_bound": lo, "users_upper_bound": hi, "estimate_ready": self.estimate_ready}
            return Resp({"data": node if self.shape == "object" else ([node] if self.shape == "list" else None)} if self.shape != "bare" else node)
        if rest.startswith("act_") and "/" not in rest:
            return Resp({"name": self.account_name, "account_status": 1, "currency": "USD"})
        return Resp({"error": {"message": f"Unknown path {rest}", "code": 100}}, 400)


class Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._body
