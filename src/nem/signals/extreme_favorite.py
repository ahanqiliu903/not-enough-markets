"""Buy the heavy favorite when its estimated win rate beats the price after fees.

When one side's ask is at least `threshold`, estimate that side's win probability from
this strategy's own settled history (Bayesian: prior wins/n plus observed wins/n), and
enter if `p_model - ask - fee >= min_edge`. Break-even win rate is roughly ask + fee, so
at 90c you need to win about 91% of the time.

The prior matters most early on, so make it explicit and modest. An optimistic prior once
had a bot buying at 97c; a `max_entry_price` gate keeps the price ceiling independent of
whatever the model believes.
"""

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt

from nem.core.context import Context
from nem.core.registry import PluginParams, register
from nem.core.types import MarketSnapshot, Side, Signal
from nem.execution.fees import fee_per_contract
from nem.signals.base import SignalPlugin


class Prior(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    wins: NonNegativeInt = 0
    n: NonNegativeInt = 0


class Priors(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    yes: Prior = Prior()
    no: Prior = Prior()


@register("signal", "extreme_favorite")
class ExtremeFavorite(SignalPlugin):
    class Params(PluginParams):
        threshold: float = Field(gt=0.5, lt=1)
        min_edge: float = 0.0
        priors: Priors = Priors()

    def __init__(self, params: Params) -> None:
        self.p = params

    def on_market(self, snap: MarketSnapshot, ctx: Context) -> Signal | None:
        quotes: list[tuple[Side, float | None]] = [("yes", snap.yes_ask), ("no", snap.no_ask)]
        for side, ask in quotes:
            if ask is None or ask < self.p.threshold:
                continue
            prior = self.p.priors.yes if side == "yes" else self.p.priors.no
            wins, n = ctx.stat(side)
            if prior.n + n == 0:
                return None  # no prior and no history: no estimate, no trade
            p_model = (prior.wins + wins) / (prior.n + n)
            fee = fee_per_contract(ask, ctx.strategy.taker_fee_rate)
            edge = p_model - ask - fee
            if edge < self.p.min_edge:
                return None
            meta = {"ask": ask, "fee_per_contract": round(fee, 5), "edge": round(edge, 5), "n": n}
            return ctx.make_signal(snap, side, limit_price=ask, p_model=p_model, meta=meta)
        return None
