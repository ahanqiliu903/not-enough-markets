"""Execution and settlement writes are atomic and reconciled (audit R4).

Failures are injected after every persistence boundary of an entry, an exit and a
settlement, on an on-disk database that is closed and reopened, as a crashed process
would be. Afterwards cash, positions, fills, learned counts and order statuses must agree,
each outcome recorded exactly once. Synthetic markets only.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from nem.core.clock import ManualClock
from nem.core.config import PortfolioConfig
from nem.core.types import Fill, MarketSnapshot, Order, Trade, client_order_id
from nem.engine.exits import run_exits
from nem.engine.reconcile import reconcile
from nem.engine.runner import Runner
from nem.engine.settlement import settle_due
from nem.engine.step import evaluate
from nem.execution.exits import exit_order_id
from nem.market.source import FakeSource
from nem.store import Store, StoreError
from nem.store.sqlite import SCHEMA_VERSION

from engine_helpers import portfolio, settle, strategy, window
from factories import T0, make_signal, make_trade

KEY = ("research", "fav90")
W = window(T0)
SNAP = W[0]
SECOND = timedelta(seconds=1)


class Crash(RuntimeError):
    """A process dying between two writes."""


def coid(attempt: int = 0) -> str:
    return client_order_id(*KEY, SNAP.window_id, "yes", attempt)


def runner(store: Store, p: PortfolioConfig | None = None) -> Runner:
    src = FakeSource([])
    return Runner([p or portfolio()], src, store, ManualClock(SNAP.ts), fetch_feeds=False)


def enter(
    store: Store, snap: MarketSnapshot = SNAP, p: PortfolioConfig | None = None
) -> Trade | None:
    r = runner(store, p)
    rt = r.strategies[0]
    return evaluate(rt, snap, r.context(rt), store)


def cash(store: Store) -> float:
    """Cash once everything so far has happened (after the window closes)."""
    p = portfolio()
    clock = ManualClock(SNAP.close_time - SECOND)
    r = Runner([p], FakeSource([]), store, clock, fetch_feeds=False)
    return r.context(r.strategies[0]).cash()


def crash_on(store: Store, method: str) -> Any:
    return patch.object(store, method, side_effect=Crash(method))


def assert_one_entry(store: Store) -> None:
    """Exactly one booked entry: one filled order, one fill, one open position, cash
    reduced by exactly its cost."""
    (t,) = store.open_trades()
    attempts = store.order_attempts(KEY, SNAP.window_id, "yes")
    filled = [store.order(coid(a)) for a in range(attempts)]
    booked = [o for o in filled if o is not None and o.fills]
    assert len(booked) == 1
    (rec,) = booked
    assert rec.status == "filled" and rec.filled_qty == t.qty and rec.cancelled_qty == 0
    (fill,) = rec.fills
    assert (fill.qty, fill.price, fill.fee) == (t.qty, t.avg_price, t.fee)
    assert cash(store) == pytest.approx(1000 - t.cost)
    assert not store.unresolved_orders()


# --- entry ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "boundary", ["record_fill", "set_order_status", "open_trade", "log_signal"]
)
def test_entry_crash_at_any_boundary_books_nothing_then_once(tmp_path: Path, boundary: str) -> None:
    db = tmp_path / "nem.db"
    with Store(db) as store, crash_on(store, boundary), pytest.raises(Crash):
        enter(store)
    with Store(db) as store:  # restart
        assert store.open_trades() == []
        assert cash(store) == pytest.approx(1000)  # nothing half-booked
        rec = store.order(coid(0))
        assert rec is not None and rec.status == "cancelled"  # resolved on startup
        assert (rec.filled_qty, rec.cancelled_qty, rec.fills) == (0, rec.order.qty, ())
        assert enter(store) is not None  # the next attempt is allowed and books
        assert_one_entry(store)
        assert store.p_models(*KEY) == {SNAP.window_id: pytest.approx(0.95)}
    with Store(db) as store:
        assert enter(store) is None  # one position per window, even after a restart
        assert_one_entry(store)


def test_unknown_outcome_blocks_retries_until_resolved(tmp_path: Path) -> None:
    db = tmp_path / "nem.db"
    with Store(db) as store:
        r = runner(store)
        rt = r.strategies[0]
        quote = rt.broker.quote(Order.from_signal(make_signal(), 5), SNAP, SNAP.ts)
        # the quote (risk check) answers, the order itself never comes back
        with (
            patch.object(rt.broker, "submit", side_effect=[quote, Crash("broker")]),
            pytest.raises(Crash),
        ):
            evaluate(rt, SNAP, r.context(rt), store)
        assert store.order_status(coid(0)) == "submitted"
        assert store.unresolved_attempt(KEY, SNAP.window_id, "yes")
        # same process, no reconciliation: no second attempt while the first is unknown
        assert evaluate(rt, SNAP, r.context(rt), store) is None
        assert store.order_attempts(KEY, SNAP.window_id, "yes") == 1
        assert store.signal_reasons(*KEY) == {"order:unresolved_attempt": 1}
    with Store(db) as store:
        assert enter(store) is not None  # reconciled on startup, then attempt 1
        assert store.order_status(coid(0)) == "cancelled"
        assert store.order_status(coid(1)) == "filled"
        assert_one_entry(store)


def test_audit_reproduction_crash_after_fill(tmp_path: Path) -> None:
    """The audit's probe: a crash after the fill used to leave a fill with no position,
    and a restart then booked a second fill, missing the first from cash."""
    db = tmp_path / "nem.db"
    with Store(db) as store, crash_on(store, "open_trade"), pytest.raises(Crash):
        enter(store)
    with Store(db) as store:
        rec = store.order(coid(0))
        assert rec is not None and rec.status == "submitted" and rec.fills == ()  # rolled back
        assert enter(store) is not None
        assert_one_entry(store)


@pytest.mark.parametrize(
    ("depth", "status", "filled"), [(0, "no_fill", 0), (3, "partial", 3), (50, "filled", 5)]
)
def test_ioc_outcomes_record_filled_and_cancelled(depth: float, status: str, filled: int) -> None:
    store = Store()
    snap = window(T0, depth=depth)[0]
    trade = enter(store, snap)
    rec = store.order(coid(0))
    assert rec is not None and rec.status == status
    assert (rec.filled_qty, rec.cancelled_qty) == (filled, 5 - filled)
    assert sum(f.qty for f in rec.fills) == filled
    assert (trade.qty if trade else 0) == filled
    if filled == 0:
        assert store.open_trades() == []
        # a no-fill is dead for sure: the next attempt may go
        assert not store.unresolved_attempt(KEY, snap.window_id, "yes")


def test_duplicate_entry_callback_does_not_debit_twice() -> None:
    store = Store()
    trade = enter(store)
    assert trade is not None
    rec = store.order(coid(0))
    assert rec is not None
    (fill,) = rec.fills
    before = cash(store)
    assert store.record_entry(fill, trade) is False
    assert store.record_fill(fill) is False
    with pytest.raises(StoreError, match="different fill"):
        store.record_fill(replace(fill, qty=fill.qty - 1))
    assert cash(store) == before and len(store.open_trades()) == 1


# --- statuses --------------------------------------------------------------------------


def test_illegal_transitions_are_refused() -> None:
    store = Store()
    order = Order.from_signal(make_signal(), qty=5)
    with pytest.raises(StoreError, match="starts submitted"):
        store.record_order(order, T0, "paper", "filled")
    store.record_order(order, T0, "paper", "submitted")
    with pytest.raises(StoreError, match="illegal transition"):
        store.set_order_status(order.client_order_id, "bogus")
    with pytest.raises(StoreError, match="partial with 5 of 5"):
        store.set_order_status(order.client_order_id, "partial", 5)
    with pytest.raises(StoreError, match="no_fill with 2 of 5"):
        store.set_order_status(order.client_order_id, "no_fill", 2)
    store.set_order_status(order.client_order_id, "partial", 2)
    rec = store.order(order.client_order_id)
    assert rec is not None and (rec.status, rec.filled_qty, rec.cancelled_qty) == ("partial", 2, 3)
    for status in ("filled", "no_fill", "cancelled", "submitted", "partial"):
        with pytest.raises(StoreError, match="illegal transition partial"):
            store.set_order_status(order.client_order_id, status)
    exit_order = replace(order, client_order_id="exit-1")
    with pytest.raises(StoreError, match="exit order's status"):
        store.record_order(exit_order, T0, "paper", "submitted", kind="exit")
    store.record_order(exit_order, T0, "paper", "exit_stop", kind="exit")
    with pytest.raises(StoreError, match="illegal transition exit_stop"):
        store.set_order_status("exit-1", "cancelled")


def test_cancellation_is_terminal_and_frees_the_next_attempt() -> None:
    store = Store()
    order = Order.from_signal(make_signal(), qty=5)
    store.record_order(order, T0, "paper", "submitted")
    assert store.unresolved_attempt(KEY, order.window_id, "yes")
    store.set_order_status(order.client_order_id, "cancelled")
    assert not store.unresolved_attempt(KEY, order.window_id, "yes")
    with pytest.raises(StoreError, match="illegal transition cancelled -> filled"):
        store.set_order_status(order.client_order_id, "filled")


def test_transaction_rolls_back_every_write() -> None:
    store = Store()
    with pytest.raises(Crash), store.transaction():
        store.bump_stat(*KEY, "yes", True)
        with store.transaction():  # nested: joins the outer one
            store.open_trade(make_trade())
        raise Crash
    assert store.stat(*KEY, "yes") == (0, 0) and store.open_trades() == []
    with store.transaction():
        store.bump_stat(*KEY, "yes", True)
    assert store.stat(*KEY, "yes") == (1, 1)


# --- exits ------------------------------------------------------------------------------

TP = {"take_profit": {"price": 0.95, "fill_model": "optimistic", "maker_fee_rate": 0.0175}}


def exit_book(snap: MarketSnapshot) -> MarketSnapshot:
    return replace(snap, yes_bid=0.96, depth={"yes": [(0.96, 50)], "no": [(0.03, 50)]})


def exits_world(store: Store) -> tuple[Runner, MarketSnapshot]:
    p = portfolio(strategies=[strategy(exit=TP)])
    r = runner(store, p)
    rt = r.strategies[0]
    evaluate(rt, SNAP, r.context(rt), store)
    return r, exit_book(W[2])


@pytest.mark.parametrize("boundary", ["record_order", "record_fill", "exit_trade"])
def test_exit_crash_at_any_boundary_then_exits_once(tmp_path: Path, boundary: str) -> None:
    db = tmp_path / "nem.db"
    with Store(db) as store:
        r, snap = exits_world(store)
        (held,) = store.open_trades()
        with crash_on(store, boundary), pytest.raises(Crash):
            run_exits(r.strategies[0], [snap], snap.ts, store)
    with Store(db) as store:
        assert store.open_trades() == [held]  # the exit left nothing behind
        assert store.order(exit_order_id(held)) is None
        r = runner(store, portfolio(strategies=[strategy(exit=TP)]))
        (closed,) = run_exits(r.strategies[0], [snap], snap.ts, store)
        assert run_exits(r.strategies[0], [snap], snap.ts, store) == []  # once
        rec = store.order(exit_order_id(held))
        assert rec is not None and rec.kind == "exit" and rec.filled_qty == held.qty
        (fill,) = rec.fills
        assert closed.realized_pnl == pytest.approx(fill.price * fill.qty - fill.fee - held.cost)
        assert store.exit_outcomes(KEY) == {held.window_id: None}  # queued once
        assert cash(store) == pytest.approx(1000 + (closed.realized_pnl or 0))
        assert not store.unresolved_orders()


def test_duplicate_exit_callback_is_a_no_op() -> None:
    store = Store()
    r, snap = exits_world(store)
    (closed,) = run_exits(r.strategies[0], [snap], snap.ts, store)
    rec = store.order(exit_order_id(closed))
    assert rec is not None
    (fill,) = rec.fills
    assert store.record_exit(rec.order, fill, "exit_take_profit", "paper", True, 0.1) is False
    with pytest.raises(StoreError, match="different fill"):
        store.record_exit(rec.order, replace(fill, fee=0), "exit_take_profit", "paper", True, 0)
    (t,) = store.settled_trades()
    assert t.realized_pnl == closed.realized_pnl


# --- settlement -------------------------------------------------------------------------


@pytest.mark.parametrize("boundary", ["settle_trade", "bump_stat"])
def test_settlement_crash_then_counts_once(tmp_path: Path, boundary: str) -> None:
    db = tmp_path / "nem.db"
    source = FakeSource([], [settle(W, "yes")])
    later = SNAP.close_time + timedelta(minutes=1)
    with Store(db) as store:
        assert enter(store) is not None
        with crash_on(store, boundary), pytest.raises(Crash):
            settle_due(store, source, later)
    with Store(db) as store:
        assert len(store.open_trades()) == 1 and store.stat(*KEY, "yes") == (0, 0)
        (done,) = settle_due(store, source, later)
        assert settle_due(store, source, later) == []
        assert store.stat(*KEY, "yes") == (1, 1)
        (t,) = store.settled_trades()
        assert t.won and t.realized_pnl == pytest.approx(t.qty - t.cost) == done.realized_pnl
        assert store.settle_and_learn(t, True, 1.0, later) is False  # duplicate: no-op
        assert store.stat(*KEY, "yes") == (1, 1)


# --- reconciliation of half-recorded (pre-v9) state --------------------------------------


def legacy(store: Store, write: Callable[[sqlite3.Connection], object]) -> None:
    conn = store._conn  # pyright: ignore[reportPrivateUsage]  # forge a pre-v9 split write
    with conn:
        write(conn)


def test_reconcile_books_a_fill_without_its_trade() -> None:
    store = Store()
    store.insert_snapshot(SNAP)
    order = Order.from_signal(make_signal(window_id=SNAP.window_id, ticker=SNAP.ticker), qty=5)
    store.record_order(order, T0, "paper", "submitted")
    store.record_fill(Fill(order.client_order_id, T0, 0.9, 5, 0.02))
    legacy(store, lambda c: c.execute("UPDATE orders SET close_time = NULL"))
    assert store.unresolved_attempt(KEY, SNAP.window_id, "yes")
    out = reconcile(store)
    assert out.unresolved == [] and len(out.resolved) == 1
    (t,) = store.open_trades()
    assert (t.qty, t.avg_price, t.fee, t.close_time) == (5, 0.9, 0.02, SNAP.close_time)
    assert store.order_status(order.client_order_id) == "filled"
    assert reconcile(store).resolved == []  # idempotent
    assert enter(store) is None  # the window has its position


def test_reconcile_exit_orders() -> None:
    store = Store()
    t = make_trade()
    store.open_trade(t)
    order = Order(exit_order_id(t), t.portfolio, t.strategy, t.window_id, t.ticker, "yes", 0.95, 5)
    store.record_order(order, T0, "paper", "exit_take_profit", kind="exit")
    reconcile(store)  # no fill: nothing was sold, so the exit can happen again
    assert store.order(order.client_order_id) is None and store.open_trades() == [t]
    store.record_order(order, T0, "paper", "exit_take_profit", kind="exit")
    store.record_fill(Fill(order.client_order_id, T0 + SECOND, 0.95, 5, 0.03))
    out = reconcile(store)  # filled but the trade still open: close it from the fill
    assert len(out.resolved) == 1
    (closed,) = store.settled_trades()
    assert closed.realized_pnl == pytest.approx(0.95 * 5 - 0.03 - t.cost)
    assert store.exit_outcomes(KEY) == {t.window_id: None}


def test_live_orders_are_never_assumed_dead() -> None:
    store = Store()
    order = Order.from_signal(make_signal(), qty=5)
    store.record_order(order, T0, "live", "submitted")
    out = reconcile(store)
    assert out.resolved == [] and len(out.unresolved) == 1
    assert store.order_status(order.client_order_id) == "submitted"
    assert store.unresolved_attempt(KEY, order.window_id, "yes")


def test_reconcile_reports_fills_missing_from_a_position() -> None:
    """Two fills, one trade (the audit's pre-v9 restart): reported, not guessed at."""
    store = Store()
    for attempt in (0, 1):
        o = Order.from_signal(make_signal(), qty=5, attempt=attempt)
        store.record_order(o, T0, "paper", "submitted")
        store.record_fill(Fill(o.client_order_id, T0, 0.9, 5, 0.02))
        store.set_order_status(o.client_order_id, "filled")
    store.open_trade(make_trade(qty=5))
    out = reconcile(store)
    assert [line for line in out.unresolved if "hold 10 contracts, the trade 5" in line]


# --- schema ----------------------------------------------------------------------------


def test_v8_database_is_upgraded(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    with Store(db) as store:
        trade = enter(store)
        assert trade is not None
        submitted = Order.from_signal(make_signal(window_id="W2"), qty=4)
        store.record_order(submitted, T0, "paper", "submitted")
    conn = sqlite3.connect(db)  # back to v8: orders and fills without lifecycle columns
    conn.executescript(
        "DROP INDEX fills_fill_id; DROP INDEX orders_attempts;"
        " ALTER TABLE fills DROP COLUMN fill_id;"
        " ALTER TABLE orders DROP COLUMN kind; ALTER TABLE orders DROP COLUMN filled_qty;"
        " ALTER TABLE orders DROP COLUMN cancelled_qty; ALTER TABLE orders DROP COLUMN close_time;"
        " PRAGMA user_version = 8;"
    )
    conn.close()
    with Store(db) as store:
        rec = store.order(coid(0))
        assert rec is not None and (rec.kind, rec.filled_qty, rec.cancelled_qty) == ("entry", 5, 0)
        assert rec.close_time is None
        (fill,) = rec.fills
        assert store.record_fill(fill) is False  # its id was backfilled
        assert store.order_status(submitted.client_order_id) == "submitted"
    assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 10
