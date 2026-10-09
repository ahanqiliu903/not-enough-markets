from dataclasses import dataclass

from nem.core.context import Context
from nem.core.registry import Plugin
from nem.core.types import Signal
from nem.execution.fees import fee_per_contract


@dataclass(frozen=True, slots=True)
class Sizing:
    qty: int
    reason: str  # logged with every trade, so a surprising size is always explained


class Sizer(Plugin):
    def size(self, signal: Signal, ctx: Context) -> Sizing:
        raise NotImplementedError


def cost_per_contract(signal: Signal, ctx: Context) -> float:
    return signal.limit_price + fee_per_contract(signal.limit_price, ctx.strategy.taker_fee_rate)
