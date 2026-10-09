from collections.abc import Sequence

from nem.core.context import Context
from nem.core.registry import Plugin
from nem.core.types import MarketSnapshot, Signal, Trade


class SignalPlugin(Plugin):
    """Decides when to enter. Called once per open market each time the strategy checks.

    Return None for "no trade" (not logged; it happens most ticks). Returned signals are
    always logged, with whatever gate, sizer or risk check stopped them.
    """

    def on_market(self, snap: MarketSnapshot, ctx: Context) -> Signal | None:
        raise NotImplementedError

    def on_settle(self, trade: Trade, ctx: Context) -> None:
        """Called after one of this strategy's trades settles. Win counts per side are
        already in `ctx.stat(side)`; override to learn anything else."""

    def feeds_used(self) -> Sequence[str]:
        """Feed names this signal reads, checked against the portfolio at startup."""
        return ()
