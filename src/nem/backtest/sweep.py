"""Parameter sweeps over recorded data.

`expand` turns a base strategy and a small grid into named variants, `split` cuts the
recorded data into an earlier (train) and later (test) period at a window boundary, and
`run_sweep` replays each period once with every variant side by side, each in its own copy
of the base portfolio so cash and risk limits behave exactly as they would alone.
"""

import copy
import itertools
from collections.abc import Sequence
from datetime import datetime, timedelta

from pydantic import ValidationError

from nem.backtest.types import Grid, ParamValue, Period, Variant, VariantResult
from nem.builtins import load_builtins
from nem.core.clock import ManualClock
from nem.core.config import PortfolioConfig, StrategyConfig
from nem.engine.runner import Runner
from nem.market.source import ReplaySource
from nem.stats.metrics import strategy_stats
from nem.store import Store

MAX_VARIANTS = 200
# Identity of the strategy, not a parameter: a variant of KXBTC15M on KXETH15M is a
# different strategy, and names are assigned by the sweep.
_FIXED = frozenset({"name", "series"})


class SweepError(ValueError):
    pass


def _set_path(tree: dict[str, object], path: str, value: ParamValue) -> None:
    parts = path.split(".")
    if parts[0] in _FIXED:
        raise SweepError(f"{path!r}: {parts[0]} can't be swept")
    node: object = tree
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        where = ".".join(parts[: i + 1])
        if isinstance(node, dict):
            d: dict[str, object] = node  # pyright: ignore[reportUnknownVariableType]
            if part not in d:
                raise SweepError(f"{path!r}: no {where!r} in the base strategy")
            if last:
                _check_leaf(path, d[part])
                d[part] = value
            else:
                node = d[part]
        elif isinstance(node, list):
            items: list[object] = node  # pyright: ignore[reportUnknownVariableType]
            if not part.isdigit() or int(part) >= len(items):
                raise SweepError(f"{path!r}: no {where!r} in the base strategy")
            if last:
                _check_leaf(path, items[int(part)])
                items[int(part)] = value
            else:
                node = items[int(part)]
        else:
            raise SweepError(f"{path!r}: {'.'.join(parts[:i])!r} is a value, not a section")


def _check_leaf(path: str, current: object) -> None:
    # Replacing a whole section (e.g. "risk") with a scalar is never what was meant.
    if isinstance(current, dict | list):
        raise SweepError(f"{path!r} is a section; sweep one of its values instead")


def _value_key(v: ParamValue) -> tuple[type, ParamValue]:
    return (type(v), v)  # so 1, 1.0 and True count as different values


def expand(base: StrategyConfig, grid: Grid, max_variants: int = MAX_VARIANTS) -> list[Variant]:
    """Every combination of the grid's values, in grid order (the first path changes
    slowest). Each variant is the base strategy with its overrides, renamed v00, v01, ..."""
    if not grid:
        raise SweepError("the grid is empty")
    for path, values in grid.items():
        if not values:
            raise SweepError(f"{path!r} has no values")
        keys = [_value_key(v) for v in values]
        if len(set(keys)) != len(keys):
            raise SweepError(f"{path!r} has duplicate values: {values}")
    total = 1
    for values in grid.values():
        total *= len(values)
    if total > max_variants:
        raise SweepError(f"{total} combinations is more than max_variants={max_variants}")

    paths = list(grid)
    width = max(2, len(str(total - 1)))
    dumped = base.model_dump()
    out: list[Variant] = []
    for n, index in enumerate(itertools.product(*(range(len(grid[p])) for p in paths))):
        name = f"v{n:0{width}d}"
        overrides = {p: grid[p][i] for p, i in zip(paths, index, strict=True)}
        tree = copy.deepcopy(dumped)
        for path, value in overrides.items():
            _set_path(tree, path, value)
        tree["name"] = name
        try:
            config = StrategyConfig.model_validate(tree)
        except ValidationError as e:
            raise SweepError(f"{name} {overrides}: {e}") from e
        out.append(Variant(name, overrides, tuple(index), config))
    return out


def split(data: Store, series: Sequence[str], holdout: float = 0.3) -> tuple[Period, Period]:
    """Train = the earliest `1 - holdout` of the recorded time span, test = the rest.

    The boundary starts at the first snapshot at or after that time, then moves forward
    until no market window has snapshots on both sides: whenever a snapshot at or after
    the boundary belongs to a window that opened before it, the boundary moves to that
    window's close. Snapshots lie within [open_time, close_time], so afterwards every
    window is wholly in one period.
    """
    if not 0 < holdout < 1:
        raise SweepError(f"holdout must be between 0 and 1, got {holdout}")
    series = list(series)
    first: datetime | None = None
    last: datetime | None = None
    for s in data.iter_snapshots(series):
        if first is None:
            first = s.ts
        last = s.ts
    if first is None or last is None:
        raise SweepError(f"no recorded snapshots for {series}")
    if last == first:
        raise SweepError(f"only one snapshot time for {series}; nothing to split")

    raw = first + (last - first) * (1 - holdout)
    boundary: datetime | None = None
    for s in data.iter_snapshots(series, raw):
        if boundary is None:
            boundary = s.ts
        if s.open_time < boundary:
            boundary = max(boundary, s.close_time)
    if boundary is None or boundary > last:
        raise SweepError(f"{series}: no complete window after the split point; record more data")
    end = last + timedelta(microseconds=1)  # periods are half-open and must include `last`
    return Period(first, boundary), Period(boundary, end)


def _variant_portfolio(base: PortfolioConfig, variant: Variant) -> PortfolioConfig:
    return base.model_copy(
        update={
            "name": f"{base.name}_{variant.name}",
            "strategies": [variant.config],
            "reporting": [],
        }
    )


def _replay(
    data: Store, portfolios: Sequence[PortfolioConfig], period: Period, results: Store
) -> None:
    source = ReplaySource(data, period.start, period.end)
    first = next(iter(data.iter_snapshots(None, period.start, period.end)), None)
    clock = ManualClock(first.ts if first is not None else period.start)
    runner = Runner(portfolios, source, results, clock, data=data, fetch_feeds=False)
    runner.run(source.ticks(runner.series))
    # Like `nem replay`: settle what the recorded data says was resolved within an hour of
    # the last tick. Trades whose result was never recorded stay open and don't count.
    runner.settle_remaining(clock.now() + timedelta(hours=1))


def run_sweep(
    data: Store,
    base: PortfolioConfig,
    strategy: str,
    grid: Grid,
    periods: Sequence[Period],
    *,
    max_variants: int = MAX_VARIANTS,
) -> dict[Period, list[VariantResult]]:
    """Replay each period once with every variant, each in its own copy of `base`.
    Results are in variant order."""
    load_builtins()
    try:
        strategy_config = base.strategy(strategy)
    except KeyError:
        raise SweepError(f"{base.name} has no strategy {strategy!r}") from None
    variants = expand(strategy_config, grid, max_variants)
    portfolios = [_variant_portfolio(base, v) for v in variants]
    out: dict[Period, list[VariantResult]] = {}
    for period in periods:
        with Store() as results:
            _replay(data, portfolios, period, results)
            rows: list[VariantResult] = []
            for p, v in zip(portfolios, variants, strict=True):
                trades = results.settled_trades(strategies=[(p.name, v.name)])
                p_by_window = results.p_models(p.name, v.name)
                p_models = [p_by_window.get(t.window_id) for t in trades]
                rows.append(VariantResult(v, period, strategy_stats(trades, p_models)))
        out[period] = rows
    return out
