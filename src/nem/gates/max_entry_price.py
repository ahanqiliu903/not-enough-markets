"""Never pay more than `value`. The paper and live brokers send the signal's price as an
immediate-or-cancel limit, so this cap also holds for the actual fill."""

from pydantic import Field

from nem.core.context import Context
from nem.core.registry import register
from nem.core.types import Decision, MarketSnapshot, Signal
from nem.gates.base import Gate, GateParams


@register("gate", "max_entry_price")
class MaxEntryPrice(Gate):
    class Params(GateParams):
        value: float = Field(gt=0, lt=1)

    def __init__(self, params: Params) -> None:
        super().__init__(params)
        self.value = params.value

    def check(self, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
        if signal.limit_price > self.value:
            return Decision.skip(self.name)
        return Decision.ok()
