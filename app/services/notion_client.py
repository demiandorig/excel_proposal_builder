"""
Thin, read-only wrapper around the Notion REST API — lets a planner search
Step 01's Requests database directly instead of copy-pasting from Notion by
hand. Auth is a Personal Access Token (not a durable workspace/internal
integration — the plan this was set up under doesn't expose that option),
which means it's tied to whatever pages/databases its owner has manually
connected it to in Notion's own UI ("···" menu on the database → Connections)
— if a query 401s/404s where you'd expect data, that's the first thing to
check, not a bug in this module.

DESIGN: rather than hand-mapping Notion's ~20 properties to
ProposalRequest's own fields (a second parser to keep in sync with
notion_parser.py forever), a fetched page's properties are reformatted
into the SAME plain-text "Label: value" shape the paste box already
accepts (see page_to_paste_text below) and run through the existing,
already-battle-tested notion_parser.parse_notion(). One parser, one
source of truth, regardless of whether the text arrived by paste or by API.

Every property name this module references (STATUS_PROPERTY, ID_PROPERTY,
_LABEL_MAP below) was confirmed against a real schema dump from
get_database_schema() before being hardcoded — see that function's own
docstring. Nothing here should be extended to a new property by guessing.
"""
from __future__ import annotations

import os
from typing import Optional

import requests

_NOTION_VERSION = "2022-06-28"  # a stable, dated API version — bump deliberately, never implicitly
_API_BASE = "https://api.notion.com/v1"
_TIMEOUT = 15  # seconds — a planner-facing search, never worth hanging the request indefinitely


def _token() -> str:
    return os.environ.get("NOTION_API_TOKEN", "")


def _database_id() -> str:
    return os.environ.get("NOTION_REQUESTS_DATABASE_ID", "")


def is_configured() -> bool:
    """Same 'app runs fine without it' convention as Drive/OpenAI — every
    caller checks this first and degrades gracefully (a disabled search
    button, not a 500) rather than assuming the token/database are set."""
    return bool(_token() and _database_id())


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }


class NotionAPIError(Exception):
    """Wraps a non-2xx Notion response with its actual status/body — never
    let a raw requests exception (or a bare KeyError from an unexpected
    shape) surface to the caller; this is the one place that translates
    "Notion said no" into something a route handler can turn into a clean
    4xx for the frontend instead of a stack trace."""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


def _request(method: str, path: str, json_body: Optional[dict] = None) -> dict:
    if not is_configured():
        raise NotionAPIError(503, "Notion integration isn't configured (NOTION_API_TOKEN / NOTION_REQUESTS_DATABASE_ID missing).")
    try:
        resp = requests.request(
            method, f"{_API_BASE}{path}", headers=_headers(), json=json_body, timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        raise NotionAPIError(502, f"Could not reach Notion: {e}")
    if not resp.ok:
        # Notion's own error body has a "message" field — surface that
        # rather than a bare status code, it's usually specific enough to
        # actually act on (e.g. "Could not find database with ID... make
        # sure the relevant pages/databases are shared with your integration").
        try:
            detail = resp.json().get("message", resp.text)
        except ValueError:
            detail = resp.text
        raise NotionAPIError(resp.status_code, detail)
    return resp.json()


def get_database_schema() -> dict:
    """
    Admin-only introspection helper (see GET /api/admin/notion/schema) —
    fetches the database's full property schema (names + types + the
    fixed option list for select/status properties) so the rest of this
    module's hardcoded property names can be confirmed against reality
    instead of guessed from a screenshot. Run this ONCE after configuring
    the token/database ID; nothing else in the app depends on calling it
    again — the real query functions below use fixed, human-reviewed
    property names, not a live re-discovery on every search.
    """
    data = _request("GET", f"/databases/{_database_id()}")
    return {
        name: {
            "type": prop.get("type"),
            "options": [o["name"] for o in prop.get(prop.get("type"), {}).get("options", [])] if prop.get("type") in ("select", "status", "multi_select") else None,
        }
        for name, prop in (data.get("properties") or {}).items()
    }


def _plain_text(rich_text_list: list) -> str:
    return "".join(t.get("plain_text", "") for t in (rich_text_list or []))


def _extract_property_value(prop: dict):
    """Generic best-effort extraction across every Notion property TYPE —
    used for rendering search results generically (Project Name/Owner/Due
    Date/etc., whatever a database actually has) without this module
    needing a hardcoded case for every property some other Requests
    database column might use. Returns None for an empty/unset property
    rather than an empty string, so a caller can tell "blank" apart from
    "there was really nothing here" if it matters."""
    t = prop.get("type")
    if t == "title":
        return _plain_text(prop.get("title")) or None
    if t == "rich_text":
        return _plain_text(prop.get("rich_text")) or None
    if t == "status":
        return (prop.get("status") or {}).get("name")
    if t == "select":
        return (prop.get("select") or {}).get("name")
    if t == "multi_select":
        return [o["name"] for o in (prop.get("multi_select") or [])] or None
    if t == "people":
        return [p.get("name") for p in (prop.get("people") or []) if p.get("name")] or None
    if t == "date":
        d = prop.get("date")
        return d.get("start") if d else None
    if t == "number":
        return prop.get("number")
    if t == "checkbox":
        return prop.get("checkbox")
    if t == "url":
        return prop.get("url")
    if t == "email":
        return prop.get("email")
    if t == "phone_number":
        return prop.get("phone_number")
    if t == "unique_id":
        uid = prop.get("unique_id") or {}
        prefix = uid.get("prefix") or ""
        number = uid.get("number")
        return f"{prefix}-{number}" if prefix and number is not None else number
    if t == "formula":
        return _extract_property_value({"type": (prop.get("formula") or {}).get("type"), **(prop.get("formula") or {})})
    if t == "created_time":
        return prop.get("created_time")
    if t == "last_edited_time":
        return prop.get("last_edited_time")
    return None


def page_to_flat_dict(page: dict) -> dict:
    """Every property on a page, generically extracted — {property_name:
    value}. Used both for the search-result list (a small subset of
    fields) and as the input to page_to_paste_text below."""
    props = page.get("properties") or {}
    return {name: _extract_property_value(prop) for name, prop in props.items()}


def query_by_status(status_value: str, status_property: str = "Status", page_size: int = 25) -> list[dict]:
    """
    Every page in the Requests database currently at `status_value` (one
    of the app's Step 01 search filters — "New"/"Paused"/"Progress" today,
    but this takes whatever string is passed, not a hardcoded enum, since
    the planner-facing status list is config on the Notion side, not this
    module's business).

    status_property defaults to "Status" — confirmed as this database's
    real property name (and a `status`-TYPE property, not `select` — the
    grouped/colored-pill UI in Notion's own editor is that type's visual
    signature) via get_database_schema() before being hardcoded as a default.
    """
    data = _request(
        "POST", f"/databases/{_database_id()}/query",
        {
            "filter": {"property": status_property, "status": {"equals": status_value}},
            "page_size": min(page_size, 100),
            "sorts": [{"timestamp": "created_time", "direction": "descending"}],
        },
    )
    return data.get("results", [])


def get_page(page_id: str) -> dict:
    """A single page by its Notion page ID (the UUID, NOT the human-facing
    "EVC-1234" unique_id shown in the app — see find_by_request_id below
    for looking a page up BY that human-facing ID instead)."""
    return _request("GET", f"/pages/{page_id}")


def find_by_request_id(id_number: int, id_property: str = "ID") -> Optional[dict]:
    """
    Look up the one page whose "ID" unique_id property equals `id_number`
    — e.g. find_by_request_id(3003) for a request shown as "EVC-3003".
    Notion's unique_id filter takes the bare NUMBER, not the "EVC-"
    prefix — confirm id_property really IS a unique_id-type property via
    get_database_schema() before relying on this (see this module's own
    header comment); a differently-typed "ID" column needs a different
    filter shape, and this function does NOT try to guess between them.

    Returns None (not an error) when nothing matches — an unrecognized ID
    is a normal, expected search-comes-back-empty outcome, not a fault.
    """
    data = _request(
        "POST", f"/databases/{_database_id()}/query",
        {"filter": {"property": id_property, "unique_id": {"equals": id_number}}, "page_size": 1},
    )
    results = data.get("results", [])
    return results[0] if results else None


# ---------------------------------------------------------------------------
# Page BODY content — the planner-confirmed win: the Requests database's
# own pages contain, in their body, the exact same "Label: value" text a
# planner would otherwise copy-paste into Step 01 by hand. Fetching this
# directly means notion_parser.parse_notion() can run on it completely
# unmodified — no property-to-paste-text field mapping needed at all,
# unlike the properties-based approach this module started with.
# ---------------------------------------------------------------------------

# Block types whose text this app's Requests-page bodies are expected to
# actually use — a plain intake form/template page, not a richly nested
# document. Deliberately NOT recursing into children (toggle contents,
# nested list items) — each recursion is its own Notion API round trip,
# and a flat page (the normal case here) needs none of that. A page that
# genuinely nests its content this deeply is a real gap this doesn't
# cover; extend here if that turns out to matter in practice.
_TEXT_BLOCK_TYPES = {
    "paragraph", "heading_1", "heading_2", "heading_3",
    "bulleted_list_item", "numbered_list_item", "to_do", "quote", "callout", "code",
}


def _block_to_text(block: dict) -> Optional[str]:
    t = block.get("type")
    if t == "divider":
        return ""  # a blank line, preserving the paste text's own blank-line separators
    if t not in _TEXT_BLOCK_TYPES:
        return None  # images, embeds, tables, etc. — nothing paste-text needs
    rich_text = (block.get(t) or {}).get("rich_text")
    return _plain_text(rich_text)


def get_block_children(block_id: str, page_size: int = 100) -> list[dict]:
    """Every direct child block of `block_id` (a page IS a block — pass a
    page's own ID to get that page's top-level content), across as many
    paginated requests as the page actually needs. One level only — see
    this section's own header comment on why this doesn't recurse."""
    blocks: list[dict] = []
    cursor: Optional[str] = None
    while True:
        path = f"/blocks/{block_id}/children?page_size={min(page_size, 100)}"
        if cursor:
            path += f"&start_cursor={cursor}"
        data = _request("GET", path)
        blocks.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return blocks


def get_page_body_text(page_id: str) -> str:
    """
    The page's body content, reconstructed as plain text — one line per
    block, in the SAME reading order they appear in Notion, blank lines
    preserved. This is what feeds Step 01's paste textarea directly (see
    GET /api/notion/page/{page_id}'s own "body_text" field) — if the
    Requests database's own page bodies really do mirror the paste
    format 1:1 (confirmed by the planner, not assumed), this needs zero
    further parsing: it IS a paste, just fetched instead of typed.
    """
    blocks = get_block_children(page_id)
    lines = [_block_to_text(b) for b in blocks]
    return "\n".join(line for line in lines if line is not None)
