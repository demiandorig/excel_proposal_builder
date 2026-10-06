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


# The catalog appends an internal rate-tier list to 11 products' descriptions
# ("Variants (contact planning for exact tier): Standard: $13 CPM; Custom
# Audience: $15 CPM."). It's useful on the planner's rate card, but it's an
# instruction to Entravision's own planning team printed into a client-facing
# proposal cell, and misleading input for an AI blurb (which then talks about
# "versions" the client didn't buy). Always the LAST thing in a description.
_VARIANTS_TAIL = re.compile(r"\s*Variants\s*\(contact planning for exact tier\):.*\Z", re.DOTALL | re.IGNORECASE)


# --- sentence splitting (shared by the blurb tightener and the Roadblocks sheet filter) -------------------------

# A new sentence starts at a capital, digit, $, quote/bracket, a bullet marker, or a camelCase brand ("eDigital", "iHeart").
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])(\s+)(?=[A-Z0-9$\"'(\[]|[-\u2022*]\s|[a-z][A-Z])")
# A sentence break that is really an abbreviation's period ("U.S.", "Inc.", "vs.", "e.g.").
_ABBREVIATION_END = re.compile(
    r"(?:\b[A-Za-z](?:\.[A-Za-z])+|\b(?:Inc|Ltd|Co|Corp|vs|St|Mr|Mrs|Ms|Dr|No|Jr|Sr|Mt|Ave|Blvd|approx|est|avg|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Sec|Fig|Ref|Vol))\.$"
)
_LIST_MARKER = re.compile(r"^\s*\d{1,2}[.)]$")                          # "1." on its own: the break is a numbered step
_CITATION_ONLY = re.compile(r"^[(\[][^()\[\]]{2,100}[)\]]\.?$")          # "(eMarketer, 2025)." standing alone


def split_sentences(text: str) -> list[str]:
    """`text` as sentence segments, each INCLUDING the whitespace that follows it, so "".join(result) == text and
    a caller can drop segments without flattening the original line breaks. Abbreviation periods ("U.S.",
    "Inc.") and numbered-step markers ("1.") don't end a sentence, and a citation standing alone
    ("(Nielsen, 2025).") stays with the sentence it supports."""
    text = text or ""
    segments: list[str] = []
    pending = ""      # a piece that ended on an abbreviation / step marker continues into the next one
    last = 0
    for m in _SENTENCE_BOUNDARY.finditer(text):
        piece = pending + text[last:m.end()]
        last = m.end()
        head = piece.rstrip()
        last_token = head.rsplit(None, 1)[-1] if head else ""      # only the final word can be the abbreviation
        if _ABBREVIATION_END.search(last_token) or _LIST_MARKER.match(head):
            pending = piece
            continue
        pending = ""
        if segments and _CITATION_ONLY.match(head):
            segments[-1] += piece
        else:
            segments.append(piece)
    tail = pending + text[last:]
    if tail or not segments:
        if segments and _CITATION_ONLY.match(tail.strip()):
            segments[-1] += tail
        else:
            segments.append(tail)
    return segments


def strip_variant_pricing(description: str | None) -> str:
    """`description` without its trailing internal "Variants (...)" rate-tier
    list. A description that is nothing BUT that list becomes ""."""
    return _VARIANTS_TAIL.sub("", description or "").rstrip()
