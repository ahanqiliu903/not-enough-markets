from datetime import timedelta
from typing import Any

import pytest

from nem.backtest.sweep import SweepError, expand, run_sweep, split
from nem.backtest.types import Grid, Period
from nem.core.clock import ManualClock
from nem.core.config import PortfolioConfig, StrategyConfig
from nem.core.types import MarketSnapshot
from nem.engine.runner import Runner
from nem.market.source import ReplaySource
from nem.stats.metrics import strategy_stats
from nem.store import Store

from engine_helpers import portfolio, settle, strategy, window
from factories import T0

MINUTE = timedelta(minutes=1)


def base_strategy(**kw: Any) -> StrategyConfig:
    return StrategyConfig.model_validate(strategy(**kw))


def windows(n: int, start_offset: int = 0) -> list[list[MarketSnapshot]]:
    return [window(T0 + timedelta(minutes=15 * (i + start_offset)), every=MINUTE) for i in range(n)]


def data_store(ws: list[list[MarketSnapshot]], results: list[str]) -> Store:
    data = Store()
    for w, r in zip(ws, results, strict=True):
        data.insert_snapshots(w)
        if r:  # "" = result never recorded
            data.record_settlement(settle(w, r))
    return data


# --- expand ----------------------------------------------------------------------------


def test_expand_order_names_and_index() -> None:
    grid: Grid = {"signal.threshold": [0.88, 0.9, 0.92], "gates.0.value": [0.94, 0.96]}
    vs = expand(base_strategy(), grid)
    assert [v.name for v in vs] == ["v00", "v01", "v02", "v03", "v04", "v05"]
    assert [v.index for v in vs] == [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)]
    assert vs[3].overrides == {"signal.threshold": 0.9, "gates.0.value": 0.96}
    assert vs[3].config.name == "v03"
    assert vs[3].config.signal.params["threshold"] == 0.9
    assert vs[3].config.gates[0].params["value"] == 0.96


def test_expand_name_width_grows_with_count() -> None:
    vs = expand(base_strategy(), {"sizing.contracts": list(range(1, 102))}, max_variants=200)
    assert vs[0].name == "v000" and vs[-1].name == "v100"


def test_expand_nested_and_risk_paths() -> None:
    grid: Grid = {"signal.priors.yes.wins": [90, 99], "risk.max_open_positions": [2]}
    [a, b] = expand(base_strategy(), grid)
    assert a.config.signal.params["priors"] == {
        "yes": {"wins": 90, "n": 100},
        "no": {"wins": 95, "n": 100},
    }
    assert b.config.signal.params["priors"] == {
        "yes": {"wins": 99, "n": 100},
        "no": {"wins": 95, "n": 100},
    }
    assert a.config.risk.max_open_positions == 2
    assert a.config.series == "KXBTC15M"


def test_expand_leaves_base_untouched() -> None:
    base = base_strategy()
    before = base.model_dump()
    expand(base, {"signal.threshold": [0.8], "gates.0.value": [0.97]})
    assert base.model_dump() == before


@pytest.mark.parametrize(
    ("grid", "match"),
    [
        ({}, "empty"),
        ({"signal.threshold": []}, "no values"),
        ({"signal.threshold": [0.9, 0.9]}, "duplicate"),
        ({"signal.treshold": [0.9]}, "no 'signal.treshold'"),
        ({"gates.1.value": [0.9]}, "no 'gates.1'"),
        ({"gates.x.value": [0.9]}, "no 'gates.x'"),
        ({"risk.max_open_position": [2]}, "no 'risk.max_open_position'"),
        ({"signal.threshold.x": [1]}, "is a value"),
        ({"risk": [1]}, "is a section"),
        ({"name": ["other"]}, "can't be swept"),
        ({"series": ["KXETH15M"]}, "can't be swept"),
        ({"risk.max_open_positions": [0]}, "v00"),  # invalid value fails validation
    ],
)
def test_expand_errors(grid: Grid, match: str) -> None:
    with pytest.raises(SweepError, match=match):
        expand(base_strategy(), grid)


def test_expand_too_many_variants() -> None:
    grid: Grid = {"sizing.contracts": list(range(1, 21)), "signal.threshold": [0.8, 0.85]}
    with pytest.raises(SweepError, match="40 combinations"):
        expand(base_strategy(), grid, max_variants=39)
    assert len(expand(base_strategy(), grid, max_variants=40)) == 40


def test_expand_distinguishes_int_and_float() -> None:
    assert len(expand(base_strategy(), {"taker_fee_rate": [0, 0.0]})) == 2


# --- split -----------------------------------------------------------------------------


def _assert_no_window_split(data: Store, train: Period, test: Period) -> None:
    sides: dict[str, set[str]] = {}
    for s in data.iter_snapshots():
        side = "train" if train.start <= s.ts < train.end else "test"
        assert side == "train" or test.start <= s.ts < test.end
        sides.setdefault(s.window_id, set()).add(side)
    assert all(len(v) == 1 for v in sides.values())
    assert {"train", "test"} == set().union(*sides.values())


def test_split_snaps_to_window_boundary_and_covers_data() -> None:
    ws = windows(10)
    data = data_store(ws, [""] * 10)
    train, test = split(data, ["KXBTC15M"], holdout=0.3)
    assert train.start == ws[0][0].ts and train.end == test.start
    assert test.end > ws[-1][-1].ts
    # raw boundary falls inside the 7th window (index 6); it moves to that window's close
    assert train.end == ws[6][0].close_time
    _assert_no_window_split(data, train, test)


def test_split_overlapping_windows() -> None:
    ws = windows(12)
    hourly = [window(T0 + timedelta(minutes=50), every=MINUTE, minutes=60)]
    data = data_store(ws + hourly, [""] * 13)
    train, test = split(data, ["KXBTC15M"], holdout=0.5)
    _assert_no_window_split(data, train, test)
    # the hourly window spanning the raw boundary pushed it to the hourly close (T0+110m),
    # which falls inside a 15-minute window, so it moved on to that window's close
    assert hourly[0][0].close_time == T0 + timedelta(minutes=110)
    assert train.end == T0 + timedelta(minutes=120)


def test_split_errors() -> None:
    with pytest.raises(SweepError, match="holdout"):
        split(Store(), ["KXBTC15M"], holdout=1.0)
    with pytest.raises(SweepError, match="holdout"):
        split(Store(), ["KXBTC15M"], holdout=0)
    with pytest.raises(SweepError, match="no recorded snapshots"):
        split(data_store(windows(2), ["", ""]), ["KXETH15M"])
    with pytest.raises(SweepError, match="no complete window"):
        split(data_store(windows(1), [""]), ["KXBTC15M"])


# --- run_sweep -------------------------------------------------------------------------


def plain_replay(data: Store, p: PortfolioConfig, period: Period) -> tuple[int, int, float]:
    with Store() as results:
        first = next(iter(data.iter_snapshots(None, period.start, period.end)))
        clock = ManualClock(first.ts)
        source = ReplaySource(data, period.start, period.end)
        runner = Runner([p], source, results, clock, data=data, fetch_feeds=False)
        runner.run(source.ticks(runner.series))
        runner.settle_remaining(clock.now() + timedelta(hours=1))
        s = strategy_stats(results.settled_trades())
        return s.n, s.wins, s.pnl


def test_run_sweep_thresholds_and_periods() -> None:
    ws = windows(6)
    data = data_store(ws, ["yes", "no", "yes", "yes", "no", ""])
    base = portfolio()
    p1 = Period(ws[0][0].ts, ws[3][0].ts)  # windows 0-2
    p2 = Period(ws[3][0].ts, ws[-1][-1].ts + MINUTE)  # windows 3-5; 5 never settles
    grid: Grid = {"signal.threshold": [0.85, 0.95], "sizing.contracts": [2, 5]}
    out = run_sweep(data, base, "fav90", grid, [p1, p2])

    assert list(out) == [p1, p2]
    for period, results in out.items():
        assert [r.variant.name for r in results] == ["v00", "v01", "v02", "v03"]
        assert all(r.period == period for r in results)
        for r in results:
            alone = portfolio(strategies=[r.variant.config.model_dump()])
            assert (r.stats.n, r.stats.wins, r.stats.pnl) == plain_replay(data, alone, period)

    first, second = out[p1], out[p2]
    # 95c threshold is above the 90c ask: never enters
    assert [r.stats.n for r in first] == [3, 3, 0, 0]
    assert [r.stats.wins for r in first] == [2, 2, 0, 0]
    # the unrecorded result of window 5 is never invented: that trade stays open
    assert [r.stats.n for r in second] == [2, 2, 0, 0]
    assert [r.stats.wins for r in second] == [1, 1, 0, 0]
    assert first[1].stats.pnl < first[0].stats.pnl < 0  # 5 contracts lose more than 2


def test_run_sweep_unknown_strategy() -> None:
    with pytest.raises(SweepError, match="no strategy 'nope'"):
        run_sweep(Store(), portfolio(), "nope", {"signal.threshold": [0.9]}, [])


def test_run_sweep_empty_period() -> None:
    data = data_store(windows(1), ["yes"])
    empty = Period(T0 + timedelta(days=1), T0 + timedelta(days=2))
    [r] = run_sweep(data, portfolio(), "fav90", {"signal.threshold": [0.85]}, [empty])[empty]
    assert r.stats.n == 0
