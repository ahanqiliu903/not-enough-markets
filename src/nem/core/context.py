"""Read-only view that signals, gates and sizers get. They never touch the network or the
raw store, so replay behaves exactly like live.

Causality: everything here is filtered to what was known *before* `now()`. A gate that
reads the same window's result is a look-ahead bug that makes backtests look far better
than anything tradeable, so this is the only door to past results and external data.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from nem.backfill.model import SOURCES, known_before
from nem.core.clock import Clock
from nem.core.config import FeedSpec, PortfolioConfig, StrategyConfig
from nem.core.registry import REGISTRY, PluginError
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


ForecastProvenance = Literal["feed", "backfill"]
# Forecast fields a feed may publish under a different key name (`{date}/{key}`):
# nws_forecast calls the precipitation chance `precip`.
FEED_KEY_ALIASES: Mapping[str, tuple[str, ...]] = {"pop": ("pop", "precip")}


@dataclass(frozen=True, slots=True)
class Forecast:
    """One source's newest forecast of one field for one station-day, as known at now.

    `provenance` says where it came from: `feed` = recorded live by `nem run` (what was
    really knowable), `backfill` = an archive fetched after the fact
    (approximate; results resting on it must go through `nem.stats.provenance.cap_verdict`).
    """

    source: str  # feed name, or backfill source (a key of nem.backfill.SOURCES)
    value: float
    known_at: datetime
    provenance: ForecastProvenance
    run_id: str | None = None  # backfill only, e.g. "GFS MOS 2026-01-23T12Z"
    model: str | None = None  # forecast model (see `forecast_model`); one value per model


def forecast_model(spec: FeedSpec) -> str:
    """A feed's forecast model, named as in `nem.backfill.SOURCES` (`gfs`, `nbm`, ...), from
    its plugin's `Feed.forecast_model`; feeds that don't say are their own model."""
    from nem.feeds.base import Feed  # here: nem.feeds imports nem.core

    try:
        cls = REGISTRY.get("feed", spec.type)
    except PluginError:
        return f"feed:{spec.name}"
    if not issubclass(cls, Feed):
        return f"feed:{spec.name}"
    return cls.forecast_model(cls.Params.model_validate(spec.params)) or f"feed:{spec.name}"


def one_per_model(forecasts: Sequence[Forecast]) -> list[Forecast]:
    """Keep one forecast per model, so a model reached two ways (a live feed and its
    backfill, GFS MOS and raw GFS) counts once: a recorded feed value wins over backfill,
    then the newest `known_at`, then the first listed. Order of first appearance is kept."""
    best: dict[str, Forecast] = {}
    for f in forecasts:
        key = f.model or f"{f.provenance}:{f.source}"
        cur = best.get(key)
        if cur is None or (f.provenance == "feed", f.known_at) > (
            cur.provenance == "feed",
            cur.known_at,
        ):
            best[key] = f
    kept = {id(f) for f in best.values()}
    return [f for f in forecasts if id(f) in kept]


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
        pass other (portfolio, strategy) keys for cross-strategy gates. Trades excluded
        from statistics (`nem trade exclude`) are left out, as from every statistic."""
        return self._store.settled_trades(
            strategies=strategies if strategies is not None else [self.key],
            before=self.now(),
            limit=limit,
            include_excluded=False,
        )

    def portfolio_settled_trades(self) -> list[Trade]:
        """Every trade in this portfolio settled strictly before now, oldest first, except
        those excluded from statistics."""
        return self._store.settled_trades(
            portfolio=self.portfolio.name, before=self.now(), include_excluded=False
        )

    def settled_positions(self, portfolio_wide: bool = False) -> list[Trade]:
        """Money view: settled trades for equity and the money risk limits. Excluded trades
        are left out here too: excluding a trade means acting as if it was never taken."""
        if portfolio_wide:
            return self.portfolio_settled_trades()
        return self.settled_trades()

    def stat(self, key: str) -> tuple[int, int]:
        """This strategy's (wins, n) for `key`. Only settlement (which runs on the clock)
        updates stats, so they never include windows that haven't closed. Trades excluded
        from statistics don't count."""
        return self._store.stat(*self.key, key)

    def open_trades(self, portfolio_wide: bool = False) -> list[Trade]:
        """Open positions, except excluded ones (as if never taken: they hold no cash here
        and don't count toward risk limits)."""
        if portfolio_wide:
            return self._store.open_trades(portfolio=self.portfolio.name, include_excluded=False)
        return self._store.open_trades(strategy=self.key, include_excluded=False)

    # --- money ----------------------------------------------------------------

    def equity(self) -> float:
        """Portfolio value with open positions at cost: starting capital + realized P&L +
        interest."""
        start = self.portfolio.starting_balance or self.portfolio.budget or 0.0
        realized = sum(t.realized_pnl or 0.0 for t in self.settled_positions(portfolio_wide=True))
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

    def forecasts(
        self,
        day: date,
        field: str,
        *,
        feeds: Sequence[str] = (),
        station: str | None = None,
        backfill_sources: Sequence[str] = (),
        backfill_max_age: timedelta | None = None,
    ) -> list[Forecast]:
        """Every source's newest forecast of `field` (high, low, cloud, pop, ...) for
        climate day `day`, known at or before now.

        - `feeds`: forecast feeds declared in this portfolio, read at key `YYYY-MM-DD/field`
          (see `FEED_KEY_ALIASES`) and subject to each feed's `max_age`.
        - `backfill_sources`: rows of the `forecast_backfill` table for `station` (NWS CLI
          id), newest run per source with `known_at <= now` (`known_before`), dropped when
          older than `backfill_max_age`.

        Each forecast model counts once (`one_per_model`): when a feed and a backfill source
        (or two of either) carry the same model, the recorded feed value is kept if there is
        one, else the newest. Sources without a value are left out; callers decide how many
        they need.
        """
        now = self.now()
        out: list[Forecast] = []
        for name in feeds:
            view = self.feed(name)
            for key in FEED_KEY_ALIASES.get(field, (field,)):
                v = view.latest(f"{day.isoformat()}/{key}")
                if v is not None:
                    model = forecast_model(view.spec)
                    out.append(Forecast(name, v.value, v.known_at, "feed", model=model))
                    break
        if backfill_sources:
            if station is None:
                raise ValueError("backfilled forecasts need a station")
            wanted = set(backfill_sources)
            rows = (self._data or self._store).backfill_rows(station, day)
            for r in known_before(rows, now):
                if r.field != field or r.source not in wanted:
                    continue
                if backfill_max_age is not None and now - r.known_at > backfill_max_age:
                    continue
                model = SOURCES[r.source].model if r.source in SOURCES else None
                out.append(Forecast(r.source, r.value, r.known_at, "backfill", r.run_id, model))
        return one_per_model(out)

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
