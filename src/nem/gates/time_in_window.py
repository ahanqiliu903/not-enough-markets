"""Only enter during part of the market's life, e.g. the last 5 minutes before close."""

from pydantic import Field, model_validator

from nem.core.context import Context
from nem.core.registry import register
from nem.core.types import Decision, MarketSnapshot, Signal
from nem.gates.base import Gate, GateParams


@register("gate", "time_in_window")
class TimeInWindow(Gate):
    class Params(GateParams):
        last_minutes: float | None = Field(default=None, gt=0)  # only this close to the end
        min_seconds_left: float = Field(default=0, ge=0)  # but not closer than this

        @model_validator(mode="after")
        def _consistent(self) -> "TimeInWindow.Params":
            if self.last_minutes is not None and self.min_seconds_left >= self.last_minutes * 60:
                raise ValueError("min_seconds_left must be less than last_minutes")
            return self

    def __init__(self, params: Params) -> None:
        super().__init__(params)
        self.p = params

    def check(self, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
        left = (snap.close_time - ctx.now()).total_seconds()
        if self.p.last_minutes is not None and left > self.p.last_minutes * 60:
            return Decision.skip(f"{self.name}:too_early")
        if left < self.p.min_seconds_left:
            return Decision.skip(f"{self.name}:too_late")
        return Decision.ok()
