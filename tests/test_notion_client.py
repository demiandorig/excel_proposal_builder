"""
Tests for app/services/notion_client.py — the Step 01 "search Notion
requests" integration. Mocks requests.request (no real Notion API access
from this dev environment — see the module's own docstring on why
everything here is confirmed by REASONING about the documented Notion API
shape, not by a live call) and asserts on both the outgoing request shape
and the parsed response.
"""
from __future__ import annotations

import pytest

from app.services import notion_client as nc


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setenv("NOTION_API_TOKEN", "fake-token")
    monkeypatch.setenv("NOTION_REQUESTS_DATABASE_ID", "fake-db-id")


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json = json_body or {}
        self.text = text or ""

    def json(self):
        return self._json


def test_is_configured_requires_both_env_vars(monkeypatch):
    assert nc.is_configured() is True
    monkeypatch.delenv("NOTION_API_TOKEN", raising=False)
    assert nc.is_configured() is False


def test_is_configured_false_when_database_id_missing(monkeypatch):
    monkeypatch.delenv("NOTION_REQUESTS_DATABASE_ID", raising=False)
    assert nc.is_configured() is False


# --- _extract_property_value, every Notion property type -------------------

def test_extract_title():
    prop = {"type": "title", "title": [{"plain_text": "Cambridge Savings Bank"}]}
    assert nc._extract_property_value(prop) == "Cambridge Savings Bank"


def test_extract_title_empty_is_none():
    assert nc._extract_property_value({"type": "title", "title": []}) is None


def test_extract_rich_text_joins_multiple_segments():
    prop = {"type": "rich_text", "rich_text": [{"plain_text": "Hello "}, {"plain_text": "world"}]}
    assert nc._extract_property_value(prop) == "Hello world"


def test_extract_status():
    prop = {"type": "status", "status": {"name": "New"}}
    assert nc._extract_property_value(prop) == "New"


def test_extract_select():
    prop = {"type": "select", "select": {"name": "Renewal"}}
    assert nc._extract_property_value(prop) == "Renewal"


def test_extract_select_unset_is_none():
    assert nc._extract_property_value({"type": "select", "select": None}) is None


def test_extract_multi_select():
    prop = {"type": "multi_select", "multi_select": [{"name": "Avails"}, {"name": "Renewal"}]}
    assert nc._extract_property_value(prop) == ["Avails", "Renewal"]


def test_extract_people_names():
    prop = {"type": "people", "people": [{"name": "Irvin Villa"}, {"name": "Camilo Arias"}]}
    assert nc._extract_property_value(prop) == ["Irvin Villa", "Camilo Arias"]


def test_extract_people_empty_is_none():
    assert nc._extract_property_value({"type": "people", "people": []}) is None


def test_extract_date():
    prop = {"type": "date", "date": {"start": "2026-10-01", "end": "2026-10-31"}}
    assert nc._extract_property_value(prop) == "2026-10-01"


def test_extract_date_unset_is_none():
    assert nc._extract_property_value({"type": "date", "date": None}) is None


def test_extract_number():
    assert nc._extract_property_value({"type": "number", "number": 5300}) == 5300


def test_extract_checkbox():
    assert nc._extract_property_value({"type": "checkbox", "checkbox": True}) is True


def test_extract_unique_id_combines_prefix_and_number():
    prop = {"type": "unique_id", "unique_id": {"prefix": "EVC", "number": 3003}}
    assert nc._extract_property_value(prop) == "EVC-3003"


def test_extract_unique_id_no_prefix_returns_bare_number():
    prop = {"type": "unique_id", "unique_id": {"prefix": None, "number": 3003}}
    assert nc._extract_property_value(prop) == 3003


def test_extract_created_time():
    prop = {"type": "created_time", "created_time": "2026-09-21T10:00:00.000Z"}
    assert nc._extract_property_value(prop) == "2026-09-21T10:00:00.000Z"


def test_extract_unknown_type_returns_none():
    assert nc._extract_property_value({"type": "some_future_type"}) is None


def test_page_to_flat_dict_extracts_every_property():
    page = {
        "properties": {
            "Project Name": {"type": "title", "title": [{"plain_text": "Acme Co"}]},
            "ID": {"type": "unique_id", "unique_id": {"prefix": "EVC", "number": 3003}},
            "Status": {"type": "status", "status": {"name": "New"}},
        }
    }
    result = nc.page_to_flat_dict(page)
    assert result == {"Project Name": "Acme Co", "ID": "EVC-3003", "Status": "New"}


# --- NotionAPIError -------------------------------------------------------

def test_request_raises_notion_api_error_on_non_2xx(monkeypatch):
    def fake_request(method, url, headers=None, json=None, timeout=None):
        return _FakeResponse(status_code=404, json_body={"message": "Could not find database with ID..."})
    monkeypatch.setattr(nc.requests, "request", fake_request)

    with pytest.raises(nc.NotionAPIError) as exc_info:
        nc.get_page("some-page-id")
    assert exc_info.value.status_code == 404
    assert "Could not find database" in str(exc_info.value)


def test_request_raises_notion_api_error_when_not_configured(monkeypatch):
    monkeypatch.delenv("NOTION_API_TOKEN", raising=False)
    with pytest.raises(nc.NotionAPIError) as exc_info:
        nc.query_by_status("New")
    assert exc_info.value.status_code == 503


def test_request_wraps_network_failure(monkeypatch):
    import requests as real_requests

    def fake_request(*a, **kw):
        raise real_requests.ConnectionError("DNS failure")
    monkeypatch.setattr(nc.requests, "request", fake_request)

    with pytest.raises(nc.NotionAPIError) as exc_info:
        nc.get_page("some-page-id")
    assert exc_info.value.status_code == 502


# --- query_by_status --------------------------------------------------------

def test_query_by_status_sends_the_correct_filter_shape(monkeypatch):
    captured = {}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse(json_body={"results": [{"id": "page-1"}]})

    monkeypatch.setattr(nc.requests, "request", fake_request)
    results = nc.query_by_status("New")

    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.notion.com/v1/databases/fake-db-id/query"
    assert captured["json"]["filter"] == {"property": "Status", "status": {"equals": "New"}}
    assert captured["headers"]["Authorization"] == "Bearer fake-token"
    assert captured["headers"]["Notion-Version"] == "2022-06-28"
    assert results == [{"id": "page-1"}]


# --- find_by_request_id -----------------------------------------------------

def test_find_by_request_id_uses_unique_id_filter_with_bare_number(monkeypatch):
    captured = {}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(json_body={"results": [{"id": "page-1"}]})

    monkeypatch.setattr(nc.requests, "request", fake_request)
    result = nc.find_by_request_id(3003)

    assert captured["json"]["filter"] == {"property": "ID", "unique_id": {"equals": 3003}}
    assert result == {"id": "page-1"}


def test_find_by_request_id_returns_none_when_no_match(monkeypatch):
    monkeypatch.setattr(nc.requests, "request", lambda *a, **kw: _FakeResponse(json_body={"results": []}))
    assert nc.find_by_request_id(99999) is None


# --- get_database_schema ----------------------------------------------------

def test_get_database_schema_includes_status_options(monkeypatch):
    def fake_request(method, url, headers=None, json=None, timeout=None):
        return _FakeResponse(json_body={
            "properties": {
                "Status": {"type": "status", "status": {"options": [{"name": "New"}, {"name": "Paused"}]}},
                "Project Name": {"type": "title"},
            }
        })
    monkeypatch.setattr(nc.requests, "request", fake_request)

    schema = nc.get_database_schema()
    assert schema["Status"] == {"type": "status", "options": ["New", "Paused"]}
    assert schema["Project Name"] == {"type": "title", "options": None}


# --- Page body content — _block_to_text / get_block_children / get_page_body_text ---

def _text_block(block_type, text):
    return {"type": block_type, block_type: {"rich_text": [{"plain_text": text}]}}


def test_block_to_text_paragraph():
    assert nc._block_to_text(_text_block("paragraph", "Requested by: Camilo Arias")) == "Requested by: Camilo Arias"


def test_block_to_text_heading():
    assert nc._block_to_text(_text_block("heading_2", "Client Info")) == "Client Info"


def test_block_to_text_bulleted_list_item():
    assert nc._block_to_text(_text_block("bulleted_list_item", "Some detail")) == "Some detail"


def test_block_to_text_divider_becomes_blank_line():
    assert nc._block_to_text({"type": "divider", "divider": {}}) == ""


def test_block_to_text_unsupported_type_returns_none():
    assert nc._block_to_text({"type": "image", "image": {}}) is None


def test_block_to_text_empty_rich_text_is_empty_string_not_none():
    assert nc._block_to_text(_text_block("paragraph", "")) == ""


def test_get_block_children_handles_pagination(monkeypatch):
    calls = []

    def fake_request(method, url, headers=None, json=None, timeout=None):
        calls.append(url)
        if "start_cursor" not in url:
            return _FakeResponse(json_body={"results": [{"id": "b1"}], "has_more": True, "next_cursor": "cursor-2"})
        return _FakeResponse(json_body={"results": [{"id": "b2"}], "has_more": False, "next_cursor": None})

    monkeypatch.setattr(nc.requests, "request", fake_request)
    blocks = nc.get_block_children("page-1")

    assert [b["id"] for b in blocks] == ["b1", "b2"]
    assert len(calls) == 2
    assert "start_cursor=cursor-2" in calls[1]


def test_get_page_body_text_joins_blocks_with_newlines_and_skips_unsupported(monkeypatch):
    def fake_request(method, url, headers=None, json=None, timeout=None):
        return _FakeResponse(json_body={
            "results": [
                _text_block("paragraph", "Requested by: Camilo Arias"),
                {"type": "image", "image": {}},  # skipped entirely, not even a blank line
                _text_block("paragraph", "Client name: Acme Corp"),
            ],
            "has_more": False,
        })
    monkeypatch.setattr(nc.requests, "request", fake_request)

    text = nc.get_page_body_text("page-1")
    assert text == "Requested by: Camilo Arias\nClient name: Acme Corp"


def test_get_page_body_text_round_trips_through_the_real_parser(monkeypatch):
    # The actual point of this feature: fetched body text needs ZERO
    # special-casing to work with the existing, already-battle-tested
    # parser — same as if the planner had pasted it by hand.
    from app.catalog import CATALOG
    from app.services.notion_parser import parse_notion

    def fake_request(method, url, headers=None, json=None, timeout=None):
        return _FakeResponse(json_body={
            "results": [
                _text_block("paragraph", "Requested by: Camilo Arias"),
                _text_block("paragraph", "Client name: Acme Corp"),
                _text_block("paragraph", "Agency Fee: 15%"),
            ],
            "has_more": False,
        })
    monkeypatch.setattr(nc.requests, "request", fake_request)

    text = nc.get_page_body_text("page-1")
    req = parse_notion(text, [p.name for p in CATALOG])
    assert req.requested_by == "Camilo Arias"
    assert req.client_name == "Acme Corp"
    assert req.agency_fee == 0.15
