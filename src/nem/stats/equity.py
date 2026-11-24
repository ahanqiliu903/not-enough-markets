"""Portfolio value over time, for charts (`equity` table). Reporting only: nothing here
reaches Context, signals, gates or risk limits.

At each time t, over trades not excluded (excluded trades count as never taken):

- realized(t): realized P&L of trades settled at or before t
- interest(t): ledger total before t
- book_value(t) = starting balance + realized + interest (open positions at cost)
- unrealized(t): over trades open at t (opened at or before t, not settled by t), the
  mark at t exactly as `nem report` marks open trades now (nem.execution.mark: the latest
  recorded snapshot at or before t, bids walked after the exit fee, uncovered contracts at
  $0, nothing when the book is stale or the market closed). Unmarked trades count at cost
  (0) and are counted in `open_unmarked`.
- value(t) = book_value + unrealized

Points: every hour on the hour from the hour of the portfolio's first activity, plus a
final point at the report's time.
"""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import accumulate

from nem.core.config import PortfolioConfig
from nem.core.types import Trade
from nem.execution.fees import DEFAULT_TAKER_RATE
from nem.execution.mark import mark_position
from nem.store import Store

STEP = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class EquityPoint:
    ts: datetime
    realized: float
    interest: float
    book_value: float  # start + realized + interest: open positions at cost
    unrealized: float  # marked open trades: liquidation value - cost; unmarked count 0
    open_positions: int
    open_unmarked: int

    @property
    def value(self) -> float:
        return self.book_value + self.unrealized


def time_grid(first: datetime, end: datetime, step: timedelta = STEP) -> list[datetime]:
    """Every `step` on the hour from the hour of `first` up to (not including) `end`, then
    `end` itself."""
    t = first.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    out: list[datetime] = []
    while t < end:
        out.append(t)
        t += step
    out.append(end)
    return out


def _open_span(trade: Trade, times: list[datetime]) -> range:
    """Indexes of `times` at which the trade is open: opened_at <= t < settled_at."""
    lo = bisect_left(times, trade.opened_at)
    hi = len(times) if trade.settled_at is None else bisect_left(times, trade.settled_at)
    return range(lo, max(lo, hi))


def equity_history(store: Store, portfolio: PortfolioConfig, now: datetime) -> list[EquityPoint]:
    """The portfolio's value at each point of `time_grid` (empty before any activity)."""
    first = store.first_activity(portfolio.name)
    if first is None:
        return []
    times = time_grid(first, now)
    start = portfolio.starting_balance or portfolio.budget or 0.0
    settled = store.settled_trades(portfolio=portfolio.name, include_excluded=False)
    trades = [*settled, *store.open_trades(portfolio=portfolio.name, include_excluded=False)]

    settled.sort(key=lambda t: t.settled_at or now)
    settle_times = [t.settled_at for t in settled if t.settled_at is not None]
    realized = list(accumulate((t.realized_pnl or 0.0 for t in settled), initial=0.0))
    ledger = store.ledger_entries(portfolio.name)
    ledger_times = [ts for ts, _ in ledger]
    interest = list(accumulate((a for _, a in ledger), initial=0.0))

    unrealized = [0.0] * len(times)
    n_open = [0] * len(times)
    unmarked = [0] * len(times)
    rates = {s.name: s.taker_rate for s in portfolio.strategies}
    by_ticker: dict[str, list[tuple[Trade, range]]] = defaultdict(list)
    for t in trades:
        span = _open_span(t, times)
        if span:
            by_ticker[t.ticker].append((t, span))
    for ticker, held in by_ticker.items():
        idx = sorted({i for _, span in held for i in span})
        snaps = dict(zip(idx, store.latest_snapshots(ticker, [times[i] for i in idx]), strict=True))
        for trade, span in held:
            # as it was then: open (a later settlement or exit must not hide the mark)
            then = replace(trade, won=None, realized_pnl=None, settled_at=None)
            rate = rates.get(trade.strategy, DEFAULT_TAKER_RATE)
            for i in span:
                n_open[i] += 1
                mark = mark_position(then, snaps[i], rate, times[i])
                if mark is None:
                    unmarked[i] += 1
                else:
                    unrealized[i] += mark.unrealized_pnl

    out: list[EquityPoint] = []
    for i, t in enumerate(times):
        r = realized[bisect_right(settle_times, t)]
        inter = interest[bisect_left(ledger_times, t)]
        out.append(
            EquityPoint(t, r, inter, start + r + inter, unrealized[i], n_open[i], unmarked[i])
        )
    return out
