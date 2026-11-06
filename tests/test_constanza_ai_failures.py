"""Deterministic, safe failure reporting for Constanza's OpenAI audience steps."""
from __future__ import annotations

import pytest

import app.main as main
from app.services import constanza_audience as audience
from app.services import llm_utils


class _ProviderFailure(Exception):
    def __init__(self, status_code=None, body=None):
        super().__init__("private prompt, output, and credential detail")
        self.status_code = status_code
        self.body = body


def _failure(category):
    return {
        "authentication": lambda: _ProviderFailure(401),
        "permission": lambda: _ProviderFailure(403),
        "quota": lambda: _ProviderFailure(429, {"error": {"code": "insufficient_quota"}}),
        "rate_limit": lambda: _ProviderFailure(429),
        "timeout": lambda: TimeoutError("private transport detail"),
        "malformed_response": lambda: ValueError("private model output"),
        "response_truncated": lambda: llm_utils.ResponseTruncated("private model output"),
        "service_unavailable": lambda: _ProviderFailure(503),
        "provider_error": lambda: _ProviderFailure(),
    }[category]()


FAILURE_CATEGORIES = (
    "authentication", "permission", "quota", "rate_limit", "timeout",
    "malformed_response", "response_truncated", "service_unavailable", "provider_error",
)


@pytest.mark.parametrize("category", FAILURE_CATEGORIES)
def test_audience_parser_classifies_provider_failures_and_logs_only_safe_metadata(monkeypatch, caplog, category):
    monkeypatch.setattr(audience, "_openai_client", lambda: object())
    error = _failure(category)
    monkeypatch.setattr(audience.llm_utils, "chat_text", lambda *args, **kwargs: (_ for _ in ()).throw(error))

    with caplog.at_level("WARNING", logger=llm_utils.__name__):
        parsed = audience._parse_audience_text("adults in a private geography")

    assert parsed.ai_failure_category == category
    assert parsed.geo_text == "adults in a private geography"
    assert f"category={category}" in caplog.text
    assert "step=constanza_audience_parse stage=parse" in caplog.text
    assert "private prompt" not in caplog.text and "credential detail" not in caplog.text
    assert "private model output" not in caplog.text


@pytest.mark.parametrize("category", FAILURE_CATEGORIES)
def test_interest_match_classifies_provider_failures_without_applying_a_rate(monkeypatch, caplog, category):
    monkeypatch.setattr(audience, "_openai_client", lambda: object())
    error = _failure(category)
    monkeypatch.setattr(audience.llm_utils, "chat_text", lambda *args, **kwargs: (_ for _ in ()).throw(error))

    with caplog.at_level("WARNING", logger=llm_utils.__name__):
        result = audience.resolve_interest("quokka collectors from planet zircon")

    assert result.ai_failure_category == category
    assert result.category is None and result.supported is False
    assert f"category={category}" in caplog.text
    assert "step=constanza_interest_match stage=classification" in caplog.text
    assert "private prompt" not in caplog.text and "credential detail" not in caplog.text


def test_missing_provider_configuration_is_reported_by_parse_and_interest_flows(monkeypatch, caplog):
    monkeypatch.setattr(audience, "_openai_client", lambda: None)

    with caplog.at_level("WARNING", logger=llm_utils.__name__):
        parsed = audience._parse_audience_text("adults in Sacramento")
        interest = audience.resolve_interest("quokka collectors from planet zircon")

    assert parsed.ai_failure_category == "configuration"
    assert interest.ai_failure_category == "configuration"
    assert "category=configuration" in caplog.text
    assert "AI generation is not configured" in llm_utils.failure_message("configuration")
    assert "category=configuration" in caplog.text


def test_parse_audience_api_returns_a_safe_actionable_warning_when_ai_is_unconfigured(monkeypatch):
    monkeypatch.setattr(audience, "_openai_client", lambda: None)

    result = main.constanza_parse_audience(main.ConstanzaParseAudienceRequest(text="adults in Sacramento"))

    assert result["geo_text"] == "adults in Sacramento"
    assert result["ai_warning"] == {
        "category": "configuration",
        "message": llm_utils.failure_message("configuration"),
    }


def test_estimate_notes_explain_when_interest_matching_failed(monkeypatch):
    monkeypatch.setattr(audience, "_openai_client", lambda: None)
    request = main.ConstanzaEstimateRequest(
        product_name="Entravision Plus - CTV/OTT Reach",
        geo_text="Los Angeles",
        months=1,
        audience=main.ConstanzaAudienceSpecModel(interest_terms=["quokka collectors from planet zircon"]),
    )

    result = main.constanza_estimate(request)

    assert any(llm_utils.failure_message("configuration") in note for note in result["audience"]["notes"])
    assert result["audience"]["interests"][0]["supported"] is False
    assert result["audience"]["interests"][0]["ai_failure_category"] == "configuration"
