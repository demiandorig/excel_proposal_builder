"""
Source rules shared by every prompt that researches on the web or cites a source: the product blurbs and emails
(ai_enricher), the strategy brief, the roadblocks report and the Gamma outline refiner.

NO WIKIPEDIA (planner rule, Oct 2026): Wikipedia is never cited directly. It may point the model at a primary source
(the study, agency report, company filing or news outlet its footnotes reference) and THAT is what gets cited. Two
layers, because a prompt alone isn't trusted anywhere else in this app either:

  1. NO_WIKIPEDIA_RULE, spliced into every research prompt.
  2. strip_wikipedia() / strip_wikipedia_source(), run on whatever the model returns. They handle every shape a
     Wikipedia citation takes in model output: a "(Wikipedia, 2024)" parenthetical, a "([en.wikipedia.org](https://...))"
     markdown link (the web-search tool's inline citation style), a bare URL, "according to Wikipedia, ..." prose and
     a dedicated citation / source field. A sentence whose only support was Wikipedia loses that support, and when it
     carried a statistic it is dropped, never left behind as an unsourced number.

The web_search tool cannot enforce this itself: the pinned SDK's tool filter (openai.types.responses.WebSearchToolParam)
only has `allowed_domains` (an allow-list), no block-list, so nothing is passed there. The responses' url_citation
annotations are not consumed anywhere in this app (llm_utils reads output_text only), so the model's own text is the
one place a Wikipedia citation can land.

Also here: the plain-text citation primitives (what counts as a real, specific, named source) that the blurb claim
validator (claim_rules.py) builds on. Everything is pure text-in/text-out, offline-testable, and a no-op on text that
never mentions Wikipedia.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from app.services.text_utils import split_sentences


# ---------------------------------------------------------------------------
# The prompt rule — one copy, spliced into every research prompt
# ---------------------------------------------------------------------------

NO_WIKIPEDIA_RULE = (
    "SOURCE RULE — NEVER CITE WIKIPEDIA: Do not cite Wikipedia (any wikipedia.org, wikimedia.org or wikidata.org "
    "page, in any language, or a copy of one) as a source, and never put its name or a link to it in a citation, "
    "a source field or a sentence. You may read it to find leads, but then follow its references to the primary "
    "source it relies on (the study, agency or government report, company filing, platform's own published data, or "
    "the news outlet) and cite THAT instead. If you cannot reach a primary source for a number, leave the number out."
)


# ---------------------------------------------------------------------------
# Wikipedia detection
# ---------------------------------------------------------------------------

# Hosts of Wikipedia, its sister projects and the usual mirrors. Matched anywhere inside a URL string so a wrapped
# link (a web-archive or redirect URL carrying the Wikipedia address in its path / query) is caught as well.
_WIKI_HOST = re.compile(
    r"(?<![a-z0-9-])(?:[a-z0-9-]+\.)*(?:wikipedia|wikimedia|wikimediafoundation|wikidata|mediawiki|wiktionary|wikibooks|"
    r"wikiquote|wikisource|wikinews|wikiversity|wikivoyage|dbpedia)\.org(?![a-z0-9-])"
    r"|(?<![a-z0-9-])(?:[a-z0-9-]+\.)*wikiwand\.com(?![a-z0-9-])",
    re.IGNORECASE,
)
# The names, for text that cites without a URL ("Wikipedia, 2024", "per Wikipedia").
_WIKI_NAME = re.compile(
    r"(?<![a-z0-9])(?:wikipedia|wikimedia|wikidata|mediawiki|wiktionary|wikibooks|wikiquote|wikisource|wikinews|"
    r"wikiversity|wikivoyage|wikiwand|dbpedia)",
    re.IGNORECASE,
)

# [label](url), tolerating one level of parentheses inside the URL ("/wiki/Hispanic_(ethnicity)") and a "title".
_MD_LINK = re.compile(r"\[([^\[\]]*)\]\(\s*<?((?:[^()\s<>]|\([^()\s]*\))*)>?(?:\s+\"[^\"]*\")?\s*\)")
# A bare URL: stops before a closing bracket / quote and never ends on sentence punctuation.
_BARE_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]*[^\s<>\"')\].,;:!?]", re.IGNORECASE)
# A scheme-less Wikipedia address ("en.wikipedia.org/wiki/X"), with its path when it has one.
_WIKI_BARE = re.compile(
    r"(?<![\w@.-])(?:[a-z0-9-]+\.)*(?:wikipedia|wikimedia|wikimediafoundation|wikidata|mediawiki|wiktionary|wikibooks|"
    r"wikiquote|wikisource|wikinews|wikiversity|wikivoyage|dbpedia)\.org(?:/[^\s<>\"')\]]*[^\s<>\"')\].,;:!?])?"
    r"|(?<![\w@.-])(?:[a-z0-9-]+\.)*wikiwand\.com(?:/[^\s<>\"')\]]*[^\s<>\"')\].,;:!?])?",
    re.IGNORECASE,
)


def is_wikipedia_url(url: Any) -> bool:
    """True for a Wikipedia / Wikimedia / Wikidata page (any language, mobile or not) or a mirror of one."""
    return bool(_WIKI_HOST.search(url)) if isinstance(url, str) else False


def _is_wiki_link(m: "re.Match") -> bool:
    return is_wikipedia_url(m.group(2)) or bool(_WIKI_NAME.search(m.group(1)))


def mentions_wikipedia(text: Any) -> bool:
    """True when `text` names or links Wikipedia. A non-Wikipedia link whose URL merely contains the word
    (nielsen.com/wikipedia-traffic-study) doesn't count: only link labels and the visible text are read."""
    if not isinstance(text, str) or not text:
        return False
    visible = _MD_LINK.sub(lambda m: m.group(0) if is_wikipedia_url(m.group(2)) else m.group(1), text)
    visible = _BARE_URL.sub(lambda m: m.group(0) if is_wikipedia_url(m.group(0)) else "", visible)
    return bool(_WIKI_NAME.search(visible))


# ---------------------------------------------------------------------------
# Plain-text citation primitives
# ---------------------------------------------------------------------------

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_DOMAIN = re.compile(r"\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|org|net|gov|edu|io|co|us|tv|info|ai|mx|uk|ca|es)\b",
                     re.IGNORECASE)
# A parenthetical / bracketed group with no nested brackets.
_GROUP_RX = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")
_CITE_SHAPE = re.compile(r"^[A-Za-z][\w &.'’/-]*,\s*(?:19|20)\d{2}$")      # "Publisher Name, 2025"

# Words that make a "publisher" no publisher at all: "Industry Report, 2025", "Market Research, 2024", "Source, Year".
_GENERIC_SOURCE_WORDS = frozenset(
    "a an the and or of for to on in by with from via per source sources publisher publishers name year yyyy "
    "industry industries market markets research report reports study studies survey surveys data analysis analyses "
    "analyst analysts recent latest various multiple several general internal company companies national consumer "
    "consumers digital media advertising marketing trade publication publications benchmark benchmarks statistics "
    "statistic estimates estimate insights insight findings finding experts expert forecast forecasts review "
    "article articles web website online internet online news information info citation reference references".split()
)

# "(Source, Year)" / "[Publisher, 2025]" copied from a prompt template.
_PLACEHOLDER_GROUP = re.compile(
    r"[(\[]\s*(?:source|publisher|source name|publisher name|name|citation|organization|org)\s*(?:,\s*|\s+)"
    r"(?:year|yyyy|20xx|\d{4})\s*[)\]]|[(\[]\s*(?:source|publisher|citation)\s*[)\]]",
    re.IGNORECASE,
)
# Prose attribution that names its source: "according to Nielsen", "per Edison Research", "reported by Pew Research Center".
_NAME = r"[A-Z][\w&.'’-]*(?:\s+(?:[A-Z][\w&.'’-]*|of|for|and|the|with|&)){0,5}"
_ATTRIBUTION = re.compile(
    r"\b(?:[Aa]ccording to|[Pp]er|[Rr]eported by|[Ff]ound by|[Dd]ata from|[Ss]urvey by|[Ss]tudy by|[Rr]eport by|"
    r"[Rr]esearch from|[Ss]ource:)\s+(?:the\s+)?(" + _NAME + r")"
)


def _publisher_is_specific(publisher: str) -> bool:
    """A publisher string names someone: at least one word that is not a generic filler word."""
    words = re.findall(r"[A-Za-z][A-Za-z&'’-]*", publisher)
    return any(w.lower().strip("'’-") not in _GENERIC_SOURCE_WORDS for w in words)


def _is_specific_citation(content: str) -> bool:
    """The inside of a parenthetical is a real citation: a named publisher AND a year (the established
    "(Publisher, Year)" form) or a domain; never Wikipedia, never a placeholder or a vague "Industry Report, 2025"."""
    content = (content or "").strip()
    if not content or mentions_wikipedia(content):
        return False
    has_year = bool(_YEAR.search(content))
    has_domain = bool(_DOMAIN.search(content))
    if not (has_year or has_domain):
        return False
    publisher = _YEAR.sub(" ", content)
    return _publisher_is_specific(publisher)


def citation_groups(text: str) -> list[str]:
    """Contents of every parenthetical / bracketed group in `text` that is a real, specific citation."""
    out = []
    for m in _GROUP_RX.finditer(text or ""):
        content = m.group(1) if m.group(1) is not None else m.group(2)
        if _is_specific_citation(content):
            out.append(content.strip())
    return out


def has_named_citation(text: str) -> bool:
    """`text` carries a real, named source: a "(Publisher, Year)" / "(publisher.com)" group, or a prose attribution
    ("according to Nielsen"). Wikipedia, placeholders and vague sources ("Industry Report, 2025") don't count."""
    text = text or ""
    if citation_groups(text):
        return True
    for m in _ATTRIBUTION.finditer(text):
        name = m.group(1)
        if not mentions_wikipedia(name) and _publisher_is_specific(name):
            return True
    return False


def remove_citation_groups(text: str) -> str:
    """`text` without its citation-looking groups, so a publisher name can't be mistaken for part of a claim."""
    def repl(m: "re.Match") -> str:
        content = m.group(1) if m.group(1) is not None else m.group(2)
        return " " if (_YEAR.search(content) or _DOMAIN.search(content)) and _is_specific_citation(content) else m.group(0)
    return _GROUP_RX.sub(repl, text or "")


def strip_unspecific_citations(text: str) -> str:
    """Drops citation groups that name no real source: template placeholders ("(Source, Year)") and vague ones
    ("(Industry Report, 2025)"). Only groups in the exact "Words, Year" shape are judged, so an ordinary
    parenthetical ("(since 2020)", "(Q4 2025)") is left alone."""
    if not text or "(" not in text and "[" not in text:
        return text

    def repl(m: "re.Match") -> str:
        content = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
        if _PLACEHOLDER_GROUP.fullmatch(m.group(0)):
            return ""
        if _CITE_SHAPE.match(content) and not _is_specific_citation(content) and not mentions_wikipedia(content):
            return ""
        return m.group(0)

    out = _GROUP_RX.sub(repl, text)
    return _tidy(out) if out != text else text


_CITATION_ONLY_SEG = re.compile(r"^[(\[]\s*([^()\[\]]{2,300}?)\s*[)\]]\.?$")


def is_citation_only(segment: str) -> bool:
    """A whole segment that is just a citation group ("(Nielsen, 2025).", "([nielsen.com](https://...))")."""
    s = (segment or "").strip()
    if not s or s[0] not in "([":
        return False
    flat = _MD_LINK.sub(lambda m: m.group(1) or m.group(2), s)
    m = _CITATION_ONLY_SEG.match(flat)
    if not m:
        return False
    content = m.group(1)
    return bool(_YEAR.search(content) or _DOMAIN.search(content) or mentions_wikipedia(content) or _MD_LINK.search(s))


def glue_citation_segments(segments: Iterable[str]) -> list[str]:
    """Sentence segments with a citation-only segment folded into the sentence it supports, so a sentence and its
    citation are kept, dropped and counted as one unit whatever their length or shape."""
    out: list[str] = []
    for seg in segments:
        if out and is_citation_only(seg):
            out[-1] += seg
        else:
            out.append(seg)
    return out


# ---------------------------------------------------------------------------
# Links in plain-text cells
# ---------------------------------------------------------------------------

def _host_of(url: str) -> str:
    u = (url or "").strip().strip("<>\"'")
    u = re.sub(r"^[a-z][a-z0-9+.-]*://", "", u, flags=re.IGNORECASE)
    host = re.split(r"[/?#]", u, maxsplit=1)[0]
    host = host.rsplit("@", 1)[-1].split(":", 1)[0].lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


_FOOTNOTE = re.compile(r"[ \t]*\[\d{1,3}\](?=[\s.,;:!?)]|$)")
_BRACKET = re.compile(r"\[([^\[\]]*)\]")


def strip_footnote_markers(text: str) -> str:
    """"[1]"-style footnote numbers removed: a plain-text cell has nothing for them to point at."""
    return _FOOTNOTE.sub("", text) if isinstance(text, str) and "[" in text else text


def has_unfilled_slot(text: str) -> bool:
    """A square-bracket slot copied from a prompt template ("[N]% of [specific group] ... ([Publisher], [Year])"):
    any bracketed group that is not itself a real citation. Run after flatten_links, which turns real links into text."""
    for m in _BRACKET.finditer(text or ""):
        content = m.group(1).strip()
        if content and not _is_specific_citation(content):
            return True
    return False


def flatten_links(text: str) -> str:
    """Markdown links and bare URLs as plain text, for a spreadsheet cell: "[Nielsen](https://...)" becomes "Nielsen"
    and a bare URL becomes its domain ("(nielsen.com)" still reads as a named source)."""
    if not isinstance(text, str) or not text:
        return text
    low = text.lower()
    if "](" not in text and "http" not in low and "www." not in low:
        return text
    out = _MD_LINK.sub(lambda m: (m.group(1) or "").strip() or _host_of(m.group(2)), text)
    return _BARE_URL.sub(lambda m: _host_of(m.group(0)), out)


# ---------------------------------------------------------------------------
# Stripping Wikipedia out of model output
# ---------------------------------------------------------------------------

# ", via Wikipedia" / "per the Wikipedia article on X" inside a citation part: the stat's real source is what remains.
_VIA_WIKI = re.compile(
    r"[,\s]*\b(?:via|through|per|from|cited (?:in|on|by)|as (?:cited|quoted|listed|reported|noted|summari[sz]ed) (?:in|on|by)|"
    r"according to|source:?|sourced from|citing)\s+(?:the\s+)?(?:(?:english|spanish)[- ](?:language\s+)?)?"
    r"(?:wikipedia|wikimedia|wikidata)\b[^;,]*",
    re.IGNORECASE,
)
# Prose lead-in: "According to Wikipedia, ..." / "As noted on Wikipedia: ..."
_LEAD_ATTRIBUTION = re.compile(
    r"^\s*(?:according to|per|as (?:noted|stated|reported|listed|described|cited|shown) (?:by|on|in)|based on|citing|from|via)\s+"
    r"(?:the\s+)?(?:(?:english|spanish)[- ](?:language\s+)?)?(?:wikipedia|wikimedia|wikidata)(?:['’]s)?"
    r"(?:\s+(?:article|entry|page|entries|pages)(?:\s+(?:on|about|for|titled)\s+[^,.;]+)?)?\s*[,:–—-]*\s*",
    re.IGNORECASE,
)
# Prose tail: "..., according to Wikipedia." / "... (source: Wikipedia)" up to the sentence end.
_TAIL_ATTRIBUTION = re.compile(
    r"\s*[,;–—-]?\s*\(?(?:according to|per|as (?:noted|stated|reported|listed) (?:by|on|in)|via|from|citing|source:?)\s+"
    r"(?:the\s+)?(?:(?:english|spanish)[- ](?:language\s+)?)?(?:wikipedia|wikimedia|wikidata)\b(?:['’]s)?[^.!?]*?\)?(?=[.!?]|\s*$)",
    re.IGNORECASE,
)
_EMPTY_GROUP = re.compile(
    r"\(\s*(?:see|source|sources|via|from|per|cited in|citing|ref|reference|references)?\s*[:;,-]*\s*\)"
    r"|\[\s*(?:see|source|sources|via|from|per|cited in|citing|ref|reference|references)?\s*[:;,-]*\s*\]",
    re.IGNORECASE,
)


_POINTER = re.compile(r"^\s*(?:see|visit|read|check|refer to|source|sources|ref|reference)\b", re.IGNORECASE)


def _has_number(sentence: str) -> bool:
    """A figure in the sentence beyond a bare year — the kind of claim that must not outlive its only source."""
    return bool(re.search(r"\d", _YEAR.sub("", sentence)))


def _tidy(text: str) -> str:
    """Whitespace / punctuation left behind where something was cut out (line breaks are never touched)."""
    text = re.sub(r"[ \t]+([.,;:!?)\]])", r"\1", text)
    text = re.sub(r"([(\[])[ \t]+", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"([,;:])\s*([.!?])", r"\2", text)
    return text.strip(" \t")


def _clean_groups(segment: str) -> str:
    """Removes Wikipedia from each parenthetical: "(Nielsen, 2025; Wikipedia, 2024)" keeps Nielsen,
    "(Nielsen, 2025, via Wikipedia)" keeps Nielsen, "(Wikipedia, 2024)" vanishes."""
    def repl(m: "re.Match") -> str:
        content = m.group(1) if m.group(1) is not None else m.group(2)
        if not _WIKI_NAME.search(content):
            return m.group(0)
        kept = []
        for part in re.split(r"\s*;\s*", content):
            part = _VIA_WIKI.sub("", part).strip(" ,;")
            if part and not _WIKI_NAME.search(part):
                kept.append(part)
        if not kept:
            return ""
        left, right = ("(", ")") if m.group(1) is not None else ("[", "]")
        return left + "; ".join(kept) + right
    return _GROUP_RX.sub(repl, segment)


def _clean_segment(seg: str, supported_elsewhere: bool = False) -> Optional[str]:
    """One sentence (with its trailing whitespace) without its Wikipedia support, or None when it has to go:
    it still leans on Wikipedia in its own prose, or it carried a statistic whose only source was Wikipedia
    (unless a separate citation field vouches for it: `supported_elsewhere`)."""
    body = seg.rstrip()
    gap = seg[len(body):]
    if _POINTER.match(body) and (_WIKI_BARE.search(body) or _WIKI_NAME.search(_MD_LINK.sub("", body))):
        return None                       # "See https://en.wikipedia.org/wiki/X for details." — nothing left without the link
    body = _MD_LINK.sub(lambda m: "" if _is_wiki_link(m) else m.group(0), body)
    body = _BARE_URL.sub(lambda m: "" if is_wikipedia_url(m.group(0)) else m.group(0), body)
    body = _WIKI_BARE.sub("", body)
    body = _EMPTY_GROUP.sub("", body)
    body = _clean_groups(body)
    body = _LEAD_ATTRIBUTION.sub("", body)
    body = _TAIL_ATTRIBUTION.sub("", body)
    body = _EMPTY_GROUP.sub("", body)
    body = _tidy(body)
    if body and body[0].islower() and _LEAD_ATTRIBUTION.match(seg.lstrip()):
        body = body[0].upper() + body[1:]
    if mentions_wikipedia(body):
        return None                       # "Wikipedia reports that ..." — the sentence itself is the Wikipedia claim
    if not re.search(r"\w", body):
        return None
    if not supported_elsewhere and _has_number(body) and not has_named_citation(body):
        return None                       # its figure was sourced to Wikipedia alone: drop it, don't leave it unsourced
    return body + gap


def strip_wikipedia(text: Any, supported_elsewhere: bool = False) -> Any:
    """`text` with every Wikipedia citation removed. A sentence that cited Wikipedia keeps its words (the claim may well
    stand on the model's other sources) unless it carried a statistic — then the whole sentence goes, rather than
    leaving a number nobody can check. Text that never mentions Wikipedia is returned unchanged, byte for byte.

    supported_elsewhere: the text's figures are vouched for by a separate, non-Wikipedia citation field (a strategy
    tactic's data_point next to its "citation"), so a figure is kept even though the sentence itself named Wikipedia."""
    if not isinstance(text, str) or not mentions_wikipedia(text):
        return text
    segments = glue_citation_segments(split_sentences(text))
    out = ""
    for seg in segments:
        if not mentions_wikipedia(seg):
            out += seg
            continue
        cleaned = _clean_segment(seg, supported_elsewhere)
        if cleaned is not None:
            out += cleaned
            continue
        gap = seg[len(seg.rstrip()):]                       # a dropped sentence that ended a paragraph keeps the break
        trailing = out[len(out.rstrip()):]
        if "\n" in gap and out and gap.count("\n") > trailing.count("\n"):
            out = out.rstrip() + gap
    return out.strip()


def strip_wikipedia_source(value: Any) -> Any:
    """A dedicated citation / source field ("Nielsen, 2025", "Wikipedia, 2024", "https://en.wikipedia.org/wiki/X"):
    "" when it is Wikipedia, the field without its Wikipedia part when it names other sources too."""
    if not isinstance(value, str) or not mentions_wikipedia(value):
        return value
    flat = _MD_LINK.sub(lambda m: "" if _is_wiki_link(m) else (m.group(1) or m.group(2)), value)
    flat = _BARE_URL.sub(lambda m: "" if is_wikipedia_url(m.group(0)) else m.group(0), flat)
    kept = []
    for part in re.split(r"\s*(?:;|\||\n|\s+and\s+|\s+&\s+|\s+\+\s+)\s*", flat):
        part = _VIA_WIKI.sub("", part).strip(" ,;()[]")
        if part and not _WIKI_NAME.search(part):
            kept.append(part)
    return "; ".join(kept)


def scrub_model_output(obj: Any) -> Any:
    """strip_wikipedia over every string in a parsed model response (dicts and lists are walked, other values left
    alone). Mutates dicts / lists in place and returns the object, so it can be dropped in right after parsing."""
    if isinstance(obj, str):
        return strip_wikipedia(obj)
    if isinstance(obj, dict):
        for key, value in obj.items():
            obj[key] = scrub_model_output(value)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            obj[i] = scrub_model_output(value)
    return obj
