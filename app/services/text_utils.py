"""
Small text-cleanup helpers shared across the AI response parsers
(ai_enricher, strategy_brief, roadblocks) — kept in one place so a fix here
doesn't need to be copy-pasted into three files (and inevitably drift).
"""
from __future__ import annotations

import re


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
