"""
The win rate Constanza applies to programmatic (MadHive / The Trade Desk) products.

WHY
---
The avails a programmatic platform reports are BID OPPORTUNITIES — impressions that exist and could be bought — not
impressions Entravision will deliver. Entravision wins only a share of the auctions it bids on (typically 10-20%), so a
planner who treats the raw avails as "max recommended monthly impressions" overstates what a line can deliver by 5-10x.
Constanza therefore multiplies the avails of every product bought through MadHive or The Trade Desk by a win rate, and those
of Prime Video (bought through the Amazon DSP) by a rate of its own. Roku Ads (bought in Roku's own platform) and TikTok's
audience API are not bought this way and are untouched, as are population-share estimates (those spread the planner's OWN
impression total; there are no avails in them). Which products are which is decided in constanza_forecast_map.win_rate_applies
(and can be corrected per product in Admin -> Forecast).

WHAT IS APPLIED
---------------
  * impressions (low / mid / high): ALL THREE are multiplied by the MID win rate (15%). The planner asked for the median to be
    factorized by the win rate; Low / High keep meaning what they always did (the platform's own planning band around the
    avails), just on the smaller number. The 10-20% experience range is NOT folded into that band (folding two uncertainties
    together made a Low below "10% of the avails", which read as a mistake): it is shown as its own sensitivity — what the
    delivery would be at the low and at the high win rate — next to the estimate.
  * unique IDs reached: NOT multiplied by the win rate — fewer impressions reach fewer distinct IDs, but not 6.7x fewer.
    A reached ID has n = avails / est_reach bid opportunities a month; winning each one independently with probability w
    reaches it with probability 1 - (1 - w)^n, so reached = est_reach x (1 - (1 - w)^n). That is capped at the impressions
    delivered (every reached ID needs at least one), so unique IDs are never above impressions and frequency is never below 1.
    It is a model (it assumes opportunities are spread evenly over the reached IDs, which slightly overstates reach), stated as such.
  * the reach universe (the pool of IDs the product can reach) is a property of the market, not of delivery: unchanged.

PRIME VIDEO (AMAZON DSP)
------------------------
Prime Video has ONE rate, 20% (CONSTANZA_WIN_RATE_PRIME), the planner's assumption for buying through the Amazon DSP: the top of
the 10-20% they see programmatically, because the DSP and the inventory belong to the same company. It is a single figure, so
the card shows no 10%-20% sensitivity for it. Unlike the MadHive / Trade Desk avails (a platform's bid opportunities), the
workbook's Prime figure is the platform's total delivered ad impressions in the market (after a 90% fill), so for Prime the rate
is best read as "the share of Prime Video's ad volume Entravision is assumed to win" — a planning ceiling to calibrate against an
Amazon DSP forecast, not an auction statistic.

CONFIGURATION
-------------
Defaults: LOW 10%, MID 15%, HIGH 20% (the planner's "typically 10-20%"). Override with the environment variables
CONSTANZA_WIN_RATE_LOW / _MID / _HIGH. A value with a % sign is a percent ("1%" is one percent, "0.5%" is half a percent); one
without is a percent from 1 up ("15" is 15%) and a fraction below 1 ("0.15" is 15%). Set only some of the three and the rest
follow (MID=8 alone gives 8% with the default 20% high and a low that never exceeds it). A set that is still not
0 < low <= mid <= high <= 1 is ignored with a warning and the defaults are used — a typo must never silently zero every
estimate. Set all three equal for a single win rate with no sensitivity range. CONSTANZA_WIN_RATE_PRIME takes the same notations as
one value (an unreadable or out-of-range one is ignored with a warning and the 20% default is used).
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_LOW, DEFAULT_MID, DEFAULT_HIGH = 0.10, 0.15, 0.20

ENV_LOW, ENV_MID, ENV_HIGH = "CONSTANZA_WIN_RATE_LOW", "CONSTANZA_WIN_RATE_MID", "CONSTANZA_WIN_RATE_HIGH"

# Series bought somewhere with a win rate of its own: {series key: (environment variable, default)}. A single figure, no range.
SERIES_RATES: dict[str, tuple[str, float]] = {"prime": ("CONSTANZA_WIN_RATE_PRIME", 0.20)}


@dataclass(frozen=True)
class WinRate:
    low: float
    mid: float
    high: float

    def to_public(self) -> dict:
        return {"low": self.low, "mid": self.mid, "high": self.high}


def _parse(raw: str | None) -> float | None:
    """A rate as a fraction; None when unset or blank; ValueError when unreadable. "%" always means percent; otherwise 1 or more is a
    percent and below 1 a fraction (a 100% win rate is never what someone typing "1" meant)."""
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip()
    percent = text.endswith("%")
    value = float(text.rstrip("%").strip())
    if not math.isfinite(value):
        raise ValueError(raw)
    return value / 100.0 if (percent or value >= 1.0) else value


def configured() -> WinRate:
    """The win rate in force: the environment's, when it is a sane set, else the defaults."""
    try:
        low = _parse(os.environ.get(ENV_LOW))
        mid = _parse(os.environ.get(ENV_MID))
        high = _parse(os.environ.get(ENV_HIGH))
    except (TypeError, ValueError):
        log.warning("Ignoring unreadable %s / %s / %s — using the default win rate", ENV_LOW, ENV_MID, ENV_HIGH)
        return WinRate(DEFAULT_LOW, DEFAULT_MID, DEFAULT_HIGH)
    if low is None and mid is None and high is None:
        return WinRate(DEFAULT_LOW, DEFAULT_MID, DEFAULT_HIGH)
    # Fill whatever was not set so the three stay in order around what was: a lone MID of 8% keeps the default high and gets a
    # low no higher than itself; a lone LOW of 18% lifts the mid to it; a lone HIGH of 12% pulls the mid down to it.
    if mid is None:
        mid = min(max(DEFAULT_MID, low if low is not None else 0.0), high if high is not None else 1.0)
    if low is None:
        low = min(DEFAULT_LOW, mid)
    if high is None:
        high = max(DEFAULT_HIGH, mid)
    if not (0.0 < low <= mid <= high <= 1.0):
        log.warning("Ignoring win rate low=%s mid=%s high=%s (need 0 < low <= mid <= high <= 1) — using the default", low, mid, high)
        return WinRate(DEFAULT_LOW, DEFAULT_MID, DEFAULT_HIGH)
    return WinRate(low, mid, high)


def series_rate(series_key: str | None) -> WinRate | None:
    """The single win rate of a series that has one of its own (Prime Video through the Amazon DSP), else None."""
    entry = SERIES_RATES.get(series_key or "")
    if entry is None:
        return None
    env, default = entry
    try:
        rate = _parse(os.environ.get(env))
    except (TypeError, ValueError):
        rate = -1.0
    if rate is None:
        rate = default
    elif not 0.0 < rate <= 1.0:
        log.warning("Ignoring %s=%r (need 0 < rate <= 1) — using the default %s", env, os.environ.get(env), default)
        rate = default
    return WinRate(rate, rate, rate)


def for_series(series_key: str | None) -> WinRate:
    """The win rate to apply to a product forecast from this series: the series' own when it has one, else the programmatic set."""
    return series_rate(series_key) or configured()


def pct_text(x: float) -> str:
    """0.15 -> "15%", 0.125 -> "12.5%", 0.1 -> "10%" (never rounds a fractional rate to a whole one)."""
    return f"{x * 100:.1f}".rstrip("0").rstrip(".") + "%"


def scale(raw_low: float, raw_mid: float, raw_high: float, wr: WinRate) -> tuple[float, float, float]:
    """(low, mid, high) impressions after the win rate: all three times the MID rate (module docstring)."""
    if not raw_mid or raw_mid <= 0:
        return 0.0, 0.0, 0.0
    return raw_low * wr.mid, raw_mid * wr.mid, raw_high * wr.mid


def reached_ids(est_reach: float | None, imps_per_period: float, win_rate: float) -> float | None:
    """Unique IDs reached in a period once only `win_rate` of the bid opportunities are won (module docstring). Never above
    the platform's own est. reach, never above the impressions delivered."""
    if est_reach is None:
        return None
    if est_reach <= 0 or imps_per_period <= 0 or win_rate <= 0:
        return 0.0
    opportunities_each = imps_per_period / est_reach
    reached = est_reach * (1.0 - (1.0 - win_rate) ** opportunities_each)
    return max(0.0, min(reached, est_reach, imps_per_period * win_rate))


def cap_uniques(uniques: float | None, imps_per_period: float) -> float | None:
    """Unique IDs can never exceed the impressions delivered to them (each needs at least one)."""
    if uniques is None:
        return None
    return min(uniques, max(imps_per_period, 0.0))
