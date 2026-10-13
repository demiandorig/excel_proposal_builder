"""
Deterministic backstop for the "no unfounded claims" rule on product blurbs (Excel "Details" column).

The prompt (ai_enricher._build_prompt) tells the model that anything about what an audience does, wants, prefers or
trusts, how big or fast-growing a market or channel is, or how well something performs needs BOTH a specific number
AND a named source in the same sentence, and must be left out otherwise. A model sometimes writes the sweeping line
anyway ("Hispanics are always researching for news on YouTube"), so — same approach as the variant / language-claim
filters in ai_enricher — a sentence that breaks the rule is dropped, together with its citation, rather than trusting
compliance: an unfounded line in a client-facing cell is worse than a missing one.

is_unsupported_claim(sentence) is True when the sentence

  * is an audience / behavior / market / performance claim (see _claim_kind) without BOTH a checkable statistic AND a
    real named source; or
  * carries a statistic of any kind with no named source at all (an unsourced number can't be checked).

It is conservative on purpose. What a product is or does ("Text ads that appear on Google when people search for what
you offer", "Banner ads shown across a network of sites, retargeting shoppers as they browse") is never a claim, and
neither is a sentence that is sourced: "62% of U.S. Hispanic adults watch YouTube weekly (Nielsen, 2025)" passes.
Product mechanics numbers (a 15-second spot, 300x250, a 5-second skip) are not statistics. Pure text in, bool out.
"""
from __future__ import annotations

import re
from typing import Optional

from app.services import source_rules

# ---------------------------------------------------------------------------
# A statistic the reader can check
# ---------------------------------------------------------------------------

_NUM = r"\d[\d,]*(?:\.\d+)?"
_STAT = re.compile(
    _NUM + r"\s?(?:%|percent\b|per ?cent\b|percentage points?\b)"
    r"|\b" + _NUM + r"\s?(?:million|billion|trillion|mm|mn|bn)\b"
    r"|(?-i:\b" + _NUM + r"[MB]\b)"                                       # 45M / 2.5B
    r"|\b" + _NUM + r"\s?(?:x|×|times)\s+(?:more|less|as|higher|lower|greater|better|faster|likely|bigger|larger|the)\b"
    r"|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?:in|out of)\s+(?:every\s+)?"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\b"
    r"|\b" + _NUM + r"\s+(?:hours?|hrs?|minutes?|mins?)\s+(?:a|per|each|every|daily|weekly|monthly|on)\b"
    r"|\b\d{1,3}(?:,\d{3}){2,}\b",                                         # 2,500,000
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# What counts as an audience / behavior / market / performance claim
# ---------------------------------------------------------------------------

# People whose habits get generalized. Not "advertisers"/"brands"/"businesses": those are the buyers of the product.
_GROUP = (
    r"hispanics?|latinos?|latinas?|latinx|latin americans?|spanish[- ]speak\w*|spanish[- ]dominant|"
    r"consumers?|shoppers?|viewers?|listeners?|audiences?|users?|customers?|buyers?|"
    r"millennials?|gen[- ]?z|gen[- ]?x|boomers?|households?|famil(?:y|ies)|parents|moms|mothers|dads|"
    r"people|adults|americans|homeowners|drivers|commuters|residents|locals|voters|fans|gamers|"
    r"demographics?|generations?|communit(?:y|ies)|teens|seniors|women|men|patients|students|travell?ers"
)
# Habits and preferences. Deliberately NOT "see/hear/skip/receive": those describe how an ad is delivered.
# Two kinds: ATTITUDE verbs are a claim about what a group prefers or how it decides, whoever the group is; NEUTRAL
# verbs just say what people do with a medium ("listeners tune in through apps") and only become a claim when the
# group is a demographic one ("Hispanics watch YouTube") or the sentence generalizes ("always", "often", ...).
_ATTITUDE_V = (
    r"prefer(?:s|red|ring)?|trust(?:s|ed|ing)?|rely|relies|relying|turn(?:s|ed|ing)? to|spend(?:s|ing)?|spent|"
    r"research(?:es|ed|ing)?|shop(?:s|ped|ping)?|buy(?:s|ing)?|bought|purchas(?:e|es|ed|ing)|engag(?:e|es|ed|ing)|"
    r"respond(?:s|ed|ing)?|convert(?:s|ed|ing)?|decid(?:e|es|ed|ing)|choos(?:e|es|ing)|chose|favou?r(?:s|ed|ing)?|"
    r"gravitat(?:e|es|ed|ing)|over-?index(?:es|ed|ing)?|skew(?:s|ed|ing)?|embrac(?:e|es|ed|ing)|adopt(?:s|ed|ing)?|"
    r"depend(?:s|ed|ing)?|seek(?:s|ing)?|sought|tend(?:s|ed)? to|lov(?:e|es|ed)|enjoy(?:s|ed)?"
)
_NEUTRAL_V = (
    r"watch(?:es|ed|ing)?|view(?:s|ed|ing)?|listen(?:s|ed|ing)?|stream(?:s|ed|ing)?|search(?:es|ed|ing)?|"
    r"us(?:e|es|ed|ing)|discover(?:s|ed|ing)?|click(?:s|ed|ing)?|scroll(?:s|ed|ing)?|consum(?:e|es|ed|ing)|"
    r"follow(?:s|ed|ing)?|go(?:es)? to|check(?:s|ed|ing)?|read(?:s|ing)?|visit(?:s|ed|ing)?|"
    r"tun(?:e|es|ed|ing) in|learn(?:s|ed|ing)?|look(?:s|ed|ing)? (?:up|for|to)"
)
_BEHAVIOR = r"(?:" + _ATTITUDE_V + r"|" + _NEUTRAL_V + r")"
# A demographic group (as opposed to a role like "listeners" or "shoppers"): any plain habit of theirs is a generalization.
_DEMOGRAPHIC = re.compile(
    r"\b(?:hispanics?|latinos?|latinas?|latinx|latin americans?|spanish[- ]speak\w*|spanish[- ]dominant|millennials?|"
    r"gen[- ]?z|gen[- ]?x|boomers?|parents|moms|mothers|dads|famil(?:y|ies)|americans|women|men|teens|seniors|"
    r"demographics?|generations?|bilingual\w*)\b",
    re.IGNORECASE,
)
_ATTITUDE_WORD = re.compile(r"\b(?:" + _ATTITUDE_V + r"|" + r"loyal|avid|heavy|frequent|active|engaged|passionate|influential|"
                            r"receptive|responsive|attentive|likely|tech-savvy|digital-first|mobile-first|early adopters?|"
                            r"power users?|brand[- ]loyal" + r")\b", re.IGNORECASE)
_ADVERB = (
    r"always|never|often|typically|usually|generally|commonly|frequently|regularly|constantly|consistently|increasingly|"
    r"more and more|heavily|overwhelmingly|naturally|inherently|already|actively|also|now|more|less|most|highly|very|"
    r"especially|particularly|really|truly|simply"
)
_TRAIT = (
    r"loyal|avid|heavy|frequent|active|engaged|passionate|influential|receptive|responsive|attentive|likely|"
    r"tech-savvy|digital-first|mobile-first|early adopters?|power users?|brand[- ]loyal"
)
_AUDIENCE_NOUN = (
    r"audiences?|listeners?|viewers?|users?|consumers?|shoppers?|customers?|communit(?:y|ies)|households?|followers?|"
    r"fans?|subscribers?|readers?"
)
_QUANT = (
    r"many|most|majority of|millions? of|thousands of|hundreds of|countless|numerous|nearly all|almost all|nearly every|"
    r"almost every|the vast majority of|a growing (?:number|share|segment) of|growing numbers? of|a large (?:share|number|portion) of"
)

_SWEEP_ADVERB = (
    r"always|never|often|typically|usually|generally|commonly|frequently|regularly|constantly|consistently|"
    r"increasingly|overwhelmingly|heavily|invariably"
)

_SWEEP_WORD = re.compile(r"\b(?:" + _SWEEP_ADVERB + r")\b", re.IGNORECASE)
_GROUP_RE = re.compile(r"\b(?:" + _GROUP + r")\b", re.IGNORECASE)
_NEXT_GROUP = re.compile(r"\s+(?:" + _GROUP + r")\b", re.IGNORECASE)
# The group is the one doing something: "<Group> [aux] [adverb]* <behavior or trait>", a few words after the noun —
# or a sweeping adverb right after it ("Audiences are always online"). A relative clause or a participle
# ("Customers who search ...", "People searching for X ...") describes who the ad reaches, not what the group does.
_AGENT_TAIL = re.compile(
    r"^(?!\s+(?:who|that|which|whom|whose|in|near|within|nearby|around|on|at|by|from|with)\b)(?!\s+\w+ing\b)"
    r"(?:"
    r"(?:\s+(?!who\b|that\b|which\b|whom\b|whose\b|to\b|ready\b|able\b|willing\b|about\b|interested\b|trying\b)[\w'’-]+)"
    r"{0,3}?\s+(?:(?:are|is|were|have|has|do|does|will|would)\s+)?"
    r"(?:(?:" + _ADVERB + r")\s+)*(?:" + _BEHAVIOR + r"|" + _TRAIT + r")\b"
    r"|(?:\s+(?:are|is|were|have|has|do|does))?\s+(?:" + _SWEEP_ADVERB + r")\b"
    r")",
    re.IGNORECASE,
)
# Words in front of the group that make the PRODUCT the actor and the group its target ("Reach shoppers ...",
# "Connects with viewers who ...", "... when people search ..."): a mechanism, not a claim about the group.
_ACTS_ON = re.compile(
    r"\b(?:reach(?:es|ing)?|target(?:s|ing)?|connect(?:s|ing)?|engag(?:e|es|ing)|attract(?:s|ing)?|put(?:s|ting)?|"
    r"get(?:s|ting)?|show(?:s|ing)?|serv(?:e|es|ed|ing)|deliver(?:s|ed|ing)?|bring(?:s|ing)?|help(?:s|ing)?|let(?:s|ting)?|"
    r"so|to|for|with|when|as|while|that|who|if|once|because|and|but|by|from|before|after|whom|through|across|"
    r"in front of|ahead of)\b",
    re.IGNORECASE,
)
# The behavior's object is the ad itself ("viewers watch the full ad", "users click your ad"): how the format works,
# not a habit claim. Matched right AFTER the behavior word only.
_AD_OBJECT_AFTER = re.compile(
    r"^\s*(?:the|your|an?|each|every|that|this)\s+(?:[\w-]+\s+){0,2}?"
    r"(?:ads?|spots?|messages?|commercials?|offers?|banners?|creative|impressions?|pre-?rolls?|promotions?)\b",
    re.IGNORECASE,
)

_QUANT_GROUP = re.compile(
    r"\b(?:" + _QUANT + r")\s+(?:[\w.'’-]+\s+){0,3}?(?:" + _GROUP + r")\b(?:\s+[\w'’-]+){0,4}?\s+"
    r"(?:(?:are|is|have|has|do|does)\s+)?(?:(?:" + _ADVERB + r")\s+)*(?:" + _BEHAVIOR + r"|" + _TRAIT + r")\b",
    re.IGNORECASE,
)
_WHERE_GROUP = re.compile(
    r"\b(?:where|how|why|what|which)\s+(?:[\w.'’-]+\s+){0,2}?(?:" + _GROUP + r")\b(?:\s+[\w'’-]+){0,3}?\s+"
    r"(?:(?:" + _ADVERB + r")\s+)*(?:" + _BEHAVIOR + r")\b",
    re.IGNORECASE,
)
_POPULAR_WITH = re.compile(
    r"\b(?:popular|preferred|favou?rite|trusted|loved|go-to|valued|common|widespread|prevalent|mainstream|in demand|"
    r"growing in popularity|resonat\w*)\s+(?:with|among|amongst|for|in)\s+(?:[\w.'’-]+\s+){0,3}?(?:" + _GROUP + r")\b"
    r"|\bamong(?:st)?\s+(?:[\w.'’-]+\s+){0,3}?(?:" + _GROUP + r")\b",
    re.IGNORECASE,
)
_FILLER_TRAIT = re.compile(
    r"\b(?:(?:highly|very|deeply|extremely|incredibly|uniquely)\s+)?(?:loyal|passionate|receptive|responsive|"
    r"attentive|captive|affluent|influential|tech-savvy)\s+(?:[\w-]+\s+)?(?:" + _AUDIENCE_NOUN + r")\b"
    # "engaged" is also plain mechanics ("retarget engaged users who opened your email"): flagged only when
    # intensified, or when it is the audience itself that is called engaged.
    r"|\b(?:highly|very|deeply|extremely|incredibly|uniquely)\s+engaged\b"
    r"|\bengaged\s+(?:[\w-]+\s+)?(?:audiences?|communit(?:y|ies)|fans?|followers?)\b",
    re.IGNORECASE,
)
_MARKET = re.compile(
    r"\b(?:fastest|fast|rapidly|quickly)[- ]growing\b"
    r"|\bgrowing (?:rapidly|fast|quickly|steadily|exponentially)\b"
    r"|\b(?:booming|surging|skyrocketing|exploding|soaring)\b"
    r"|\b(?:continues?|continuing|continued) to (?:grow|rise|surge|climb|expand|dominate)\b"
    r"|\b(?:rising|growing|surging|increasing|soaring|record) (?:demand|popularity|adoption|usage|listenership|viewership|"
    r"consumption|spend|spending|share|penetration)\b"
    r"|\b(?:is|are|has been|have been|remains?|continues to be|becoming|became) (?:now )?(?:the )?(?:fastest|largest|biggest|"
    r"most (?:popular|watched|used|trusted|listened[- ]to|effective|engaging|powerful)|#1|number[- ]one|go-to|preferred)\b"
    r"|\b(?:market|category|industry|segment) (?:leader|share|size)\b"
    r"|\b(?:largest|biggest|most popular) (?:[\w-]+\s+){0,3}(?:platform|channel|medium|network|market|segment|category|audience|"
    r"source|destination|way)\b",
    re.IGNORECASE,
)
_PERFORMANCE = re.compile(
    r"\b(?:outperform\w*|out-perform\w*|over-?index\w*)\b"
    r"|\b(?:higher|better|stronger|greater|improved) (?:conversion|engagement|response|retention|recall|completion|"
    r"click-through|ctr|roi|return|results|performance|brand lift)\b"
    r"|\b(?:best|top)[- ]performing\b"
    r"|\b(?:trusted|highly engaging|highly effective|proven|powerful|impactful)(?:,?\s+(?:and\s+)?[\w-]+){0,2}\s+"
    r"(?:channel|medium|platform|format)\b",
    re.IGNORECASE,
)


def _group_led(low: str) -> bool:
    """The sentence's subject is the group and it is doing something: "Hispanics are always researching ...",
    "Podcast listeners trust host recommendations", "Many Latinos prefer streaming audio"."""
    m = _GROUP_RE.search(low)
    if not m:
        return False
    head = low[:m.start()]
    if len(head.split()) > 5 or _ACTS_ON.search(head):
        return False
    end = m.end()
    while True:                                   # "Hispanic households": the subject is the whole noun phrase
        nxt = _NEXT_GROUP.match(low, end)
        if not nxt:
            break
        end = nxt.end()
    tail = low[end:]
    agent = _AGENT_TAIL.match(tail[:100])
    if not agent:
        return False
    if _AD_OBJECT_AFTER.match(tail[agent.end():]):
        return False
    said = agent.group(0)
    # An attitude / trait, a generalizing adverb, or a demographic group makes it a claim; a role doing something
    # neutral with a medium ("Listeners tune in through apps") is just a description of how the medium is used.
    return bool(_ATTITUDE_WORD.search(said) or _SWEEP_WORD.search(said) or _DEMOGRAPHIC.search(low[m.start():end]))


def _claim_kind(sentence: str) -> Optional[str]:
    """Which kind of audience / behavior / market / performance claim the sentence makes, else None."""
    low = " " + source_rules.remove_citation_groups(sentence).lower().strip() + " "
    low = low.strip()
    if _group_led(low):
        return "audience behavior"
    if _QUANT_GROUP.search(low):
        return "audience generalization"
    if _WHERE_GROUP.search(low):
        return "audience behavior"
    if _POPULAR_WITH.search(low):
        return "audience preference"
    if _FILLER_TRAIT.search(low):
        return "audience trait"
    if _MARKET.search(low):
        return "market claim"
    if _PERFORMANCE.search(low):
        return "performance claim"
    return None


def has_statistic(sentence: str) -> bool:
    return bool(_STAT.search(sentence or ""))


def is_unsupported_claim(sentence: str) -> bool:
    """See the module docstring. Never raises; an empty sentence is not a claim."""
    s = (sentence or "").strip()
    if not s:
        return False
    cited = source_rules.has_named_citation(s)
    stat = has_statistic(source_rules.remove_citation_groups(s))
    if stat and not cited:
        return True                     # a number nobody can check
    if _claim_kind(s) is not None and not (stat and cited):
        return True                     # a sweeping claim: it needs the number AND the source, or it goes
    return False
