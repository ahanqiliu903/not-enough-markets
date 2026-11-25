"""A signal or sizer bug skips that market for that strategy, never trades, and never stops
the tick. Synthetic markets only."""

from unittest.mock import patch

import pytest

from nem.core.clock import ManualClock
from nem.engine.runner import Runner
from nem.market.source import FakeSource, Tick
from nem.ops import process_health
from nem.store import Store

from engine_helpers import portfolio, settle, strategy, ticks, window
from factories import T0

W = window(T0)


def clock(r: Runner) -> ManualClock:
    assert isinstance(r.clock, ManualClock)
    return r.clock


def two_strategies() -> Runner:
    p = portfolio(strategies=[strategy(name="buggy"), strategy(name="fine")])
    store = Store()
    return Runner([p], FakeSource(ticks(W), [settle(W, "yes")]), store, ManualClock(W[0].ts))


@pytest.mark.parametrize(("part", "method"), [("signal", "on_market"), ("sizer", "size")])
def test_plugin_error_skips_only_that_strategy(part: str, method: str) -> None:
    r = two_strategies()
    buggy = r.strategies[0]
    with patch.object(getattr(buggy, part), method, side_effect=ZeroDivisionError("bug")):
        trades = r.on_tick(Tick(W[0].ts, [W[0]]))
        clock(r).set(W[1].ts)
        r.on_tick(Tick(W[1].ts, [W[1]]))
    assert [t.strategy for t in trades] == ["fine"]  # the other strategy still trades
    assert [t.strategy for t in r.store.open_trades()] == ["fine"]
    health = process_health(r.store, ["runner"], W[1].ts, W[1].ts - W[0].ts)
    assert health[0].state == "failing"
    assert health[0].detail == f"error: research/buggy {part}:error:ZeroDivisionError: bug"
    if part == "sizer":  # a sizer has a signal to log the reason against
        assert r.store.signal_reasons("research", "buggy") == {"sizer:error:ZeroDivisionError": 1}
    clock(r).set(W[2].ts)
    r.on_tick(Tick(W[2].ts, [W[2]]))  # fixed: the next tick reports ok again
    assert r.store.heartbeat_status("runner") == (W[2].ts, "ok")


def test_on_settle_error_does_not_undo_settlement() -> None:
    r = two_strategies()
    r.run(ticks(W))
    with patch.object(r.strategies[0].signal, "on_settle", side_effect=RuntimeError("bug")):
        r.settle_remaining(W[0].close_time + (W[1].ts - W[0].ts))
    assert len(r.store.settled_trades()) == 2
    assert r.strategies[0].errors == ["research/buggy signal:on_settle:error:RuntimeError: bug"]
