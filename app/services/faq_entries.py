"""
Process FAQs — the rows on every export's "Process FAQs" tab, edited in Admin -> FAQ.

Two blocks, one table: `section = 'faq'` rows are the FAQ table (SERVICES | Frequently Asked Question | Resource
Link), `section = 'restriction'` rows are the "CATEGORY AND PRODUCT RESTRICTIONS" block under it (same three
columns: category / what it relates to / link). Each row carries a link TITLE and a link URL; the export writes the
title as a real hyperlink to the URL (a title with no URL stays plain text).

`DEFAULT_ENTRIES` are the rows the tab has always had (titles only — their URLs were never recorded anywhere, so an
admin fills those in). They seed an empty table the first time the admin page reads it, and they are what the export
falls back to when the table is empty or unreachable — the tab is never blank and generating a proposal never
depends on this table existing.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

from app.db import get_connection

log = logging.getLogger(__name__)

SECTIONS = ("faq", "restriction")
SECTION_LABELS = {"faq": "Process FAQ", "restriction": "Category and product restriction"}

# The guidelines site feeds from the same "Restricted Verticals by Platforms" sheet Roadblocks reads.
GUIDELINES_URL = "https://entravision-digital-ad-guidelines.netlify.app/"
GUIDELINES_LINK_TITLE = "See latest information about Entravision's Ad Guidelines"

_LIMITS = {"services": 300, "question": 1000, "link_title": 200, "link_url": 2000}
# Control characters break the xlsx writer (and mean nothing in a FAQ); ordinary whitespace is kept.
_CONTROL_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# (section, services, question, link title) — the rows the tab has always shown.
_DEFAULTS = [
    ("faq", "All Products",
     "Where can I see the specs and best practices needed for my digital product?",
     "Entravision's Digital Specs and Best Practices"),
    ("faq", "All Products",
     "Why should I install Entravision's GTM (Google Tag Manager) on my website?",
     "Entravision's GTM Process"),
    ("faq", "All Products",
     "How can I install the provided GTM in my CMS?",
     "Entravision's GTM Process for Most Common CMS Website Providers"),
    ("faq", "Facebook & Instagram Ads on EVC Radio | Noticias Ya Pages",
     "How do I accept an Entravision Meta Business Account request to initiate a brand partnership or post a co-branded ad?",
     "How to Meta partnership for co-branded ads guide"),
    ("faq", "TikTok Ads (Client's Handle) or TikTok Ads on EVC Radio | Noticias Ya Pages",
     "How can I provide access to my TikTok Page to Run Ads?",
     "How to TikTok Access Guide"),
    ("faq", "TikTok Ads",
     "How can I authorize Entravision's TikTok Business Center account via QR code?",
     "How to TikTok Business Center Entravision Account (QR Code Method)"),
    ("restriction", "Healthcare Advertisers — Google Search and YouTube Ads",
     "Filling out the Google healthcare advertising form is mandatory.",
     "Apply for healthcare-related advertising"),
    ("restriction", "Consumer Finance Advertisers — Google Search and YouTube Ads",
     "Related to: 36% APR, state licenses for Financial Verification, APR & Repayment disclosures above the fold, and \"Instant\" claims.",
     "Consumer Finance Google Advertising Guidelines"),
    ("restriction", "Government and Advocacy — Google Search and YouTube Ads",
     "Related to: Official Contracts, State License, SOS Filing, direct URL to government directories.",
     "Government Google Advertising Guidelines"),
    ("restriction", "Alcohol — All products",
     "Related to: Alcohol or alcohol consumption.",
     "FTC & TTB Alcohol Advertising Guidelines"),
    ("restriction", "Loans — All products",
     "Related to: Truth-in-lending compliance and disclosure requirements.",
     "FTC Advertising Compliance Guide - Loan Campaigns"),
]


def default_entries() -> list[dict]:
    """The built-in rows, in display order, shaped like stored ones (no id)."""
    return [{"id": None, "section": section, "services": services, "question": question,
             "link_title": title, "link_url": "", "sort_order": i}
            for i, (section, services, question, title) in enumerate(_DEFAULTS)]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def clean_url(value: Any) -> str:
    """A web link or "". Only http(s) is accepted (a link in a client-facing workbook must never be a
    javascript:/file: URL); a bare "example.com/page" gets https:// put in front."""
    url = _CONTROL_CHARS.sub("", str(value or "")).strip()
    if not url:
        return ""
    if re.search(r"\s", url):
        raise ValueError("The link can't contain spaces.")
    if "://" not in url and not url.lower().startswith(("mailto:", "javascript:", "data:", "file:")):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc or "." not in parsed.netloc.split("@")[-1]:
        raise ValueError("The link must be a web address starting with http:// or https://.")
    if len(url) > _LIMITS["link_url"]:
        raise ValueError(f"The link is too long (max {_LIMITS['link_url']} characters).")
    return url


def clean_entry(fields: dict, base: Optional[dict] = None) -> dict:
    """`base` (an existing entry) with `fields` laid over it, validated. ValueError carries a planner-readable
    message. An absent key keeps the base value."""
    data = {"section": "faq", "services": "", "question": "", "link_title": "", "link_url": ""}
    if base:
        data.update({k: base.get(k, data[k]) for k in data})
    for key in data:
        if key in fields and fields[key] is not None:
            data[key] = fields[key]
    section = str(data["section"] or "faq").strip().lower()
    if section not in SECTIONS:
        raise ValueError("Section must be 'Process FAQ' or 'Category and product restriction'.")
    out: dict[str, Any] = {"section": section}
    for key in ("services", "question", "link_title"):
        text = re.sub(r"[ \t]+", " ", _CONTROL_CHARS.sub("", str(data[key] or ""))).strip()
        if len(text) > _LIMITS[key]:
            raise ValueError(f"{key.replace('_', ' ').capitalize()} is too long (max {_LIMITS[key]} characters).")
        out[key] = text
    if not out["services"]:
        raise ValueError("SERVICES is required.")
    if not out["question"]:
        raise ValueError("The question (or description) is required.")
    out["link_url"] = clean_url(data["link_url"])
    if out["link_url"] and not out["link_title"]:
        out["link_title"] = out["link_url"]            # a link needs something to click on
    return out


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

_COLUMNS = "id, section, services, question, link_title, link_url, sort_order"
_ORDER = "ORDER BY CASE section WHEN 'faq' THEN 0 ELSE 1 END, sort_order, id"


def _as_dict(row) -> dict:
    return {"id": row["id"], "section": row["section"], "services": row["services"], "question": row["question"],
            "link_title": row["link_title"] or "", "link_url": row["link_url"] or "", "sort_order": row["sort_order"]}


def load_entries() -> list[dict]:
    """The rows for an export: the stored ones, or the built-in rows when the table is empty or can't be read.
    Never raises."""
    try:
        with get_connection() as conn:
            rows = conn.execute(f"SELECT {_COLUMNS} FROM faq_entries {_ORDER}").fetchall()
        if rows:
            return [_as_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("faq_entries unavailable, using the built-in Process FAQs: %s", exc)
    return default_entries()


def _seed(conn) -> None:
    for i, entry in enumerate(default_entries()):
        conn.execute(
            "INSERT INTO faq_entries (section, services, question, link_title, link_url, sort_order) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (entry["section"], entry["services"], entry["question"], entry["link_title"], entry["link_url"], i),
        )


def list_entries() -> list[dict]:
    """The admin list. An empty table is seeded with the built-in rows first. RuntimeError (with a planner-readable
    message) when the table can't be reached."""
    try:
        with get_connection() as conn:
            rows = conn.execute(f"SELECT {_COLUMNS} FROM faq_entries {_ORDER}").fetchall()
            if not rows:
                _seed(conn)
                rows = conn.execute(f"SELECT {_COLUMNS} FROM faq_entries {_ORDER}").fetchall()
        return [_as_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "UndefinedTable":
            raise RuntimeError("The FAQ table doesn't exist yet — apply the latest schema.sql and reload.") from exc
        log.warning("couldn't read faq_entries: %s", exc)
        raise RuntimeError("Couldn't read the FAQ entries — try again in a moment.") from exc


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _next_order(conn, section: str) -> int:
    return conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM faq_entries WHERE section = %s",
                        (section,)).fetchone()["n"]


def create_entry(fields: dict) -> dict:
    entry = clean_entry(fields)
    list_entries()                       # seeds an empty table before the first write, or it would never be seeded
    with get_connection() as conn:
        entry["sort_order"] = _next_order(conn, entry["section"])
        row = conn.execute(
            "INSERT INTO faq_entries (section, services, question, link_title, link_url, sort_order) "
            f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_COLUMNS}",
            (entry["section"], entry["services"], entry["question"], entry["link_title"], entry["link_url"], entry["sort_order"]),
        ).fetchone()
    return _as_dict(row)


def update_entry(entry_id: int, fields: dict) -> dict:
    """LookupError when there is no such entry."""
    with get_connection() as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM faq_entries WHERE id = %s", (entry_id,)).fetchone()
        if row is None:
            raise LookupError("FAQ entry not found.")
        current = _as_dict(row)
        entry = clean_entry(fields, current)
        order = current["sort_order"] if entry["section"] == current["section"] else _next_order(conn, entry["section"])
        updated = conn.execute(
            "UPDATE faq_entries SET section = %s, services = %s, question = %s, link_title = %s, link_url = %s, "
            f"sort_order = %s, updated_at = now() WHERE id = %s RETURNING {_COLUMNS}",
            (entry["section"], entry["services"], entry["question"], entry["link_title"], entry["link_url"], order, entry_id),
        ).fetchone()
    return _as_dict(updated)


def delete_entry(entry_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute("DELETE FROM faq_entries WHERE id = %s RETURNING id", (entry_id,)).fetchone()
    return row is not None


def move_entry(entry_id: int, direction: str) -> None:
    """Swap an entry with its neighbour in the same section ("up" or "down"). LookupError when there is no such
    entry; moving past either end is a no-op."""
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'.")
    with get_connection() as conn:
        row = conn.execute("SELECT id, section FROM faq_entries WHERE id = %s", (entry_id,)).fetchone()
        if row is None:
            raise LookupError("FAQ entry not found.")
        ordered = [r["id"] for r in conn.execute(
            "SELECT id FROM faq_entries WHERE section = %s ORDER BY sort_order, id", (row["section"],)).fetchall()]
        i = ordered.index(entry_id)
        j = i - 1 if direction == "up" else i + 1
        if j < 0 or j >= len(ordered):
            return
        ordered[i], ordered[j] = ordered[j], ordered[i]
        for position, eid in enumerate(ordered):               # renumber the whole section: no ties, no gaps
            conn.execute("UPDATE faq_entries SET sort_order = %s WHERE id = %s", (position, eid))
