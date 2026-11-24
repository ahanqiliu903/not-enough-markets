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
        """Called once per trade when its market settles. Win counts per side are already
        in `ctx.stat(side)`; override to learn anything else. For a trade closed early by
        an exit this comes at the market's settlement, with `won` = whether the side won
        at settlement; its `realized_pnl` and `settled_at` stay the exit's."""

    def feeds_used(self) -> Sequence[str]:
        """Feed names this signal reads, checked against the portfolio at startup."""
        return ()
