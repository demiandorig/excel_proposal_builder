"""An in-memory stand-in for TikTok's Marketing API (v1.3), shaped like TikTok's documented answers, for the tests and for the
offline mock server used to try the dock without an ad account.

It implements only what Constanza calls - the audience-size estimate, the location search, the interest / behavior search and the
language list - and it refuses what TikTok's documentation says TikTok refuses (a missing field, overlapping locations, purchase
intent on a Reach campaign, interactions on Pangle, a wrong token), with the same return codes, so a test that passes here has
at least asked TikTok a question TikTok would take.

What it can NOT do is know TikTok's real market names or numbers: the names of the markets follow a configurable style
(`market_style`), and the audience numbers are a deterministic model (population x the share each setting keeps), not data."""
from __future__ import annotations

import json as _json
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlsplit

import requests

from app.data.constanza import load_dma_reference
from app.services.constanza_geo import _STATE_NAMES_TO_ABBR

GOOD_TOKEN = "test-token-123"
ADVERTISER = "7000000000000000001"
US_ID = "6252001"
TOTAL_US_USERS = 118_000_000

STATE_IDS = {abbr: str(5_000_000 + i) for i, abbr in enumerate(sorted(set(_STATE_NAMES_TO_ABBR.values())))}
_STATE_NAME = {}
for _n, _a in _STATE_NAMES_TO_ABBR.items():
    if len(_n) > len(_STATE_NAME.get(_a, "")):
        _STATE_NAME[_a] = _n.title().replace(" Of ", " of ")


def dma_id(rank: int) -> str:
    return str(800_000 + rank)


# a few counties and cities for the one-by-one look-ups: (id, name, state, geo_type, description)
PLACES = [
    ("910001", "Los Angeles County", "CA", "CITY", "County"),
    ("910002", "Adams County", "WA", "CITY", "County"),
    ("910003", "Adams County", "CO", "CITY", "County"),
    ("910004", "Adams County", "IL", "CITY", "County"),
    ("910005", "Harris County", "TX", "CITY", "County"),
    ("920001", "Sacramento", "CA", "DISTRICT", "City"),
    ("920002", "Springfield", "IL", "DISTRICT", "City"),
    ("920003", "Springfield", "MO", "DISTRICT", "City"),
    ("920004", "Plano", "TX", "DISTRICT", "City"),
]
COUNTRIES = [("6252001", "United States", "US"), ("6251999", "Canada", "CA"), ("3996063", "Mexico", "MX")]

# interest / behavior catalogs: (id, name, level, parent id or None)
GENERAL_INTERESTS = [
    ("10000", "Apparel & Accessories", 1, None), ("10100", "Women's Apparel", 2, "10000"), ("10200", "Men's Apparel", 2, "10000"),
    ("11000", "Vehicles & Transportation", 1, None), ("11100", "Cars & Trucks", 2, "11000"), ("11200", "Motorcycles", 2, "11000"),
    ("12000", "Beauty & Personal Care", 1, None), ("12100", "Skincare", 2, "12000"), ("12200", "Makeup", 2, "12000"),
    ("14000", "Sports & Outdoors", 1, None), ("14100", "Soccer", 2, "14000"), ("14200", "Fitness", 2, "14000"),
    ("16000", "Family & Parenting", 1, None), ("16100", "Early Childhood & Preschool Education", 2, "16000"),
]
PURCHASE_INTENTIONS = [("26100", "Automotive", 1, None), ("26101", "Photography", 2, None), ("26200", "Home Improvement", 1, None)]
VIDEO_INTERACTIONS = [("1311100", "Scenery & Plants", 3, None), ("1612", "Other Fashion", 2, None), ("1700", "Cars & Motorcycles", 1, None)]
CREATOR_INTERACTIONS = [("10", "Fashion & Beauty", 1, None), ("11", "DIY & Life Hacks", 1, None), ("12", "Sports", 1, None)]
HASHTAG_BUNDLES = [("21", "cybermonday", 1, None), ("22", "christmas", 1, None)]
ADDITIONAL = {"auto": [("19155001", "Auto Racing"), ("19155002", "Auto Repair"), ("19155003", "Auto Insurance")],
              "beauty": [("19156001", "Beauty Tutorials"), ("19156002", "Beauty Products")]}
LANGUAGES = [("en", "English"), ("es", "Spanish"), ("zh", "Chinese"), ("pt", "Portuguese"), ("fr", "French")]
AGE_WEIGHT = {"AGE_18_24": 0.22, "AGE_25_34": 0.27, "AGE_35_44": 0.21, "AGE_45_54": 0.16, "AGE_55_100": 0.14}


def market_name(dma, style: str) -> str:
    """How the fake names a TV market, so the tests can check the mapping against different conventions."""
    name = dma.dma
    if style == "plain":
        return name
    if style == "state":
        return f"{name} {dma.state}"
    if style == "comma_state":
        return f"{name}, {dma.state}"
    if style == "dma_suffix":
        return f"{name} DMA"
    raise ValueError(style)


class FakeResponse:
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self._payload = payload
        self.text = payload if isinstance(payload, str) else _json.dumps(payload)

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


def ok(data: dict, request_id: str = "REQ-OK") -> dict:
    return {"code": 0, "message": "OK", "request_id": request_id, "data": data}


def fail(code: int, message: str, request_id: str = "REQ-FAIL") -> dict:
    return {"code": code, "message": message, "request_id": request_id}


class FakeTikTok:
    def __init__(self, market_style: str = "state", token: str = GOOD_TOKEN, advertiser: str = ADVERTISER):
        self.market_style, self.token, self.advertiser = market_style, token, advertiser
        self.calls: list[dict] = []
        self.queues: dict[str, list] = {}           # path -> [FakeResponse | Exception | callable] used before the normal answer
        self.dmas = list(load_dma_reference())
        self.estimate_overrides: Optional[Callable[[dict], dict]] = None

    # ---- the session adapter (what requests.Session.request looks like to the client) ---------------------------------------
    def session(self) -> "FakeSession":
        return FakeSession(self)

    def queue(self, path: str, *responses) -> None:
        self.queues.setdefault(path, []).extend(responses)

    def count(self, path: str) -> int:
        return sum(1 for c in self.calls if c["path"] == path)

    # ---- the API ---------------------------------------------------------------------------------------------------------
    def handle(self, method: str, path: str, headers: dict, params: dict, body: Optional[dict]) -> tuple[int, Any]:
        self.calls.append({"method": method, "path": path, "headers": dict(headers), "params": dict(params or {}), "body": body})
        queued = self.queues.get(path)
        if queued:
            item = queued.pop(0)
            if isinstance(item, BaseException):
                raise item
            if callable(item):
                return item()
            return item.status_code, item._payload
        if headers.get("Access-Token") != self.token:
            return 200, fail(40105, "Access token is incorrect or has been revoked.")
        route = {
            ("GET", "/tool/language/"): self._languages,
            ("GET", "/targeting/search/"): self._targeting,
            ("POST", "/tool/targeting/search/"): self._locations,
            ("POST", "/ad/audience_size/estimate/"): self._estimate,
        }.get((method, path))
        if route is None:
            return 404, fail(40008, "The interface has not been implemented.")
        return 200, route(params or {}, body or {})

    def _check_advertiser(self, value: Any) -> Optional[dict]:
        if not value:
            return fail(40002, "advertiser_id: Missing data for required field.")
        if str(value) != self.advertiser:
            return fail(40300, "The advertiser does not exist or has been deleted.")
        return None

    # ---- languages -------------------------------------------------------------------------------------------------------
    def _languages(self, params: dict, body: dict) -> dict:
        bad = self._check_advertiser(params.get("advertiser_id"))
        return bad or ok({"languages": [{"code": c, "name": n} for c, n in LANGUAGES]})

    # ---- interests & behaviors -------------------------------------------------------------------------------------------
    @staticmethod
    def _item(kind: str, row: tuple, table: list) -> dict:
        ident, name, level, _parent = row
        children = [r[0] for r in table if r[3] == ident]
        return {"id": ident, "sub_targeting_type": kind.upper(), "name": name, "children_ids": children, "level": level,
                "supported_special_industries": [], "hashtag_type": "HASHTAG_BUNDLE" if kind == "hashtag_interaction" else None}

    def _targeting(self, params: dict, body: dict) -> dict:
        bad = self._check_advertiser(params.get("advertiser_id"))
        if bad:
            return bad
        if params.get("targeting_type") != "INTEREST_AND_BEHAVIOR":
            return fail(40002, "targeting_type: Must be INTEREST_AND_BEHAVIOR.")
        keywords = _json.loads(params["search_keywords"]) if params.get("search_keywords") else None
        if params.get("sub_targeting_types"):
            subs = _json.loads(params["sub_targeting_types"])
        else:                    # TikTok's documented defaults: every kind for one keyword, all but the additional interests for a list
            subs = ["GENERAL_INTEREST", "PURCHASE_INTENTION", "VIDEO_INTERACTION", "CREATOR_INTERACTION", "HASHTAG_INTERACTION"]
            if keywords is not None:
                subs.insert(1, "ADDITIONAL_INTEREST")
        tables = {"general_interest": GENERAL_INTERESTS, "purchase_intention": PURCHASE_INTENTIONS, "video_interaction": VIDEO_INTERACTIONS,
                  "creator_interaction": CREATOR_INTERACTIONS, "hashtag_interaction": HASHTAG_BUNDLES}
        sub_names = {"GENERAL_INTEREST": "general_interest", "ADDITIONAL_INTEREST": "additional_interest", "PURCHASE_INTENTION": "purchase_intention",
                     "VIDEO_INTERACTION": "video_interaction", "CREATOR_INTERACTION": "creator_interaction", "HASHTAG_INTERACTION": "hashtag_interaction"}
        data: dict[str, Any] = {k: None for k in sub_names.values()}
        for sub in subs:
            kind = sub_names.get(sub)
            if kind is None:
                return fail(40002, f"sub_targeting_types: {sub} is not supported.")
            if keywords is None:
                if kind == "additional_interest":
                    continue
                table = tables[kind]
                data[kind] = {"list_result": [self._item(kind, r, table) for r in table], "search_result": None}
                continue
            word = keywords[0].lower()
            if kind == "additional_interest":
                hits = [{"id": i, "sub_targeting_type": "ADDITIONAL_INTEREST", "name": n, "children_ids": None, "level": None,
                         "supported_special_industries": [], "hashtag_type": None}
                        for key, rows in ADDITIONAL.items() if key in word or word in key for i, n in rows]
            elif kind == "hashtag_interaction":
                hits = [{"id": "6700" + str(i), "sub_targeting_type": "HASHTAG_INTERACTION", "name": f"#{word}{suffix}", "children_ids": None,
                         "level": None, "supported_special_industries": [], "hashtag_type": "HASHTAG"} for i, suffix in enumerate(("", "tips"))]
            else:
                table = tables[kind]
                hits = [self._item(kind, r, table) for r in table if word in r[1].lower()]
            data[kind] = {"list_result": None, "search_result": {keywords[0]: hits}}
        return ok(data)

    # ---- locations -------------------------------------------------------------------------------------------------------
    def _all_locations(self) -> list[dict]:
        out = [{"id": i, "name": n, "type": "COUNTRY", "parent": "0", "region": r, "description": "Country"} for i, n, r in COUNTRIES]
        for abbr, ident in STATE_IDS.items():
            out.append({"id": ident, "name": _STATE_NAME.get(abbr, abbr), "type": "PROVINCE", "parent": US_ID, "region": "US", "description": "State"})
        for d in self.dmas:
            out.append({"id": dma_id(d.rank), "name": market_name(d, self.market_style), "type": "DMA", "parent": US_ID, "region": "US", "description": "DMA"})
        for ident, name, state, gtype, desc in PLACES:
            out.append({"id": ident, "name": name, "type": gtype, "parent": STATE_IDS[state], "region": "US", "description": desc})
        return out

    @staticmethod
    def _tag(loc: dict, keyword: str) -> dict:
        return {"keyword": keyword, "name": loc["name"], "targeting_type": "GEO", "status_info": {"status": "ENABLED", "reason": None},
                "geo": {"geo_id": loc["id"], "geo_type": loc["type"], "parent_id": loc["parent"], "region_code": loc["region"], "description": loc["description"]}}

    def _locations(self, params: dict, body: dict) -> dict:
        bad = self._check_advertiser(body.get("advertiser_id"))
        if bad:
            return bad
        for need in ("objective_type", "placements", "search_type", "keywords"):
            if not body.get(need):
                return fail(40002, f"{need}: Missing data for required field.")
        kind, keywords = body["search_type"], body["keywords"]
        geo_types, regions = body.get("geo_types"), body.get("region_codes")
        if kind in ("BATCH_REGION_SEARCH", "BATCH_ZIPCODE_SEARCH") and not regions:
            return fail(40002, "region_codes: Required when search_type is BATCH_REGION_SEARCH or BATCH_ZIPCODE_SEARCH.")
        if kind == "FUZZY_SEARCH" and len(keywords) != 1:
            return fail(40002, "keywords: Only one keyword is allowed for FUZZY_SEARCH.")
        if len(keywords) > 1000:
            return fail(40011, "Too many IDs are provided in a single request.")
        tags: list[dict] = []
        if kind == "BATCH_ZIPCODE_SEARCH":
            for z in keywords:
                if z.isdigit() and len(z) == 5 and z not in ("00000", "99999"):
                    tags.append({"keyword": z, "name": z, "targeting_type": "GEO", "status_info": {"status": "ENABLED", "reason": None},
                                 "geo": {"geo_id": "88400004" + z, "geo_type": "ZIP_CODE", "parent_id": "910001", "region_code": "US", "description": "postcode"}})
            return ok({"targeting_tag_list": tags, "parent_tags": []})
        pool = [l for l in self._all_locations() if (not geo_types or l["type"] in geo_types) and (not regions or l["region"] in regions)]
        limit = 5 if kind == "BATCH_REGION_SEARCH" else 100
        for kw in keywords:
            word = kw.lower()
            hits = [l for l in pool if word in l["name"].lower()]
            hits.sort(key=lambda l: (not l["name"].lower().startswith(word), len(l["name"]), l["name"]))
            tags.extend(self._tag(l, kw) for l in hits[:limit])
        return ok({"targeting_tag_list": tags, "parent_tags": []})

    # ---- the audience-size estimate ------------------------------------------------------------------------------------------
    def _location_users(self, body: dict) -> tuple[Optional[float], Optional[dict]]:
        """(the users in the requested locations, None) or (None, the error TikTok would answer with)."""
        ids = list(body.get("location_ids") or [])
        zips = list(body.get("zipcode_ids") or [])
        by_id = {l["id"]: l for l in self._all_locations()}
        pop = {dma_id(d.rank): d.population for d in self.dmas}
        total = 0.0
        kinds = set()
        for ident in ids:
            loc = by_id.get(str(ident))
            if loc is None:
                return None, fail(40002, f"location_ids: {ident} is not a valid location ID.")
            kinds.add(loc["type"])
            if loc["type"] == "COUNTRY":
                total += TOTAL_US_USERS if loc["region"] == "US" else TOTAL_US_USERS / 10
            elif loc["type"] == "PROVINCE":
                total += TOTAL_US_USERS * (0.01 + (int(ident) % 7) * 0.012)
            elif loc["type"] == "DMA":
                total += TOTAL_US_USERS * pop.get(str(ident), 1_000_000) / 330_000_000
            else:
                total += TOTAL_US_USERS * 0.004
        total += len(zips) * 21_000
        if US_ID in ids and (len(ids) > 1 or zips):
            return None, fail(40002, "Overlapping locations are not supported. For instance, you cannot target the U.S. and the state of California at the same time.")
        if "COUNTRY" in kinds and "PROVINCE" in kinds:
            return None, fail(40002, "Overlapping locations are not supported.")
        return total, None

    def _estimate(self, params: dict, body: dict) -> dict:
        bad = self._check_advertiser(body.get("advertiser_id"))
        if bad:
            return bad
        for need in ("objective_type", "optimization_goal"):
            if not body.get(need):
                return fail(40002, f"{need}: Missing data for required field.")
        if body.get("placement_type", "PLACEMENT_TYPE_NORMAL") == "PLACEMENT_TYPE_NORMAL" and not body.get("placements"):
            return fail(40002, "placements: Required when placement_type is PLACEMENT_TYPE_NORMAL.")
        if not body.get("location_ids") and not body.get("zipcode_ids"):
            return fail(40002, "You need to set location_ids or zipcode_ids or both.")
        if body.get("zipcode_ids") and "PLACEMENT_TIKTOK" not in (body.get("placements") or []):
            return fail(40002, "Zip code targeting is only supported on the TikTok placement.")
        if body.get("purchase_intention_keyword_ids") and body.get("interest_keyword_ids"):
            return fail(40002, "Do not pass in purchase_intention_keyword_ids and interest_keyword_ids at the same time.")
        if body.get("purchase_intention_keyword_ids") and body.get("objective_type") in ("REACH", "VIDEO_VIEWS", "ENGAGEMENT"):
            return fail(40002, "purchase_intention_keyword_ids is not supported for this objective_type.")
        actions = body.get("actions") or []
        if actions and (body.get("placements") or []) != ["PLACEMENT_TIKTOK"]:
            return fail(40002, "action_category_ids: Valid only when TikTok placement is the only placement selected.")
        for g in body.get("age_groups") or []:
            if g not in AGE_WEIGHT and g != "AGE_13_17":
                return fail(40002, f"age_groups: {g} is not a valid value.")
        if body.get("gender") not in (None, "GENDER_MALE", "GENDER_FEMALE", "GENDER_UNLIMITED"):
            return fail(40002, "gender: Invalid value.")
        base, err = self._location_users(body)
        if err:
            return err
        share = 1.0
        if body.get("age_groups"):
            share *= sum(AGE_WEIGHT.get(g, 0.0) for g in body["age_groups"])
        if body.get("gender") == "GENDER_MALE":
            share *= 0.48
        elif body.get("gender") == "GENDER_FEMALE":
            share *= 0.52
        if body.get("languages"):
            language_share = {"en": 0.8, "es": 0.25, "zh": 0.03, "pt": 0.02, "fr": 0.02}
            share *= min(1.0, sum(language_share.get(code, 0.01) for code in body["languages"]))
        for field, factor in (("interest_category_ids", 0.55), ("interest_keyword_ids", 0.45), ("purchase_intention_keyword_ids", 0.4)):
            for _ in body.get(field) or []:
                share *= factor
        for action in actions:
            for _ in action.get("action_category_ids") or []:
                share *= 0.35
        if body.get("spending_power") == "HIGH":
            share *= 0.3
        if body.get("household_income"):
            share *= 0.12 * len(body["household_income"])
        if body.get("operating_systems") == ["IOS"]:
            share *= 0.55
        elif body.get("operating_systems") == ["ANDROID"]:
            share *= 0.45
        if body.get("network_types"):
            share *= 0.3 + 0.2 * len(body["network_types"])
        mid = base * min(share, 1.0)
        lower, upper = round(mid * 0.9, -3), round(mid * 1.1, -3)
        fraction = min(share, 1.0)
        stage = 1 if (upper < 10_000 and fraction < 0.2) else 2 if fraction < 0.2 else 3 if fraction < 0.8 else 4
        data = {"user_count": {"lower_end": lower, "upper_end": upper}, "user_count_stage": stage}
        if self.estimate_overrides:
            data = self.estimate_overrides(data)
        return ok(data, request_id=f"REQ-{len(self.calls):04d}")


class FakeSession:
    """requests.Session.request, answered by a FakeTikTok."""

    def __init__(self, fake: FakeTikTok):
        self.fake = fake

    def request(self, method: str, url: str, headers: Optional[dict] = None, params: Optional[dict] = None, json: Optional[dict] = None,
                timeout: Any = None, **_ignored) -> FakeResponse:
        base = urlsplit(url)
        path = base.path
        if "/open_api/v1.3" in path:
            path = path.split("/open_api/v1.3", 1)[1]
        merged = dict(parse_qsl(base.query))
        merged.update(params or {})
        status, payload = self.fake.handle(method, path, headers or {}, merged, json)
        return FakeResponse(status, payload)


def make_client(fake: Optional[FakeTikTok] = None, **kw):
    """A TikTokClient talking to a fake, with no real waiting."""
    from app.services.tiktok_client import TikTokClient
    fake = fake or FakeTikTok()
    sleeps: list[float] = []
    clock = kw.pop("clock", None)
    args = dict(token=GOOD_TOKEN, advertiser=ADVERTISER, session=fake.session(), sleep=sleeps.append, min_interval=0.0)
    if clock is not None:
        args["clock"] = clock
    args.update(kw)
    client = TikTokClient(**args)
    client.sleeps = sleeps               # type: ignore[attr-defined]
    client.fake = fake                   # type: ignore[attr-defined]
    return client


def timeout_error() -> BaseException:
    return requests.Timeout("timed out")
