"""
The registry of Constanza's avails METHODS: one place that says which sources of an avails estimate exist and how each one is reached.

A method is anything with its own estimate (and, in the browser, its own panel). Two are built in and need no entry here - `platform`
(avails loaded from a data series) and `generic` (the line's own budget spread by population) both run through
constanza_engine.estimate_avails. Everything else is REGISTERED here by the module that implements it (today TikTok: constanza_tiktok.py,
and Meta: constanza_meta.py), with one `register(MethodSpec(...))` call at the bottom of that module.

The rest of the app asks this registry instead of naming a method:
  * POST /api/constanza/estimate dispatches to `spec.estimate` for any registered method (main.py `_run_method`) and checks the answer
    against the STANDARD ESTIMATE below, so every method answers in the shape the dock and the Step 05 card already read;
  * Admin -> Forecast builds its "What it computes" list, its summary cards and its connection cards from `all()`;
  * constanza_forecast_map validates an admin's choice against `method_keys()` and takes the default method of a product from `default_for`;
  * the database no longer enumerates method names (migration 0006): this registry is the validator.

To add the next source, follow docs/constanza_methods.md. The backend part is a module `constanza_foo.py` with `estimate_response`,
`status` and a diagnose function, one `register(MethodSpec(key="foo", ...))`, and its path added to MODULES below.

IMPORT RULES (the reason this module imports nothing from the app): constanza_forecast_map -> constanza_tiktok -> constanza_audience ->
constanza_engine -> constanza_forecast_map is a cycle the moment the forecast map loads a method module at import time. So this module is a
leaf; `ensure_loaded()` (which imports the method modules) is called only when the app is already running - by main.py after its imports and
by the first read of the registry - never while a module is being imported and never under constanza_forecast_map's lock.
"""
from __future__ import annotations

import builtins
import dataclasses
import importlib
import math
import os
import re
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator, Optional

if TYPE_CHECKING:                                  # a type only: importing it for real would make this module depend on the app
    from app.services.constanza_geo import GeoResolution

_all = builtins.all                                # this module defines its own `all()` (the registry list)

BUILTIN_METHODS = ("platform", "generic")          # data methods: constanza_engine.estimate_avails, no registry entry
# The key format: also the database CHECK (migration 0006) and the prefix of a method's plugin file / routes / ids. Test a key with `fullmatch`:
# Python's `$` also matches before a final newline ("meta\n"), which Postgres's does not.
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
# The modules that register a method, in the order the methods are listed. A new method adds its module path here once; the contract test
# fails when a module under app/services calls `register(` and is missing from this tuple.
MODULES = ("app.services.constanza_tiktok", "app.services.constanza_meta")

KINDS = ("api", "dataset")


# ----------------------------------------------------------------------------------------------------------------- the types

@dataclass(frozen=True)
class MethodRequest:
    """What the generic dispatch hands a method's `estimate`."""
    product_name: str
    months: int                                    # already range-checked (1..120) by main._check_constanza_estimate_input
    flight_days: Optional[float]
    args: dict                                     # body.<request_field>.model_dump(), or {} (pydantic already validated the shape and the caps)
    resolution: "Optional[GeoResolution]"          # None when spec.owns_geography: the shared geography is NOT resolved for such a method
    geo_notes: list                                # main._geo_warning_notes(resolution), or []
    hispanic_asked: bool                           # the planner asked for Hispanic targeting (flag or an interest term): say "not applied" if you cannot


@dataclass(frozen=True)
class MethodSpec:
    key: str                                       # ForecastConfig.method, forecast_product_map.method, ConstanzaEstimateRequest field, plugin file prefix
    label: str                                     # Admin "What it computes" option: "Meta API (live)"
    short_label: str                               # Admin summary card / picker group: "Meta"
    description: str                               # Admin hint, one sentence in plain words
    kind: str                                      # "api" | "dataset"
    default_products: tuple                        # catalog names this is the DEFAULT method of (renamed variants are resolved by default_for)
    owns_geography: bool                           # True: the shared Where card is hidden and geo_text is neither required nor resolved
    request_field: str                             # == key (checked): the field of ConstanzaEstimateRequest that carries its block
    secrets: tuple                                 # env var NAMES (never values): shown in Admin and in "not connected" messages
    estimate: Callable[[MethodRequest], dict]      # -> the STANDARD ESTIMATE; raises ValueError (422) or one of `errors`
    status: Callable[[], dict]                     # {"configured": bool, "missing": [str, name first], "account": str|None (last four digits), "setup_hint": str, ...extras}
    diagnose: str                                  # "module:function" (resolved at call time, which avoids import cycles) -> list[Check]; required for kind="api"
    errors: tuple = ()
    to_http: Optional[Callable[[Exception], tuple]] = None        # (status, plain-language detail); default: not used (a ValueError is a 422 with its own words)
    product_info: Optional[Callable[[str], dict]] = None          # per-product facts merged into the catalog as forecast.info (Meta: {"retail_cpm": 12.0})
    status_route: str = ""                         # default f"/api/constanza/{key}/status"

    @property
    def status_url(self) -> str:
        return self.status_route or f"/api/constanza/{self.key}/status"

    @property
    def test_url(self) -> str:
        return f"/api/admin/constanza/{self.key}/test"

    def http_error(self, exc: Exception) -> tuple:
        """(HTTP status, plain-language detail) of an error this method raises. A method with no `to_http` answers 502 with the error's text."""
        if self.to_http is not None:
            status, detail = self.to_http(exc)
            return int(status), str(detail)
        return 502, str(exc)


@dataclass
class Check:
    """One line of a connection test (Admin -> Forecast "Test the connection", python -m app.services.<key>_diagnose). Shared by every method."""
    name: str
    ok: bool
    detail: str
    warn: bool = False                # worked, but something deserves a look
    seconds: float = 0.0

    def to_public(self) -> dict:
        return {"name": self.name, "ok": self.ok, "warn": self.warn and self.ok, "detail": self.detail, "seconds": round(self.seconds, 2)}


# ----------------------------------------------------------------------------------------------------------------- the registry

_lock = threading.RLock()                          # guards the dictionaries below (never held while a module is imported)
_load_lock = threading.RLock()                     # serialises ensure_loaded(); a different lock, so a module that registers while another thread loads cannot deadlock it
_REGISTRY: dict[str, MethodSpec] = {}
_ORIGIN: dict[str, str] = {}                       # key -> the module that registered it (decides the listing order)
_SEQUENCE: dict[str, int] = {}                     # key -> registration order (the tie-break)
_loaded = False
_loading = False


def _origin_of_caller() -> str:
    try:
        return str(sys._getframe(2).f_globals.get("__name__", ""))
    except (AttributeError, ValueError):           # pragma: no cover - an interpreter without frames
        return ""


def register(spec: MethodSpec) -> MethodSpec:
    """Add a method. ValueError (naming what is wrong) for a spec that could not work; returns the spec so a module can keep it as `SPEC`."""
    if not isinstance(spec, MethodSpec):
        raise ValueError("register() takes a MethodSpec.")
    if not isinstance(spec.key, str) or not KEY_RE.fullmatch(spec.key):
        raise ValueError(f"A method key is a lower-case word of 2-32 letters, digits or underscores (got {spec.key!r}).")
    if spec.key in BUILTIN_METHODS:
        raise ValueError(f"'{spec.key}' is a built-in method and cannot be registered.")
    if spec.request_field != spec.key:
        raise ValueError(f"Method '{spec.key}': request_field must equal the key ({spec.request_field!r} given) - the estimate request carries its block under the method's key.")
    if spec.kind not in KINDS:
        raise ValueError(f"Method '{spec.key}': kind must be one of {', '.join(KINDS)}.")
    for name in ("label", "short_label", "description"):
        if not isinstance(getattr(spec, name), str) or not getattr(spec, name).strip():
            raise ValueError(f"Method '{spec.key}': {name} must say something (it is shown in Admin).")
    if not callable(spec.estimate) or not callable(spec.status):
        raise ValueError(f"Method '{spec.key}': estimate and status must be callable.")
    if spec.kind == "api" and not (isinstance(spec.diagnose, str) and re.fullmatch(r"[\w.]+:[A-Za-z_]\w*", spec.diagnose)):
        raise ValueError(f"Method '{spec.key}': an API method needs a diagnose function (\"module:function\") for Admin's \"Test the connection\".")
    if spec.kind == "api" and not spec.secrets:
        raise ValueError(f"Method '{spec.key}': an API method names the environment variables it needs (secrets).")
    with _lock:
        if spec.key in _REGISTRY:
            raise ValueError(f"Method '{spec.key}' is already registered.")
        taken = {name: key for key, other in _REGISTRY.items() for name in other.default_products}
        clash = next((name for name in spec.default_products if name in taken), None)
        if clash is not None:
            raise ValueError(f"'{clash}' is already the default product of method '{taken[clash]}'.")
        _REGISTRY[spec.key] = spec
        _ORIGIN[spec.key] = _origin_of_caller()
        _SEQUENCE[spec.key] = len(_SEQUENCE)
    return spec


def unregister(key: str) -> None:
    """Remove a method. For tests that register a throw-away one; the app never removes a method."""
    with _lock:
        _REGISTRY.pop(key, None)
        _ORIGIN.pop(key, None)


def ensure_loaded() -> None:
    """Import the method modules (MODULES) so they register. Idempotent and thread-safe; a call that arrives while the modules are being imported
    (the same thread, from inside one of them) returns at once. Never call it while a module is being imported or under another lock."""
    global _loaded, _loading
    if _loaded:
        return
    with _load_lock:
        if _loaded or _loading:
            return
        _loading = True
        try:
            for name in MODULES:
                importlib.import_module(name)
            _loaded = True
        finally:
            _loading = False


def _order(key: str) -> tuple:
    origin = _ORIGIN.get(key, "")
    return (MODULES.index(origin) if origin in MODULES else len(MODULES), _SEQUENCE.get(key, 0))


def get(key: Any) -> Optional[MethodSpec]:
    ensure_loaded()
    return _REGISTRY.get(key) if isinstance(key, str) else None


def all() -> list:                                  # noqa: A001 - the registry's own list, in listing order
    ensure_loaded()
    with _lock:
        return [_REGISTRY[key] for key in sorted(_REGISTRY, key=_order)]


def keys() -> list:
    return [spec.key for spec in all()]


def method_keys() -> tuple:
    """Every method a product can be mapped to: the built-ins, then the registered ones."""
    return BUILTIN_METHODS + tuple(keys())


def default_products() -> dict:
    """{catalog product name: method key} for every registered method's default products."""
    return {name: spec.key for spec in all() for name in spec.default_products}


def default_for(product_name: str, variants: Optional[Iterable] = None) -> Optional[str]:
    """The registered method a product uses when it has no admin row, found through every name the product has answered to (so a renamed
    SKU keeps its method, the way DEFAULT_PLATFORM_PRODUCTS is resolved). None for a product no method claims."""
    by_name = default_products()
    if product_name in by_name:
        return by_name[product_name]
    return next((by_name[v] for v in sorted(variants or ()) if v in by_name), None)


def summary_text(spec: MethodSpec) -> str:
    """The sentence under a method's count on Admin's summary card."""
    return f"a live audience-size estimate from {spec.short_label}" if spec.kind == "api" else f"{spec.short_label} data"


def redact_secrets(spec: MethodSpec, text: Any) -> str:
    """`text` with the value of each environment variable the method names in `spec.secrets` taken out: an all-digit one (an ad account, with or
    without Meta's `act_`) is left as its last four digits, any other as its name in brackets. A value shorter than five characters is left alone
    (it would be found inside ordinary numbers and words). A method's own diagnose redacts what it knows (Meta also knows its app secret and the
    proof made from it); this is the net under it, so the next method's test cannot print the one thing every method declares."""
    out = str(text)
    for name in spec.secrets:
        value = (os.environ.get(name) or "").strip()
        bare = value.removeprefix("act_")
        if bare.isdigit() and len(bare) > 4:
            out = out.replace(bare, "…" + bare[-4:])
        elif len(value) > 4:
            out = out.replace(value, f"[{name}]")
    return out


def run_diagnose(key: str) -> list:
    """The connection checks of a method ([Check]), every line with its secrets redacted. Its diagnose function is looked up now, so a test (or a
    hot fix) that replaces it is honoured."""
    spec = get(key)
    if spec is None or not spec.diagnose:
        raise KeyError(key)
    module_name, _, function_name = spec.diagnose.partition(":")
    checks = list(getattr(importlib.import_module(module_name), function_name)())
    return [dataclasses.replace(c, detail=redact_secrets(spec, c.detail)) if isinstance(c, Check) else c for c in checks]


# ----------------------------------------------------------------------------------------------------------------- one connection test at a time

class ConnectionTestBusy(Exception):
    """A connection test of this method is already running."""


_test_locks: dict[str, threading.Lock] = {}


@contextmanager
def connection_test(key: str) -> Iterator[None]:
    """Hold the one-test-at-a-time lock of a method. A second caller gets ConnectionTestBusy at once instead of queueing: a Meta test makes about
    fourteen Graph calls and Meta rate-limits an ad account after a burst, and a button that is disabled in one browser protects only that browser."""
    with _lock:
        lock = _test_locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ConnectionTestBusy(key)
    try:
        yield
    finally:
        lock.release()


# ----------------------------------------------------------------------------------------------------------------- the standard estimate

class StandardEstimateError(Exception):
    """A method answered in a shape the dock cannot read. Not a ValueError on purpose: it is a bug in the method (a 500 with the reason logged), never the planner's mistake."""


STANDARD_KEYS = ("method", "product_name", "platform", "confidence", "geo_method", "geo_confidence", "months", "imps_low", "imps_mid", "imps_high",
                 "est_uniques", "reach_universe", "series", "weeks", "notes", "unmatched_geo_tokens", "unmatched_geo_details", "ambiguous",
                 "geo_interpretations", "geo_suggestions", "by_area", "hispanic_targeting")
_LIST_KEYS = ("notes", "unmatched_geo_tokens", "unmatched_geo_details", "ambiguous", "geo_interpretations", "geo_suggestions", "by_area")


def standard_estimate(spec: MethodSpec, *, product_name: str, months: int, imps: dict, est_uniques: Optional[float], notes: list, block: dict,
                      resolution: "Optional[GeoResolution]" = None, geo_method: Optional[str] = None, geo_confidence: Optional[str] = None,
                      confidence: str = "live_api", unmatched: Iterable = ()) -> dict:
    """The answer every method returns: the keys the data path answers with, `method`, and the method's own block under its key.

    `imps` is {"low", "mid", "high"} FLIGHT totals (monthly x months; the card divides by months). The geography lists come from `resolution`
    when the shared geography was resolved for this method and are empty for one that owns its geography; `unmatched` ([{input, reason}]) adds
    places the method itself could not use (TikTok: a market it could not place) to those lists."""
    extra = [dict(u) for u in unmatched]
    resolved = resolution is not None
    if geo_method is None:
        geo_method = resolution.method if resolved else f"{spec.key}_audience"
    if geo_confidence is None:
        geo_confidence = resolution.geo_confidence if resolved else "selected"
    tokens = (list(resolution.unmatched_tokens) if resolved else []) + [u["input"] for u in extra]
    details = ([{"input": d["input"], "reason": d["reason"]} for d in resolution.unmatched_details] if resolved else []) \
        + [{"input": u["input"], "reason": u["reason"]} for u in extra]
    return {
        "method": spec.key,
        "product_name": product_name,
        "platform": spec.key,
        "confidence": confidence,
        "geo_method": geo_method,
        "geo_confidence": geo_confidence,
        "months": months,
        "imps_low": imps["low"], "imps_mid": imps["mid"], "imps_high": imps["high"],
        "est_uniques": est_uniques,
        "reach_universe": None,
        "series": None,
        "weeks": None,
        "notes": list(notes),
        "unmatched_geo_tokens": tokens,
        "unmatched_geo_details": details,
        "ambiguous": resolution.ambiguous if resolved else [],
        "geo_interpretations": resolution.interpretations if resolved else [],
        "geo_suggestions": resolution.suggestions if resolved else [],
        "by_area": [],
        "hispanic_targeting": {"default": False, "on": False, "applies": False},
        spec.key: block,
    }


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_standard(spec: MethodSpec, out: Any) -> dict:
    """The answer, checked: every standard key present, `method` the spec's key, the method's block there, the impression figures real numbers in
    order. StandardEstimateError (naming what is wrong) otherwise. Nothing is filled in: a default impression count would be a silently wrong
    number, and an answer that differs between tests and production could not be reproduced."""
    if not isinstance(out, dict):
        raise StandardEstimateError(f"Method '{spec.key}' answered with {type(out).__name__}, not an object.")
    missing = [k for k in (*STANDARD_KEYS, spec.key) if k not in out]
    if missing:
        raise StandardEstimateError(f"Method '{spec.key}' answered without: {', '.join(missing)}.")
    if out["method"] != spec.key:
        raise StandardEstimateError(f"Method '{spec.key}' answered as method {out['method']!r}.")
    if not isinstance(out[spec.key], dict):
        raise StandardEstimateError(f"Method '{spec.key}': its block ('{spec.key}') must be an object.")
    months = out["months"]
    if isinstance(months, bool) or not isinstance(months, int) or months < 1:
        raise StandardEstimateError(f"Method '{spec.key}': months must be a whole number of at least 1.")
    for k in ("imps_low", "imps_mid", "imps_high"):
        if not _number(out[k]) or out[k] < 0:
            raise StandardEstimateError(f"Method '{spec.key}': {k} must be a number of impressions (got {out[k]!r}).")
    if not out["imps_low"] <= out["imps_mid"] <= out["imps_high"]:
        raise StandardEstimateError(f"Method '{spec.key}': the impressions must run low <= mid <= high.")
    if out["est_uniques"] is not None and (not _number(out["est_uniques"]) or out["est_uniques"] < 0):
        raise StandardEstimateError(f"Method '{spec.key}': est_uniques must be null or a number.")
    for k in _LIST_KEYS:
        if not isinstance(out[k], list):
            raise StandardEstimateError(f"Method '{spec.key}': {k} must be a list.")
    if not _all(isinstance(n, str) for n in out["notes"]):
        raise StandardEstimateError(f"Method '{spec.key}': every note must be a string.")
    hispanic = out["hispanic_targeting"]
    if not isinstance(hispanic, dict) or not _all(isinstance(hispanic.get(k), bool) for k in ("default", "on", "applies")):
        raise StandardEstimateError(f"Method '{spec.key}': hispanic_targeting must be {{default, on, applies}} booleans.")
    return out


# ----------------------------------------------------------------------------------------------------------------- the standard status

def validate_status(spec: MethodSpec, status: Any) -> dict:
    """The status answer, checked against the standard shape (F8): `configured` (bool), `missing` (list of strings, the secret's NAME first),
    `account` (None or text), `setup_hint` (text). ValueError naming what is wrong."""
    if not isinstance(status, dict):
        raise ValueError(f"Method '{spec.key}': status() must answer an object.")
    if not isinstance(status.get("configured"), bool):
        raise ValueError(f"Method '{spec.key}': status() needs configured: true or false.")
    missing = status.get("missing")
    if not isinstance(missing, list) or not _all(isinstance(m, str) and m.strip() for m in missing):
        raise ValueError(f"Method '{spec.key}': status().missing must list what is missing, the secret's name first.")
    if status["configured"] and missing:
        raise ValueError(f"Method '{spec.key}': status() is configured but still lists something missing.")
    if "account" not in status or not (status["account"] is None or isinstance(status["account"], str)):
        raise ValueError(f"Method '{spec.key}': status().account must be null or the account's last four digits.")
    if not isinstance(status.get("setup_hint"), str) or not status["setup_hint"].strip():
        raise ValueError(f"Method '{spec.key}': status() needs a setup_hint (what an admin has to do).")
    return status
