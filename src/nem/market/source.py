"""Market sources: where snapshots and settlements come from.

The engine only sees `MarketSource`, so swapping live Kalshi for recorded data (replay) or
scripted data (tests) changes nothing downstream.
"""

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import groupby
from typing import Protocol

from nem.core.clock import Clock, SystemClock
from nem.core.types import Depth, MarketSnapshot, Settlement
from nem.market.kalshi import KalshiClient, KalshiMarket
from nem.store import Store


@dataclass(frozen=True, slots=True)
class Tick:
    """Every snapshot from one poll, all stamped with the same time."""

    ts: datetime
    snapshots: Sequence[MarketSnapshot]


class MarketSource(Protocol):
    def ticks(self, series: Sequence[str]) -> Iterator[Tick]: ...

    def result(self, ticker: str, as_of: datetime) -> Settlement | None:
        """The market's outcome if it was known at `as_of`, else None."""
        ...


def _round(p: float) -> float:
    return round(p, 4)  # 1 - 0.932 == 0.06799999...; Kalshi's finest tick is 0.001


def to_snapshot(
    series: str, m: KalshiMarket, ts: datetime, book: Depth | None, depth: int
) -> MarketSnapshot:
    """Top of book comes from the orderbook when we have one (consistent with the depth),
    otherwise from the market listing."""
    snap = MarketSnapshot(
        ts=ts,
        series=series,
        window_id=m.event_ticker,
        ticker=m.ticker,
        open_time=m.open_time,
        close_time=m.close_time,
        yes_bid=m.yes_bid,
        yes_ask=m.yes_ask,
        no_bid=m.no_bid,
        no_ask=m.no_ask,
        strike_type=m.strike_type,
        floor_strike=m.floor_strike,
        cap_strike=m.cap_strike,
    )
    if book is None:
        return snap
    yes, no = book["yes"], book["no"]
    return replace(
        snap,
        yes_bid=yes[0][0] if yes else None,
        yes_ask=_round(1 - no[0][0]) if no else None,
        no_bid=no[0][0] if no else None,
        no_ask=_round(1 - yes[0][0]) if yes else None,
        depth={"yes": list(yes[:depth]), "no": list(no[:depth])},
    )


class KalshiSource:
    """Live polling. `depth=0` skips orderbook requests and records top of book only."""

    def __init__(
        self,
        client: KalshiClient,
        clock: Clock | None = None,
        *,
        interval: float = 5.0,
        depth: int = 10,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._clock = clock or SystemClock()
        self._interval = interval
        self._depth = depth
        self._sleep = sleep

    def poll(self, series: Sequence[str]) -> Tick:
        ts = self._clock.now()
        markets = [(s, m) for s in series for m in self._client.markets(s, status="open")]
        books = self._client.orderbooks([m.ticker for _, m in markets]) if self._depth else {}
        return Tick(
            ts, [to_snapshot(s, m, ts, books.get(m.ticker), self._depth) for s, m in markets]
        )

    def ticks(self, series: Sequence[str]) -> Iterator[Tick]:
        while True:
            started = time.monotonic()
            yield self.poll(series)
            self._sleep(max(0.0, self._interval - (time.monotonic() - started)))

    def result(self, ticker: str, as_of: datetime) -> Settlement | None:
        # Live is causal by construction: Kalshi only reports results that exist now.
        del as_of
        m = self._client.market(ticker)
        if m.result is None:
            return None
        series = m.event_ticker.partition("-")[0]
        return Settlement(ticker, series, m.event_ticker, m.result, m.settled_at or m.close_time)


class ReplaySource:
    """Recorded snapshots and settlements from the store, in time order."""

    def __init__(
        self, store: Store, start: datetime | None = None, end: datetime | None = None
    ) -> None:
        self._store = store
        self._start = start
        self._end = end

    def ticks(self, series: Sequence[str]) -> Iterator[Tick]:
        snaps = self._store.iter_snapshots(series, self._start, self._end)
        for ts, group in groupby(snaps, key=lambda s: s.ts):
            yield Tick(ts, list(group))

    def result(self, ticker: str, as_of: datetime) -> Settlement | None:
        return self._store.settlement(ticker, as_of)


class FakeSource:
    """Scripted ticks and settlements, for tests."""

    def __init__(self, ticks: Sequence[Tick], settlements: Sequence[Settlement] = ()) -> None:
        self._ticks = ticks
        self._settlements = {s.ticker: s for s in settlements}

    def ticks(self, series: Sequence[str]) -> Iterator[Tick]:
        for tick in self._ticks:
            snaps = [s for s in tick.snapshots if s.series in series]
            if snaps:
                yield Tick(tick.ts, snaps)

    def result(self, ticker: str, as_of: datetime) -> Settlement | None:
        s = self._settlements.get(ticker)
        return s if s is not None and s.settled_at <= as_of else None
