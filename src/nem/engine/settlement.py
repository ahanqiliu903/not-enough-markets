"""Settle trades whose markets have resolved, and accrue interest."""

import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, time, timedelta

from nem.core.clock import EXCHANGE_TZ
from nem.core.config import PortfolioConfig
from nem.core.types import Trade
from nem.market.source import MarketSource
from nem.store import Store

log = logging.getLogger(__name__)


def realized_pnl(trade: Trade, won: bool) -> float:
    """Each winning contract pays $1. P&L = payout - (price paid + fees)."""
    return round((trade.qty if won else 0.0) - trade.cost, 6)


def settle_due(
    store: Store,
    source: MarketSource,
    now: datetime,
    on_settle: Callable[[Trade], None] | None = None,
) -> list[Trade]:
    """Settle every open trade whose market has closed and has a known result."""
    settled: list[Trade] = []
    for t in store.trades_due(now):
        result = source.result(t.ticker, now)
        if result is None:
            continue
        won = result.result == t.side
        pnl = realized_pnl(t, won)
        key = (t.portfolio, t.strategy)
        store.settle_trade(key, t.window_id, won, pnl, result.settled_at)
        store.bump_stat(*key, t.side, won)
        log.info("%s/%s %s %s: %+.2f", *key, t.ticker, "won" if won else "lost", pnl)
        done = replace(t, won=won, realized_pnl=pnl, settled_at=result.settled_at)
        settled.append(done)
        if on_settle is not None:
            on_settle(done)
    return settled


def _midnights(after: datetime, upto: datetime) -> list[datetime]:
    """Exchange-time midnights in (after, upto]."""
    day = after.astimezone(EXCHANGE_TZ).date() + timedelta(days=1)
    out: list[datetime] = []
    while (m := datetime.combine(day, time(0), tzinfo=EXCHANGE_TZ)) <= upto:
        out.append(m)
        day += timedelta(days=1)
    return out


def accrue_interest(
    store: Store,
    portfolios: Sequence[PortfolioConfig],
    now: datetime,
    equity_at: Callable[[PortfolioConfig, datetime], float],
) -> None:
    """Credit APY/365 of equity at each exchange-time midnight, like Kalshi's daily accrual
    (Kalshi pays monthly; paper credits daily, a negligible difference). The first call
    only records a starting point."""
    for p in portfolios:
        if not p.interest_apy:
            continue
        last = store.last_ledger(p.name, "interest")
        if last is None:
            store.add_ledger(p.name, now, "interest", 0.0)
            continue
        for midnight in _midnights(last, now):
            amount = round(equity_at(p, midnight) * p.interest_apy / 365, 6)
            store.add_ledger(p.name, midnight, "interest", amount)
