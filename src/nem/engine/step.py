"""One decision: signal -> gates -> sizer -> risk -> order -> fill -> trade.

Every signal that fires is logged with its outcome and the reason, so you can later count
what each gate, sizer and risk limit actually did.
"""

import logging

from nem.core.context import Context, DataUnavailable
from nem.core.types import Decision, DecisionKind, MarketSnapshot, Order, Signal, Trade
from nem.engine.risk import check_risk
from nem.engine.runtime import StrategyRuntime
from nem.gates.base import Gate
from nem.store import Store

log = logging.getLogger(__name__)


def check_gate(gate: Gate, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
    """Run one gate. If its data is missing or it errors, `fail_open` decides."""
    try:
        return gate.check(signal, snap, ctx)
    except DataUnavailable as e:
        outcome = "fail_open" if gate.fail_open else "fail_closed"
        log.info("gate %s: %s (%s)", gate.name, e, outcome)
    except Exception:
        outcome = "fail_open" if gate.fail_open else "fail_closed"
        log.exception("gate %s errored (%s)", gate.name, outcome)
        outcome = f"error_{outcome}"
    if gate.fail_open:
        return Decision.ok()
    return Decision.skip(f"{gate.name}:{outcome}")


def _log(
    rt: StrategyRuntime, store: Store, sig: Signal, ctx: Context, kind: DecisionKind, reason: str
) -> None:
    marker = (sig.window_id, kind, reason)
    if kind != "take" and marker in rt.logged:
        return
    rt.logged.add(marker)
    edge = sig.meta.get("edge")
    store.log_signal(sig, ctx.now(), kind, reason, edge if isinstance(edge, float) else None)


def evaluate(rt: StrategyRuntime, snap: MarketSnapshot, ctx: Context, store: Store) -> Trade | None:
    """Decide on one market for one strategy. Returns the new trade, if any."""
    if store.has_trade(rt.key, snap.window_id):
        return None  # one position per strategy per market window
    try:
        sig = rt.signal.on_market(snap, ctx)
    except DataUnavailable:
        return None
    if sig is None:
        return None

    for gate in rt.gates:
        d = check_gate(gate, sig, snap, ctx)
        if not d.take:
            _log(rt, store, sig, ctx, "skip", d.reason)
            return None

    sizing = rt.sizer.size(sig, ctx)
    if sizing.qty <= 0:
        _log(rt, store, sig, ctx, "skip", f"size_zero: {sizing.reason}")
        return None

    est_cost = sizing.qty * sig.limit_price
    d = check_risk(ctx, est_cost)
    if not d.take:
        _log(rt, store, sig, ctx, "skip", d.reason)
        return None

    now = ctx.now()
    attempt = store.order_attempts(rt.key, sig.window_id, sig.side)
    order = Order.from_signal(sig, sizing.qty, attempt=attempt)
    store.record_order(order, now, rt.broker.mode, "submitted")
    fill = rt.broker.submit(order, snap, now)
    if fill is None:
        store.set_order_status(order.client_order_id, "no_fill")
        _log(rt, store, sig, ctx, "no_fill", "no_liquidity_at_limit")
        return None

    store.record_fill(fill)
    store.set_order_status(order.client_order_id, "filled" if fill.qty == order.qty else "partial")
    trade = Trade(
        portfolio=sig.portfolio,
        strategy=sig.strategy,
        window_id=sig.window_id,
        ticker=sig.ticker,
        side=sig.side,
        avg_price=fill.price,
        qty=fill.qty,
        fee=fill.fee,
        mode=rt.broker.mode,
        opened_at=now,
        close_time=snap.close_time,
    )
    store.open_trade(trade)
    _log(rt, store, sig, ctx, "take", f"ok: {sizing.reason}")
    log.info(
        "%s/%s bought %d %s %s @ %.4f (fee %.2f)",
        *rt.key, fill.qty, sig.side.upper(), sig.ticker, fill.price, fill.fee,
    )  # fmt: skip
    return trade
