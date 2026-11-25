"""Resolve orders a crash left half-recorded, before any new attempt.

Each execution outcome is one transaction (Store.record_entry / record_exit /
settle_and_learn), so a crash mid-outcome leaves nothing of it. What it can leave is an
entry order recorded `submitted` (written before the order is sent) with no outcome, and a
database written before schema v9 can hold orders, fills and trades from separate commits.

For paper orders local state is the truth: the paper broker fills in-process, so a fill
that isn't in the store never happened. Rules, applied in order:

- entry `submitted`, no fill: `cancelled` (nothing filled, the whole quantity cancelled).
- entry with fills but no trade: the fills are the position. The order gets `filled` or
  `partial` and the trade is opened from its fills (price averaged, fees summed), with
  the market's close time from the order (v9) or the recorded snapshots.
- exit with a fill whose trade is still open: the trade is closed from that fill, exactly
  as the exit would have recorded it.
- exit without a fill: discarded (nothing was sold), so the position can exit again.

Anything else stays unresolved and keeps blocking new attempts for its strategy, window
and side (Store.unresolved_attempt): a live order (a real broker must be asked, never
assumed dead), or a filled entry whose market close time is unknown. Windows whose entry
fills hold more contracts than their trade (only pre-v9 writes) are reported, not changed.
"""

import logging
from dataclasses import dataclass, field

from nem.core.types import OrderRecord, Trade
from nem.store import Store

log = logging.getLogger(__name__)


@dataclass
class Reconciliation:
    resolved: list[str] = field(default_factory=list[str])
    unresolved: list[str] = field(default_factory=list[str])


def _where(rec: OrderRecord) -> str:
    o = rec.order
    return f"{o.portfolio}/{o.strategy} {o.window_id} {o.side} order {o.client_order_id}"


def _entry_trade(rec: OrderRecord, store: Store, data: Store) -> Trade | None:
    o = rec.order
    close_time = (
        rec.close_time or store.market_close_time(o.ticker) or data.market_close_time(o.ticker)
    )
    if close_time is None:
        return None
    qty = sum(f.qty for f in rec.fills)
    price = sum(f.price * f.qty for f in rec.fills) / qty
    return Trade(
        portfolio=o.portfolio,
        strategy=o.strategy,
        window_id=o.window_id,
        ticker=o.ticker,
        side=o.side,
        avg_price=price,
        qty=qty,
        fee=sum(f.fee for f in rec.fills),
        mode=rec.mode,
        opened_at=rec.fills[0].ts,
        close_time=close_time,
    )


def _resolve(rec: OrderRecord, store: Store, data: Store) -> str | None:
    """What was done, or None if `rec` can't be resolved locally."""
    o = rec.order
    coid = o.client_order_id
    if rec.kind == "exit":
        if not rec.fills:
            store.discard_order(coid)
            return "unfilled exit discarded"
        (fill,) = rec.fills
        trade = next(
            (t for t in store.open_trades(strategy=(o.portfolio, o.strategy))
             if t.window_id == o.window_id),
            None,
        )  # fmt: skip
        if trade is None:
            return None
        pnl = round(fill.price * fill.qty - fill.fee - trade.cost, 6)
        store.exit_trade((o.portfolio, o.strategy), o.window_id, pnl > 0, pnl, fill.ts)
        return f"trade closed from its exit fill ({pnl:+.2f})"
    if not rec.fills:
        if rec.status != "submitted":
            return None
        store.set_order_status(coid, "cancelled")
        return "no fill recorded: cancelled"
    qty = sum(f.qty for f in rec.fills)
    if qty > o.qty:
        return None
    if store.has_trade((o.portfolio, o.strategy), o.window_id):
        return None  # another order's trade holds the window (pre-v9); reported below
    trade = _entry_trade(rec, store, data)
    if trade is None:
        return None
    with store.transaction():
        if rec.status == "submitted":
            store.set_order_status(coid, "filled" if qty == o.qty else "partial", qty)
        store.open_trade(trade)
    return f"trade opened from {len(rec.fills)} recorded fill(s), {qty} contracts"


def reconcile(store: Store, data: Store | None = None) -> Reconciliation:
    """Resolve every half-recorded order in `store` (see the module docstring). `data` is
    where snapshots were recorded, if not in `store`. Safe to run any number of times."""
    data = data or store
    out = Reconciliation()
    for rec in store.unresolved_orders():
        where = _where(rec)
        if rec.mode != "paper":
            out.unresolved.append(f"{where}: {rec.mode} order, outcome must come from the broker")
            continue
        with store.transaction():
            done = _resolve(rec, store, data)
        if done is None:
            out.unresolved.append(f"{where} ({rec.status}): can't be resolved locally")
        else:
            out.resolved.append(f"{where}: {done}")
    for key, window_id, filled, held in store.unbooked_entry_fills():
        out.unresolved.append(
            f"{key[0]}/{key[1]} {window_id}: entry fills hold {filled} contracts, the trade"
            f" {held}; the difference is in no position (written before schema v9)"
        )
    for line in out.resolved:
        log.warning("reconciled %s", line)
    for line in out.unresolved:
        log.error("unresolved %s; new entries for it stay blocked", line)
    return out
