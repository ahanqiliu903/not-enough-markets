from datetime import timedelta

from nem.core.clock import ManualClock
from nem.core.config import PortfolioConfig
from nem.core.context import Context
from nem.store import Store

from factories import T0, WINDOW, make_trade, portfolio_dict

CLOSE = T0 + timedelta(minutes=15)


def make_context(store: Store, clock: ManualClock) -> Context:
    p = PortfolioConfig.model_validate(portfolio_dict())
    return Context(p, p.strategy("fav90"), clock, store)


def test_same_window_result_is_invisible_until_settled() -> None:
    """Look-ahead guard: a result settled at or after `now` must not leak into a signal."""
    store, clock = Store(), ManualClock(T0)
    ctx = make_context(store, clock)
    store.open_trade(make_trade())
    store.settle_trade(ctx.key, WINDOW, False, -0.9, CLOSE)

    clock.set(CLOSE - timedelta(seconds=1))  # still inside the window
    assert ctx.settled_trades() == []
    clock.set(CLOSE)  # strict: settled exactly now is not yet known
    assert ctx.settled_trades() == []
    clock.advance(timedelta(microseconds=1))
    assert len(ctx.settled_trades()) == 1


def test_defaults_to_own_strategy() -> None:
    store, clock = Store(), ManualClock(T0)
    ctx = make_context(store, clock)
    for strategy in ("fav90", "fav85"):
        store.open_trade(make_trade(strategy=strategy))
        store.settle_trade(("research", strategy), WINDOW, True, 0.5, T0)
    clock.advance(timedelta(seconds=1))
    assert [t.strategy for t in ctx.settled_trades()] == ["fav90"]
    both = ctx.settled_trades(strategies=[("research", "fav90"), ("research", "fav85")])
    assert len(both) == 2


def test_stat_reads_own_strategy() -> None:
    store = Store()
    ctx = make_context(store, ManualClock(T0))
    store.bump_stat("research", "fav90", "yes", True)
    store.bump_stat("research", "fav85", "yes", False)
    assert ctx.stat("yes") == (1, 1)
