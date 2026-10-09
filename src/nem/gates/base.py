from collections.abc import Sequence
from typing import ClassVar

from nem.core.context import Context
from nem.core.registry import Plugin, PluginParams
from nem.core.types import Decision, MarketSnapshot, Signal


class GateParams(PluginParams):
    # What happens if this gate's own data is unavailable (it raises DataUnavailable or
    # errors): fail open = let the trade through, fail closed = block it. Both are logged.
    fail_open: bool = False


class Gate(Plugin):
    """A veto on a signal. Gates run in order and the first `skip` wins.

    Gates must only use data known at signal time, through `ctx`. Never build a gate from
    the outcomes of the trades it gates: once it blocks, no new trades arrive to unblock
    it, and it can freeze forever. Feed it from ungated sources instead.
    """

    Params: ClassVar[type[PluginParams]] = GateParams

    def __init__(self, params: GateParams) -> None:
        self.fail_open = params.fail_open

    def check(self, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
        raise NotImplementedError

    def feeds_used(self) -> Sequence[str]:
        return ()
