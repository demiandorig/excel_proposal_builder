"""
The per-line "line note" (field name: `line_modifier`).

A short, optional, free-text label a planner types on a Step 04 line — "Hispanic, Phoenix only" — so two lines of the
SAME product can be told apart. It is shown in purple, in a small font, directly under the product name (and the
objective) in the Excel export, the same way in the PowerPoint decks and the Gamma outline, and as read-only reference
text in Steps 05-08. It is a LABEL only: it never feeds a number, a rate, a total or a validation.

Not to be confused with `notes_override` ("Planner note"), which is appended to the export's Planner Notes COLUMN
(an internal comment that sits beside the catalog's own notes). The two are independent: a line can carry either, both
or neither.

One sanitizer, used by every entry point (the API model, the dataclass the exporters read, the email prompts), so a
value is cleaned exactly once and the same way everywhere:

  * plain text only — control characters (newline, tab, NUL ... , zero-width / bidi marks, lone surrogates) become a
    space, runs of whitespace collapse to one, the ends are trimmed;
  * capped at LINE_MODIFIER_MAX_LEN characters (the Step 04 input carries the same `maxlength`);
  * never raises: anything that is not text is coerced, and "nothing" cleans to "".

Markup is deliberately NOT stripped here — it is plain text, and every place that renders it into HTML escapes it
(app.js escapeHtml); Excel / PowerPoint / Word receive it as literal text.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Short on purpose: it sits under a product name in a table cell. ~2 lines of the export's small purple font.
LINE_MODIFIER_MAX_LEN = 100

# C0 / C1 controls (incl. \n \r \t and NUL), line/paragraph separators, zero-width + bidi formatting marks, the BOM
# and lone surrogates (which cannot be encoded to UTF-8 and would break saving the workbook).
_UNWANTED = re.compile("[\x00-\x1f\x7f-\x9f​-‏ -‮⁠-⁤﻿\ud800-\udfff]+")


def clean_line_modifier(value: Any) -> str:
    """The sanitized line note: "" when there is none. Idempotent."""
    if value is None:
        return ""
    if not isinstance(value, str):
        if isinstance(value, bool):
            return ""
        value = str(value)
    text = " ".join(_UNWANTED.sub(" ", value).split())
    if len(text) > LINE_MODIFIER_MAX_LEN:
        text = text[:LINE_MODIFIER_MAX_LEN].rstrip()
    return text


def line_modifier_of(line: Any) -> str:
    """The cleaned note of a line given as a dataclass / model / dict (a line without the field has none)."""
    if line is None:
        return ""
    raw = line.get("line_modifier") if isinstance(line, dict) else getattr(line, "line_modifier", None)
    return clean_line_modifier(raw)


def clean_line_modifier_or_none(value: Any) -> Optional[str]:
    """For model fields: the cleaned note, or None when there is nothing to keep."""
    return clean_line_modifier(value) or None
