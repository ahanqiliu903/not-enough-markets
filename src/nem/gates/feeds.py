"""Gates driven by external feeds.

`feed_agrees`: the feed's current value must be on the same side of the market's strike
as the bet, with a safety margin. Buying YES on "BTC ends at or above $81,780" needs spot
comfortably above $81,780; buying NO on "NYC high is 75-76F" needs the forecast high
comfortably outside 75-76.

`feed_threshold`: veto unless a feed value is inside [min, max], e.g. skip when the
precipitation chance is above 60%.

Keys can contain `{date}`, replaced by the market's date from its event ticker (e.g.
KXHIGHNY-26OCT09 -> 2026-10-09), to pick the right day's forecast.
"""

import re
from collections.abc import Sequence
from datetime import date

from pydantic import Field, model_validator

from nem.core.clock import MONTHS
from nem.core.context import Context, DataUnavailable
from nem.core.registry import register
from nem.core.types import Decision, MarketSnapshot, Signal
from nem.gates.base import Gate, GateParams

_DATE_IN_TICKER = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")


def market_date(window_id: str) -> date | None:
    """The date in an event ticker: KXHIGHNY-26OCT09 and KXBTC15M-26OCT081430 -> Oct 8/9."""
    m = _DATE_IN_TICKER.search(window_id)
    if m is None or m.group(2) not in MONTHS:
        return None
    return date(2000 + int(m.group(1)), MONTHS.index(m.group(2)) + 1, int(m.group(3)))


def resolve_key(template: str, snap: MarketSnapshot) -> str:
    if "{date}" not in template:
        return template
    d = market_date(snap.window_id)
    if d is None:
        raise DataUnavailable(f"no date in {snap.window_id!r} for key {template!r}")
    return template.replace("{date}", d.isoformat())


@register("gate", "feed_agrees")
class FeedAgrees(Gate):
    class Params(GateParams):
        feed: str
        key: str = "value"
        margin: float = Field(default=0.0, ge=0)  # in the feed's units, e.g. 2 (degrees F)
        margin_pct: float = Field(default=0.0, ge=0)  # or % of the value, e.g. 0.1

    def __init__(self, params: Params) -> None:
        super().__init__(params)
        self.p = params

    def feeds_used(self) -> Sequence[str]:
        return [self.p.feed]

    def check(self, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
        value = ctx.feed(self.p.feed).require(resolve_key(self.p.key, snap)).value
        m = max(self.p.margin, abs(value) * self.p.margin_pct / 100)
        lo, hi = value - m, value + m
        # Check the ends of [value - m, value + m] plus any strike inside it, so a range
        # that straddles a bracket counts as disagreeing.
        points = [lo, value, hi, *(s for s in (snap.floor_strike, snap.cap_strike)
                                    if s is not None and lo < s < hi)]  # fmt: skip
        outcomes = {snap.resolves_yes(x) for x in points}
        if None in outcomes:
            raise DataUnavailable(f"{snap.ticker} has no usable strike")
        if outcomes == {signal.side == "yes"}:
            return Decision.ok()
        return Decision.skip(f"{self.name}:{self.p.feed}")


@register("gate", "feed_threshold")
class FeedThreshold(Gate):
    class Params(GateParams):
        feed: str
        key: str = "value"
        min: float | None = None
        max: float | None = None

        @model_validator(mode="after")
        def _bounds(self) -> "FeedThreshold.Params":
            if self.min is None and self.max is None:
                raise ValueError("set min, max, or both")
            if self.min is not None and self.max is not None and self.min > self.max:
                raise ValueError("min must be <= max")
            return self

    def __init__(self, params: Params) -> None:
        super().__init__(params)
        self.p = params

    def feeds_used(self) -> Sequence[str]:
        return [self.p.feed]

    def check(self, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
        value = ctx.feed(self.p.feed).require(resolve_key(self.p.key, snap)).value
        if self.p.max is not None and value > self.p.max:
            return Decision.skip(f"{self.name}:{self.p.feed}:above_max")
        if self.p.min is not None and value < self.p.min:
            return Decision.skip(f"{self.name}:{self.p.feed}:below_min")
        return Decision.ok()
