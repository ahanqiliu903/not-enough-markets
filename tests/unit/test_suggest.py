import copy
from datetime import UTC, datetime, timedelta
from itertools import product
from typing import Any

import pytest

from nem.backtest.suggest import Suggestion, render_suggestion, strategy_yaml, suggest
from nem.backtest.types import Grid, ParamValue, Period, Variant, VariantResult
from nem.core.config import PortfolioConfig, StrategyConfig, load_yaml
from nem.manage import PORTFOLIO_TEMPLATE, STRATEGY_TEMPLATE
from nem.stats.metrics import StrategyStats, strategy_stats

from factories import T0, make_trade, portfolio_dict

TRAIN = Period(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC))
TEST = Period(datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 20, tzinfo=UTC))

# (n, wins) at a 90c break-even (no fees), so the edge is wins/n - 0.90.
Outcome = tuple[int, int]
NEUTRAL: Outcome = (100, 90)
LOSER: Outcome = (100, 85)
PLATEAU: Outcome = (100, 93)
SPIKE: Outcome = (100, 98)


def stats(n: int, wins: int) -> StrategyStats:
    trades = [
        make_trade(
            f"W{i}", avg_price=0.9, qty=1, fee=0.0, won=i < wins,
            realized_pnl=(1.0 if i < wins else 0.0) - 0.9,
            settled_at=T0 + timedelta(minutes=15 * i),
        )
        for i in range(n)
    ]  # fmt: skip
    return strategy_stats(trades)


def base_config() -> dict[str, Any]:
    s: dict[str, Any] = portfolio_dict()["strategies"][0]
    s["check_every"] = "2s"
    s["signal"]["priors"] = {"yes": {"wins": 93, "n": 100}, "no": {"wins": 93, "n": 100}}
    return s


def apply(base: dict[str, Any], overrides: dict[str, ParamValue], name: str) -> StrategyConfig:
    d = copy.deepcopy(base)
    for path, value in overrides.items():
        *parents, last = path.split(".")
        node: Any = d
        for p in parents:
            node = node[p if isinstance(node, dict) else int(p)]
        node[last if isinstance(node, dict) else int(last)] = value
    d["name"] = name
    return StrategyConfig.model_validate(d)


def variants(grid: Grid) -> list[Variant]:
    base = base_config()
    out: list[Variant] = []
    axes = list(grid.items())
    for k, index in enumerate(product(*(range(len(v)) for _, v in axes))):
        overrides = {path: values[i] for (path, values), i in zip(axes, index, strict=True)}
        name = f"v{k:02d}"
        out.append(Variant(name, overrides, tuple(index), apply(base, overrides, name)))
    return out


def results(
    vs: list[Variant], outcomes: dict[tuple[int, ...], Outcome], period: Period
) -> list[VariantResult]:
    return [VariantResult(v, period, stats(*outcomes[v.index])) for v in vs]


GRID_1D: Grid = {"signal.threshold": [0.80, 0.82, 0.84, 0.86, 0.88, 0.90, 0.92]}


def plateau_vs_spike() -> dict[tuple[int, ...], Outcome]:
    # index 2 is the single best variant, but its neighbours lose; 4-6 are a plateau.
    row = [LOSER, LOSER, SPIKE, LOSER, PLATEAU, PLATEAU, PLATEAU]
    return {(i,): o for i, o in enumerate(row)}


def run(
    grid: Grid,
    train: dict[tuple[int, ...], Outcome],
    test: dict[tuple[int, ...], Outcome],
    **kw: Any,
) -> Suggestion:
    vs = variants(grid)
    return suggest(grid, results(vs, train, TRAIN), results(vs, test, TEST), **kw)


def test_broad_plateau_beats_sharp_peak() -> None:
    s = run(GRID_1D, plateau_vs_spike(), {(i,): NEUTRAL for i in range(7)})
    assert s.status == "suggested"
    assert s.variant is not None
    # 5 and 6 tie on score; 5 is closer to the grid centre.
    assert s.variant.index == (5,)
    assert s.variant.overrides == {"signal.threshold": 0.90}
    assert [r.variant.index for r in s.neighbours] == [(4,), (6,)]
    assert s.score == pytest.approx(0.03 * 100 / 200)
    text = " ".join(s.reasons)
    assert "100 trades" in text
    assert "not evidence of an edge" in text
    assert "did not clearly reject" in text
    assert "trades would be needed" in text


def test_insufficient_when_no_variant_has_enough_trades() -> None:
    small = {(i,): (12, 11) for i in range(7)}
    s = run(GRID_1D, small, small)
    assert s.status == "insufficient"
    assert s.variant is None and s.score is None
    assert "30" in s.reasons[0] and "12" in s.reasons[0]


def test_variant_needs_a_majority_of_measured_neighbours() -> None:
    grid: Grid = {"signal.threshold": [0.88, 0.90, 0.92]}
    # The middle has two neighbours but only one measured: not a majority.
    train = {(0,): (5, 5), (1,): PLATEAU, (2,): (100, 92)}
    s = run(grid, train, {(i,): NEUTRAL for i in range(3)})
    assert s.variant is not None
    assert s.variant.index == (2,)


def test_rejected_when_test_clearly_loses() -> None:
    test = {(i,): NEUTRAL for i in range(7)}
    test[(5,)] = (200, 150)
    s = run(GRID_1D, plateau_vs_spike(), test)
    assert s.status == "rejected"
    assert s.variant is not None and s.variant.index == (5,)
    assert any("clearly rejects" in r and "200 trades" in r for r in s.reasons)


def test_rejected_when_nothing_profitable_on_train() -> None:
    train = {(i,): (100, 80 + i) for i in range(7)}  # all below 90%
    s = run(GRID_1D, train, {(i,): NEUTRAL for i in range(7)})
    assert s.status == "rejected"
    assert s.variant is not None
    assert s.score is not None and s.score < 0
    assert any("least-bad" in r for r in s.reasons)


def test_small_test_period_cannot_reject() -> None:
    test = {(i,): (10, 5) for i in range(7)}
    s = run(GRID_1D, plateau_vs_spike(), test)
    assert s.status == "suggested"
    assert any("too few to reject" in r for r in s.reasons)


def test_choice_ignores_test_results() -> None:
    train = plateau_vs_spike()
    great_on_test = {(i,): NEUTRAL for i in range(7)}
    great_on_test[(0,)] = (300, 299)
    great_on_test[(1,)] = (300, 299)
    a = run(GRID_1D, train, great_on_test)
    b = run(GRID_1D, train, {(i,): NEUTRAL for i in range(7)})
    assert a.variant is not None and b.variant is not None
    assert a.variant.index == b.variant.index == (5,)
    assert a.test is not None and a.test.variant.name == a.variant.name


def test_mismatched_variants_raise() -> None:
    vs = variants(GRID_1D)
    train = results(vs, plateau_vs_spike(), TRAIN)
    with pytest.raises(ValueError, match="different variants"):
        suggest(GRID_1D, train, train[:-1])
    other = [VariantResult(variants({"signal.threshold": [0.5] * 7})[0], TEST, stats(*NEUTRAL))]
    with pytest.raises(ValueError):
        suggest(GRID_1D, train[:1], other)


GRID_2D: Grid = {"signal.threshold": [0.88, 0.90, 0.92], "gates.0.value": [0.94, 0.95, 0.96]}


def test_render_one_axis() -> None:
    s = run(GRID_1D, plateau_vs_spike(), {(i,): NEUTRAL for i in range(7)})
    text = render_suggestion(s, GRID_1D)
    assert text.startswith("Suggestion: suggested")
    assert "signal.threshold = 0.9" in text
    lines = text.splitlines()
    assert any(line.lstrip().startswith("train") and "100" in line for line in lines)
    assert any(line.lstrip().startswith("test") for line in lines)
    chosen = [line for line in lines if line.rstrip().endswith("*")]
    assert len(chosen) == 1 and chosen[0].strip().startswith("0.9 ")
    assert "+8.0c n=100" in text  # the spike is still shown


def test_render_two_axes() -> None:
    outcomes = {(i, j): PLATEAU for i in range(3) for j in range(3)}
    outcomes[(0, 0)] = LOSER
    s = run(GRID_2D, outcomes, {k: NEUTRAL for k in outcomes})
    assert s.variant is not None
    assert s.variant.index == (1, 1)  # ties with others clear of (0, 0); it is the centre
    text = render_suggestion(s, GRID_2D)
    lines = text.splitlines()
    header = next(line for line in lines if "0.94" in line and "0.96" in line)
    assert header.strip().startswith("signal.threshold")
    row = next(line for line in lines if line.strip().startswith("0.9 "))
    assert row.count("n=100") == 3 and row.count("*") == 1


def test_render_insufficient() -> None:
    small = {(i,): (12, 11) for i in range(7)}
    text = render_suggestion(run(GRID_1D, small, small), GRID_1D)
    assert "insufficient" in text.splitlines()[0]
    assert "*" not in text


def test_strategy_yaml_round_trips() -> None:
    outcomes = {(i, j): PLATEAU for i in range(3) for j in range(3)}
    s = run(GRID_2D, outcomes, {k: NEUTRAL for k in outcomes})
    assert s.variant is not None
    text = strategy_yaml(s, "fav90")
    assert text.startswith("\n  - name: fav90_suggested\n")
    assert "status: active" in text
    assert "    gates:\n      - {type: max_entry_price, value: 0.95}" in text
    assert "    sizing: {type: fixed, contracts: 5}" in text
    comments = [line for line in text.splitlines() if line.strip().startswith("#")]
    assert len(comments) == 2
    assert all("100 train trades" in c and "2026-09-01" in c for c in comments)
    assert all("100 test trades" in c and "2026-10-20" in c for c in comments)

    loaded: Any = load_yaml("strategies:" + text)
    config = StrategyConfig.model_validate(loaded["strategies"][0])
    assert config.name == "fav90_suggested"
    assert config == s.variant.config.model_copy(update={"name": "fav90_suggested"})


def test_strategy_yaml_refuses_unless_suggested() -> None:
    small = {(i,): (12, 11) for i in range(7)}
    with pytest.raises(ValueError):
        strategy_yaml(run(GRID_1D, small, small), "fav90")
    with pytest.raises(ValueError, match="valid strategy name"):
        s = run(GRID_1D, plateau_vs_spike(), {(i,): NEUTRAL for i in range(7)})
        strategy_yaml(s, "Bad-Name")


def test_strategy_yaml_appends_like_strategy_add() -> None:
    s = run(GRID_1D, plateau_vs_spike(), {(i,): NEUTRAL for i in range(7)})
    text = PORTFOLIO_TEMPLATE.format(name="research", balance="1000").replace(
        "strategies: []", "strategies:"
    )
    text = text.rstrip("\n") + "\n" + STRATEGY_TEMPLATE.format(name="fav90", series="KXBTC15M")
    text = text.rstrip("\n") + "\n" + strategy_yaml(s, "fav90")
    portfolio = PortfolioConfig.model_validate(load_yaml(text))
    assert [x.name for x in portfolio.strategies] == ["fav90", "fav90_suggested"]
