"""Liquidity is only what the recorded book shows (audit R2) and is shared by the strategies
of one portfolio within a tick (audit R8). Synthetic markets only."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from nem.backtest.sweep import run_sweep
from nem.backtest.types import Grid, Period
from nem.core.clock import ManualClock
from nem.core.config import PortfolioConfig
from nem.core.types import Level, MarketSnapshot, Trade
from nem.engine.runner import Runner
from nem.execution.book import BookTaken
from nem.history.backtest import quote_fills
from nem.market.source import FakeSource
from nem.stats.provenance import QUOTE_FILLS_LABEL
from nem.stats.report import build_report, render_text
from nem.store import Store

from engine_helpers import portfolio, settle, strategy, ticks, window
from factories import T0, make_snapshot, portfolio_dict

MINUTE = timedelta(minutes=1)


def book(s: MarketSnapshot, yes: list[Level], no: list[Level]) -> MarketSnapshot:
    return replace(
        s,
        yes_bid=yes[0][0] if yes else None,
        no_bid=no[0][0] if no else None,
        yes_ask=round(1 - no[0][0], 4) if no else None,
        no_ask=round(1 - yes[0][0], 4) if yes else None,
        depth={"yes": yes, "no": no},
    )


def runner(
    portfolios: list[PortfolioConfig], *windows: list[MarketSnapshot]
) -> tuple[Runner, Store]:
    store = Store()
    source = FakeSource(ticks(*windows), [settle(w, "yes") for w in windows])
    return Runner(portfolios, source, store, ManualClock(windows[0][0].ts)), store


def opened(store: Store, name: str) -> list[Trade]:
    return [t for t in store.open_trades() if t.strategy == name]


def qty(store: Store, name: str) -> int:
    return sum(t.qty for t in opened(store, name))


# --- R8: one portfolio, one tick, one book -----------------------------------------------


@pytest.mark.parametrize(("depth", "a", "b"), [(5, 5, 0), (7, 5, 2), (10, 5, 5)])
def test_strategies_of_one_portfolio_share_the_book(depth: float, a: int, b: int) -> None:
    w = window(T0, every=MINUTE, depth=depth)
    p = portfolio(strategies=[strategy(name="a"), strategy(name="b")])
    r, store = runner([p], w)
    r.run(ticks(w)[:1])
    assert (qty(store, "a"), qty(store, "b")) == (a, b)  # file order: a first
    assert qty(store, "a") + qty(store, "b") <= depth
    if b == 0:
        assert store.signal_reasons("research", "b") == {"book_empty": 1}


def test_book_replenishes_on_the_next_tick() -> None:
    w = window(T0, every=MINUTE, depth=5)
    p = portfolio(strategies=[strategy(name="a"), strategy(name="b")])
    r, store = runner([p], w)
    r.run(ticks(w)[:2])
    assert (qty(store, "a"), qty(store, "b")) == (5, 5)
    assert opened(store, "b")[0].opened_at == w[1].ts


def test_separate_portfolios_do_not_share_a_book() -> None:
    w = window(T0, every=MINUTE, depth=5)
    p1 = portfolio(name="one", strategies=[strategy(name="a")])
    p2 = portfolio(name="two", strategies=[strategy(name="b")])
    r, store = runner([p1, p2], w)
    r.run(ticks(w)[:1])
    assert (qty(store, "a"), qty(store, "b")) == (5, 5)


def test_exits_of_one_portfolio_share_the_bids() -> None:
    w = window(T0, every=MINUTE, depth=10)  # both enter 5 at 0.90 on the first tick
    w[1] = book(w[1], [(0.60, 5)], [(0.39, 10)])  # stop level, bids for one position only
    w[2] = book(w[2], [(0.60, 5)], [(0.39, 10)])
    stop = {"stop": {"bid_at_or_below": 0.60}}
    p = portfolio(strategies=[strategy(name="a", exit=stop), strategy(name="b", exit=stop)])
    r, store = runner([p], w)
    r.run(ticks(w)[:2])
    assert (qty(store, "a"), qty(store, "b")) == (0, 5)  # a sold into the 5 bids first
    r.run(ticks(w)[2:3])
    assert store.open_trades() == []  # the next poll's bids take b's position


def test_take_marks_only_the_levels_a_fill_walked() -> None:
    snap = make_snapshot(depth={"yes": [(0.88, 3.0), (0.87, 4.0)], "no": [(0.10, 2.0)]})
    taken = BookTaken()
    taken.take(taken.view(snap), "yes", 5)  # e.g. a stop selling 5 YES
    assert taken.view(snap).depth == {"yes": [(0.87, 2.0)], "no": [(0.10, 2.0)]}
    taken.take(taken.view(snap), "yes", 9, at_or_above=0.88)  # nothing left at 0.88
    assert taken.view(snap).depth["yes"] == [(0.87, 2.0)]
    other = replace(snap, ticker="OTHER")
    assert taken.view(other) is other


def test_view_keeps_unrecorded_sides_unrecorded() -> None:
    snap = make_snapshot(depth={"no": [(0.10, 2.0)]})
    taken = BookTaken()
    taken.take(snap, "no", 2)
    assert taken.view(snap).depth == {"no": []}  # recorded, now empty; YES still unknown


def test_sweep_variants_each_see_the_whole_book() -> None:
    ws = [window(T0 + timedelta(minutes=15 * i), every=MINUTE, depth=5) for i in range(2)]
    data = Store()
    for w in ws:
        data.insert_snapshots(w)
        data.record_settlement(settle(w, "yes"))
    grid: Grid = {"gates.0.value": [0.95, 0.96]}  # both variants trade identically
    period = Period(ws[0][0].ts, ws[-1][-1].ts + MINUTE)
    [a, b] = run_sweep(data, portfolio(), "fav90", grid, [period])[period]
    assert (a.stats.n, a.stats.pnl) == (b.stats.n, b.stats.pnl) == (2, a.stats.pnl)
    assert a.stats.pnl > 0


# --- R2: no recorded depth, no liquidity -------------------------------------------------


def top_of_book_only(w: list[MarketSnapshot]) -> list[MarketSnapshot]:
    return [replace(s, depth={}) for s in w]


def quote(**kw: Any) -> PortfolioConfig:
    return portfolio(fills={"missing_depth": "quote"}, **kw)


def test_top_of_book_only_never_enters_by_default() -> None:
    w = top_of_book_only(window(T0, every=MINUTE))
    r, store = runner([portfolio()], w)
    r.run(ticks(w))
    assert store.open_trades() == []
    assert store.signal_reasons("research", "fav90") == {"no_depth_recorded": 1}


def test_quote_mode_enters_and_is_labelled() -> None:
    w = top_of_book_only(window(T0, every=MINUTE))
    p = quote()
    r, store = runner([p], w)
    r.run(ticks(w)[:1])
    (t,) = store.open_trades()
    assert (t.qty, t.avg_price) == (5, 0.90)
    text = render_text(build_report(store, p, w[1].ts))
    assert f"[{QUOTE_FILLS_LABEL}]" in text
    assert QUOTE_FILLS_LABEL not in render_text(build_report(store, portfolio(), w[1].ts))


@pytest.mark.parametrize(
    "exit",
    [
        {"stop": {"bid_at_or_below": 0.60}},
        {"take_profit": {"price": 0.95, "fill_model": "realistic", "maker_fee_rate": 0}},
    ],
)
def test_exits_need_recorded_bids(exit: dict[str, Any]) -> None:
    w = window(T0, every=MINUTE)
    bid = 0.60 if "stop" in exit else 0.95
    for i in range(1, len(w)):  # after the entry: a bid at the trigger, no depth recorded
        w[i] = replace(w[i], yes_bid=bid, depth={})
    for p, exits in ((portfolio(strategies=[strategy(exit=exit)]), False),
                     (quote(strategies=[strategy(exit=exit)]), True)):  # fmt: skip
        r, store = runner([p], w)
        r.run(ticks(w)[:2])
        assert (store.open_trades() == []) is exits


def test_stop_does_not_fill_from_a_verified_empty_bid_side() -> None:
    w = window(T0, every=MINUTE)
    w[1] = replace(w[1], yes_bid=0.60, depth={"yes": [], "no": [(0.39, 10.0)]})
    r, store = runner([quote(strategies=[strategy(exit={"stop": {"bid_at_or_below": 0.6}})])], w)
    r.run(ticks(w)[:2])
    assert len(store.open_trades()) == 1


def test_fills_config_validates() -> None:
    assert portfolio().fills.missing_depth == "none"
    assert quote().fills.missing_depth == "quote"
    for bad in ({"missing_depth": "top_of_book"}, {"missing_depth": "quote", "extra": 1}):
        with pytest.raises(ValidationError):
            PortfolioConfig.model_validate(portfolio_dict(fills=bad))


def test_history_replay_sets_quote_fills_explicitly() -> None:
    p = portfolio()
    q = quote_fills(p)
    assert (p.fills.missing_depth, q.fills.missing_depth) == ("none", "quote")
    assert quote_fills(q) is q
