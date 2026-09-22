"""
Tests for the /api/notion/* route handlers in app/main.py — mocks
app.main.notion_client's own functions directly (not requests.request;
that layer is covered by test_notion_client.py) so these focus purely on
the route's own request validation / error translation / response shape.
"""
from __future__ import annotations

import asyncio
import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import pytest
from fastapi import HTTPException

import app.main as m
from app.services import notion_client as nc


def test_search_returns_not_configured_without_raising(monkeypatch):
    monkeypatch.setattr(m.notion_client, "is_configured", lambda: False)
    result = asyncio.run(m.notion_search(status="New"))
    assert result == {"configured": False, "results": []}


def test_search_rejects_a_status_outside_the_fixed_list(monkeypatch):
    monkeypatch.setattr(m.notion_client, "is_configured", lambda: True)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(m.notion_search(status="Done"))
    assert exc_info.value.status_code == 400


def test_search_returns_flattened_results_with_page_id(monkeypatch):
    monkeypatch.setattr(m.notion_client, "is_configured", lambda: True)
    monkeypatch.setattr(m.notion_client, "query_by_status", lambda status: [{"id": "uuid-1"}, {"id": "uuid-2"}])
    monkeypatch.setattr(m.notion_client, "page_to_flat_dict", lambda p: {"Project Name": f"Client for {p['id']}"})

    result = asyncio.run(m.notion_search(status="New"))

    assert result["configured"] is True
    assert result["results"] == [
        {"page_id": "uuid-1", "Project Name": "Client for uuid-1"},
        {"page_id": "uuid-2", "Project Name": "Client for uuid-2"},
    ]


def test_search_translates_notion_api_error_to_http_exception(monkeypatch):
    monkeypatch.setattr(m.notion_client, "is_configured", lambda: True)
    def raise_error(status):
        raise nc.NotionAPIError(404, "database not found")
    monkeypatch.setattr(m.notion_client, "query_by_status", raise_error)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(m.notion_search(status="New"))
    assert exc_info.value.status_code == 404
    assert "database not found" in exc_info.value.detail


def test_get_page_returns_503_when_not_configured(monkeypatch):
    monkeypatch.setattr(m.notion_client, "is_configured", lambda: False)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(m.notion_get_page("uuid-1"))
    assert exc_info.value.status_code == 503


def test_get_page_returns_flat_properties_and_body_text(monkeypatch):
    monkeypatch.setattr(m.notion_client, "is_configured", lambda: True)
    monkeypatch.setattr(m.notion_client, "get_page", lambda page_id: {"id": page_id})
    monkeypatch.setattr(m.notion_client, "page_to_flat_dict", lambda p: {"Project Name": "Acme"})
    monkeypatch.setattr(m.notion_client, "get_page_body_text", lambda page_id: "Requested by: Camilo Arias")

    result = asyncio.run(m.notion_get_page("uuid-1"))
    assert result == {
        "page_id": "uuid-1",
        "properties": {"Project Name": "Acme"},
        "body_text": "Requested by: Camilo Arias",
    }


def test_admin_notion_schema_propagates_notion_api_error(monkeypatch):
    def raise_error():
        raise nc.NotionAPIError(401, "invalid token")
    monkeypatch.setattr(m.notion_client, "get_database_schema", raise_error)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(m.admin_notion_schema())
    assert exc_info.value.status_code == 401


def test_admin_notion_schema_returns_wrapped_properties(monkeypatch):
    monkeypatch.setattr(m.notion_client, "get_database_schema", lambda: {"Status": {"type": "status", "options": ["New"]}})
    result = asyncio.run(m.admin_notion_schema())
    assert result == {"properties": {"Status": {"type": "status", "options": ["New"]}}}
