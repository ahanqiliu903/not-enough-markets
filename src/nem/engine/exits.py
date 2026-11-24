"""Exit open positions before settlement, per the strategy's `exit:` config.

Runs every tick on that tick's snapshots only, so an exit can only use a book recorded at
or before now and after the position opened (no look-ahead). A strategy without exits
(the default) does nothing here and touches nothing in the store.

An exit closes the position in the existing tables: an order row whose status names the
exit kind (`EXIT_STATUS`), a fill, and the trade settled at the exit time with
`realized_pnl` from the sale and `won = realized_pnl > 0`. The trade is also queued
(`exit_learning`, schema v7): learned win counts (`ctx.stat`) and `on_settle` mean "the
side won at settlement", which isn't known when a position is sold early, so they are
updated later, when the market settles (`nem.engine.settlement.learn_exits`).
"""

import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from nem.core.types import Fill, MarketSnapshot, Order, Trade
from nem.engine.runtime import StrategyRuntime
from nem.execution.exits import EXIT_STATUS, ExitFill, TradedVolume, decide_exit, exit_order_id
from nem.store import Store

log = logging.getLogger(__name__)


def exit_pnl(trade: Trade, fill: ExitFill) -> float:
    """Sale proceeds minus the exit fee, minus what the position cost (price + entry fee)."""
    return round(fill.price * fill.qty - fill.fee - trade.cost, 6)


def _record(trade: Trade, fill: ExitFill, store: Store) -> Trade:
    coid = exit_order_id(trade)
    order = Order(
        coid,
        trade.portfolio,
        trade.strategy,
        trade.window_id,
        trade.ticker,
        trade.side,
        fill.limit_price,
        fill.qty,
    )
    store.record_order(order, fill.ts, trade.mode, EXIT_STATUS[fill.kind])
    store.record_fill(Fill(coid, fill.ts, fill.price, fill.qty, fill.fee))
    pnl = exit_pnl(trade, fill)
    won = pnl > 0
    store.exit_trade((trade.portfolio, trade.strategy), trade.window_id, won, pnl, fill.ts)
    log.info(
        "%s/%s %s %s: sold %d @ %.4f (fee %.2f) %+.2f",
        trade.portfolio, trade.strategy, fill.kind, trade.ticker, fill.qty, fill.price,
        fill.fee, pnl,
    )  # fmt: skip
    return replace(trade, won=won, realized_pnl=pnl, settled_at=fill.ts)


def run_exits(
    rt: StrategyRuntime,
    snapshots: Sequence[MarketSnapshot],
    now: datetime,
    store: Store,
    traded: TradedVolume | None = None,
) -> list[Trade]:
    """Check this strategy's open positions against this tick's books. Returns the trades
    closed by an exit. `traded` (trade prints) is needed by the trade_through model only."""
    cfg = rt.config.exit
    if cfg is None or cfg.holds:
        return []
    books = {
        s.ticker: s
        for s in snapshots
        if s.series == rt.config.series and s.ts <= now and s.open_time <= now < s.close_time
    }
    if not books:
        return []
    closed: list[Trade] = []
    for trade in store.open_trades(strategy=rt.key):
        snap = books.get(trade.ticker)
        if snap is None or snap.window_id != trade.window_id:
            continue
        missing = rt.portfolio.fills.missing_depth
        fill = decide_exit(trade, cfg, snap, rt.config.taker_rate, traded, missing)
        if fill is None:
            continue
        assert fill.qty == trade.qty, "exits are all-or-nothing"
        closed.append(_record(trade, fill, store))
    return closed
