"""Per-city exposure cap (risk) and city filter (gate). Synthetic data only."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from nem.core.clock import ManualClock
from nem.core.config import PortfolioConfig, RiskConfig
from nem.core.types import MarketSnapshot
from nem.engine.risk import check_city_limit
from nem.engine.runner import Runner
from nem.engine.runtime import BuildError, build_strategies
from nem.market.source import FakeSource
from nem.store import Store

from engine_helpers import portfolio, settle, strategy, ticks, window
from factories import make_trade, portfolio_dict

OPEN = datetime(2026, 10, 7, 14, 0, tzinfo=UTC)
NO_LIMITS: dict[str, Any] = {}  # strategy-level risk with every limit off


def run(
    p: PortfolioConfig, *windows: list[MarketSnapshot], result: str = "yes"
) -> tuple[Store, Runner]:
    store = Store()
    source = FakeSource(ticks(*windows), [settle(w, result) for w in windows])
    runner = Runner([p], source, store, ManualClock(windows[0][0].ts))
    runner.run(ticks(*windows))
    return store, runner


# --- config -------------------------------------------------------------------------


@pytest.mark.parametrize("cap", [0, -1])
def test_zero_city_cap_rejected(cap: float) -> None:
    with pytest.raises(ValidationError, match="use null to disable"):
        PortfolioConfig.model_validate(portfolio_dict(risk={"max_city_allocation": cap}))


def test_city_cap_off_by_default() -> None:
    assert RiskConfig().max_city_allocation is None
    d = check_city_limit(RiskConfig(), "p", [make_trade()], None, 1e9)
    assert d.take


# --- the cap ------------------------------------------------------------------------


def test_cap_allows_entry_under_limit() -> None:
    w = window(OPEN, series="KXHIGHNY")
    p = portfolio(strategies=[strategy(series="KXHIGHNY")], risk={"max_city_allocation": 10})
    store, _ = run(p, w)
    assert len(store.open_trades()) == 1


def test_cap_blocks_entry_over_limit() -> None:
    w = window(OPEN, series="KXHIGHNY")  # 5 contracts at 0.90 = $4.50
    p = portfolio(strategies=[strategy(series="KXHIGHNY")], risk={"max_city_allocation": 4})
    store, _ = run(p, w)
    assert store.open_trades() == []
    assert store.signal_reasons("research", "fav90") == {
        "risk:portfolio:max_city_allocation:KNYC": 1
    }


def test_strategy_level_cap() -> None:
    w = window(OPEN, series="KXHIGHNY")
    p = portfolio(strategies=[strategy(series="KXHIGHNY", risk={"max_city_allocation": 4})])
    store, _ = run(p, w)
    assert store.signal_reasons("research", "fav90") == {
        "risk:strategy:max_city_allocation:KNYC": 1
    }


def test_cap_aggregates_across_strategies_per_city() -> None:
    ny = window(OPEN, series="KXHIGHNY")
    phl = window(OPEN, series="KXHIGHPHIL")
    strategies = [
        strategy(name="ny_a", series="KXHIGHNY"),
        strategy(name="ny_b", series="KXHIGHNY"),
        strategy(name="phl", series="KXHIGHPHIL"),
    ]
    p = portfolio(strategies=strategies, risk={"max_city_allocation": 6})
    store, _ = run(p, ny, phl)
    assert sorted(t.strategy for t in store.open_trades()) == ["ny_a", "phl"]  # other city ok
    assert store.signal_reasons("research", "ny_b") == {
        "risk:portfolio:max_city_allocation:KNYC": 1
    }


def test_high_and_low_on_one_station_share_the_cap() -> None:
    hi = window(OPEN, series="KXHIGHTDC")
    lo = window(OPEN, series="KXLOWTDC")
    strategies = [strategy(name="hi", series="KXHIGHTDC"), strategy(name="lo", series="KXLOWTDC")]
    store, _ = run(portfolio(strategies=strategies, risk={"max_city_allocation": 6}), hi, lo)
    assert [t.strategy for t in store.open_trades()] == ["hi"]


def test_cap_releases_after_settlement() -> None:
    first = window(OPEN, series="KXHIGHNY")
    # opens while `first` is still open and keeps quoting after it settles
    second = window(OPEN + timedelta(minutes=5), series="KXHIGHNY")
    strat = strategy(series="KXHIGHNY", risk=NO_LIMITS)
    store, _ = run(portfolio(strategies=[strat], risk={"max_city_allocation": 6}), first, second)
    trades = store.open_trades() + store.settled_trades()
    assert len(trades) == 2
    reasons = store.signal_reasons("research", "fav90")
    assert reasons["risk:portfolio:max_city_allocation:KNYC"] == 1
    late = next(t for t in trades if t.window_id == second[0].window_id)
    assert late.opened_at > first[0].close_time  # entered only once the first had settled


def test_unknown_city_fails_closed() -> None:
    w = window(OPEN)  # KXBTC15M has no station
    store, _ = run(portfolio(risk={"max_city_allocation": 100}), w)
    assert store.open_trades() == []
    assert store.signal_reasons("research", "fav90") == {
        "risk:portfolio:max_city_allocation:unknown_city": 1
    }
    d = check_city_limit(RiskConfig(max_city_allocation=100), "p", [], None, 1)
    assert d.reason == "risk:p:max_city_allocation:unknown_city"


def test_unmapped_open_trades_do_not_count_toward_a_city() -> None:
    btc = make_trade(avg_price=0.9, qty=100)  # KXBTC15M, $90 open
    d = check_city_limit(RiskConfig(max_city_allocation=5), "p", [btc], "KXHIGHNY-X-B1", 4.5)
    assert d.take


# --- the filter gate ----------------------------------------------------------------


def _filtered(series: str, **params: Any) -> dict[str, Any]:
    gates = [{"type": "max_entry_price", "value": 0.95}, {"type": "city_filter", **params}]
    return strategy(name=series.lower(), series=series, gates=gates)


def test_exclude_blocks_dc_only() -> None:
    dc, ny = window(OPEN, series="KXHIGHTDC"), window(OPEN, series="KXHIGHNY")
    p = portfolio(
        strategies=[
            _filtered("KXHIGHTDC", exclude=["KDCA"]),
            _filtered("KXHIGHNY", exclude=["KDCA"]),
        ]
    )
    store, _ = run(p, dc, ny)
    assert [t.strategy for t in store.open_trades()] == ["kxhighny"]
    assert store.signal_reasons("research", "kxhightdc") == {"city_filter:KDCA": 1}


def test_allow_list() -> None:
    dc, ny = window(OPEN, series="KXHIGHTDC"), window(OPEN, series="KXHIGHNY")
    p = portfolio(
        strategies=[_filtered("KXHIGHTDC", allow=["KNYC"]), _filtered("KXHIGHNY", allow=["KNYC"])]
    )
    store, _ = run(p, dc, ny)
    assert [t.strategy for t in store.open_trades()] == ["kxhighny"]


def test_filter_unknown_city_blocks_even_fail_open() -> None:
    w = window(OPEN)
    p = portfolio(strategies=[_filtered("KXBTC15M", exclude=["KDCA"], fail_open=True)])
    store, _ = run(p, w)
    assert store.open_trades() == []
    assert store.signal_reasons("research", "kxbtc15m") == {"city_filter:unknown_city": 1}


@pytest.mark.parametrize(
    ("params", "match"),
    [({}, "allow and/or exclude"), ({"exclude": ["DC"]}, "unknown stations")],
)
def test_filter_params_validated(params: dict[str, Any], match: str) -> None:
    p = portfolio(strategies=[_filtered("KXHIGHNY", **params)])
    with pytest.raises(BuildError, match=match):
        build_strategies([p])


def test_example_config_builds() -> None:
    from pathlib import Path

    from nem.core.config import load_portfolio

    root = Path(__file__).resolve().parents[2]
    p = load_portfolio(root / "examples/risk/city_caps.yaml")
    assert p.risk.max_city_allocation is not None
    assert len(build_strategies([p])) == 2
