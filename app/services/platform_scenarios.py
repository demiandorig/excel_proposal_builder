"""
Impression scenarios for the platforms that answer with an AUDIENCE SIZE instead of avails: TikTok's audience-size estimator and Meta's
reach estimate (Constanza's two live platform connections).

Neither platform says how many impressions a flight can deliver (that depends on budget, bid and creative). Each says how many accounts
match a targeting, as a range with a lower and an upper end. Entravision's planning rule turns that head count into impressions with
three scenarios, all taken from the LOWER end of the platform's range:

    conservative = lower-end audience x 4.3       the low estimate
    mid          = lower-end audience x 5.5       the default
    upper        = lower-end audience x 8.6       the high estimate

so a month's avails are the mid scenario unless the planner picks another. The lower end is the base on purpose: it is the figure the
platform stands behind (the upper end is the most the estimate would allow), and the planning multipliers were set against it.
The upper end of the platform's range is still shown as context; it is not a base.

The multipliers are the planning team's, not the platform's. Change them (together, they stay in order) with
CONSTANZA_AUDIENCE_X_CONSERVATIVE / CONSTANZA_AUDIENCE_X_MID / CONSTANZA_AUDIENCE_X_UPPER; a set that is not 0.1 <= conservative <= mid
<= upper <= 100 is ignored with a warning (the defaults are used) so a typo never half-applies.
"""
from __future__ import annotations

import logging
import math
import os

log = logging.getLogger(__name__)

SCENARIOS = ("conservative", "mid", "upper")
DEFAULT_MULTIPLIERS = {"conservative": 4.3, "mid": 5.5, "upper": 8.6}
ENV = {
    "conservative": "CONSTANZA_AUDIENCE_X_CONSERVATIVE",
    "mid": "CONSTANZA_AUDIENCE_X_MID",
    "upper": "CONSTANZA_AUDIENCE_X_UPPER",
}
# the scenario the estimate's headline and "Insert into Avails" use
DEFAULT_SCENARIO = "mid"
# what the three names are called in an estimate's low / mid / high slots
SLOT = {"conservative": "low", "mid": "mid", "upper": "high"}


def multipliers() -> dict[str, float]:
    """The three multipliers in force: the environment's when it is a sane set (unset ones keep their default), else the defaults."""
    out = dict(DEFAULT_MULTIPLIERS)
    for name in SCENARIOS:
        raw = os.environ.get(ENV[name])
        if raw is None or not raw.strip():
            continue
        try:
            value = float(raw.strip())
        except ValueError:
            log.warning("Ignoring %s=%r (not a number) - using the default multipliers", ENV[name], raw)
            return dict(DEFAULT_MULTIPLIERS)
        out[name] = value
    values = [out[n] for n in SCENARIOS]
    if not all(math.isfinite(v) and 0.1 <= v <= 100 for v in values) or not values[0] <= values[1] <= values[2]:
        log.warning("Ignoring the audience multipliers %s (need 0.1 <= conservative <= mid <= upper <= 100) - using the defaults", out)
        return dict(DEFAULT_MULTIPLIERS)
    return out


def monthly(lower_audience: float) -> dict[str, float]:
    """{"low", "mid", "high"} monthly impressions for a lower-end audience size: conservative / mid / upper."""
    m = multipliers()
    base = max(0.0, float(lower_audience or 0.0))
    return {SLOT[name]: base * m[name] for name in SCENARIOS}


def to_public(lower_audience: float | None = None) -> dict:
    """What the API tells a client about the rule (and, with an audience, what each scenario comes to a month)."""
    m = multipliers()
    out = {"basis": "lower_end", "default": DEFAULT_SCENARIO, "multipliers": dict(m)}
    if lower_audience is not None:
        out["monthly"] = {name: max(0.0, float(lower_audience)) * m[name] for name in SCENARIOS}
    return out


def describe() -> str:
    """The rule in a sentence, for notes and cards: "lower-end audience x 4.3 (conservative) / 5.5 (mid) / 8.6 (upper)"."""
    m = multipliers()
    return f"lower-end audience x {m['conservative']:g} (conservative) / {m['mid']:g} (mid) / {m['upper']:g} (upper)"
