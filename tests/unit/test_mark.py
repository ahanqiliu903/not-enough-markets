"""Mark-to-market of open positions (nem.execution.mark) and its report columns."""

import csv
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nem.execution.exits import walk_bids
from nem.execution.fees import taker_fee
from nem.execution.mark import MARK_MAX_AGE, mark_position
from nem.stats.report import (
    MARK_LABEL,
    NO_OPEN,
    STRATEGY_COLUMNS,
    SUMMARY_COLUMNS,
    TRADE_COLUMNS,
    build_report,
    open_marks,
    render_text,
    report_data,
    tables,
)
from nem.store import Store

from engine_helpers import portfolio, strategy
from factories import T0, make_snapshot, make_trade

RATE = 0.07
NOW = T0 + timedelta(minutes=5)
FIXTURE = Path(__file__).parents[2] / "contract" / "fixtures" / "basic" / "out" / "fixture"


def test_full_depth_yes() -> None:
    snap = make_snapshot(ts=NOW, depth={"yes": [(0.88, 3.0), (0.87, 10.0)], "no": [(0.10, 20.0)]})
    m = mark_position(make_trade(qty=5), snap, RATE, NOW)
    assert m is not None and m.no_bid == 0
    fee = taker_fee(0.88, 3, RATE) + taker_fee(0.87, 2, RATE)
    gross = 3 * 0.88 + 2 * 0.87
    assert m.contracts == 5
    assert m.bid == pytest.approx(gross / 5)
    assert m.value == pytest.approx(gross - fee)
    assert m.unrealized_pnl == pytest.approx(gross - fee - (0.90 * 5 + 0.02))
    assert m.mid == pytest.approx((0.88 + 0.90) / 2)  # best ask = 1 - best NO bid
    assert m.marked_at == NOW


def test_no_side_walks_no_bids() -> None:
    snap = make_snapshot(ts=NOW, depth={"yes": [(0.88, 50.0)], "no": [(0.11, 50.0)]})
    m = mark_position(make_trade(side="no", avg_price=0.10, qty=4), snap, RATE, NOW)
    assert m is not None and m.no_bid == 0
    assert m.bid == pytest.approx(0.11)
    assert m.value == pytest.approx(4 * 0.11 - taker_fee(0.11, 4, RATE))
    assert m.mid == pytest.approx((0.11 + 0.12) / 2)


def test_partial_depth_values_rest_at_zero() -> None:
    snap = make_snapshot(ts=NOW, depth={"yes": [(0.88, 2.5)], "no": [(0.10, 20.0)]})
    t = make_trade(qty=5)
    m = mark_position(t, snap, RATE, NOW)
    assert m is not None
    assert (m.contracts, m.no_bid) == (2, 3)  # fractional size floors; 3 have no bids
    assert m.bid == pytest.approx(0.88)  # price of the covered contracts only
    value = 2 * 0.88 - taker_fee(0.88, 2, RATE)
    assert m.value == pytest.approx(value)  # + $0 for the 3 uncovered
    assert m.unrealized_pnl == pytest.approx(value - t.cost)


def test_empty_book_side_is_worth_zero() -> None:
    snap = make_snapshot(ts=NOW, yes_bid=None, depth={"yes": [], "no": [(0.10, 20.0)]})
    t = make_trade()
    m = mark_position(t, snap, RATE, NOW)
    assert m is not None
    assert (m.contracts, m.no_bid, m.bid, m.value, m.mid) == (0, t.qty, None, 0.0, None)
    assert m.unrealized_pnl == pytest.approx(-t.cost)


def test_empty_book_side_keeps_reference_mid() -> None:
    snap = make_snapshot(ts=NOW, depth={"yes": [], "no": [(0.10, 20.0)]})
    m = mark_position(make_trade(), snap, RATE, NOW)
    assert m is not None and m.value == 0.0 and m.bid is None
    assert m.mid == pytest.approx((0.88 + 0.90) / 2)  # top-of-book bid, reference only


def test_no_depth_recorded_is_unmarked() -> None:
    # Top of book only (depth not polled): the bids are unknown, not absent, so nothing is
    # valued at $0 and the trade stays unmarked.
    assert mark_position(make_trade(), make_snapshot(ts=NOW), RATE, NOW) is None
    snap = make_snapshot(ts=NOW, depth={"no": [(0.10, 20.0)]})
    assert mark_position(make_trade(), snap, RATE, NOW) is None


@pytest.mark.parametrize(
    ("snap_ts", "now"),
    [
        (NOW - MARK_MAX_AGE - timedelta(seconds=1), NOW),  # stale
        (NOW + timedelta(seconds=1), NOW),  # from the future
        (T0 + timedelta(minutes=14), T0 + timedelta(minutes=15)),  # market closed
    ],
)
def test_stale_or_closed_is_none(snap_ts: datetime, now: datetime) -> None:
    snap = make_snapshot(ts=snap_ts, depth={"yes": [(0.88, 50.0)]})
    assert mark_position(make_trade(), snap, RATE, now) is None


def test_settled_trade_or_missing_snapshot_is_none() -> None:
    snap = make_snapshot(ts=NOW, depth={"yes": [(0.88, 50.0)]})
    settled = make_trade(won=True, realized_pnl=0.48, settled_at=NOW)
    assert mark_position(settled, snap, RATE, NOW) is None
    assert mark_position(make_trade(), None, RATE, NOW) is None
    assert mark_position(make_trade(ticker="OTHER"), snap, RATE, NOW) is None


def test_walk_bids_matches_stop_walk() -> None:
    sale = walk_bids([(0.5, 2.0), (0.4, 10.0)], 5, RATE)
    assert (sale.contracts, sale.lowest) == (5, 0.4)
    assert sale.gross == pytest.approx(2 * 0.5 + 3 * 0.4)
    assert sale.fee == round(taker_fee(0.5, 2, RATE) + taker_fee(0.4, 3, RATE), 2)
    short = walk_bids([(0.5, 2.0)], 5, RATE)
    assert short.contracts == 2


def test_open_marks_include_partial_and_empty_books() -> None:
    assert open_marks([], {}) == NO_OPEN
    a, b, c, d = (make_trade(f"W{i}", ticker=f"T{i}") for i in range(4))
    full = mark_position(
        a, make_snapshot(ts=NOW, ticker="T0", depth={"yes": [(0.88, 50.0)]}), RATE, NOW
    )
    part = mark_position(
        b, make_snapshot(ts=NOW, ticker="T1", depth={"yes": [(0.88, 1.0)]}), RATE, NOW
    )
    empty = mark_position(c, make_snapshot(ts=NOW, ticker="T2", depth={"yes": []}), RATE, NOW)
    assert full is not None and part is not None and empty is not None
    marks = {("fav90", "W0"): full, ("fav90", "W1"): part, ("fav90", "W2"): empty}
    o = open_marks([a, b, c, d], marks)  # d has no mark: the only unmarked one
    assert o.value == pytest.approx(full.value + part.value)
    assert o.unrealized_pnl == pytest.approx(
        full.unrealized_pnl + part.unrealized_pnl + empty.unrealized_pnl
    )
    assert (o.unmarked, o.no_bid) == (1, (b.qty - 1) + c.qty)
    only_empty = open_marks([c], {("fav90", "W2"): empty})
    assert (only_empty.value, only_empty.unmarked, only_empty.no_bid) == (0.0, 0, c.qty)
    assert only_empty.unrealized_pnl == pytest.approx(-c.cost)
    none = open_marks([d], marks)
    assert (none.value, none.unrealized_pnl, none.unmarked, none.no_bid) == (None, None, 1, None)


def _store(depth: float) -> Store:
    store = Store()
    t = make_trade(opened_at=T0)
    store.open_trade(t)
    store.insert_snapshot(make_snapshot(ts=T0, depth={"yes": [(0.80, 50.0)]}))
    store.insert_snapshot(
        make_snapshot(
            ts=NOW - timedelta(minutes=1), depth={"yes": [(0.88, depth)] if depth else []}
        )
    )
    store.insert_snapshot(make_snapshot(ts=NOW + timedelta(minutes=1)))  # after NOW: unseen
    return store


def test_report_rows_and_text() -> None:
    p = portfolio(strategies=[strategy(), strategy(name="idle")])
    r = build_report(_store(depth=50), p, NOW)
    summary, strategies, trades = tables(r)
    assert summary.columns == SUMMARY_COLUMNS and strategies.columns == STRATEGY_COLUMNS
    assert trades.columns == TRADE_COLUMNS
    value = round(5 * 0.88 - taker_fee(0.88, 5), 2)
    pnl = round(value - (0.90 * 5 + 0.02), 2)
    s = dict(zip(summary.columns, summary.rows[0], strict=True))
    assert (s["open_value_usd"], s["unrealized_pnl_usd"], s["open_unmarked"]) == (value, pnl, 0)
    assert s["open_no_bid_contracts"] == 0
    fav, idle = (dict(zip(strategies.columns, row, strict=True)) for row in strategies.rows)
    assert (fav["open_value_usd"], fav["unrealized_pnl_usd"]) == (value, pnl)
    assert (idle["open_value_usd"], idle["unrealized_pnl_usd"], idle["open_unmarked"]) == (
        0.0,
        0.0,
        0,
    )
    assert (fav["open_no_bid_contracts"], idle["open_no_bid_contracts"]) == (0, 0)
    (row,) = (dict(zip(trades.columns, row, strict=True)) for row in trades.rows)
    assert row["mark_bid_cents"] == 88.0 and row["mark_contracts"] == 5
    assert row["mark_value_usd"] == value and row["unrealized_pnl_usd"] == pnl
    assert row["marked_at"] == "2026-10-07T19:19:00Z"
    text = render_text(r)
    assert f"unrealized  {pnl:+,.2f} on 1 of 1 open (${value:,.2f} {MARK_LABEL})" in text
    assert "no bids" not in text
    data = report_data(r)
    assert data["summary"]["unrealized_pnl_usd"] == pnl  # type: ignore[index]


def test_report_partial_book_values_rest_at_zero() -> None:
    r = build_report(_store(depth=2), portfolio(), NOW)
    summary, strategies, trades = tables(r)
    value = round(2 * 0.88 - taker_fee(0.88, 2), 2)
    pnl = round(value - (0.90 * 5 + 0.02), 2)
    s = dict(zip(summary.columns, summary.rows[0], strict=True))
    assert (s["open_value_usd"], s["unrealized_pnl_usd"], s["open_unmarked"]) == (value, pnl, 0)
    assert s["open_no_bid_contracts"] == 3
    st = dict(zip(strategies.columns, strategies.rows[0], strict=True))
    assert (st["unrealized_pnl_usd"], st["open_no_bid_contracts"]) == (pnl, 3)
    row = dict(zip(trades.columns, trades.rows[0], strict=True))
    assert (row["contracts"], row["mark_contracts"], row["mark_bid_cents"]) == (5, 2, 88.0)
    assert (row["mark_value_usd"], row["unrealized_pnl_usd"]) == (value, pnl)
    text = render_text(r)
    assert f"unrealized  {pnl:+,.2f} on 1 of 1 open" in text
    assert "; 3 contracts with no bids valued at $0 (pending loss)" in text


def test_report_empty_bid_side_is_full_loss() -> None:
    r = build_report(_store(depth=0), portfolio(), NOW)
    summary, _, trades = tables(r)
    cost = round(0.90 * 5 + 0.02, 2)
    s = dict(zip(summary.columns, summary.rows[0], strict=True))
    assert (s["open_value_usd"], s["unrealized_pnl_usd"], s["open_unmarked"]) == (0.0, -cost, 0)
    assert s["open_no_bid_contracts"] == 5
    row = dict(zip(trades.columns, trades.rows[0], strict=True))
    assert (row["mark_contracts"], row["mark_bid_cents"], row["mark_value_usd"]) == (0, None, 0.0)
    assert row["unrealized_pnl_usd"] == -cost
    assert "; 5 contracts with no bids valued at $0 (pending loss)" in render_text(r)


MARK_COLUMNS = (
    "mark_bid_cents", "mark_mid_cents", "mark_contracts", "mark_value_usd",
    "unrealized_pnl_usd", "marked_at",
)  # fmt: skip


def test_report_stale_snapshot_leaves_marks_empty() -> None:
    r = build_report(_store(depth=50), portfolio(), NOW + MARK_MAX_AGE)
    summary, _, trades = tables(r)
    row = dict(zip(trades.columns, trades.rows[0], strict=True))
    assert all(row[c] is None for c in MARK_COLUMNS)
    s = dict(zip(summary.columns, summary.rows[0], strict=True))
    assert (s["open_value_usd"], s["open_unmarked"], s["open_no_bid_contracts"]) == (None, 1, None)
    assert "none of 1 open has a fresh book" in render_text(r)


def test_settled_trades_have_no_mark_columns() -> None:
    store = _store(depth=50)
    store.settle_trade(("research", "fav90"), make_trade().window_id, True, 0.48, NOW)
    _, _, trades = tables(build_report(store, portfolio(), NOW))
    row = dict(zip(trades.columns, trades.rows[0], strict=True))
    assert row["result"] == "won"
    assert all(row[c] is None for c in MARK_COLUMNS)


def test_basic_fixture_has_mark_columns() -> None:
    with (FIXTURE / "trades.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows and all(r["result"] != "open" for r in rows)
    assert all(r["marked_at"] == "" and r["unrealized_pnl_usd"] == "" for r in rows)
