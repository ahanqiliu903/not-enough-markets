"""Read-only view that signals and gates get. They never touch the network or the raw
store, so replay behaves exactly like live.

Causality: everything here is filtered to what was known *before* `now()`. A gate that
reads the same window's result is a look-ahead bug that makes backtests look far better
than anything tradeable, so this is the only door to past results.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from nem.core.clock import Clock
from nem.core.config import PortfolioConfig, StrategyConfig
from nem.core.types import StrategyKey, Trade
from nem.store import Store


@dataclass(frozen=True)
class Context:
    portfolio: PortfolioConfig
    strategy: StrategyConfig
    clock: Clock
    _store: Store

    @property
    def key(self) -> StrategyKey:
        return (self.portfolio.name, self.strategy.name)

    def now(self) -> datetime:
        return self.clock.now()

    def settled_trades(
        self, strategies: Sequence[StrategyKey] | None = None, limit: int | None = None
    ) -> list[Trade]:
        """Trades settled strictly before now, oldest first. Defaults to this strategy;
        pass other (portfolio, strategy) keys for cross-strategy gates."""
        return self._store.settled_trades(
            strategies=strategies if strategies is not None else [self.key],
            before=self.now(),
            limit=limit,
        )

    def stat(self, key: str) -> tuple[int, int]:
        """This strategy's (wins, n) for `key`. Only settlement (which runs on the clock)
        updates stats, so they never include windows that haven't closed."""
        return self._store.stat(*self.key, key)
