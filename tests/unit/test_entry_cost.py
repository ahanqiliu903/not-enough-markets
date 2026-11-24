"""Pre-trade risk sees the full entry cost, fees included. Synthetic data only.

5 contracts at 0.90 cost $4.50 plus a $0.04 taker fee (0.07 x 5 x 0.9 x 0.1 = 0.0315,
rounded up to the cent) = $4.54.
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import Field

from nem.core.clock import ManualClock
from nem.core.context import Context
from nem.core.registry import PluginParams, register
from nem.core.types import MarketSnapshot, Order, Signal, Trade
from nem.engine.runner import Runner
from nem.engine.step import evaluate
from nem.execution.fees import taker_fee
from nem.execution.paper_broker import PaperBroker
from nem.market.source import FakeSource
from nem.signals.base import SignalPlugin
from nem.store import Store

from engine_helpers import portfolio, strategy, window

OPEN = datetime(2026, 10, 7, 14, 0, tzinfo=UTC)
KEY = ("research", "fav90")
COST = 4.54


@register("signal", "test_limit_buy")
class LimitBuy(SignalPlugin):
    """Buy YES at a fixed limit, so an order can walk several levels."""

    class Params(PluginParams):
        limit: float = Field(gt=0, lt=1)

    def __init__(self, params: Params) -> None:
        self.limit = params.limit

    def on_market(self, snap: MarketSnapshot, ctx: Context) -> Signal | None:
        return ctx.make_signal(snap, "yes", limit_price=self.limit, p_model=0.99)


def enter(snap: MarketSnapshot | None = None, **pkw: Any) -> tuple[Trade | None, Store, Runner]:
    snap = snap or window(OPEN)[0]
    store = Store()
    runner = Runner(
        [portfolio(**pkw)], FakeSource([]), store, ManualClock(snap.ts), fetch_feeds=False
    )
    rt = runner.strategies[0]
    return evaluate(rt, snap, runner.context(rt), store), store, runner


def cash(runner: Runner) -> float:
    return runner.context(runner.strategies[0]).cash()


def no_order(store: Store, snap: MarketSnapshot | None = None) -> bool:
    snap = snap or window(OPEN)[0]
    return store.order_attempts(KEY, snap.window_id, "yes") == 0


# --- cash -----------------------------------------------------------------------------


def test_fee_makes_cash_insufficient() -> None:
    # $4.50 covers the contracts but not the fee: nothing is ordered, filled or opened
    trade, store, runner = enter(starting_balance=4.5)
    assert trade is None
    assert store.open_trades() == []
    assert no_order(store)
    assert store.signal_reasons(*KEY) == {"risk:portfolio:insufficient_cash": 1}
    assert cash(runner) == pytest.approx(4.5)


def test_cash_exactly_covering_cost_and_fee_trades() -> None:
    trade, _, runner = enter(starting_balance=COST)
    assert trade is not None
    assert trade.cost == pytest.approx(COST)
    assert cash(runner) == pytest.approx(0.0, abs=1e-9)


def test_cash_one_cent_short_blocks() -> None:
    trade, store, _ = enter(starting_balance=COST - 0.01)
    assert trade is None
    assert store.signal_reasons(*KEY) == {"risk:portfolio:insufficient_cash": 1}


# --- allocation caps ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("risk_where", "series", "limit", "reason"),
    [
        ("strategy", "KXBTC15M", "max_allocation", "risk:strategy:max_allocation"),
        ("portfolio", "KXBTC15M", "max_allocation", "risk:portfolio:max_allocation"),
        (
            "strategy",
            "KXHIGHNY",
            "max_city_allocation",
            "risk:strategy:max_city_allocation:KNYC",
        ),
        (
            "portfolio",
            "KXHIGHNY",
            "max_city_allocation",
            "risk:portfolio:max_city_allocation:KNYC",
        ),
    ],
)
@pytest.mark.parametrize(("cap", "trades"), [(COST, True), (COST - 0.01, False)])
def test_allocation_caps_include_fee(
    risk_where: str, series: str, limit: str, reason: str, cap: float, trades: bool
) -> None:
    snap = window(OPEN, series=series)[0]
    risk = {limit: cap}
    if risk_where == "strategy":
        trade, store, _ = enter(snap, strategies=[strategy(series=series, risk=risk)])
    else:
        trade, store, _ = enter(snap, strategies=[strategy(series=series)], risk=risk)
    assert (trade is not None) is trades
    if trades:
        assert trade is not None and trade.cost == pytest.approx(COST)
    else:
        assert no_order(store, snap)
        assert store.signal_reasons(*KEY) == {reason: 1}


# --- fee schedules and fills ----------------------------------------------------------


@pytest.mark.parametrize(("balance", "trades"), [(4.5, True), (4.49, False)])
def test_zero_fee_schedule(balance: float, trades: bool) -> None:
    trade, _, _ = enter(starting_balance=balance, strategies=[strategy(taker_fee_rate=0)])
    assert (trade is not None) is trades
    if trade is not None:
        assert (trade.fee, trade.cost) == (0.0, pytest.approx(4.5))


def _two_levels() -> MarketSnapshot:
    # YES asks at 0.959 and 0.960, one contract each (NO bids at 0.041 and 0.040)
    return replace(
        window(OPEN)[0],
        yes_ask=0.959,
        no_bid=0.041,
        depth={"yes": [(0.95, 10)], "no": [(0.041, 1), (0.04, 1)]},
    )


# Per-level fees: 0.01 + 0.01 (each rounded up); notional 0.959 + 0.960 = 1.919
MULTI_LEVEL_COST = 1.939


def test_multi_level_fill_cost_is_exact_not_a_limit_price_estimate() -> None:
    # qty x limit with one fee at the limit (0.07 x 2 x 0.96 x 0.04, rounded up) understates
    # this fill: per-level rounding adds a cent. Risk uses the exact quote instead.
    naive = 2 * 0.96 + taker_fee(0.96, 2)
    assert naive == pytest.approx(1.93)
    order = Order("o", "p", "s", "W", "T", "yes", 0.96, 2)
    quote = PaperBroker(0.07).quote(order, _two_levels(), OPEN)
    fill = PaperBroker(0.07).submit(order, _two_levels(), OPEN)
    assert quote == fill
    assert fill is not None and fill.price * fill.qty + fill.fee == pytest.approx(MULTI_LEVEL_COST)


@pytest.mark.parametrize(("cap", "trades"), [(MULTI_LEVEL_COST, True), (1.93, False)])
def test_multi_level_fill_checked_at_its_real_cost(cap: float, trades: bool) -> None:
    strat = strategy(
        signal={"type": "test_limit_buy", "limit": 0.96},
        gates=[],
        sizing={"type": "fixed", "contracts": 2},
        risk={"max_allocation": cap},
    )
    trade, store, _ = enter(_two_levels(), strategies=[strat])
    assert (trade is not None) is trades
    if trade is not None:
        assert trade.qty == 2
        assert trade.cost == pytest.approx(MULTI_LEVEL_COST)
        assert trade.cost <= cap + 1e-9
    else:
        assert store.signal_reasons(*KEY) == {"risk:strategy:max_allocation": 1}


def test_partial_fill_checked_at_what_can_fill() -> None:
    # Only 3 of 5 contracts are offered at 0.90: 2.70 + fee 0.02 (0.0189 rounded up)
    snap = replace(window(OPEN)[0], depth={"yes": [(0.89, 50)], "no": [(0.10, 3)]})
    trade, _, runner = enter(snap, starting_balance=2.72)
    assert trade is not None
    assert (trade.qty, trade.cost) == (3, pytest.approx(2.72))
    assert cash(runner) == pytest.approx(0.0, abs=1e-9)
