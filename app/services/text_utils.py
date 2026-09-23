"""
Small text-cleanup helpers shared across the AI response parsers
(ai_enricher, strategy_brief, roadblocks) — kept in one place so a fix here
doesn't need to be copy-pasted into three files (and inevitably drift).
"""
from __future__ import annotations

import json
import re
from typing import Iterable

# strict=False tolerates raw newlines/tabs inside string values, which models sometimes emit.
_JSON_DECODER = json.JSONDecoder(strict=False)


def extract_json_object(raw: str, *, expect_any: Iterable[str]) -> dict:
    """First JSON object in `raw` that has at least one of `expect_any`'s keys.

    Tolerates preamble, trailing commentary and code fences. `expect_any` stops a
    truncated outer object from falling through to a complete NESTED one (e.g. a
    single tactic) and being mistaken for the whole response.
    """
    if not raw or not raw.strip():
        raise ValueError("Empty response from the model.")
    expected = set(expect_any)
    first_error = None
    pos = raw.find("{")
    while pos != -1:
        try:
            obj, _ = _JSON_DECODER.raw_decode(raw, pos)
        except json.JSONDecodeError as exc:
            first_error = first_error or exc
        else:
            if isinstance(obj, dict) and expected & obj.keys():
                return obj
        pos = raw.find("{", pos + 1)
    if first_error is not None:
        raise ValueError(f"JSON parse error: {first_error}")
    raise ValueError("No structured response received.")


def normalize_newlines(text: str) -> str:
    """
    Fix a model over-escaping newlines as literal backslash sequences
    instead of real newline bytes. json.loads() already turns a correctly-
    escaped "\\n" into a real newline — this only fixes text that's still
    escaped after that (single- or multiply-escaped), and is a no-op on
    text that's already correct.

    Runs to a fixed point (not just one pass) because a single
    `.replace("\\\\n", "\\n")` on a DOUBLY-escaped sequence like "\\\\\\\\n"
    (backslash, backslash, n) only consumes the last backslash+n pair,
    leaving a stray backslash sitting next to a now-real newline — visibly
    wrong output that a single pass can't fully clean up.
    """
    if not text:
        return text
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\\+r\\+n", "\n", text)
        text = re.sub(r"\\+n", "\n", text)
    return text
