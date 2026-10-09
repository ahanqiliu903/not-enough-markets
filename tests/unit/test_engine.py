from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from nem.core.clock import EXCHANGE_TZ, ManualClock
from nem.core.context import Context, DataUnavailable
from nem.core.registry import PluginParams, register
from nem.core.types import Decision, MarketSnapshot, Signal
from nem.engine.runner import Runner, run_live
from nem.engine.runtime import BuildError
from nem.engine.settlement import realized_pnl
from nem.feeds.base import Feed, Observation
from nem.gates.base import Gate, GateParams
from nem.market.kalshi import KalshiError
from nem.market.source import FakeSource, Tick
from nem.store import Store

from engine_helpers import portfolio, settle, strategy, ticks, window
from factories import make_trade

OPEN = datetime(2026, 10, 7, 19, 0, tzinfo=UTC)
KEY = ("research", "fav90")


# Test-only plugins, registered once under names no real plugin uses.
@register("gate", "test_needs_feed")
class NeedsFeed(Gate):
    class Params(GateParams):
        feed: str
        below: float

    def __init__(self, params: Params) -> None:
        super().__init__(params)
        self.p = params

    def check(self, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
        value = ctx.feed(self.p.feed).require().value
        return Decision.ok() if value < self.p.below else Decision.skip(self.name)

    def feeds_used(self) -> Sequence[str]:
        return [self.p.feed]


@register("gate", "test_crashes")
class Crashes(Gate):
    def check(self, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
        raise RuntimeError("bug")


FETCHES: list[datetime] = []


@register("feed", "test_clock_feed")
class ClockFeed(Feed):
    """Reports the minute of the hour, known at fetch time."""

    class Params(PluginParams):
        pass

    def fetch(self, now: datetime) -> Sequence[Observation]:
        FETCHES.append(now)
        return [("value", float(now.minute), now)]


def make_runner(
    w: list[MarketSnapshot], result: str = "yes", fetch_feeds: bool = False, **pkw: Any
) -> tuple[Runner, Store, ManualClock]:
    store, clock = Store(), ManualClock(w[0].ts)
    source = FakeSource(ticks(w), [settle(w, result)])
    runner = Runner([portfolio(**pkw)], source, store, clock, fetch_feeds=fetch_feeds)
    return runner, store, clock


def replay(runner: Runner, w: list[MarketSnapshot]) -> None:
    runner.run(ticks(w))
    runner.settle_remaining(w[0].close_time + timedelta(minutes=1))


def test_full_cycle_buys_once_and_settles_win() -> None:
    w = window(OPEN)
    runner, store, _ = make_runner(w, "yes")
    replay(runner, w)
    [t] = store.settled_trades()
    assert (t.side, t.qty, t.avg_price, t.won) == ("yes", 5, 0.90, True)
    assert t.realized_pnl == pytest.approx(5 - (0.90 * 5 + t.fee))
    assert t.opened_at == OPEN  # first tick; later ticks see the open trade and skip
    assert store.stat(*KEY, "yes") == (1, 1)
    assert store.signal_reasons(*KEY) == {"ok: fixed 5": 1}


def test_loss_settles_negative() -> None:
    w = window(OPEN)
    runner, store, _ = make_runner(w, "no")
    replay(runner, w)
    [t] = store.settled_trades()
    assert t.won is False
    assert t.realized_pnl == pytest.approx(-(0.90 * 5 + t.fee))
    assert store.stat(*KEY, "yes") == (0, 1)


def test_realized_pnl() -> None:
    t = make_trade(avg_price=0.9, qty=10, fee=0.07)
    assert realized_pnl(t, True) == pytest.approx(10 - 9.07)
    assert realized_pnl(t, False) == pytest.approx(-9.07)


def test_unsettled_until_result_known() -> None:
    w = window(OPEN)
    runner, store, _ = make_runner(w)
    runner.run(ticks(w))  # last tick is before close: nothing to settle yet
    assert len(store.open_trades()) == 1
    runner.settle_remaining(w[0].close_time)  # result is published 3s after close
    assert len(store.open_trades()) == 1
    runner.settle_remaining(w[0].close_time + timedelta(seconds=3))
    assert store.open_trades() == []


def test_gate_skip_logged_once_per_window() -> None:
    w = window(OPEN, yes_ask=0.96)  # above the 0.95 max_entry_price gate
    confident = strategy()
    confident["signal"]["priors"] = {"yes": {"wins": 99, "n": 100}}  # enough edge at 0.96
    runner, store, _ = make_runner(w, strategies=[confident])
    replay(runner, w)
    assert store.settled_trades() == []
    assert store.signal_reasons(*KEY) == {"max_entry_price": 1}


def test_check_every_throttles_evaluation() -> None:
    w = window(OPEN, yes_ask=0.96)
    strat = strategy(check_every="1m", gates=[{"type": "time_in_window", "last_minutes": 1}])
    runner, store, _ = make_runner(w, strategies=[strat])
    seen: list[datetime] = []
    original = runner.strategies[0].signal.on_market

    def spy(snap: MarketSnapshot, ctx: Context) -> Signal | None:
        seen.append(ctx.now())
        return original(snap, ctx)

    runner.strategies[0].signal.on_market = spy  # type: ignore[method-assign]
    runner.run(ticks(w))
    assert len(seen) == 15  # once a minute across a 15-minute window, not every 5s
    del store


def test_paused_strategy_does_not_enter_but_still_settles() -> None:
    w1, w2 = window(OPEN), window(OPEN + timedelta(minutes=15))
    store, clock = Store(), ManualClock(OPEN)
    store.open_trade(make_trade(w1[0].window_id, ticker=w1[0].ticker, close_time=w1[0].close_time))
    p = portfolio(strategies=[strategy(status="paused")])
    runner = Runner([p], FakeSource(ticks(w1, w2), [settle(w1, "yes")]), store, clock)
    runner.run(ticks(w1, w2))
    assert len(store.settled_trades()) == 1  # existing position settled
    assert store.open_trades() == []  # but no new entry in w2


def test_retired_strategy_is_not_built() -> None:
    p = portfolio(strategies=[strategy(status="retired"), strategy(name="live_one")])
    runner = Runner([p], FakeSource([]), Store(), ManualClock(OPEN))
    assert [rt.config.name for rt in runner.strategies] == ["live_one"]


def test_live_portfolio_refused_until_implemented() -> None:
    with pytest.raises(BuildError, match="live trading"):
        Runner(
            [portfolio(mode="live", starting_balance=None, budget=50)],
            FakeSource([]),
            Store(),
            ManualClock(OPEN),
        )


def test_build_error_names_strategy_and_plugin() -> None:
    bad = strategy(sizing={"type": "fixed", "contracts": 0})
    with pytest.raises(BuildError, match=r"research/fav90 sizer 'fixed'"):
        Runner([portfolio(strategies=[bad])], FakeSource([]), Store(), ManualClock(OPEN))


# --- risk ---------------------------------------------------------------------------


def test_max_open_positions_blocks_second_market() -> None:
    btc, eth = window(OPEN), window(OPEN, series="KXETH15M")
    strategies = [strategy(), strategy(name="eth", series="KXETH15M")]
    store, clock = Store(), ManualClock(OPEN)
    p = portfolio(strategies=strategies, risk={"max_open_positions": 1})
    runner = Runner([p], FakeSource(ticks(btc, eth)), store, clock)
    runner.run(ticks(btc, eth))
    assert len(store.open_trades()) == 1
    assert store.signal_reasons("research", "eth") == {"risk:portfolio:max_open_positions": 1}


def test_insufficient_cash_blocks() -> None:
    w = window(OPEN)
    runner, store, _ = make_runner(w, starting_balance=3)  # 5 contracts at 0.90 = $4.50
    replay(runner, w)
    assert store.signal_reasons(*KEY) == {"risk:portfolio:insufficient_cash": 1}


def test_stop_if_losing_after_kills_strategy() -> None:
    # Each window's first tick comes before the previous window's result is published, so
    # after losses in windows 1 and 2, window 3 still trades and window 4 is the first block.
    windows = [window(OPEN + timedelta(minutes=15 * i)) for i in range(4)]
    store, clock = Store(), ManualClock(OPEN)
    strat = strategy(risk={"stop_if_losing_after": 2})
    source = FakeSource(ticks(*windows), [settle(w, "no") for w in windows])
    runner = Runner([portfolio(strategies=[strat])], source, store, clock)
    runner.run(ticks(*windows))
    runner.settle_remaining(windows[-1][0].close_time + timedelta(minutes=1))
    assert len(store.settled_trades()) == 3
    assert store.signal_reasons(*KEY)["risk:strategy:stop_if_losing_after"] == 1


def test_daily_loss_cap_and_drawdown() -> None:
    from nem.core.config import RiskConfig
    from nem.engine.risk import check_limits, max_drawdown

    now = datetime(2026, 10, 7, 20, 0, tzinfo=UTC)
    loss = make_trade(realized_pnl=-4.0, won=False, settled_at=now - timedelta(hours=1))
    win = make_trade(realized_pnl=3.0, won=True, settled_at=now - timedelta(hours=2))
    yesterday = make_trade(realized_pnl=-50.0, won=False, settled_at=now - timedelta(days=1))
    assert max_drawdown([win, loss]) == 4.0
    assert max_drawdown([yesterday, win]) == 50.0

    capped = RiskConfig(daily_loss_cap=4)
    assert check_limits(capped, "s", [], [win, loss], 1, now).take  # net today is only -1
    assert check_limits(capped, "s", [], [loss], 1, now).reason == "risk:s:daily_loss_cap"
    # yesterday's loss doesn't count toward today's cap
    assert check_limits(RiskConfig(daily_loss_cap=5), "s", [], [yesterday, loss], 1, now).take
    d = check_limits(RiskConfig(max_drawdown=40), "s", [], [yesterday, win], 1, now)
    assert d.reason == "risk:s:max_drawdown"
    d = check_limits(RiskConfig(max_allocation=5), "s", [make_trade()], [], 1, now)
    assert d.reason == "risk:s:max_allocation"


def test_risk_errors_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from nem.engine import risk

    def boom(*_a: object, **_k: object) -> Decision:
        raise RuntimeError("bug")

    monkeypatch.setattr(risk, "check_limits", boom)
    w = window(OPEN)
    runner, store, _ = make_runner(w)
    runner.run(ticks(w))
    assert store.open_trades() == []
    assert store.signal_reasons(*KEY) == {"risk:error:RuntimeError": 1}


# --- gates failing, feeds ------------------------------------------------------------


@pytest.mark.parametrize(("fail_open", "trades"), [(True, 1), (False, 0)])
def test_crashing_gate_respects_fail_open(fail_open: bool, trades: int) -> None:
    w = window(OPEN)
    strat = strategy(gates=[{"type": "test_crashes", "fail_open": fail_open}])
    runner, store, _ = make_runner(w, strategies=[strat])
    runner.run(ticks(w))
    assert len(store.open_trades()) == trades
    if not fail_open:
        assert store.signal_reasons(*KEY) == {"test_crashes:error_fail_closed": 1}


FEED = {"name": "minute", "type": "test_clock_feed", "every": "1m", "max_age": "90s"}


def test_feed_gate_reads_fresh_values() -> None:
    FETCHES.clear()
    w = window(OPEN)
    gates = [{"type": "test_needs_feed", "feed": "minute", "below": 5}]
    runner, store, _ = make_runner(
        w, fetch_feeds=True, feeds=[FEED], strategies=[strategy(gates=gates)]
    )
    runner.run(ticks(w))
    assert len(FETCHES) == 15  # every minute, not every tick
    assert len(store.feed_values("minute", "value", as_of=w[-1].ts)) == 15
    [t] = store.open_trades()
    assert t.opened_at == OPEN  # minute 0 < 5


def test_stale_or_missing_feed_fails_closed_by_default() -> None:
    w = window(OPEN)
    gates = [{"type": "test_needs_feed", "feed": "minute", "below": 99}]
    # replay without recorded feed data: the gate's data is unavailable
    runner, store, _ = make_runner(w, feeds=[FEED], strategies=[strategy(gates=gates)])
    runner.run(ticks(w))
    assert store.open_trades() == []
    assert store.signal_reasons(*KEY) == {"test_needs_feed:fail_closed": 1}


def test_feed_respects_max_age_and_known_at() -> None:
    from nem.core.types import FeedValue

    w = window(OPEN)
    store = Store()
    store.record_feed_values([FeedValue("minute", "value", 1.0, OPEN)], recorded_at=OPEN)
    p = portfolio(feeds=[FEED])
    ctx = Context(p, p.strategy("fav90"), ManualClock(OPEN - timedelta(seconds=1)), store)
    assert ctx.feed("minute").latest() is None  # not known yet
    ctx.clock.set(OPEN + timedelta(seconds=90))  # type: ignore[attr-defined]
    assert ctx.feed("minute").require().value == 1.0
    ctx.clock.set(OPEN + timedelta(seconds=91))  # type: ignore[attr-defined]
    with pytest.raises(DataUnavailable):
        ctx.feed("minute").require()
    with pytest.raises(KeyError, match="not declared"):
        ctx.feed("nope")
    del w


def test_undeclared_feed_is_a_build_error() -> None:
    gates = [{"type": "test_needs_feed", "feed": "missing", "below": 1}]
    with pytest.raises(BuildError, match="undeclared feeds"):
        Runner(
            [portfolio(strategies=[strategy(gates=gates)])],
            FakeSource([]),
            Store(),
            ManualClock(OPEN),
        )


# --- interest -----------------------------------------------------------------------


def test_interest_accrues_daily_on_equity() -> None:
    day1 = datetime(2026, 10, 7, 12, 0, tzinfo=EXCHANGE_TZ)
    store, clock = Store(), ManualClock(day1)
    p = portfolio(starting_balance=1000, interest_apy=0.0365, strategies=[])
    runner = Runner([p], FakeSource([]), store, clock)
    for hours in (0, 12, 24, 48):
        clock.set(day1 + timedelta(hours=hours))
        runner.on_tick(Tick(clock.now(), []))
    # two midnights crossed (Oct 8 and 9, 00:00 ET): $0.10/day on $1000 at 3.65%, and the
    # second day earns a hair more on the first day's interest
    assert store.ledger_total("research") == pytest.approx(0.20001, abs=1e-6)


def test_no_interest_when_unset() -> None:
    store, clock = Store(), ManualClock(OPEN)
    runner = Runner([portfolio(strategies=[])], FakeSource([]), store, clock)
    clock.set(OPEN + timedelta(days=3))
    runner.on_tick(Tick(clock.now(), []))
    assert store.ledger_total("research") == 0


# --- live loop ----------------------------------------------------------------------


def test_run_live_survives_errors() -> None:
    w = window(OPEN)
    runner, store, clock = make_runner(w)
    polls = iter([KalshiError("down"), httpx.ConnectError("down"), Tick(OPEN, w[:1])])

    def poll() -> Tick:
        item = next(polls)
        if isinstance(item, Exception):
            raise item
        return item

    sleeps: list[float] = []
    run_live(runner, poll, interval=5, iterations=3, sleep=sleeps.append)
    assert len(store.open_trades()) == 1
    assert len(sleeps) == 2
    assert store.last_heartbeat("runner") == clock.now()
