"""Excluding trades (`nem trade exclude`): kept and listed, but treated as never taken:
statistics, ctx, P&L, equity, cash and every risk limit leave them out. Synthetic data only."""

import csv
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from nem.cli import main
from nem.core.config import RiskConfig
from nem.core.types import Trade
from nem.engine.risk import check_limits, check_risk
from nem.reporting.csv_reporter import CsvReporter
from nem.stats.report import build_report, render_text, report_data, tables
from nem.store import Store, StoreError
from nem.store.sqlite import SCHEMA_VERSION

from engine_helpers import context, portfolio
from factories import T0, make_trade

KEY = ("research", "fav90")
CLOSE = T0 + timedelta(minutes=15)
LATER = CLOSE + timedelta(hours=1)
SECOND = timedelta(seconds=1)


def _window(i: int) -> str:
    return f"KXFAKE-26JAN0{i}"


def _settled(store: Store, i: int, won: bool, strategy: str = "fav90") -> Trade:
    t = make_trade(_window(i), strategy=strategy)
    store.open_trade(t)
    pnl = t.qty - t.cost if won else -t.cost
    store.settle_trade((t.portfolio, strategy), t.window_id, won, pnl, CLOSE + i * SECOND)
    store.bump_stat(t.portfolio, strategy, t.side, won)  # as settlement does
    return t


def _exclude(store: Store, t: Trade, reason: str = "bad fill") -> None:
    store.set_excluded(t, reason, CLOSE)


def _three(store: Store) -> list[Trade]:
    """Two wins and a big loss; the loss gets excluded in the tests."""
    return [_settled(store, 1, True), _settled(store, 2, True), _settled(store, 3, False)]


# --- store ------------------------------------------------------------------------------


def test_exclude_include_round_trip() -> None:
    store = Store()
    t = _settled(store, 1, False)
    (found,) = store.trades_for_ticker(t.ticker)
    assert not found.excluded and found.exclude_reason is None
    _exclude(store, t, "bad fill")
    (found,) = store.excluded_trades()
    assert found.excluded and found.exclude_reason == "bad fill" and found.excluded_at == CLOSE
    store.set_excluded(t, "replayed twice", LATER)  # re-exclude updates the reason
    (found,) = store.excluded_trades("research")
    assert (found.exclude_reason, found.excluded_at) == ("replayed twice", LATER)
    assert store.excluded_trades("other") == []
    store.set_excluded(t, None, None)
    assert store.excluded_trades() == []
    assert not store.trades_for_ticker(t.ticker)[0].excluded


def test_set_excluded_needs_reason_and_time_together() -> None:
    store = Store()
    t = _settled(store, 1, True)
    with pytest.raises(StoreError):
        store.set_excluded(t, "why", None)
    with pytest.raises(StoreError, match="no trade"):
        store.set_excluded(make_trade("KXFAKE-NOPE"), "why", CLOSE)


def test_settled_trades_filter_and_open_trades_keep_excluded() -> None:
    store = Store()
    trades = _three(store)
    _exclude(store, trades[2])
    assert len(store.settled_trades()) == 3  # default: every trade (money)
    assert len(store.settled_trades(include_excluded=False)) == 2
    open_t = make_trade(_window(4))
    store.open_trade(open_t)
    _exclude(store, open_t, "test order")
    (o,) = store.open_trades()
    assert o.excluded


def test_stat_leaves_out_excluded_trades_and_comes_back() -> None:
    store = Store()
    trades = _three(store)
    assert store.stat(*KEY, "yes") == (2, 3)
    _exclude(store, trades[2])
    assert store.stat(*KEY, "yes") == (2, 2)
    _exclude(store, trades[0])
    assert store.stat(*KEY, "yes") == (1, 1)
    store.set_excluded(trades[2], None, None)
    store.set_excluded(trades[0], None, None)
    assert store.stat(*KEY, "yes") == (2, 3)


def test_stat_excluded_before_settlement() -> None:
    store = Store()
    t = make_trade(_window(1))
    store.open_trade(t)
    _exclude(store, t)
    assert store.stat(*KEY, "yes") == (0, 0)
    store.settle_trade(KEY, t.window_id, True, 0.4, CLOSE)
    store.bump_stat(*KEY, "yes", True)
    assert store.stat(*KEY, "yes") == (0, 0)


def test_stat_exited_trade_counts_once_learned() -> None:
    store = Store()
    t = make_trade(_window(1))
    store.open_trade(t)
    store.exit_trade(KEY, t.window_id, True, 0.2, CLOSE - timedelta(minutes=5))
    _settled(store, 2, True)
    _exclude(store, t)
    assert store.stat(*KEY, "yes") == (1, 1)  # exit not learned yet: nothing to take out
    (due,) = store.learning_due(CLOSE)
    assert store.learn_exit(due, False, CLOSE)  # side lost at settlement
    assert store.stat(*KEY, "yes") == (1, 1)
    store.set_excluded(t, None, None)
    assert store.stat(*KEY, "yes") == (1, 2)


def test_v7_database_is_migrated(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    with Store(db) as s:
        t = _settled(s, 1, True)
    conn = sqlite3.connect(db)  # back to v7: trades without the exclusion columns
    conn.executescript(
        "ALTER TABLE trades DROP COLUMN excluded_at;"
        " ALTER TABLE trades DROP COLUMN exclude_reason;"
        " PRAGMA user_version = 7;"
    )
    conn.close()
    with Store(db) as s:
        (found,) = s.settled_trades()
        assert not found.excluded and found.realized_pnl == pytest.approx(t.qty - t.cost)
        _exclude(s, found)
        assert s.stat(*KEY, "yes") == (0, 0)
    assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert SCHEMA_VERSION == 8


# --- context and risk ---------------------------------------------------------------------


def test_context_leaves_excluded_out_of_statistics_and_money() -> None:
    store = Store()
    trades = _three(store)
    p = portfolio()
    ctx = context(p, store, LATER)
    _exclude(store, trades[2])
    assert [t.window_id for t in ctx.settled_trades()] == [_window(1), _window(2)]
    assert len(ctx.portfolio_settled_trades()) == 2
    assert [t.window_id for t in ctx.settled_trades(limit=1)] == [_window(2)]
    assert len(ctx.settled_positions()) == 2
    assert len(ctx.settled_positions(portfolio_wide=True)) == 2
    assert ctx.stat("yes") == (2, 2)
    assert ctx.equity() == pytest.approx(1000 + sum(t.qty - t.cost for t in trades[:2]))


def test_context_cash_ignores_excluded_open_positions() -> None:
    store = Store()
    t = make_trade(_window(1))
    store.open_trade(t)
    ctx = context(portfolio(), store, T0)
    assert ctx.cash() == pytest.approx(1000 - t.cost)
    _exclude(store, t)
    assert ctx.cash() == pytest.approx(1000)
    assert ctx.open_trades() == [] and ctx.open_trades(portfolio_wide=True) == []
    assert len(store.open_trades()) == 1  # still listed (and settled when it closes)


def test_risk_limits_ignore_excluded() -> None:
    store = Store()
    trades = _three(store)  # +0.48, +0.48, -4.52: losing overall
    loss = trades[2].cost
    limits = (
        RiskConfig(stop_if_losing_after=2),
        RiskConfig(daily_loss_cap=loss - 1),
        RiskConfig(max_drawdown=loss - 1),
    )
    for rule in limits:
        assert not check_limits(rule, "strategy", [], store.settled_trades(), 1.0, CLOSE).take
    _exclude(store, trades[2])
    ctx = context(portfolio(), store, LATER)
    for rule in limits:
        assert check_limits(rule, "strategy", [], ctx.settled_positions(), 1.0, CLOSE).take


def test_check_risk_ignores_excluded() -> None:
    store = Store()
    trades = _three(store)
    p = portfolio(risk={"daily_loss_cap": trades[2].cost - 1})
    assert check_risk(context(p, store, LATER), 1.0).reason == "risk:portfolio:daily_loss_cap"
    _exclude(store, trades[2])
    assert check_risk(context(p, store, LATER), 1.0).take


def test_excluded_open_trade_frees_its_slot() -> None:
    store = Store()
    t = make_trade(_window(1))
    store.open_trade(t)
    p = portfolio(risk={"max_open_positions": 1})
    assert check_risk(context(p, store, T0), 1.0).reason.endswith(":max_open_positions")
    _exclude(store, t)
    assert check_risk(context(p, store, T0), 1.0).take


# --- reports ------------------------------------------------------------------------------


def test_report_statistics_skip_excluded_money_keeps_them(tmp_path: Path) -> None:
    store = Store()
    trades = _three(store)
    p = portfolio()
    before = build_report(store, p, LATER)
    _exclude(store, trades[2], "fill at a stale price")
    r = build_report(store, p, LATER)
    (sr,) = r.strategies
    assert before.strategies[0].stats.n == 3
    assert (sr.stats.n, sr.stats.wins, sr.stats.win_rate) == (2, 2, 1.0)
    assert len(sr.trades) == 3 and len(sr.excluded) == 1  # still listed
    assert r.realized == pytest.approx(sum(t.qty - t.cost for t in trades[:2]))
    assert r.equity == pytest.approx(before.equity + trades[2].cost)  # the loss is gone

    summary, strategies, trade_table = tables(r)
    s_row = dict(zip(summary.columns, summary.rows[0], strict=True))
    st_row = dict(zip(strategies.columns, strategies.rows[0], strict=True))
    assert s_row["excluded_trades"] == 1 and st_row["excluded_trades"] == 1
    assert st_row["trades"] == 2
    rows = [dict(zip(trade_table.columns, row, strict=True)) for row in trade_table.rows]
    assert [(x["excluded"], x["exclude_reason"]) for x in rows] == [
        ("false", None), ("false", None), ("true", "fill at a stale price"),
    ]  # fmt: skip
    data = report_data(r)
    assert data["strategies"][0]["excluded_trades"] == 1  # type: ignore[index]

    text = render_text(r)
    assert f"  1 excluded (1 settled, P&L {-trades[2].cost:+,.2f} not counted" in text
    assert "    1 excluded (1 settled" in text  # strategy line
    assert "2 trades (2 won)" in text
    assert "excluded" not in render_text(before)

    CsvReporter(CsvReporter.Params(path=str(tmp_path))).publish(r)
    with (tmp_path / p.name / "trades.csv").open(newline="") as f:
        cells = [(row["excluded"], row["exclude_reason"]) for row in csv.DictReader(f)]
    assert cells[-1] == ("true", "fill at a stale price") and cells[0] == ("false", "")


def test_report_leaves_excluded_open_trade_out_of_money() -> None:
    store = Store()
    t = make_trade(_window(1))
    store.open_trade(t)
    _exclude(store, t)
    r = build_report(store, portfolio(), T0)
    assert (r.open_positions, r.open_cost, len(r.excluded)) == (0, 0, 1)
    (sr,) = r.strategies
    assert sr.open_positions == 0 and len(sr.open_trades) == 1  # still listed
    assert "1 excluded (all open;" in render_text(r)


# --- CLI ----------------------------------------------------------------------------------


def _cli(db: Path, tmp_path: Path, *args: str) -> int:
    return main(
        ["trade", *args, "--db", str(db)] + (["--dir", str(tmp_path)] if args[0] != "list" else [])
    )


def test_cli_exclude_include_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "nem.db"
    with Store(db) as s:
        t = _settled(s, 1, False)
        _settled(s, 2, True)
    assert _cli(db, tmp_path, "exclude", t.ticker, "--reason", "duplicate order") == 0
    out = capsys.readouterr().out
    assert f"Excluded research/fav90 {t.ticker}" in out and "duplicate order" in out
    assert _cli(db, tmp_path, "exclude", t.ticker, "--reason", "bad fill") == 0
    assert "Updated the reason" in capsys.readouterr().out
    assert _cli(db, tmp_path, "list", "--excluded") == 0
    out = capsys.readouterr().out
    assert "1 trades, 1 excluded" in out and "bad fill" in out and _window(2) not in out
    assert _cli(db, tmp_path, "list") == 0
    out = capsys.readouterr().out
    assert "2 trades, 1 excluded" in out and _window(2) in out
    with Store(db) as s:
        (x,) = s.excluded_trades()
        assert x.exclude_reason == "bad fill"
    assert _cli(db, tmp_path, "include", t.ticker) == 0
    assert "Included research/fav90" in capsys.readouterr().out
    with Store(db) as s:
        assert s.excluded_trades() == []
    assert _cli(db, tmp_path, "list", "--excluded") == 0
    assert "No excluded trades" in capsys.readouterr().out


def test_cli_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "nem.db"
    with Store(db) as s:
        a = _settled(s, 1, True)
        _settled(s, 1, False, strategy="fav85")  # same market, another strategy
    assert _cli(db, tmp_path, "exclude", "KXFAKE-NONE-T1", "--reason", "x") == 1
    assert "no trade on KXFAKE-NONE-T1" in capsys.readouterr().err
    assert _cli(db, tmp_path, "exclude", a.ticker, "--reason", "x") == 1
    err = capsys.readouterr().err
    assert "research/fav85, research/fav90" in err and "--strategy" in err
    assert _cli(db, tmp_path, "include", a.ticker, "--strategy", "fav90") == 1
    assert "no excluded trade" in capsys.readouterr().err
    assert _cli(db, tmp_path, "exclude", a.ticker, "--strategy", "fav85", "--reason", "x") == 0
    with Store(db) as s:
        (x,) = s.excluded_trades()
        assert x.strategy == "fav85"
    assert _cli(db, tmp_path, "include", a.ticker) == 0  # only one excluded: unambiguous
