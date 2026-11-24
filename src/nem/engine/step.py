"""One decision: signal -> gates -> sizer -> risk -> order -> fill -> trade.

Every signal that fires is logged with its outcome and the reason, so you can later count
what each gate, sizer and risk limit actually did.
"""

import logging
from datetime import datetime, timedelta

from nem.core.context import Context, DataUnavailable
from nem.core.types import Decision, DecisionKind, Fill, MarketSnapshot, Order, Signal, Trade
from nem.engine.risk import check_risk
from nem.engine.runtime import StrategyRuntime
from nem.gates.base import Gate
from nem.store import Store

log = logging.getLogger(__name__)

COST_EPS = 1e-9  # float noise, in dollars


def fill_cost(fill: Fill) -> float:
    """Cash a fill takes, computed exactly as `Trade.cost` will be: contracts at the
    average price, plus fees."""
    return fill.price * fill.qty + fill.fee


def check_gate(gate: Gate, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
    """Run one gate. If its data is missing or stale (`DataUnavailable`), `fail_open`
    decides. Any other exception is a bug, and a bug never lets a trade through: it always
    blocks, whatever `fail_open` says, with the error type in the logged reason."""
    try:
        return gate.check(signal, snap, ctx)
    except DataUnavailable as e:
        outcome = "fail_open" if gate.fail_open else "fail_closed"
        log.info("gate %s: %s (%s)", gate.name, e, outcome)
        if gate.fail_open:
            return Decision.ok()
        return Decision.skip(f"{gate.name}:{outcome}")
    except Exception as e:
        log.exception("gate %s errored; blocking the trade", gate.name)
        return Decision.skip(f"{gate.name}:error:{type(e).__name__}")


def snapshot_problem(snap: MarketSnapshot, now: datetime, max_age: timedelta) -> str | None:
    """Why a snapshot can't be traded on at `now`, or None. A snapshot from after `now`
    is look-ahead; one older than `max_age` is a stale book. Exactly `max_age` is fine."""
    if snap.ts > now:
        return "snapshot:future"
    if now - snap.ts > max_age:
        return "snapshot:stale"
    return None


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
    stale = snapshot_problem(snap, ctx.now(), rt.portfolio.max_snapshot_age)
    if stale is not None:
        # Checked before the signal sees the snapshot: a future one would be look-ahead.
        marker = (snap.window_id, "skip", stale)
        if marker not in rt.logged:
            rt.logged.add(marker)
            log.warning(
                "%s/%s: %s %s at %s, decision at %s; not entering",
                *rt.key, stale, snap.ticker, snap.ts.isoformat(), ctx.now().isoformat(),
            )  # fmt: skip
        return None
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

    # Risk sees what the order would actually cost, fees included: the broker's quote walks
    # the same levels with the same per-level fee rounding as the fill itself.
    now = ctx.now()
    attempt = store.order_attempts(rt.key, sig.window_id, sig.side)
    order = Order.from_signal(sig, sizing.qty, attempt=attempt)
    quote = rt.broker.quote(order, snap, now)
    approved = fill_cost(quote) if quote is not None else 0.0  # nothing fills, nothing spent
    d = check_risk(ctx, approved, sig.ticker)
    if not d.take:
        _log(rt, store, sig, ctx, "skip", d.reason)
        return None

    store.record_order(order, now, rt.broker.mode, "submitted")
    fill = rt.broker.submit(order, snap, now)
    if fill is None:
        store.set_order_status(order.client_order_id, "no_fill")
        _log(rt, store, sig, ctx, "no_fill", "no_liquidity_at_limit")
        return None
    if fill_cost(fill) > approved + COST_EPS:
        # Can't happen with the paper broker (the quote is exact); never book more than
        # risk approved if it ever does.
        log.error(
            "%s/%s fill cost %.6f above approved %.6f; not booked",
            *rt.key, fill_cost(fill), approved,
        )  # fmt: skip
        store.set_order_status(order.client_order_id, "rejected_over_risk")
        _log(rt, store, sig, ctx, "skip", "risk:fill_above_approved_cost")
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
