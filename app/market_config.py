"""
Per-market admin config: the Entravision office address that appears on
every proposal export's meta block, and the CC list for the seller email
mailto link (Step 07). Both used to be one hardcoded value (Burbank HQ,
no CCs) regardless of which market the request came from — this makes
both editable per market from the admin console, with a "__default__"
entry every market falls back to until someone gives it its own values.

The configuration is stored in PostgreSQL and read fresh on every call so an
admin edit takes effect immediately with no restart.
"""
from __future__ import annotations

from typing import Optional

from app.db import get_connection

DEFAULT_KEY = "__default__"
# Always-CC'd addresses (every proposal, every market — the seller-email
# mailto's equivalent of the old Notion formula's hardcoded leading
# "salesplanning@entravision.com;"). Stored as a bare list, not a
# per-market {address, ccs} dict, so it's excluded from the reserved-key
# checks below wherever a "real market" dict shape is assumed.
BASE_CCS_KEY = "__base_ccs__"
# T1 escalation CCs — added on top of the base+market CCs whenever ANY
# tier of the proposal spends $10k/mo or more (see T1_SPEND_THRESHOLD).
# Below that on every tier, these are left out entirely and only the
# regular market assignees are CC'd.
T1_CCS_KEY = "__t1_ccs__"
_RESERVED_KEYS = {DEFAULT_KEY, BASE_CCS_KEY, T1_CCS_KEY}

T1_SPEND_THRESHOLD = 10000.0

_FALLBACK_DEFAULT = {
    "address_line1": "1 Estrella Way",
    "address_line2": "Burbank, CA 91504",
    "dsc_email": None,
    "dsm_email": None,
    "ccs": [],
}
_FALLBACK_BASE_CCS = ["salesplanning@entravision.com"]
_FALLBACK_T1_CCS = ["jwoods@entravision.com"]


def load_market_config() -> dict:
    """{market_name_or_"__default__": {address_line1, address_line2,
    dsc_email, dsm_email, ccs}}, plus a "__base_ccs__": [...] entry. Always
    includes both — seeded with the Burbank HQ address / no per-market CCs
    / just salesplanning@ as the base CC the first time this is ever read,
    so everything has a sane fallback even before an admin configures
    anything."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT market_key, address_line1, address_line2, dsc_email, dsm_email, ccs FROM market_config ORDER BY market_key"
        ).fetchall()
    data = {}
    for row in rows:
        key = row["market_key"]
        if key in (BASE_CCS_KEY, T1_CCS_KEY):
            data[key] = list(row["ccs"] or [])
        else:
            data[key] = {
                "address_line1": row["address_line1"],
                "address_line2": row["address_line2"],
                "dsc_email": row["dsc_email"],
                "dsm_email": row["dsm_email"],
                "ccs": list(row["ccs"] or []),
            }
    if DEFAULT_KEY not in data:
        data[DEFAULT_KEY] = dict(_FALLBACK_DEFAULT)
    if BASE_CCS_KEY not in data:
        data[BASE_CCS_KEY] = list(_FALLBACK_BASE_CCS)
    if T1_CCS_KEY not in data:
        data[T1_CCS_KEY] = list(_FALLBACK_T1_CCS)
    return data


def save_market_config(config: dict) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM market_config")
        for market_key, value in config.items():
            if market_key in (BASE_CCS_KEY, T1_CCS_KEY):
                address_line1 = None
                address_line2 = None
                dsc_email = None
                dsm_email = None
                ccs = list(value or [])
            else:
                entry = dict(value or {})
                address_line1 = entry.get("address_line1")
                address_line2 = entry.get("address_line2")
                dsc_email = entry.get("dsc_email")
                dsm_email = entry.get("dsm_email")
                ccs = list(entry.get("ccs") or [])
            conn.execute(
                """
                INSERT INTO market_config
                    (market_key, address_line1, address_line2, dsc_email, dsm_email, ccs)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (market_key, address_line1, address_line2, dsc_email, dsm_email, ccs),
            )


def set_market_entry(market_key: str, fields: dict) -> dict:
    """Upsert one market's config (or "__default__"). `fields` may contain
    any of address_line1/address_line2/dsc_email/dsm_email/ccs — only given
    keys are touched, the rest of that market's existing entry (if any) is
    preserved."""
    config = load_market_config()
    entry = dict(config.get(market_key, {}))
    for k in ("address_line1", "address_line2", "dsc_email", "dsm_email", "ccs"):
        if k in fields:
            entry[k] = fields[k]
    config[market_key] = entry
    save_market_config(config)
    return entry


def delete_market_entry(market_key: str) -> bool:
    """Remove a market's own override, reverting it to "__default__".
    Can't delete "__default__" itself."""
    if market_key == DEFAULT_KEY:
        return False
    config = load_market_config()
    if market_key not in config:
        return False
    del config[market_key]
    save_market_config(config)
    return True


def _market_lookup_key(market: Optional[str], config: dict) -> str:
    """The parsed `salesperson_market` field carries free text like "Los
    Angeles (Tier 1)" — match against a configured market name as a
    case-insensitive prefix/substring rather than requiring an exact
    string match, so "Los Angeles" configured once in the admin still
    matches "Los Angeles (Tier 1)", "Los Angeles (Tier 2)", etc."""
    if not market:
        return DEFAULT_KEY
    market_lower = market.strip().lower()
    for key in config:
        if key in _RESERVED_KEYS:
            continue
        key_lower = key.strip().lower()
        if key_lower and (key_lower in market_lower or market_lower in key_lower):
            return key
    return DEFAULT_KEY


def get_market_address(market: Optional[str]) -> tuple[str, str]:
    """(address_line1, address_line2) for this market, falling back to the
    default entry for any field the matched market doesn't itself set."""
    config = load_market_config()
    key = _market_lookup_key(market, config)
    entry = config.get(key, {})
    default = config.get(DEFAULT_KEY, _FALLBACK_DEFAULT)
    return (
        entry.get("address_line1") or default.get("address_line1") or _FALLBACK_DEFAULT["address_line1"],
        entry.get("address_line2") or default.get("address_line2") or _FALLBACK_DEFAULT["address_line2"],
    )


def get_market_ccs(market: Optional[str]) -> list[str]:
    """CC email list for this market, falling back to the default entry's
    list (not merged — a market with its own explicit ccs list, even an
    empty one, means exactly that list; only a market with NO ccs key at
    all falls back)."""
    config = load_market_config()
    key = _market_lookup_key(market, config)
    entry = config.get(key, {})
    if "ccs" in entry:
        return entry["ccs"]
    return config.get(DEFAULT_KEY, _FALLBACK_DEFAULT).get("ccs", [])


def get_base_ccs() -> list[str]:
    """Addresses CC'd on every seller-email mailto regardless of market —
    e.g. a shared salesplanning@ inbox. Admin-editable; starts out as just
    that one address."""
    config = load_market_config()
    return config.get(BASE_CCS_KEY, _FALLBACK_BASE_CCS)


def set_base_ccs(ccs: list[str]) -> list[str]:
    config = load_market_config()
    config[BASE_CCS_KEY] = list(ccs)
    save_market_config(config)
    return config[BASE_CCS_KEY]


def get_t1_ccs() -> list[str]:
    """Escalation CCs added when any tier of a proposal spends $10k/mo or
    more — see T1_SPEND_THRESHOLD. Admin-editable; starts out as just the
    one address."""
    config = load_market_config()
    return config.get(T1_CCS_KEY, _FALLBACK_T1_CCS)


def set_t1_ccs(ccs: list[str]) -> list[str]:
    config = load_market_config()
    config[T1_CCS_KEY] = list(ccs)
    save_market_config(config)
    return config[T1_CCS_KEY]


def get_market_dsc_dsm(market: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(dsc_email, dsm_email) for this market — the Digital Sales
    Coordinator (assistant) and Digital Sales Manager, CC'd on the internal
    seller email alongside the flat ccs list. Unlike get_market_ccs, these
    always fall back to the default entry's own dsc/dsm (if any) when the
    matched market doesn't set its own — there's no "explicit empty list"
    concept for a single email field the way there is for ccs."""
    config = load_market_config()
    key = _market_lookup_key(market, config)
    entry = config.get(key, {})
    default = config.get(DEFAULT_KEY, _FALLBACK_DEFAULT)
    return (
        entry.get("dsc_email") or default.get("dsc_email"),
        entry.get("dsm_email") or default.get("dsm_email"),
    )


def get_all_ccs_for_market(market: Optional[str]) -> list[str]:
    """Base CCs + this market's own CCs + this market's DSC/DSM (when set),
    deduped (case-insensitive) while preserving first-seen order — what the
    seller-email mailto link actually uses. Product/renewal-based CCs are
    layered on top of this client-side (see app.js) since they depend on
    the specific proposal's products/title, not just its market."""
    dsc_email, dsm_email = get_market_dsc_dsm(market)
    seen = set()
    combined = []
    for email in get_base_ccs() + get_market_ccs(market) + [dsc_email, dsm_email]:
        e = (email or "").strip()
        if e and e.lower() not in seen:
            seen.add(e.lower())
            combined.append(e)
    return combined
