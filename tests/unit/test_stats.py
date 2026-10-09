import math
from datetime import timedelta

import pytest

from nem.stats.metrics import (
    brier,
    calibration,
    cost_per_contract,
    max_drawdown,
    sprt,
    strategy_stats,
    trades_needed,
    wilson,
)

from factories import T0, make_trade


def trade(won: bool, price: float = 0.9, qty: int = 10, fee: float = 0.07, i: int = 0):
    pnl = (qty if won else 0) - (price * qty + fee)
    return make_trade(
        f"W{i}", avg_price=price, qty=qty, fee=fee, won=won, realized_pnl=pnl,
        settled_at=T0 + timedelta(minutes=15 * i),
    )  # fmt: skip


def test_cost_per_contract_includes_fees() -> None:
    assert cost_per_contract(trade(True, price=0.9, qty=10, fee=0.07)) == pytest.approx(0.907)


@pytest.mark.parametrize(
    ("wins", "n", "low", "high"),
    [
        (4, 5, 0.3755, 0.9638),
        (0, 10, 0.0, 0.2775),
        (10, 10, 0.7225, 1.0),
        (50, 100, 0.4038, 0.5962),
    ],
)
def test_wilson_known_values(wins: int, n: int, low: float, high: float) -> None:
    ci = wilson(wins, n)
    assert ci is not None
    assert (ci.low, ci.high) == (pytest.approx(low, abs=1e-4), pytest.approx(high, abs=1e-4))


def test_wilson_empty() -> None:
    assert wilson(0, 0) is None


def test_trades_needed_matches_formula() -> None:
    # one-sided 5%, 80% power, break-even 90%, true 92%
    za, zb = 1.6448536, 0.8416212
    expected = ((za * math.sqrt(0.09) + zb * math.sqrt(0.92 * 0.08)) / 0.02) ** 2
    assert trades_needed(0.90, 0.02) == math.ceil(expected)
    assert trades_needed(0.90, 0.05) < trades_needed(0.90, 0.02)  # type: ignore[operator]


@pytest.mark.parametrize(("be", "edge"), [(0.9, 0), (0.9, -0.01), (0.99, 0.02), (0, 0.1)])
def test_trades_needed_impossible(be: float, edge: float) -> None:
    assert trades_needed(be, edge) is None


def test_sprt_decides_both_ways() -> None:
    assert sprt([]).decision == "undecided"
    # Winning every trade at 90c is strong evidence of an edge...
    wins = [trade(True, i=i) for i in range(200)]
    s = sprt(wins, edge=0.05)
    assert s.decision == "edge"
    assert s.llr >= s.upper
    # ...and winning only 80% at 90c is strong evidence against one
    mixed = [trade(i % 5 != 0, i=i) for i in range(200)]
    assert sprt(mixed, edge=0.05).decision == "no_edge"


def test_sprt_stops_at_first_crossing() -> None:
    losses_then_wins = [trade(False, i=i) for i in range(10)] + [
        trade(True, i=i) for i in range(10, 500)
    ]
    assert sprt(losses_then_wins, edge=0.05).decision == "no_edge"


def test_calibration_buckets() -> None:
    pairs = [(0.91, True), (0.915, False), (0.93, True), (0.95, True)]
    buckets = calibration(pairs, width=0.02)
    assert [(b.low, b.n) for b in buckets] == [(0.9, 2), (0.92, 1), (0.94, 1)]
    assert buckets[0].actual == 0.5
    assert buckets[0].predicted == pytest.approx(0.9125)
    assert calibration([(1.0, True)])[-1].high == 1.0  # p=1 lands in the top bucket


def test_brier() -> None:
    assert brier([]) is None
    assert brier([(1.0, True), (0.0, False)]) == 0
    assert brier([(0.9, False)]) == pytest.approx(0.81)


def test_max_drawdown() -> None:
    assert max_drawdown([]) == 0
    assert max_drawdown([1, 1, -3, 1, -2, 5]) == 4


def test_strategy_stats_empty() -> None:
    st = strategy_stats([])
    assert (st.n, st.win_rate, st.breakeven, st.edge, st.trades_needed) == (
        0,
        None,
        None,
        None,
        None,
    )
    assert st.sprt.decision == "undecided"


def test_strategy_stats() -> None:
    trades = [trade(True, i=0), trade(True, i=1), trade(False, i=2), trade(True, i=3)]
    st = strategy_stats(trades, [0.95, 0.95, 0.95, None])
    assert (st.n, st.wins, st.win_rate) == (4, 3, 0.75)
    assert st.breakeven == pytest.approx(0.907)
    assert st.edge == pytest.approx(0.75 - 0.907)
    assert st.edge_ci is not None
    assert st.edge_ci.low < st.edge < st.edge_ci.high  # type: ignore[operator]
    assert st.pnl == pytest.approx(3 * 0.93 - 9.07)
    assert st.max_drawdown == pytest.approx(9.07)
    assert st.z_vs_breakeven is not None and st.z_vs_breakeven < 0
    assert st.trades_needed_for == 0.02  # observed edge is negative: fall back to target
    assert st.brier == pytest.approx((0.05**2 * 2 + 0.95**2) / 3)  # None p_model skipped
    assert sum(b.n for b in st.calibration) == 3


def test_strategy_stats_uses_plausible_observed_edge() -> None:
    trades = [trade(i % 20 != 0, i=i) for i in range(100)]  # 95% wins at 90.7c
    st = strategy_stats(trades)
    assert st.trades_needed_for == pytest.approx(0.95 - 0.907)
    assert st.trades_needed == trades_needed(0.907, 0.95 - 0.907)
