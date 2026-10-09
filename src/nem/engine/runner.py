"""One process runs every portfolio and strategy.

Each tick (one market poll, live or replayed): settle resolved trades, accrue interest,
fetch due feeds (live only), then let every active strategy whose `check_every` has
elapsed evaluate the open markets of its series.

Live and replay differ only in the source, the clock, and whether feeds are fetched (in
replay they were recorded, so they're read from the data store).
"""

import logging
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime

import httpx

from nem.core.clock import Clock, ManualClock
from nem.core.config import PortfolioConfig
from nem.core.context import Context
from nem.core.types import FeedValue, Trade
from nem.engine.runtime import FeedRuntime, StrategyRuntime, build_feeds, build_strategies
from nem.engine.settlement import accrue_interest, settle_due
from nem.engine.step import evaluate
from nem.market.kalshi import KalshiError
from nem.market.source import MarketSource, Tick
from nem.store import Store

log = logging.getLogger(__name__)

PROCESS = "runner"


class Runner:
    def __init__(
        self,
        portfolios: Sequence[PortfolioConfig],
        source: MarketSource,
        store: Store,
        clock: Clock,
        *,
        data: Store | None = None,
        fetch_feeds: bool = True,
    ) -> None:
        self.portfolios = list(portfolios)
        self.strategies: list[StrategyRuntime] = build_strategies(self.portfolios)
        self._fetch = fetch_feeds
        self.feeds: list[FeedRuntime] = build_feeds(self.portfolios) if fetch_feeds else []
        self.source = source
        self.store = store
        self.data = data or store
        self.clock = clock
        self._by_key = {rt.key: rt for rt in self.strategies}

    @property
    def series(self) -> list[str]:
        return sorted({rt.config.series for rt in self.strategies if rt.config.status == "active"})

    def context(self, rt: StrategyRuntime) -> Context:
        return Context(rt.portfolio, rt.config, self.clock, self.store, self.data)

    def _on_settle(self, trade: Trade) -> None:
        rt = self._by_key.get((trade.portfolio, trade.strategy))
        if rt is not None:
            rt.signal.on_settle(trade, self.context(rt))

    def _equity_at(self, p: PortfolioConfig, at: datetime) -> float:
        start = p.starting_balance or p.budget or 0.0
        realized = sum(
            t.realized_pnl or 0.0 for t in self.store.settled_trades(portfolio=p.name, before=at)
        )
        return start + realized + self.store.ledger_total(p.name, before=at)

    def _fetch_feeds(self, now: datetime) -> None:
        for fr in self.feeds:
            if fr.last_fetch is not None and now - fr.last_fetch < fr.spec.every:
                continue
            fr.last_fetch = now
            try:
                obs = fr.feed.fetch(now)
            except Exception:
                log.exception("feed %s failed", fr.spec.name)
                continue
            values = [FeedValue(fr.spec.name, k, v, known_at) for k, v, known_at in obs]
            self.data.record_feed_values(values, recorded_at=now)

    def on_tick(self, tick: Tick) -> list[Trade]:
        now = self.clock.now()
        settle_due(self.store, self.source, now, self._on_settle)
        accrue_interest(self.store, self.portfolios, now, self._equity_at)
        self._fetch_feeds(now)
        trades: list[Trade] = []
        for rt in self.strategies:
            if rt.config.status != "active":
                continue
            if self.store.halted(*rt.key) is not None:
                continue  # kill switch: no new entries; open trades still settle above
            every = rt.portfolio.check_every_for(rt.config)
            if rt.last_check is not None and now - rt.last_check < every:
                continue
            snaps = [
                s
                for s in tick.snapshots
                if s.series == rt.config.series and s.open_time <= now < s.close_time
            ]
            if not snaps:
                continue
            rt.last_check = now
            ctx = self.context(rt)
            for snap in snaps:
                trade = evaluate(rt, snap, ctx, self.store)
                if trade is not None:
                    trades.append(trade)
        self.store.heartbeat(now, PROCESS, "ok")
        return trades

    def reload(self, portfolios: Sequence[PortfolioConfig]) -> None:
        """Swap in edited portfolio files without restarting. Building the new strategies
        happens first, so an invalid edit raises and leaves the running set untouched.
        Per-strategy state (last check, logged skips) carries over by key."""
        strategies = build_strategies(portfolios)
        feeds = build_feeds(portfolios) if self.feeds or any(p.feeds for p in portfolios) else []
        for rt in strategies:
            old = self._by_key.get(rt.key)
            if old is not None:
                rt.last_check, rt.logged = old.last_check, old.logged
        old_feeds = {f.spec.name: f for f in self.feeds}
        for fr in feeds:
            if fr.spec.name in old_feeds:
                fr.last_fetch = old_feeds[fr.spec.name].last_fetch
        self.portfolios = list(portfolios)
        self.strategies = strategies
        self.feeds = feeds if self._fetch else []
        self._by_key = {rt.key: rt for rt in strategies}

    def run(self, ticks: Iterable[Tick]) -> None:
        """Process ticks until the source ends (replay) or forever (live). With a manual
        clock, the clock follows the recorded tick times."""
        for tick in ticks:
            if isinstance(self.clock, ManualClock):
                self.clock.set(tick.ts)
            self.on_tick(tick)

    def settle_remaining(self, until: datetime) -> None:
        """Replay only: after the last tick, move the clock forward and settle every trade
        whose result is known by `until`."""
        if isinstance(self.clock, ManualClock) and until > self.clock.now():
            self.clock.set(until)
        settle_due(self.store, self.source, self.clock.now(), self._on_settle)


def run_live(
    runner: Runner,
    poll: Callable[[], Tick],
    interval: float,
    iterations: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Live loop: poll, process, sleep. Network errors skip that poll; they never stop the
    loop (a silently dead bot was one of the costliest failures in the original system)."""
    n = 0
    while iterations is None or n < iterations:
        started = time.monotonic()
        try:
            runner.on_tick(poll())
        except (KalshiError, httpx.HTTPError) as e:
            log.warning("tick failed: %s", e)
            runner.store.heartbeat(runner.clock.now(), PROCESS, f"error: {e}"[:200])
        n += 1
        if iterations is None or n < iterations:
            sleep(max(0.0, interval - (time.monotonic() - started)))
