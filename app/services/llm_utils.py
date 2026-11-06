"""Shared OpenAI call, safe failure diagnostics, and JSON-parse plumbing."""
from __future__ import annotations

import logging
import re
import time
from typing import Iterable, Optional

from app.services.text_utils import extract_json_object

_logger = logging.getLogger(__name__)

REPAIR_MODEL = "gpt-5-mini"
REPAIR_MAX_COMPLETION_TOKENS = 16000
OPENAI_REQUEST_TIMEOUT_SECONDS = 50.0
OPENAI_MAX_RETRIES = 0


def is_timeout_error(exc: Exception) -> bool:
    """Identify SDK and transport timeouts so callers do not start a slower fallback request."""
    return (
        isinstance(exc, TimeoutError)
        or type(exc).__name__ in {"APITimeoutError", "TimeoutException"}
        or "timed out" in str(exc).lower()
    )


_FAILURE_MESSAGES = {
    "authentication": "The AI provider rejected its credentials. Ask an administrator to check the provider connection.",
    "permission": "The AI provider account does not have access to this model or feature. Ask an administrator to review its permissions.",
    "quota": "The AI provider usage limit has been reached. Ask an administrator to review billing or usage limits.",
    "rate_limit": "The AI provider is receiving too many requests. Wait a moment, then retry this step.",
    "timeout": "This AI step timed out before it could finish. Please retry.",
    "malformed_response": "The AI returned a response this step could not read. Please regenerate it.",
    "response_truncated": "The AI response was cut off before it finished. Please regenerate this step.",
    "service_unavailable": "The AI provider is temporarily unavailable. Please retry shortly.",
    "configuration": "AI generation is not configured. Ask an administrator to check the provider setup.",
    "provider_error": "The AI provider could not complete this step. Please retry shortly.",
}


def classify_failure(exc: Exception) -> str:
    """Return a safe, stable category without relying on provider error text."""
    if isinstance(exc, ResponseTruncated):
        return "response_truncated"
    if is_timeout_error(exc):
        return "timeout"

    name = type(exc).__name__.lower()
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    body = getattr(exc, "body", None)
    detail = body.get("error", body) if isinstance(body, dict) else {}
    code = getattr(exc, "code", None) or (detail.get("code") if isinstance(detail, dict) else None)
    error_type = getattr(exc, "type", None) or (detail.get("type") if isinstance(detail, dict) else None)
    markers = " ".join(str(v).lower() for v in (name, code, error_type) if v is not None)

    if any(word in markers for word in ("insufficient_quota", "billing_hard_limit", "quota_exceeded", "usage_limit")):
        return "quota"
    if status == 401 or any(word in markers for word in ("authentication", "invalid_api_key", "unauthorized")):
        return "authentication"
    if status == 403 or any(word in markers for word in (
        "permission", "forbidden", "access_denied", "permissiondenied", "model_not_found", "not_authorized"
    )):
        return "permission"
    if status == 429 or "ratelimit" in name or "rate_limit" in markers:
        return "rate_limit"
    if (status is not None and status >= 500) or any(
        word in markers for word in (
            "serviceunavailable", "internalserver", "server_error", "apiconnectionerror", "connectionerror"
        )
    ):
        return "service_unavailable"
    if isinstance(exc, (ValueError, IndexError, KeyError, AttributeError)) or any(
        word in markers for word in ("invalidresponse", "malformedresponse", "jsondecode")
    ):
        return "malformed_response"
    return "provider_error"


def failure_message(category: str) -> str:
    """Safe user-facing guidance for a classified AI failure."""
    return _FAILURE_MESSAGES.get(category, _FAILURE_MESSAGES["provider_error"])


def _request_id(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    request_id = (
        getattr(exc, "request_id", None)
        or getattr(response, "request_id", None)
        or (headers.get("x-request-id") if hasattr(headers, "get") else None)
    )
    if not isinstance(request_id, str):
        return "-"
    safe_id = re.sub(r"[^A-Za-z0-9._:-]", "", request_id)[:120]
    return safe_id or "-"


def log_ai_failure(step: str, stage: str, started: float, exc: Exception,
                   category: Optional[str] = None) -> str:
    """Log diagnostic metadata only; never include exception text, prompts, or credentials."""
    category = category or classify_failure(exc)
    _logger.warning(
        "ai_failure step=%s stage=%s duration_ms=%d category=%s request_id=%s",
        step, stage, round((time.perf_counter() - started) * 1000), category, _request_id(exc),
    )
    return category


_REPAIR_PROMPT = (
    "The text below was meant to be one JSON object, but it has a JSON syntax error. "
    "Return that same JSON object with ONLY the syntax fixed (escape stray double quotes inside "
    "strings, add or remove commas/brackets). Do not change, shorten, reorder or add any content. "
    "Respond with only the JSON object.\n\n"
)


class ResponseTruncated(Exception):
    """The model hit its output-token cap before finishing (reasoning tokens count toward the cap)."""


def _usage_summary(response) -> str:
    usage = getattr(response, "usage", None)
    details = getattr(usage, "output_tokens_details", None) if usage is not None else None
    return (f"output_tokens={getattr(usage, 'output_tokens', None)} "
            f"reasoning_tokens={getattr(details, 'reasoning_tokens', None)}")


def _incomplete_reason(response) -> Optional[str]:
    if getattr(response, "status", None) != "incomplete":
        return None
    return getattr(getattr(response, "incomplete_details", None), "reason", None) or "unknown"


def extract_response_text(response) -> str:
    """Output text of a Responses API result; walks `.output` for SDKs without `.output_text`."""
    text = getattr(response, "output_text", None)
    if text:
        return text
    chunks = []
    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            t = getattr(content, "text", None)
            if t:
                chunks.append(t)
    if chunks:
        return "\n".join(chunks)
    raise ValueError("Responses API returned no extractable text")


def responses_text(client, *, model: str, prompt: str, max_output_tokens: int,
                   tools: Optional[list] = None) -> str:
    """Responses API call; retries once at double the cap if cut off, then raises ResponseTruncated."""
    kwargs = {"model": model, "input": prompt, "max_output_tokens": max_output_tokens}
    if tools:
        kwargs["tools"] = tools
    response = client.responses.create(**kwargs)
    if _incomplete_reason(response) == "max_output_tokens":
        _logger.warning("%s cut off at max_output_tokens=%s (%s); retrying at %s",
                        model, max_output_tokens, _usage_summary(response), max_output_tokens * 2)
        kwargs["max_output_tokens"] = max_output_tokens * 2
        response = client.responses.create(**kwargs)
        if _incomplete_reason(response) == "max_output_tokens":
            raise ResponseTruncated(f"cut off at max_output_tokens={kwargs['max_output_tokens']}")
    reason = _incomplete_reason(response)
    if reason:
        raise RuntimeError(f"model response incomplete ({reason})")
    return extract_response_text(response)


def chat_text(client, *, model: str, prompt: str, max_completion_tokens: int) -> str:
    """chat.completions call that raises ResponseTruncated instead of returning a cut-off answer."""
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=max_completion_tokens,
    )
    choice = response.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise ResponseTruncated(f"cut off at max_completion_tokens={max_completion_tokens}")
    return choice.message.content or ""


def parse_json_object(raw: str, *, expect_any: Iterable[str], client=None) -> dict:
    """extract_json_object, plus one gpt-5-mini syntax-repair pass when a client is given."""
    expect_any = tuple(expect_any)
    try:
        return extract_json_object(raw, expect_any=expect_any)
    except ValueError as first_exc:
        if client is None or not raw or "{" not in raw:
            raise
        _logger.warning("JSON parse failed; attempting one repair pass")
        repair_started = time.perf_counter()
        try:
            repaired = chat_text(client, model=REPAIR_MODEL, prompt=_REPAIR_PROMPT + raw,
                                 max_completion_tokens=REPAIR_MAX_COMPLETION_TOKENS)
            return extract_json_object(repaired, expect_any=expect_any)
        except Exception as repair_exc:
            category = log_ai_failure("shared", "json_repair", repair_started, repair_exc)
            if category not in {"malformed_response", "response_truncated"}:
                raise
            raise first_exc
