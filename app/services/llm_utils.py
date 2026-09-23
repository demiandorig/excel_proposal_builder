"""Shared OpenAI call + JSON-parse plumbing for strategy_brief, roadblocks and ai_enricher."""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from app.services.text_utils import extract_json_object

_logger = logging.getLogger(__name__)

REPAIR_MODEL = "gpt-5-mini"
REPAIR_MAX_COMPLETION_TOKENS = 16000

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
        _logger.warning("JSON parse failed (%s); attempting one repair pass", first_exc)
        try:
            repaired = chat_text(client, model=REPAIR_MODEL, prompt=_REPAIR_PROMPT + raw,
                                 max_completion_tokens=REPAIR_MAX_COMPLETION_TOKENS)
            return extract_json_object(repaired, expect_any=expect_any)
        except Exception as repair_exc:
            _logger.warning("JSON repair pass failed: %s", repair_exc)
            raise first_exc
