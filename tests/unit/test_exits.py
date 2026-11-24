"""Exits (take-profit, stop) and the maker fill models. Synthetic markets only."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from nem.core.clock import ManualClock
from nem.core.config import ConfigError, ExitConfig, load_portfolio
from nem.core.types import Level, MarketSnapshot, Trade
from nem.engine.exits import run_exits
from nem.engine.runner import Runner
from nem.engine.settlement import learn_exits, settle_due
from nem.execution.exits import EXIT_STATUS, exit_order_id, side_bids, take_profit_fill
from nem.execution.fees import maker_fee, taker_fee
from nem.market.source import FakeSource
from nem.stats.exits import by_exit_kind, exit_kind
from nem.stats.report import build_report, render_text, report_data
from nem.store import Store
from nem.store.sqlite import SCHEMA_VERSION

from engine_helpers import portfolio, settle, strategy, ticks, window
from factories import portfolio_dict

OPEN = datetime(2026, 10, 7, 19, 0, tzinfo=UTC)
KEY = ("research", "fav90")
MINUTE = timedelta(minutes=1)


def book(s: MarketSnapshot, yes: list[Level], no: list[Level]) -> MarketSnapshot:
    """`s` with these resting bids (best first) and the matching top of book."""
    return replace(
        s,
        yes_bid=yes[0][0] if yes else None,
        no_bid=no[0][0] if no else None,
        yes_ask=round(1 - no[0][0], 4) if no else None,
        no_ask=round(1 - yes[0][0], 4) if yes else None,
        depth={"yes": yes, "no": no},
    )


def market(*books: tuple[list[Level], list[Level]], start: datetime = OPEN) -> list[MarketSnapshot]:
    """A 15-minute window quoted every minute. Entry (YES at 0.90) happens on the first
    snapshot; `books` replace the snapshots after it, in order."""
    w = window(start, every=MINUTE)
    for i, (yes, no) in enumerate(books, start=1):
        w[i] = book(w[i], yes, no)
    return w


def run(
    w: list[MarketSnapshot],
    exit: dict[str, Any] | None,
    result: str = "yes",
    extra: list[list[MarketSnapshot]] | None = None,
) -> tuple[Store, Runner]:
    windows = [w, *(extra or [])]
    kw: dict[str, Any] = {} if exit is None else {"exit": exit}
    store, clock = Store(), ManualClock(w[0].ts)
    source = FakeSource(ticks(*windows), [settle(x, result) for x in windows])
    runner = Runner([portfolio(strategies=[strategy(**kw)])], source, store, clock)
    runner.run(ticks(*windows))
    runner.settle_remaining(windows[-1][0].close_time + MINUTE)
    return store, runner


def only_trade(store: Store) -> Trade:
    (t,) = store.settled_trades()
    return t


def tp(price: float, model: str, **kw: Any) -> dict[str, Any]:
    # maker_fee_rate has no default any more (null = from the series at `nem run` start,
    # and these runs are offline), so the helper sets the maker rate these tests assume
    return {"take_profit": {"price": price, "fill_model": model, "maker_fee_rate": 0.0175, **kw}}


def dump(store: Store) -> list[list[tuple[Any, ...]]]:
    conn = store._conn  # pyright: ignore[reportPrivateUsage]
    return [
        [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in ("trades", "orders", "fills", "signals", "stats", "exit_learning")
    ]


# --- hold (default) -----------------------------------------------------------------


@pytest.mark.parametrize("exit", [None, {}, {"take_profit": None, "stop": None}])
def test_hold_is_unchanged(exit: dict[str, Any] | None) -> None:
    w = market(([(0.99, 100)], [(0.005, 100)]))  # a book any exit would sell into
    baseline, _ = run(w, None)
    store, _ = run(w, exit)
    assert dump(store) == dump(baseline)
    t = only_trade(store)
    assert t.settled_at == w[0].close_time + timedelta(seconds=3)
    assert t.realized_pnl == pytest.approx(t.qty - t.cost)
    assert exit_kind(store, t) == "settlement"


# --- take-profit fill models ------------------------------------------------------------

# t1: ask comes up to 0.95 (bid 0.94); t2: bid 0.95 x10; t3: bid 0.96 x10
LADDER = market(
    ([(0.94, 10)], [(0.05, 10)]),
    ([(0.95, 10)], [(0.04, 10)]),
    ([(0.96, 10)], [(0.03, 10)]),
)


@pytest.mark.parametrize(
    ("model", "step"), [("optimistic", 1), ("realistic", 2), ("pessimistic", 3)]
)
def test_take_profit_fill_models(model: str, step: int) -> None:
    store, _ = run(LADDER, tp(0.95, model), result="no")
    t = only_trade(store)
    fee = maker_fee(0.95, t.qty, 0.0175)
    assert t.settled_at == LADDER[step].ts
    assert t.realized_pnl == pytest.approx(0.95 * t.qty - fee - t.cost)
    assert t.won is True  # sold above cost, though the market later settled NO
    assert exit_kind(store, t) == "take_profit"
    assert store.order_status(exit_order_id(t)) == EXIT_STATUS["take_profit"]


def test_take_profit_needs_size_for_the_whole_position() -> None:
    w = market(([(0.95, 3)], [(0.04, 10)]), ([(0.95, 5)], [(0.04, 10)]))
    store, _ = run(w, tp(0.95, "realistic"))
    assert only_trade(store).settled_at == w[2].ts  # 3 < 5 contracts; 5 covers it


def test_pessimistic_99c_never_fills() -> None:
    w = market(([(0.99, 1000)], [(0.005, 10)]))
    store, _ = run(w, tp(0.99, "pessimistic"))
    t = only_trade(store)
    assert exit_kind(store, t) == "settlement"
    assert t.realized_pnl == pytest.approx(t.qty - t.cost)


def test_realistic_99c_fills_at_99c_bid() -> None:
    w = market(([(0.99, 1000)], [(0.005, 10)]))
    store, _ = run(w, tp(0.99, "realistic", maker_fee_rate=0))
    t = only_trade(store)
    assert (t.settled_at, t.realized_pnl) == (w[1].ts, pytest.approx(0.99 * t.qty - t.cost))


def test_optimistic_does_not_fill_while_others_offer_below() -> None:
    w = market(([(0.93, 10)], [(0.06, 10)]))  # ask 0.94 < 0.95: we'd be behind
    store, _ = run(w, tp(0.95, "optimistic"))
    assert exit_kind(store, only_trade(store)) == "settlement"


def test_top_of_book_only_counts_as_enough_size() -> None:
    s = replace(LADDER[2], depth={})
    assert side_bids(s, "yes", 7) == [(0.95, 7.0)]


# --- stop ------------------------------------------------------------------------------


def test_stop_walks_bids_with_taker_fees() -> None:
    bids: list[Level] = [(0.60, 2), (0.55, 2), (0.50, 10)]
    w = market(([(0.61, 10)], [(0.38, 10)]), (bids, [(0.39, 10)]))
    store, _ = run(w, {"stop": {"bid_at_or_below": 0.60}}, result="yes")
    t = only_trade(store)
    avg = (0.60 * 2 + 0.55 * 2 + 0.50 * 1) / 5
    fee = taker_fee(0.60, 2) + taker_fee(0.55, 2) + taker_fee(0.50, 1)
    assert t.settled_at == w[2].ts  # 0.61 didn't trigger
    assert t.realized_pnl == pytest.approx(avg * 5 - fee - t.cost)
    assert t.won is False
    assert exit_kind(store, t) == "stop"
    conn = store._conn  # pyright: ignore[reportPrivateUsage]
    price, qty, f = conn.execute(
        "SELECT price, qty, fee FROM fills WHERE client_order_id = ?", (exit_order_id(t),)
    ).fetchone()
    assert (price, qty, f) == (pytest.approx(avg), 5, pytest.approx(fee))


def test_stop_waits_when_bids_cannot_absorb_the_position() -> None:
    w = market(([(0.40, 2)], [(0.50, 10)]), ([], [(0.50, 10)]), ([(0.30, 50)], [(0.60, 10)]))
    store, _ = run(w, {"stop": {"bid_at_or_below": 0.60}})
    t = only_trade(store)
    assert t.settled_at == w[3].ts
    assert t.realized_pnl == pytest.approx(0.30 * 5 - taker_fee(0.30, 5) - t.cost)


def test_take_profit_and_stop_together() -> None:
    cfg = {**tp(0.95, "realistic"), "stop": {"bid_at_or_below": 0.70}}
    up = market(([(0.95, 10)], [(0.04, 10)]))
    down = market(([(0.70, 10)], [(0.29, 10)]), start=OPEN + timedelta(minutes=15))
    store, _ = run(up, cfg, extra=[down])
    kinds = by_exit_kind(store, store.settled_trades())
    assert set(kinds) == {"take_profit", "stop"}
    assert kinds["take_profit"].n == kinds["stop"].n == 1
    assert kinds["stop"].pnl < 0 < kinds["take_profit"].pnl


def test_exits_run_while_halted() -> None:
    w = market(([(0.95, 10)], [(0.04, 10)]))
    store, clock = Store(), ManualClock(w[0].ts)
    p = portfolio(strategies=[strategy(exit=tp(0.95, "realistic"))])
    runner = Runner([p], FakeSource(ticks(w), [settle(w, "yes")]), store, clock)
    runner.run(ticks(w)[:1])
    store.halt("research", "test", w[0].ts)
    runner.run(ticks(w)[1:2])
    assert exit_kind(store, only_trade(store)) == "take_profit"


# --- no look-ahead --------------------------------------------------------------------


def test_a_later_book_does_not_trigger_an_earlier_exit() -> None:
    w = market(([(0.89, 10)], [(0.10, 10)]), ([(0.89, 10)], [(0.10, 10)]), ([(0.95, 10)], []))
    store, clock = Store(), ManualClock(w[0].ts)
    p = portfolio(strategies=[strategy(exit=tp(0.95, "realistic"))])
    runner = Runner([p], FakeSource(ticks(w), [settle(w, "yes")]), store, clock)
    runner.run(ticks(w)[:3])
    assert len(store.open_trades()) == 1
    runner.run(ticks(w)[3:4])
    assert only_trade(store).settled_at == w[3].ts


def test_book_after_now_is_ignored() -> None:
    w = market(([(0.95, 10)], [(0.04, 10)]))
    store, clock = Store(), ManualClock(w[0].ts)
    p = portfolio(strategies=[strategy(exit=tp(0.95, "realistic"))])
    runner = Runner([p], FakeSource(ticks(w), []), store, clock)
    runner.run(ticks(w)[:1])
    (rt,) = runner.strategies
    assert run_exits(rt, [w[1]], w[1].ts - timedelta(seconds=1), store) == []
    assert len(run_exits(rt, [w[1]], w[1].ts, store)) == 1


def test_entry_book_cannot_fill_the_take_profit() -> None:
    w = market()
    w[0] = book(w[0], [(0.95, 10)], [(0.10, 50)])  # entry at 0.90 while a 0.95 bid rests
    store, _ = run(w, tp(0.95, "optimistic"))
    t = only_trade(store)
    assert exit_kind(store, t) == "settlement"
    cfg = ExitConfig.model_validate(tp(0.95, "optimistic")).take_profit
    assert cfg is not None
    assert take_profit_fill(t, cfg, w[0]) is None
    assert take_profit_fill(t, cfg, replace(w[0], ts=t.opened_at + MINUTE)) is not None


# --- exits feed learning at settlement ----------------------------------------------------


def test_exit_learns_settlement_outcome_not_pnl() -> None:
    store, _ = run(LADDER, tp(0.95, "realistic"), result="no")
    t = only_trade(store)
    assert t.won is True and exit_kind(store, t) == "take_profit"  # P&L > 0 ...
    assert store.stat(*KEY, "yes") == (0, 1)  # ... but YES lost at settlement
    assert store.exit_outcomes(KEY) == {t.window_id: False}
    assert t.realized_pnl == pytest.approx(0.95 * t.qty - maker_fee(0.95, t.qty, 0.0175) - t.cost)
    tp_row = by_exit_kind(store, [t])["take_profit"]
    assert (tp_row.wins, tp_row.won_at_settlement, tp_row.pending) == (1, 0, 0)


def test_stop_that_would_have_won_counts_as_a_win() -> None:
    w = market(([(0.60, 10)], [(0.39, 10)]))
    store, _ = run(w, {"stop": {"bid_at_or_below": 0.60}}, result="yes")
    t = only_trade(store)
    assert t.won is False and exit_kind(store, t) == "stop"
    assert store.stat(*KEY, "yes") == (1, 1)


def test_learning_waits_for_the_settlement_and_happens_once() -> None:
    w = LADDER
    result = settle(w, "yes")
    store, clock = Store(), ManualClock(w[0].ts)
    p = portfolio(strategies=[strategy(exit=tp(0.95, "realistic"))])
    source = FakeSource(ticks(w), [result])
    runner = Runner([p], source, store, clock)
    runner.run(ticks(w))
    assert exit_kind(store, only_trade(store)) == "take_profit"
    # sold, market still open: nothing learned, nothing due
    assert store.stat(*KEY, "yes") == (0, 0)
    assert store.learning_due(w[-1].ts) == []
    # closed but the result isn't known yet: still pending
    assert len(store.learning_due(w[0].close_time)) == 1
    assert learn_exits(store, source, result.settled_at - timedelta(seconds=1)) == []
    assert store.stat(*KEY, "yes") == (0, 0)
    calls: list[Trade] = []
    (learned,) = learn_exits(store, source, result.settled_at, calls.append)
    assert calls == [learned]
    assert learned.won is True  # the settlement outcome ...
    assert learned.realized_pnl == only_trade(store).realized_pnl  # ... P&L stays the exit's
    assert store.stat(*KEY, "yes") == (1, 1)
    # again, and through the runner's settlement pass: no double count
    assert learn_exits(store, source, result.settled_at, calls.append) == []
    settle_due(store, source, result.settled_at + MINUTE, calls.append)
    assert store.stat(*KEY, "yes") == (1, 1) and len(calls) == 1
    assert not store.learn_exit(learned, True, result.settled_at)


def test_pending_learning_survives_a_restart(tmp_path: Path) -> None:
    w = LADDER
    db = tmp_path / "nem.db"
    p = portfolio(strategies=[strategy(exit=tp(0.95, "realistic"))])
    source = FakeSource(ticks(w), [settle(w, "yes")])
    with Store(db) as store:
        Runner([p], source, store, ManualClock(w[0].ts)).run(ticks(w))
        assert store.stat(*KEY, "yes") == (0, 0)
    with Store(db) as store:
        runner = Runner([p], source, store, ManualClock(w[-1].ts))
        runner.settle_remaining(w[0].close_time + MINUTE)
        runner.settle_remaining(w[0].close_time + 2 * MINUTE)
        assert store.stat(*KEY, "yes") == (1, 1)


def test_exit_learning_feeds_the_signal() -> None:
    """The next window's p_model includes the exited trade's settlement outcome."""
    first = market(([(0.95, 10)], [(0.04, 10)]))
    second = market(start=OPEN + timedelta(minutes=30))
    store, _ = run(first, tp(0.95, "realistic"), result="no", extra=[second])
    conn = store._conn  # pyright: ignore[reportPrivateUsage]
    p_models = [r[0] for r in conn.execute("SELECT p_model FROM signals ORDER BY ts")]
    assert p_models == [pytest.approx(95 / 100), pytest.approx(95 / 101)]


def test_replay_with_exits_is_deterministic() -> None:
    cfg = {**tp(0.95, "realistic"), "stop": {"bid_at_or_below": 0.70}}
    up = market(([(0.95, 10)], [(0.04, 10)]))
    down = market(([(0.70, 10)], [(0.29, 10)]), start=OPEN + timedelta(minutes=15))
    a, _ = run(up, cfg, extra=[down])
    b, _ = run(up, cfg, extra=[down])
    assert dump(a) == dump(b)
    assert store_rows(a, "exit_learning") == 2


def store_rows(store: Store, table: str) -> int:
    conn = store._conn  # pyright: ignore[reportPrivateUsage]
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_v6_exits_are_queued_by_the_migration(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    w = LADDER
    p = portfolio(strategies=[strategy(exit=tp(0.95, "realistic"))])
    source = FakeSource(ticks(w), [settle(w, "no")])
    with Store(db) as store:
        Runner([p], source, store, ManualClock(w[0].ts)).run(ticks(w))
    conn = sqlite3.connect(db)  # back to v6: the exit was recorded without a learning row
    conn.executescript("DROP TABLE exit_learning; PRAGMA user_version = 6;")
    conn.close()
    with Store(db) as store:
        (t,) = store.learning_due(w[0].close_time)
        assert exit_kind(store, t) == "take_profit"
        settle_due(store, source, w[0].close_time + MINUTE)
        assert store.stat(*KEY, "yes") == (0, 1)
    assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


# --- fees -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "qty", "rate", "fee"),
    [
        (0.99, 100, 0.0175, 0.02),
        (0.95, 5, 0.0175, 0.01),
        (0.50, 100, 0.0175, 0.44),
        (0.95, 5, 0, 0),
    ],
)
def test_maker_fee(price: float, qty: int, rate: float, fee: float) -> None:
    assert maker_fee(price, qty, rate) == fee


def test_maker_fee_rate_zero_is_free() -> None:
    store, _ = run(LADDER, tp(0.95, "realistic", maker_fee_rate=0))
    t = only_trade(store)
    assert t.realized_pnl == pytest.approx(0.95 * t.qty - t.cost)


# --- config validation ------------------------------------------------------------------


def write_portfolio(tmp_path: Path, exit: object) -> Path:
    d = portfolio_dict()
    d["strategies"][0]["exit"] = exit
    path = tmp_path / "research.yaml"
    path.write_text(yaml.safe_dump(d))
    return path


def test_exit_yaml_loads(tmp_path: Path) -> None:
    exit = {**tp(0.99, "pessimistic"), "stop": {"bid_at_or_below": 0.5}}
    cfg = load_portfolio(write_portfolio(tmp_path, exit)).strategies[0].exit
    assert cfg is not None and cfg.take_profit is not None and cfg.stop is not None
    assert (cfg.take_profit.price, cfg.take_profit.fill_model) == (0.99, "pessimistic")
    assert cfg.take_profit.maker_fee_rate == 0.0175  # set by tp(); no default (test_fees)
    assert not cfg.holds


@pytest.mark.parametrize(
    ("exit", "match"),
    [
        ({"take_profit": {"price": 0.99}}, "fill_model"),
        (tp(0.99, "hopeful"), "fill_model"),
        (tp(1.0, "realistic"), "price"),
        (tp(0.0, "realistic"), "price"),
        (tp(0.955, "realistic"), "whole cent"),
        (tp(0.99, "realistic", maker_fee_rate=-0.01), "maker_fee_rate"),
        ({"stop": {"bid_at_or_below": 0}}, "bid_at_or_below"),
        ({"stop": {"bid_at_or_below": 0.5, "limit": 0.1}}, "limit"),
        ({**tp(0.9, "realistic"), "stop": {"bid_at_or_below": 0.9}}, "below take_profit"),
        ({"trailing": {}}, "trailing"),
        ("hold", "exit"),
    ],
)
def test_exit_yaml_errors(tmp_path: Path, exit: object, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        load_portfolio(write_portfolio(tmp_path, exit))


# --- reports ---------------------------------------------------------------------------


def test_summary_splits_exits_and_shows_settlement_outcomes() -> None:
    cfg = {**tp(0.95, "realistic"), "stop": {"bid_at_or_below": 0.70}}
    up = market(([(0.95, 10)], [(0.04, 10)]))
    down = market(([(0.70, 10)], [(0.29, 10)]), start=OPEN + timedelta(minutes=15))
    store, runner = run(up, cfg, result="yes", extra=[down])
    (p,) = runner.portfolios
    report = build_report(store, p, down[-1].close_time + MINUTE)
    text = render_text(report)
    assert "by exit     take_profit 1 " in text
    assert "(1 with P&L > 0; side won at settlement 1)" in text
    assert "stop 1 " in text and "(0 with P&L > 0; side won at settlement 1)" in text
    (row,) = cast("list[dict[str, Any]]", report_data(report)["strategies"])
    assert row["exits"]["stop"] == {
        "trades": 1, "wins": 0, "contracts": 5, "pnl_usd": row["exits"]["stop"]["pnl_usd"],
        "won_at_settlement": 1, "learning_pending": 0,
    }  # fmt: skip
    assert set(row["exits"]) == {"take_profit", "stop"}


def test_summary_of_a_holding_strategy_has_no_exit_split() -> None:
    store, runner = run(market(), None)
    (p,) = runner.portfolios
    report = build_report(store, p, OPEN + timedelta(hours=1))
    assert "by exit" not in render_text(report)
    (row,) = cast("list[dict[str, Any]]", report_data(report)["strategies"])
    assert "exits" not in row
