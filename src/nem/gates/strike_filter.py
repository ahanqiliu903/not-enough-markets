"""Trade only some strike types, e.g. `[between]` for the inner brackets of a daily
temperature event, or `[greater]` for the top one ("53 or above").

A market without a known strike type is always blocked: the filter can't tell whether
it's allowed. That is a data problem the market itself can't fix, so `fail_open` doesn't
apply to it (as with `city_filter`).
"""

from pydantic import Field

from nem.core.context import Context
from nem.core.registry import register
from nem.core.types import Decision, MarketSnapshot, Signal, StrikeType
from nem.gates.base import Gate, GateParams


@register("gate", "strike_filter")
class StrikeFilter(Gate):
    class Params(GateParams):
        strike_types: list[StrikeType] = Field(min_length=1)

    def __init__(self, params: Params) -> None:
        super().__init__(params)
        self.allowed = frozenset(params.strike_types)

    def check(self, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
        if snap.strike_type is None:
            return Decision.skip(f"{self.name}:unknown")
        if snap.strike_type not in self.allowed:
            return Decision.skip(f"{self.name}:{snap.strike_type}")
        return Decision.ok()
