"""Shared OpenAI call/parse layer (app/services/llm_utils.py + text_utils.extract_json_object)."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services import llm_utils
from app.services import strategy_brief as sb
from app.services.notion_parser import ProposalRequest
from app.services.text_utils import extract_json_object

BRIEF_KEYS = ("client_summary", "recommended_tactics")


def _brief_json(n_tactics: int = 5) -> str:
    return json.dumps({
        "client_summary": "Client does X.",
        "market_context": "Market.",
        "objectives_analysis": "Objectives.",
        "strategy_summary": "Plan.",
        "recommended_tactics": [
            {"product_family": "Audio", "rationale": f"r{i}", "data_point": "d", "citation": "c",
             "entravision_advantage": "e", "suggested_budget_pct": 20}
            for i in range(n_tactics)
        ],
        "key_insights": ["one", "two", "three"],
    }, indent=2)


# --- extract_json_object ---------------------------------------------------

def test_extract_tolerates_preamble_trailing_text_and_fences():
    raw = "Here's the brief:\n```json\n" + _brief_json(1) + "\n```\nLet me know if {anything} changes."
    assert extract_json_object(raw, expect_any=BRIEF_KEYS)["client_summary"] == "Client does X."


def test_extract_allows_raw_newlines_inside_strings():
    raw = '{"client_summary": "line one\nline two", "recommended_tactics": []}'
    assert extract_json_object(raw, expect_any=BRIEF_KEYS)["client_summary"] == "line one\nline two"


def test_truncated_outer_object_never_falls_through_to_a_nested_tactic():
    full = _brief_json(5)
    truncated = full[: full.index('"key_insights"') + 30]  # the reported failure: cut inside key_insights
    with pytest.raises(ValueError, match="JSON parse error"):
        extract_json_object(truncated, expect_any=BRIEF_KEYS)


def test_extract_reports_missing_json_and_empty_input():
    with pytest.raises(ValueError, match="No structured response"):
        extract_json_object("no json here", expect_any=BRIEF_KEYS)
    with pytest.raises(ValueError, match="Empty response"):
        extract_json_object("   ", expect_any=BRIEF_KEYS)


# --- responses_text / chat_text -------------------------------------------

def _response(text: str, status: str = "completed", reason: str | None = None):
    return SimpleNamespace(output_text=text, status=status,
                           incomplete_details=SimpleNamespace(reason=reason) if reason else None,
                           usage=None)


class _FakeResponses:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def test_responses_text_retries_once_at_double_cap_when_truncated():
    fake = _FakeResponses([_response("{partial", "incomplete", "max_output_tokens"), _response("ok")])
    client = SimpleNamespace(responses=fake)
    assert llm_utils.responses_text(client, model="m", prompt="p", max_output_tokens=1000) == "ok"
    assert [c["max_output_tokens"] for c in fake.calls] == [1000, 2000]


def test_responses_text_raises_when_still_truncated_after_retry():
    fake = _FakeResponses([_response("{a", "incomplete", "max_output_tokens"),
                           _response("{ab", "incomplete", "max_output_tokens")])
    with pytest.raises(llm_utils.ResponseTruncated):
        llm_utils.responses_text(SimpleNamespace(responses=fake), model="m", prompt="p", max_output_tokens=10)


def test_responses_text_raises_on_other_incomplete_reasons():
    fake = _FakeResponses([_response("", "incomplete", "content_filter")])
    with pytest.raises(RuntimeError, match="content_filter"):
        llm_utils.responses_text(SimpleNamespace(responses=fake), model="m", prompt="p", max_output_tokens=10)


def _chat_client(*contents_and_reasons):
    queue = list(contents_and_reasons)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        content, reason = queue.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                                        finish_reason=reason)])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def test_chat_text_raises_on_length_finish_reason():
    client, calls = _chat_client(("{cut", "length"))
    with pytest.raises(llm_utils.ResponseTruncated):
        llm_utils.chat_text(client, model="m", prompt="p", max_completion_tokens=5)
    assert "max_tokens" not in calls[0] and calls[0]["max_completion_tokens"] == 5


def test_parse_json_object_repairs_a_complete_but_invalid_object():
    broken = '{"client_summary": "He said "hi" there", "recommended_tactics": []}'
    client, calls = _chat_client(('{"client_summary": "He said \\"hi\\" there", "recommended_tactics": []}', "stop"))
    data = llm_utils.parse_json_object(broken, expect_any=BRIEF_KEYS, client=client)
    assert data["client_summary"] == 'He said "hi" there'
    assert calls[0]["model"] == llm_utils.REPAIR_MODEL


def test_parse_json_object_raises_original_error_when_repair_fails():
    client, _ = _chat_client(("still not json", "stop"))
    with pytest.raises(ValueError, match="JSON parse error"):
        llm_utils.parse_json_object('{"client_summary": "x" "y"}', expect_any=BRIEF_KEYS, client=client)


# --- strategy brief end to end (fake OpenAI) --------------------------------

class _FakeOpenAI:
    def __init__(self, responses):
        self.responses = _FakeResponses(responses)


def _request():
    return ProposalRequest(client_name="Acme", monthly_budget=5000, total_months=3)


def test_brief_truncated_twice_returns_clear_regenerate_message(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    full = _brief_json(5)
    cut = full[: full.index('"key_insights"') + 30]
    fake = _FakeOpenAI([_response(cut, "incomplete", "max_output_tokens"),
                        _response(cut, "incomplete", "max_output_tokens")])
    monkeypatch.setattr(sb, "_OpenAI", lambda api_key: fake)
    brief = asyncio.run(sb.generate_brief(_request()))
    assert "cut off" in brief["error"]
    assert "delimiter" not in brief["error"]
    assert [c["max_output_tokens"] for c in fake.responses.calls] == [sb._MAX_OUTPUT_TOKENS, sb._MAX_OUTPUT_TOKENS * 2]


def test_brief_recovers_when_retry_completes(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    fake = _FakeOpenAI([_response(_brief_json(5)[:500], "incomplete", "max_output_tokens"),
                        _response(_brief_json(5))])
    monkeypatch.setattr(sb, "_OpenAI", lambda api_key: fake)
    brief = asyncio.run(sb.generate_brief(_request()))
    assert brief["error"] is None
    assert len(brief["recommended_tactics"]) == 5 and brief["key_insights"] == ["one", "two", "three"]
    assert "ad_presence" not in brief


def test_brief_folds_in_and_echoes_a_prior_ad_presence_check(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    fake = _FakeOpenAI([_response(_brief_json(1))])
    monkeypatch.setattr(sb, "_OpenAI", lambda api_key: fake)
    prior = {"summary": "Meta: ACTIVE, 3 ads, 100% English.", "meta": {"status": "active"}}
    brief = asyncio.run(sb.generate_brief(_request(), ad_presence=prior))
    assert "Meta: ACTIVE, 3 ads" in fake.responses.calls[0]["input"]
    assert brief["ad_presence"] is prior
