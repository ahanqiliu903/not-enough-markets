"""Read-only view that signals, gates and sizers get. They never touch the network or the
raw store, so replay behaves exactly like live.

Causality: everything here is filtered to what was known *before* `now()`. A gate that
reads the same window's result is a look-ahead bug that makes backtests look far better
than anything tradeable, so this is the only door to past results and external data.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from nem.core.clock import Clock
from nem.core.config import FeedSpec, PortfolioConfig, StrategyConfig
from nem.core.types import FeedValue, MarketSnapshot, Side, Signal, StrategyKey, Trade
from nem.store import Store


class DataUnavailable(Exception):
    """Raised by a gate or signal when data it needs is missing or stale. The engine then
    applies the gate's `fail_open` setting (signals simply don't fire)."""


@dataclass(frozen=True)
class FeedView:
    """One external feed, as of now. Values older than the feed's `max_age` don't count."""

    spec: FeedSpec
    _store: Store
    _now: datetime

    def latest(self, key: str = "value") -> FeedValue | None:
        values = self._store.feed_values(self.spec.name, key, as_of=self._now, limit=1)
        if not values or self._now - values[0].known_at > self.spec.max_age:
            return None
        return values[0]

    def require(self, key: str = "value") -> FeedValue:
        v = self.latest(key)
        if v is None:
            raise DataUnavailable(f"feed {self.spec.name!r} has no fresh {key!r}")
        return v

    def history(self, key: str = "value", lookback: timedelta | None = None) -> list[FeedValue]:
        since = None if lookback is None else self._now - lookback
        return self._store.feed_values(self.spec.name, key, as_of=self._now, since=since)


@dataclass(frozen=True)
class Context:
    portfolio: PortfolioConfig
    strategy: StrategyConfig
    clock: Clock
    _store: Store  # trades, orders, stats (this run's results)
    _data: Store | None = None  # market and feed data; defaults to `_store`
    _feeds: Mapping[str, FeedSpec] = field(default_factory=dict[str, FeedSpec])

    def __post_init__(self) -> None:
        if not self._feeds:
            object.__setattr__(self, "_feeds", {f.name: f for f in self.portfolio.feeds})

    @property
    def key(self) -> StrategyKey:
        return (self.portfolio.name, self.strategy.name)

    def now(self) -> datetime:
        return self.clock.now()

    # --- past results -------------------------------------------------------

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

    def portfolio_settled_trades(self) -> list[Trade]:
        """Every trade in this portfolio settled strictly before now, oldest first."""
        return self._store.settled_trades(portfolio=self.portfolio.name, before=self.now())

    def stat(self, key: str) -> tuple[int, int]:
        """This strategy's (wins, n) for `key`. Only settlement (which runs on the clock)
        updates stats, so they never include windows that haven't closed."""
        return self._store.stat(*self.key, key)

    def open_trades(self, portfolio_wide: bool = False) -> list[Trade]:
        if portfolio_wide:
            return self._store.open_trades(portfolio=self.portfolio.name)
        return self._store.open_trades(strategy=self.key)

    # --- money ----------------------------------------------------------------

    def equity(self) -> float:
        """Portfolio value with open positions at cost: starting capital + realized P&L +
        interest."""
        start = self.portfolio.starting_balance or self.portfolio.budget or 0.0
        realized = sum(t.realized_pnl or 0.0 for t in self.portfolio_settled_trades())
        return start + realized + self._store.ledger_total(self.portfolio.name, before=self.now())

    def cash(self) -> float:
        """Equity minus what's tied up in open positions."""
        return self.equity() - sum(t.cost for t in self.open_trades(portfolio_wide=True))

    # --- external data ------------------------------------------------------------

    def feed(self, name: str) -> FeedView:
        try:
            spec = self._feeds[name]
        except KeyError:
            raise KeyError(
                f"feed {name!r} is not declared in portfolio {self.portfolio.name!r}"
            ) from None
        return FeedView(spec, self._data or self._store, self.now())

    # --- helpers --------------------------------------------------------------------

    def make_signal(
        self,
        snap: MarketSnapshot,
        side: Side,
        limit_price: float,
        p_model: float,
        meta: Mapping[str, object] | None = None,
    ) -> Signal:
        return Signal(
            portfolio=self.portfolio.name,
            strategy=self.strategy.name,
            window_id=snap.window_id,
            ticker=snap.ticker,
            side=side,
            limit_price=limit_price,
            p_model=p_model,
            meta=dict(meta or {}),
        )
