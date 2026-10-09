"""Built-in sizers: fixed contracts, percent of equity, fractional Kelly."""

import math

from pydantic import Field, PositiveInt

from nem.core.context import Context
from nem.core.registry import PluginParams, register
from nem.core.types import Signal
from nem.sizing.base import Sizer, Sizing, cost_per_contract


@register("sizer", "fixed")
class Fixed(Sizer):
    class Params(PluginParams):
        contracts: PositiveInt

    def __init__(self, params: Params) -> None:
        self.contracts = params.contracts

    def size(self, signal: Signal, ctx: Context) -> Sizing:
        return Sizing(self.contracts, f"fixed {self.contracts}")


@register("sizer", "percent")
class Percent(Sizer):
    """Spend `percent`% of portfolio equity per trade."""

    class Params(PluginParams):
        percent: float = Field(gt=0, le=100)

    def __init__(self, params: Params) -> None:
        self.percent = params.percent

    def size(self, signal: Signal, ctx: Context) -> Sizing:
        equity, cost = ctx.equity(), cost_per_contract(signal, ctx)
        qty = math.floor(self.percent / 100 * equity / cost)
        return Sizing(qty, f"{self.percent:g}% of ${equity:.2f} at ${cost:.4f}/contract")


@register("sizer", "kelly")
class Kelly(Sizer):
    """Fractional Kelly for a binary contract. Paying c per contract (price + fee) to win 1
    with probability q, the Kelly fraction of equity is (q - c) / (1 - c).

    Kelly trusts `p_model` completely, so a small overestimate oversizes badly. Use a
    fraction (0.25 is common) and a hard `max_contracts` cap; the reason says when it binds.
    """

    class Params(PluginParams):
        fraction: float = Field(gt=0, le=1)
        max_contracts: PositiveInt

    def __init__(self, params: Params) -> None:
        self.p = params

    def size(self, signal: Signal, ctx: Context) -> Sizing:
        c, q = cost_per_contract(signal, ctx), signal.p_model
        if q <= c:
            return Sizing(0, f"no kelly edge (p={q:.4f} <= cost={c:.4f})")
        f = (q - c) / (1 - c)
        equity = ctx.equity()
        raw = math.floor(self.p.fraction * f * equity / c)
        if raw > self.p.max_contracts:
            return Sizing(self.p.max_contracts, f"kelly {raw} capped at {self.p.max_contracts}")
        return Sizing(raw, f"kelly {self.p.fraction:g}x f={f:.4f} of ${equity:.2f}")
