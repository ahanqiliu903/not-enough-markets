"""Replay a history file: one tick per minute with every open market's latest quote.

The history file stores a snapshot only when a market's top of book changed
(`nem.history.db`); this source fills the minutes in between with the last quote, so a
strategy sees every open market at every minute, as a one-minute poller would have.

Markets are chosen by **market date** (the date in the event ticker), not by tick time, so
a train period and a test period never share a market even though a market opens the day
before its date. Each market is replayed over its whole life.

Trade prints back the `trade_through` take-profit model (`traded_volume`).
"""

import bisect
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta

from nem.core.types import MarketSnapshot, Settlement, Side
from nem.history.db import HistoryDb, Print
from nem.market.source import Tick

EPS = 1e-9


class HistorySource:
    def __init__(
        self,
        db: HistoryDb,
        start: date | None = None,
        end: date | None = None,
        *,
        step: timedelta = timedelta(minutes=1),
    ) -> None:
        if step <= timedelta(0):
            raise ValueError("step must be positive")
        self._db = db
        self._start = start
        self._end = end
        self._step = step
        self._prints: dict[str, tuple[list[datetime], list[Print]]] = {}

    def tickers(self, series: Sequence[str]) -> set[str]:
        return {m.ticker for m in self._db.markets(series, self._start, self._end)}

    def ticks(self, series: Sequence[str]) -> Iterator[Tick]:
        markets = self._db.markets(series, self._start, self._end)
        if not markets:
            return
        wanted = {m.ticker for m in markets}
        first = min(m.open_time for m in markets)
        last = max(m.close_time for m in markets)
        rows = (s for s in self._db.store.iter_snapshots(series, first, last) if s.ticker in wanted)
        pending = next(rows, None)
        state: dict[str, MarketSnapshot] = {}
        t = first.replace(second=0, microsecond=0)
        while t < last:
            while pending is not None and pending.ts <= t:
                state[pending.ticker] = pending
                pending = next(rows, None)
            for ticker in [k for k, s in state.items() if s.close_time <= t]:
                del state[ticker]
            if state:
                snaps = [s if s.ts == t else replace(s, ts=t) for s in state.values()]
                yield Tick(t, sorted(snaps, key=lambda s: s.ticker))
            t += self._step

    def result(self, ticker: str, as_of: datetime) -> Settlement | None:
        return self._db.store.settlement(ticker, as_of)

    def _ticker_prints(self, ticker: str) -> tuple[list[datetime], list[Print]]:
        got = self._prints.get(ticker)
        if got is None:
            prints = self._db.prints([ticker])[ticker]
            got = ([p.created_time for p in prints], prints)
            self._prints[ticker] = got
        return got

    def traded_volume(
        self, ticker: str, side: Side, price: float, after: datetime, until: datetime
    ) -> float:
        """Contracts of `side` printed at or above `price` in (after, until]. A print's
        price for NO is its `no_price` (1 - the YES price)."""
        times, prints = self._ticker_prints(ticker)
        lo = bisect.bisect_right(times, after)
        hi = bisect.bisect_right(times, until)
        return sum(p.count for p in prints[lo:hi] if p.price(side) >= price - EPS)
