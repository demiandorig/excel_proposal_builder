"""
The audience (interest) taxonomy and its sourced incidence rates — Constanza's "factorizer" for interest targeting.

A segment is an audience the sales team sells ("Dental Care Seekers", "Sports Fans - Soccer"). Each belongs to a group
(Automotive, Health & Wellness, ...), carries the names the team uses for it plus search keywords, and has ONE incidence
record: the share of US adults 18+ who match it, what that number measures, its source and the evidence, how it was
verified, and how far to trust it.

  * status 'sourced'        a published figure answers the definition directly
  * status 'derived'        computed from cited published inputs (the arithmetic is in `derivation`)
  * status 'no_public_rate' listed and searchable, but NO number: the engine never guesses one (it says so instead)

The data lives in Postgres (audience_group / audience_segment / audience_incidence, migrations/0002) and is seeded from
app/data/constanza/audience_taxonomy.json. When the database cannot be read, the bundled JSON answers, so interest
targeting never depends on the database being up. Search is in Python: the whole taxonomy is about a hundred rows.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "constanza" / "audience_taxonomy.json"
STATUSES = ("sourced", "derived", "no_public_rate")
CONFIDENCES = ("high", "moderate", "low", "none")
_TTL = 60.0
_APPLY_THRESHOLD = 55.0      # best_match applies a segment's rate only at or above this score; below it the planner is shown suggestions
# Role words that say WHO but not WHAT ("owners", "shoppers", "seekers"): typing only these is too generic to pick a segment.
_GENERIC = frozenset({"owner", "shopper", "seeker", "intender", "user", "buyer", "lover", "enthusiast", "professional", "service", "product"})
_RETRY_AFTER_FAILURE = 15.0


class TaxonomyError(ValueError):
    pass


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    sort: int = 0


@dataclass(frozen=True)
class Segment:
    id: str
    group_key: str
    group_label: str
    label: str
    parent_id: Optional[str] = None
    company_names: tuple = ()
    keywords: tuple = ()
    description: str = ""
    sort: int = 0
    status: str = "no_public_rate"
    rate_all: Optional[float] = None
    rate_male: Optional[float] = None
    rate_female: Optional[float] = None
    definition: str = ""
    denominator: str = ""
    time_window: str = ""
    source_title: Optional[str] = None
    source_publisher: Optional[str] = None
    source_url: Optional[str] = None
    source_date: Optional[str] = None
    evidence: Optional[str] = None
    derivation: Optional[str] = None
    confidence: str = "none"
    caveats: str = ""
    supporting_sources: tuple = ()
    verification: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def supported(self) -> bool:
        """True when there is a sourced rate the engine may apply."""
        return self.status in ("sourced", "derived") and self.rate_all is not None

    def rate_for(self, gender: Optional[str]) -> Optional[float]:
        """The rate for 'male' / 'female' when the source gives it, else the all-adults rate."""
        if not self.supported:
            return None
        if gender == "male" and self.rate_male is not None:
            return self.rate_male
        if gender == "female" and self.rate_female is not None:
            return self.rate_female
        return self.rate_all

    def source_line(self) -> str:
        parts = [p for p in (self.source_publisher, self.source_title) if p]
        return " — ".join(parts) if parts else ""

    def to_public(self, detail: bool = False) -> dict:
        out = {
            "id": self.id, "label": self.label, "group": self.group_key, "status": self.status, "supported": self.supported,
            "rate_all": self.rate_all, "rate_male": self.rate_male, "rate_female": self.rate_female,
            "confidence": self.confidence, "company_names": list(self.company_names), "keywords": list(self.keywords),
            "source": self.source_line(), "source_url": self.source_url, "source_date": self.source_date,
        }
        if detail:
            out.update(description=self.description, definition=self.definition, denominator=self.denominator, time_window=self.time_window,
                       evidence=self.evidence, derivation=self.derivation, caveats=self.caveats,
                       supporting_sources=list(self.supporting_sources), verification=self.verification)
        return out


# --------------------------------------------------------------------------------------------- matching

def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text.replace("&", " and ")).strip()


def _stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


_STOP = frozenset({"the", "a", "an", "of", "for", "and", "in", "to", "on", "who", "that", "with", "by", "like", "likes", "love", "loves",
                   "interested", "interest", "interests", "fan", "fans", "people", "person", "market", "audience", "audiences", "general"})


def tokens(text: str) -> list[str]:
    return [_stem(t) for t in _fold(text).split() if t and t not in _STOP]


_FUNCTION_WORDS = frozenset({"the", "a", "an", "of", "for", "and", "in", "to", "on", "who", "that", "with", "by"})


def _topic_words(name: str) -> int:
    """How many words of a folded name say WHAT the audience is: neither a function word nor a bare role word ("shoppers")."""
    return sum(1 for w in name.split() if w not in _FUNCTION_WORDS and _stem(w) not in _GENERIC)


def _phrase_in(needle: str, haystack: str) -> bool:
    """needle appears in haystack as whole words (both already folded)."""
    return bool(needle) and f" {needle} " in f" {haystack} "


@dataclass(frozen=True)
class Hit:
    segment: Segment
    score: float
    how: str            # name | keyword | partial | words | fuzzy


class Taxonomy:
    def __init__(self, groups: list[Group], segments: list[Segment]) -> None:
        self.groups = sorted(groups, key=lambda g: (g.sort, g.label))
        self.segments: dict[str, Segment] = {s.id: s for s in segments}
        self._order = {g.key: i for i, g in enumerate(self.groups)}
        self._index: list[tuple[Segment, list[str], list[set[str]]]] = []
        for s in segments:
            names = [_fold(n) for n in (s.label, *s.company_names, *s.keywords) if n]
            self._index.append((s, names, [set(tokens(n)) for n in names]))

    def ordered(self) -> list[Segment]:
        return sorted(self.segments.values(), key=lambda s: (self._order.get(s.group_key, 99), s.sort, s.label))

    def supported(self) -> list[Segment]:
        return [s for s in self.ordered() if s.supported]

    def search(self, text: str, limit: int = 8) -> list[Hit]:
        """Segments ranked by how well they match what a planner typed: an exact name or alias, a keyword phrase inside the
        text ("likes sports"), the text inside a name ("dental"), shared words, or a near-spelling ("fitnes gym")."""
        query = _fold(text)
        qtokens = set(tokens(text))
        if not query:
            return []
        collapsed_query = query.replace(" ", "")
        hits: dict[str, Hit] = {}

        def offer(seg: Segment, score: float, how: str, weak: bool = False) -> None:
            if weak:
                score, how = min(score, _APPLY_THRESHOLD - 0.1), "words"
            cur = hits.get(seg.id)
            if cur is None or score > cur.score:
                hits[seg.id] = Hit(seg, score, how)

        for seg, names, tokensets in self._index:
            if seg.id == _fold(text).replace(" ", "_"):
                offer(seg, 100.0, "name")
            for name, toks in zip(names, tokensets):
                if not name:
                    continue
                # A name made only of role words ("shoppers") says WHO and not WHAT. Inside a longer phrase ("mortgage shoppers")
                # it is a hint for the planner: it neither applies a rate nor ties with the segment that names the topic.
                weak = bool(toks) and toks <= _GENERIC and query != name
                if query == name:
                    offer(seg, 100.0, "name" if name == _fold(seg.label) else "keyword")
                elif _phrase_in(name, query):                      # the planner's text CONTAINS this keyword phrase
                    # more of the topic words covered wins ("luxury car" over "car shopper"); length only breaks the rest
                    offer(seg, 80.0 + 0.6 * min(_topic_words(name), 3) + min(len(name), 30) / 100, "keyword", weak)
                elif _phrase_in(query, name) and not (qtokens and qtokens <= _GENERIC):   # the planner typed part of this name
                    offer(seg, 62.0 + min(len(query), 20) / 10, "partial")
                elif len(name.replace(" ", "")) >= 6 and name.replace(" ", "") in collapsed_query:
                    offer(seg, 70.0 + min(len(name.replace(" ", "")), 20) / 4, "keyword", weak)   # "videogames" for "video games"; the longer phrase wins
                elif toks and qtokens:
                    common = toks & qtokens
                    if common:
                        overlap = len(common) / max(len(toks), len(qtokens))
                        if overlap >= 0.5 or (len(qtokens) == 1 and len(common) == 1):
                            score = 40.0 + 30.0 * overlap
                            if not toks <= qtokens:
                                # Only part of the keyword is in what was typed ("pet OWNERS" against "small business OWNERS",
                                # "health CARE" against "dental CARE"): a shared common word is a hint, not an answer. Offered
                                # as a suggestion, never strong enough for best_match to apply its rate.
                                score = min(score, _APPLY_THRESHOLD - 0.1)
                            offer(seg, score, "words")
                if len(query) >= 4 and abs(len(query) - len(name)) <= max(6, len(name) // 2):
                    ratio = difflib.SequenceMatcher(None, query, name).ratio()
                    if ratio >= 0.82:
                        offer(seg, 45.0 * ratio, "fuzzy")
        ranked = sorted(hits.values(), key=lambda h: (-h.score, 0 if h.segment.supported else 1, self._order.get(h.segment.group_key, 99), h.segment.label))
        return ranked[:limit]

    def best_match(self, text: str) -> Optional[Hit]:
        """The one segment to apply for a phrase, or None when nothing is close enough (the caller then reports the closest
        ones instead of guessing)."""
        hits = self.search(text, limit=3)
        if not hits or hits[0].score < _APPLY_THRESHOLD:
            return None
        if len(hits) > 1 and hits[1].segment.id != hits[0].segment.id and hits[0].score - hits[1].score < 0.25 and hits[0].how != "name":
            return None            # two audiences fit equally well ("insurance", "contractors"): the planner chooses, we do not
        return hits[0]

    def categories(self) -> list[dict]:
        """The shape the audience layer has always used (one dict per segment with a sourced rate)."""
        return [{
            "category": s.id, "label": s.label, "keywords": list(s.keywords) + list(s.company_names),
            "rate_by_gender": {"all": s.rate_all, "male": s.rate_male if s.rate_male is not None else s.rate_all,
                               "female": s.rate_female if s.rate_female is not None else s.rate_all},
            "source": s.source_line(), "source_url": s.source_url, "source_date": s.source_date,
            "sample_scope": s.definition or s.denominator, "confidence": s.confidence, "status": s.status, "group": s.group_key,
        } for s in self.supported()]

    def to_public(self, detail: bool = False) -> dict:
        by_group: dict[str, list[dict]] = {g.key: [] for g in self.groups}
        for s in self.ordered():
            by_group.setdefault(s.group_key, []).append(s.to_public(detail))
        return {
            "groups": [{"key": g.key, "label": g.label, "segments": by_group.get(g.key, [])} for g in self.groups],
            "totals": {"segments": len(self.segments), "with_rate": len(self.supported()),
                       "no_public_rate": sum(1 for s in self.segments.values() if not s.supported)},
        }


# --------------------------------------------------------------------------------------------- parsing + validation

def _num(value: Any, where: str) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise TaxonomyError(f"{where}: {value!r} is not a number")
    if not (0 < v <= 1):
        raise TaxonomyError(f"{where}: a rate must be a share between 0 and 1, got {v}")
    return v


def parse(data: dict) -> Taxonomy:
    """Build a Taxonomy from the JSON structure, enforcing the rules the database also enforces."""
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list) or not isinstance(data.get("groups"), list):
        raise TaxonomyError("expected an object with 'groups' and 'segments' lists")
    groups: list[Group] = []
    for g in data["groups"]:
        if not re.fullmatch(r"[a-z0-9_]+", g.get("key", "")) or not g.get("label"):
            raise TaxonomyError(f"bad group {g!r}")
        groups.append(Group(g["key"], g["label"], int(g.get("sort", 0))))
    keys = {g.key: g.label for g in groups}
    if len(keys) != len(groups):
        raise TaxonomyError("duplicate group key")
    segments: list[Segment] = []
    seen: set[str] = set()
    for raw in data["segments"]:
        sid = raw.get("id", "")
        where = f"segment {sid!r}"
        if not re.fullmatch(r"[a-z0-9_]+", sid):
            raise TaxonomyError(f"{where}: id must be lower-case letters, digits and underscores")
        if sid in seen:
            raise TaxonomyError(f"{where}: duplicate id")
        seen.add(sid)
        if raw.get("group") not in keys:
            raise TaxonomyError(f"{where}: unknown group {raw.get('group')!r}")
        if not raw.get("label"):
            raise TaxonomyError(f"{where}: no label")
        inc = raw.get("incidence") or {}
        status = inc.get("status", "no_public_rate")
        if status not in STATUSES:
            raise TaxonomyError(f"{where}: status {status!r} is not one of {STATUSES}")
        rate_all = _num(inc.get("all"), f"{where} all")
        if (status == "no_public_rate") != (rate_all is None):
            raise TaxonomyError(f"{where}: a rate must be present exactly when the status is sourced/derived")
        src = inc.get("source") or {}
        if status != "no_public_rate" and not (src.get("url") and src.get("title")):
            raise TaxonomyError(f"{where}: a sourced rate must name its source (title and url)")
        confidence = inc.get("confidence", "none")
        if confidence not in CONFIDENCES:
            raise TaxonomyError(f"{where}: confidence {confidence!r}")
        segments.append(Segment(
            id=sid, group_key=raw["group"], group_label=keys[raw["group"]], label=raw["label"], parent_id=raw.get("parent"),
            company_names=tuple(raw.get("company_names") or ()), keywords=tuple(raw.get("keywords") or ()),
            description=raw.get("description") or "", sort=int(raw.get("sort", 0)), status=status, rate_all=rate_all,
            rate_male=_num(inc.get("male"), f"{where} male"), rate_female=_num(inc.get("female"), f"{where} female"),
            definition=inc.get("definition") or "", denominator=inc.get("denominator") or "", time_window=inc.get("time_window") or "",
            source_title=src.get("title"), source_publisher=src.get("publisher"), source_url=src.get("url"), source_date=src.get("date"),
            evidence=inc.get("evidence"), derivation=inc.get("derivation"), confidence=confidence, caveats=inc.get("caveats") or "",
            supporting_sources=tuple(inc.get("supporting_sources") or ()), verification=inc.get("verification") or {}))
    for s in segments:
        if s.parent_id and s.parent_id not in seen:
            raise TaxonomyError(f"segment {s.id!r}: unknown parent {s.parent_id!r}")
    return Taxonomy(groups, segments)


def load_bundled() -> Taxonomy:
    return parse(json.loads(DATA_PATH.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------------------------- database

def _read_db() -> Optional[Taxonomy]:
    """The taxonomy from Postgres, or None when it can't be read or is empty (the bundled file then answers)."""
    from app.db import get_connection
    try:
        with get_connection() as conn:
            groups = conn.execute("SELECT group_key, label, sort_order FROM audience_group").fetchall()
            rows = conn.execute(
                "SELECT s.*, g.label AS group_label, i.status, i.rate_all, i.rate_male, i.rate_female, i.definition, i.denominator, "
                "i.time_window, i.source_title, i.source_publisher, i.source_url, i.source_date, i.evidence_quote, i.derivation, "
                "i.confidence, i.caveats, i.supporting_sources, i.verification "
                "FROM audience_segment s JOIN audience_group g USING (group_key) LEFT JOIN audience_incidence i USING (segment_id) "
                "WHERE s.is_active").fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("audience taxonomy unavailable in the database (the bundled file answers): %s", exc)
        return None
    if not rows:
        return None
    f = lambda v: None if v is None else float(v)  # noqa: E731
    segments = [Segment(
        id=r["segment_id"], group_key=r["group_key"], group_label=r["group_label"], label=r["label"], parent_id=r["parent_id"],
        company_names=tuple(r["company_names"] or ()), keywords=tuple(r["keywords"] or ()), description=r["description"],
        sort=r["sort_order"], status=r["status"] or "no_public_rate", rate_all=f(r["rate_all"]), rate_male=f(r["rate_male"]),
        rate_female=f(r["rate_female"]), definition=r["definition"] or "", denominator=r["denominator"] or "",
        time_window=r["time_window"] or "", source_title=r["source_title"], source_publisher=r["source_publisher"],
        source_url=r["source_url"], source_date=r["source_date"], evidence=r["evidence_quote"], derivation=r["derivation"],
        confidence=r["confidence"] or "none", caveats=r["caveats"] or "", supporting_sources=tuple(r["supporting_sources"] or ()),
        verification=r["verification"] or {}) for r in rows]
    return Taxonomy([Group(g["group_key"], g["label"], g["sort_order"]) for g in groups], segments)


_lock = threading.Lock()
_cache: dict[str, Any] = {"taxonomy": None, "at": 0.0, "retry_at": 0.0}


def invalidate() -> None:
    with _lock:
        _cache.update(taxonomy=None, at=0.0, retry_at=0.0)


def load_taxonomy() -> Taxonomy:
    """The current taxonomy: Postgres (cached a minute) or, failing that, the bundled file."""
    now = time.monotonic()
    with _lock:
        cached, at, retry_at = _cache["taxonomy"], _cache["at"], _cache["retry_at"]
    if cached is not None and now - at < _TTL:
        return cached
    if now >= retry_at:
        fresh = _read_db()
        with _lock:
            if fresh is not None:
                _cache.update(taxonomy=fresh, at=now, retry_at=0.0)
                return fresh
            _cache.update(retry_at=now + _RETRY_AFTER_FAILURE)
            if _cache["taxonomy"] is not None:
                return _cache["taxonomy"]
    return _bundled()


_bundled_cache: dict[str, Taxonomy] = {}


def _bundled() -> Taxonomy:
    if "t" not in _bundled_cache:
        _bundled_cache["t"] = load_bundled()
    return _bundled_cache["t"]


# --------------------------------------------------------------------------------------------- seeding

def seed_to_db(conn, data: Optional[dict] = None) -> dict[str, int]:
    """Make the database match the data file. Idempotent. A row an admin has edited (origin = 'admin') is left alone; a
    segment dropped from the file is deactivated, not deleted (saved proposals may still name it)."""
    data = data if data is not None else json.loads(DATA_PATH.read_text(encoding="utf-8"))
    tax = parse(data)                                   # refuses to seed anything that would not pass the database's own rules
    for g in tax.groups:
        conn.execute("INSERT INTO audience_group (group_key, label, sort_order) VALUES (%s, %s, %s) "
                     "ON CONFLICT (group_key) DO UPDATE SET label = EXCLUDED.label, sort_order = EXCLUDED.sort_order", (g.key, g.label, g.sort))
    done = 0
    for s in tax.ordered():
        conn.execute(
            "INSERT INTO audience_segment (segment_id, group_key, label, company_names, keywords, description, sort_order, is_active) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE) ON CONFLICT (segment_id) DO UPDATE SET group_key = EXCLUDED.group_key, "
            "label = EXCLUDED.label, company_names = EXCLUDED.company_names, keywords = EXCLUDED.keywords, "
            "description = EXCLUDED.description, sort_order = EXCLUDED.sort_order, is_active = TRUE, updated_at = now() "
            "WHERE audience_segment.origin = 'seed'",
            (s.id, s.group_key, s.label, list(s.company_names), list(s.keywords), s.description, s.sort))
        conn.execute(
            "INSERT INTO audience_incidence (segment_id, status, rate_all, rate_male, rate_female, definition, denominator, time_window, "
            "source_title, source_publisher, source_url, source_date, evidence_quote, derivation, confidence, caveats, supporting_sources, verification) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb) "
            "ON CONFLICT (segment_id) DO UPDATE SET status = EXCLUDED.status, rate_all = EXCLUDED.rate_all, rate_male = EXCLUDED.rate_male, "
            "rate_female = EXCLUDED.rate_female, definition = EXCLUDED.definition, denominator = EXCLUDED.denominator, "
            "time_window = EXCLUDED.time_window, source_title = EXCLUDED.source_title, source_publisher = EXCLUDED.source_publisher, "
            "source_url = EXCLUDED.source_url, source_date = EXCLUDED.source_date, evidence_quote = EXCLUDED.evidence_quote, "
            "derivation = EXCLUDED.derivation, confidence = EXCLUDED.confidence, caveats = EXCLUDED.caveats, "
            "supporting_sources = EXCLUDED.supporting_sources, verification = EXCLUDED.verification, updated_at = now() "
            "WHERE audience_incidence.origin = 'seed'",
            (s.id, s.status, s.rate_all, s.rate_male, s.rate_female, s.definition, s.denominator, s.time_window, s.source_title,
             s.source_publisher, s.source_url, s.source_date, s.evidence, s.derivation, s.confidence, s.caveats,
             json.dumps(list(s.supporting_sources)), json.dumps(s.verification, default=str)))
        done += 1
    # parents after every segment exists
    for s in tax.ordered():
        if s.parent_id:
            conn.execute("UPDATE audience_segment SET parent_id = %s WHERE segment_id = %s AND origin = 'seed'", (s.parent_id, s.id))
    deactivated = conn.execute(
        "UPDATE audience_segment SET is_active = FALSE WHERE origin = 'seed' AND is_active AND NOT (segment_id = ANY(%s))",
        (list(tax.segments),)).rowcount
    invalidate()
    return {"groups": len(tax.groups), "segments": done, "deactivated": deactivated}
