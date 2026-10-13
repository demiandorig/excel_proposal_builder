"""
"Create in Gamma": the outline is sent to Gamma's public API through the server (which holds the key) and the
planner is taken to the finished deck. Everything here runs against a stubbed `requests` — never the network.
"""
from __future__ import annotations

import asyncio
import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import pytest
import requests
from fastapi import HTTPException

from app.services import gamma_client as gc

OUTLINE = "MEDIA PLAN OUTLINE — Acme\n\nCLIENT & CAMPAIGN\n- Client: Acme\n"


class _Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Req:
    """What the create endpoint reads off the request: who is asking."""
    def __init__(self, email="planner@entravision.com"):
        class _State:
            user = {"email": email}
        self.state = _State()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key in ("GAMMA_API_KEY", "GAMMA_THEME_ID", "GAMMA_FOLDER_ID", "GAMMA_WORKSPACE_ACCESS"):
        monkeypatch.delenv(key, raising=False)
    import app.main as m
    m._gamma_last_start.clear()


def test_without_a_key_nothing_is_called_and_the_page_is_told_so(monkeypatch):
    import app.main as m
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("no key, no request"))
    assert gc.is_configured() is False
    assert asyncio.run(m.gamma_create(m.GammaCreateRequest(outline=OUTLINE), _Req())) == {"configured": False}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.gamma_status("abc123"))
    assert exc.value.status_code == 409


def test_the_request_carries_the_key_the_outline_and_the_safe_defaults(monkeypatch):
    monkeypatch.setenv("GAMMA_API_KEY", "sk-gamma-test")
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, headers=headers, json=json, timeout=timeout)
        return _Resp(200, {"generationId": "gen_ABC123"})
    monkeypatch.setattr(requests, "post", fake_post)
    assert gc.start_generation(OUTLINE, "EVC-1 | Acme") == "gen_ABC123"
    assert sent["url"] == "https://public-api.gamma.app/v1.0/generations"
    assert sent["headers"]["X-API-KEY"] == "sk-gamma-test" and sent["timeout"] == gc._TIMEOUT
    body = sent["json"]
    assert body["inputText"] == OUTLINE and body["textMode"] == "generate" and body["format"] == "presentation"
    assert body["title"] == "EVC-1 | Acme" and body["sharingOptions"] == {"workspaceAccess": "edit"}
    assert "exactly as written" in body["additionalInstructions"] and len(body["additionalInstructions"]) < 5000
    assert "themeId" not in body and "folderId" not in body


def test_optional_theme_folder_and_sharing_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("GAMMA_API_KEY", "k")
    monkeypatch.setenv("GAMMA_THEME_ID", "theme_1")
    monkeypatch.setenv("GAMMA_FOLDER_ID", "folder_9")
    monkeypatch.setenv("GAMMA_WORKSPACE_ACCESS", "comment")
    captured = {}
    monkeypatch.setattr(requests, "post", lambda url, headers=None, json=None, timeout=None: captured.update(json=json) or _Resp(200, {"generationId": "gen12345"}))
    gc.start_generation(OUTLINE)
    assert captured["json"]["themeId"] == "theme_1" and captured["json"]["folderId"] == "folder_9"
    assert captured["json"]["sharingOptions"] == {"workspaceAccess": "comment"} and "title" not in captured["json"]
    monkeypatch.setenv("GAMMA_WORKSPACE_ACCESS", "FullAccess")
    gc.start_generation(OUTLINE)
    assert captured["json"]["sharingOptions"] == {"workspaceAccess": "fullAccess"}    # any capitalisation
    monkeypatch.setenv("GAMMA_WORKSPACE_ACCESS", "everyone-forever")
    gc.start_generation(OUTLINE)
    assert captured["json"]["sharingOptions"] == {"workspaceAccess": "view"}          # a typo never grants MORE access


@pytest.mark.parametrize("status,expected", [
    (401, "rejected the API key"), (403, "rejected the API key"), (402, "out of credits"), (429, "rate-limiting"), (500, "returned 500"),
])
def test_gamma_errors_become_messages_a_planner_can_act_on(monkeypatch, status, expected):
    monkeypatch.setenv("GAMMA_API_KEY", "k")
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(status, {"message": "details here"}))
    with pytest.raises(gc.GammaError, match=expected) as exc:
        gc.start_generation(OUTLINE)
    assert exc.value.status_code == (429 if status == 429 else 502)


def test_a_network_failure_or_a_missing_id_is_reported_not_raised_raw(monkeypatch):
    monkeypatch.setenv("GAMMA_API_KEY", "k")

    def down(*a, **k):
        raise requests.ConnectionError("no route")
    monkeypatch.setattr(requests, "post", down)
    with pytest.raises(gc.GammaError, match="Couldn't reach Gamma"):
        gc.start_generation(OUTLINE)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200, {"unexpected": True}))
    with pytest.raises(gc.GammaError, match="generation id"):
        gc.start_generation(OUTLINE)


def test_polling_reports_pending_completed_and_failed(monkeypatch):
    monkeypatch.setenv("GAMMA_API_KEY", "k")
    replies = iter([
        _Resp(200, {"status": "pending"}),
        _Resp(200, {"status": "completed", "gammaUrl": "https://gamma.app/docs/Acme-abc123", "credits": {"deducted": 12}}),
        _Resp(200, {"status": "failed", "error": {"message": "Out of credits"}}),
        _Resp(200, {"status": "somethingnew"}),
    ])
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, headers=None, timeout=None: calls.append(url) or next(replies))
    assert gc.get_generation("gen_ABC123") == {"status": "pending", "gamma_url": None, "error": None}
    done = gc.get_generation("gen_ABC123")
    assert done["status"] == "completed" and done["gamma_url"] == "https://gamma.app/docs/Acme-abc123"
    failed = gc.get_generation("gen_ABC123")
    assert failed["status"] == "failed" and failed["error"] == "Out of credits" and failed["gamma_url"] is None
    assert gc.get_generation("gen_ABC123")["status"] == "pending"                   # an unknown status keeps polling
    assert calls[0] == "https://public-api.gamma.app/v1.0/generations/gen_ABC123"


@pytest.mark.parametrize("url", ["http://gamma.app/docs/x", "https://evil.example/docs/x", "https://gamma.app.evil.example/x",
                                 "javascript:alert(1)", "", None, "https://gamma.app\\@evil.example/x", "https://gamma.app@evil.example/x",
                                 "https://evil.example\\.gamma.app/x", "https://gamma.app:8443/x", "https://gamma.app/x y", 7])
def test_only_a_real_gamma_page_is_ever_handed_to_the_browser(monkeypatch, url):
    monkeypatch.setenv("GAMMA_API_KEY", "k")
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(200, {"status": "completed", "gammaUrl": url}))
    done = gc.get_generation("gen_ABC123")
    assert done["gamma_url"] is None
    # a finished deck with no usable link stops the polling with a pointer to the workspace instead of spinning forever
    assert done["status"] == "failed" and "Gamma workspace" in done["error"]


@pytest.mark.parametrize("body", [[], "ok", None, 5])
def test_a_reply_that_is_not_a_json_object_is_reported_not_crashed_on(monkeypatch, body):
    monkeypatch.setenv("GAMMA_API_KEY", "k")
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200, body))
    with pytest.raises(gc.GammaError):
        gc.start_generation(OUTLINE)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(200, body))
    with pytest.raises(gc.GammaError):
        gc.get_generation("gen_ABC123")


def test_a_workspace_subdomain_is_accepted(monkeypatch):
    monkeypatch.setenv("GAMMA_API_KEY", "k")
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(200, {"status": "completed", "gammaUrl": "https://acme.gamma.app/docs/x"}))
    assert gc.get_generation("gen_ABC123")["gamma_url"] == "https://acme.gamma.app/docs/x"


@pytest.mark.parametrize("bad", ["", "../etc/passwd", "a/b", "x" * 200, "ab", "gen id"])
def test_a_generation_id_is_validated_before_it_goes_into_a_url(monkeypatch, bad):
    monkeypatch.setenv("GAMMA_API_KEY", "k")
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("must not call Gamma with a bad id"))
    with pytest.raises(gc.GammaError) as exc:
        gc.get_generation(bad)
    assert exc.value.status_code == 400


def test_the_endpoints_hand_back_the_id_and_map_errors(monkeypatch):
    import app.main as m
    monkeypatch.setenv("GAMMA_API_KEY", "k")
    monkeypatch.setattr(gc, "start_generation", lambda outline, title=None: "gen_ABC123")
    out = asyncio.run(m.gamma_create(m.GammaCreateRequest(outline=OUTLINE, title="T"), _Req()))
    assert out == {"configured": True, "generation_id": "gen_ABC123"}

    def refused(outline, title=None):
        raise gc.GammaError("Gamma is rate-limiting requests — wait a minute and try again.", 429)
    monkeypatch.setattr(gc, "start_generation", refused)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.gamma_create(m.GammaCreateRequest(outline=OUTLINE), _Req("someone.else@entravision.com")))
    assert exc.value.status_code == 429 and "rate-limiting" in exc.value.detail


def test_one_user_cannot_start_decks_back_to_back_but_a_failed_start_costs_no_wait(monkeypatch, caplog):
    import logging
    import app.main as m
    monkeypatch.setenv("GAMMA_API_KEY", "k")
    started = []
    monkeypatch.setattr(gc, "start_generation", lambda outline, title=None: started.append(1) or "gen_ABC123")
    body = m.GammaCreateRequest(outline=OUTLINE)
    with caplog.at_level(logging.INFO):
        asyncio.run(m.gamma_create(body, _Req("a@entravision.com")))
    assert "gen_ABC123" in caplog.text and "a@entravision.com" in caplog.text            # who started which build
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.gamma_create(body, _Req("a@entravision.com")))
    assert exc.value.status_code == 429 and started == [1]
    asyncio.run(m.gamma_create(body, _Req("b@entravision.com")))                       # another planner is unaffected
    assert started == [1, 1]

    def broken(outline, title=None):
        raise gc.GammaError("Gamma rejected the API key", 502)
    monkeypatch.setattr(gc, "start_generation", broken)
    with pytest.raises(HTTPException):
        asyncio.run(m.gamma_create(body, _Req("c@entravision.com")))
    monkeypatch.setattr(gc, "start_generation", lambda outline, title=None: "gen_ABC123")
    asyncio.run(m.gamma_create(body, _Req("c@entravision.com")))                       # retry right away is fine


def test_the_outline_size_limits_match_gammas(monkeypatch):
    import app.main as m
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        m.GammaCreateRequest(outline="short")
    with pytest.raises(ValidationError):
        m.GammaCreateRequest(outline="x" * 400_001)


def test_the_example_env_documents_the_new_variables_without_a_value():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "GAMMA_API_KEY=\n" in text and "GAMMA_WORKSPACE_ACCESS=edit" in text
