"""Pick starting settings from a sweep without fooling ourselves (AGENTS.md "Don't overfit").

The rule:

1. Choose on the train period only. Each variant scores the mean of a conservative edge
   over itself and its grid neighbours, so a broad plateau beats a lone spike.
2. Check the choice once, on the later test period it wasn't tuned on. The test can only
   reject; it never picks.
3. Say how many trades each number rests on. A suggestion is where paper trading starts,
   not evidence of an edge.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal, cast

import yaml

from nem.backtest.types import Grid, ParamValue, Variant, VariantResult
from nem.core.config import NAME_PATTERN
from nem.stats.metrics import StrategyStats

# Per-variant conservative edge = edge * n / (n + SHRINK_TRADES): the observed edge pulled
# toward "no edge" as if we'd also seen SHRINK_TRADES break-even trades. 100 matches the
# modest priors AGENTS.md asks for. Unlike edge_ci.low, which is negative for nearly every
# variant at realistic n and so ranks mostly by sample size, this keeps the sign of the
# observed edge and still trusts 1,000 trades far more than 40.
SHRINK_TRADES = 100

Status = Literal["suggested", "rejected", "insufficient"]


@dataclass(frozen=True)
class Suggestion:
    status: Status
    variant: Variant | None
    train: VariantResult | None  # the chosen variant on the train period
    test: VariantResult | None  # the chosen variant on the test period
    score: float | None  # stability score on train, dollars per contract
    neighbours: list[VariantResult]  # train results of the neighbours used in the score
    reasons: list[str]
    # Every variant's train result, for the grid table in render_suggestion.
    train_results: list[VariantResult] = field(default_factory=list[VariantResult])


def shrunk_edge(stats: StrategyStats) -> float | None:
    if stats.edge is None or stats.n == 0:
        return None
    return stats.edge * stats.n / (stats.n + SHRINK_TRADES)


def neighbours_of(
    index: tuple[int, ...], by_index: dict[tuple[int, ...], VariantResult]
) -> list[VariantResult]:
    """Variants one step away along exactly one axis. Diagonals are left out on purpose:
    they change two settings at once, so they say less about how sensitive the result is
    to any one knob, and in 2-D they would outnumber the axis neighbours 4 to 4."""
    out: list[VariantResult] = []
    for axis in range(len(index)):
        for step in (-1, 1):
            other = (*index[:axis], index[axis] + step, *index[axis + 1 :])
            if other in by_index:
                out.append(by_index[other])
    return out


@dataclass(frozen=True)
class _Scored:
    result: VariantResult
    score: float
    neighbours: list[VariantResult]
    centre_distance: float


def _validate(grid: Grid, train: Sequence[VariantResult], test: Sequence[VariantResult]) -> None:
    if not train:
        raise ValueError("no variants to choose from")
    for label, results in (("train", train), ("test", test)):
        names = [r.variant.name for r in results]
        if len(set(names)) != len(names):
            raise ValueError(f"{label} results repeat a variant name")
        for r in results:
            idx = r.variant.index
            if len(idx) != len(grid) or any(
                not 0 <= i < len(values) for i, values in zip(idx, grid.values(), strict=True)
            ):
                raise ValueError(
                    f"{label} variant {r.variant.name} has index {idx} outside the grid"
                )
    train_by_name = {r.variant.name: r.variant for r in train}
    test_by_name = {r.variant.name: r.variant for r in test}
    if train_by_name.keys() != test_by_name.keys():
        only_train = sorted(train_by_name.keys() - test_by_name.keys())
        only_test = sorted(test_by_name.keys() - train_by_name.keys())
        raise ValueError(
            f"train and test describe different variants (only in train: {only_train},"
            f" only in test: {only_test})"
        )
    for name, v in train_by_name.items():
        t = test_by_name[name]
        if v.index != t.index or v.overrides != t.overrides:
            raise ValueError(f"variant {name} has different settings in train and test")
    indexes = [r.variant.index for r in train]
    if len(set(indexes)) != len(indexes):
        raise ValueError("two train variants share the same grid position")


def _centre_distance(index: tuple[int, ...], grid: Grid) -> float:
    return sum(abs(i - (len(v) - 1) / 2) for i, v in zip(index, grid.values(), strict=True))


def _score_all(grid: Grid, train: Sequence[VariantResult], min_trades: int) -> list[_Scored]:
    by_index = {r.variant.index: r for r in train}
    scored: list[_Scored] = []
    for r in train:
        if r.stats.n < min_trades:
            continue
        nbrs = neighbours_of(r.variant.index, by_index)
        known = [x for x in nbrs if x.stats.n >= min_trades]
        # A strict majority of neighbours must be measured; otherwise "stable" is a guess.
        # Unmeasured neighbours are left out of the mean rather than counted as zero.
        if nbrs and 2 * len(known) <= len(nbrs):
            continue
        edges = [e for x in [r, *known] if (e := shrunk_edge(x.stats)) is not None]
        scored.append(
            _Scored(r, sum(edges) / len(edges), known, _centre_distance(r.variant.index, grid))
        )
    return scored


def _cents(x: float | None) -> str:
    # + 0.0 turns -0.0 into 0.0, so a break-even result doesn't print as "-0.0c".
    return "-" if x is None else f"{round(100 * x, 1) + 0.0:+.1f}c"


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.1f}%"


def _value(v: ParamValue) -> str:
    return "null" if v is None else str(v).lower() if isinstance(v, bool) else str(v)


def _overrides(v: Variant) -> str:
    return ", ".join(f"{k}={_value(x)}" for k, x in v.overrides.items())


def _period(r: VariantResult) -> str:
    return f"{r.period.start:%Y-%m-%d} to {r.period.end:%Y-%m-%d}"


def _describe(label: str, r: VariantResult) -> str:
    st = r.stats
    if st.n == 0:
        return f"{label} ({_period(r)}): no trades."
    ci = st.edge_ci
    ci_s = f" [{_cents(ci.low)}, {_cents(ci.high)}]" if ci else ""
    return (
        f"{label} ({_period(r)}): {st.n} trades, win rate {_pct(st.win_rate)} vs break-even"
        f" {_pct(st.breakeven)}, edge {_cents(st.edge)} per contract{ci_s}."
    )


def suggest(
    grid: Grid,
    train: Sequence[VariantResult],
    test: Sequence[VariantResult],
    *,
    min_trades: int = 30,
) -> Suggestion:
    """Choose settings on `train`, then let `test` (a later period) reject them."""
    if min_trades < 1:
        raise ValueError("min_trades must be at least 1")
    _validate(grid, train, test)
    all_train = list(train)
    scored = _score_all(grid, train, min_trades)
    if not scored:
        best_n = max(r.stats.n for r in train)
        return Suggestion(
            status="insufficient",
            variant=None,
            train=None,
            test=None,
            score=None,
            neighbours=[],
            reasons=[
                f"No variant had at least {min_trades} train trades itself and in a majority"
                f" of its grid neighbours; the most any variant had was {best_n}.",
                "Record more data, lengthen the train period, or use a coarser grid.",
            ],
            train_results=all_train,
        )

    # Round so float noise doesn't break ties that the centre rule should decide.
    best = min(
        scored,
        key=lambda s: (-round(s.score, 9), s.centre_distance, sum(s.result.variant.index)),
    )
    variant = best.result.variant
    chosen_test = next(r for r in test if r.variant.name == variant.name)
    tr, te = best.result.stats, chosen_test.stats
    reasons = [
        f"Chose {variant.name} ({_overrides(variant)}) on train only: stability score"
        f" {_cents(best.score)} per contract, the mean shrunk edge of it and its"
        f" {len(best.neighbours)} measured neighbour(s), out of {len(scored)} variant(s)"
        f" with at least {min_trades} trades.",
        _describe("Train", best.result),
        _describe("Test", chosen_test),
    ]

    status: Status
    if best.score <= 0:
        status = "rejected"
        reasons.append(
            "No variant looked profitable even on the train period it was chosen on;"
            " this is the least-bad one."
        )
    elif te.n >= min_trades and te.edge_ci is not None and te.edge_ci.high < 0:
        status = "rejected"
        reasons.append(
            f"The test period clearly rejects it: over {te.n} trades even the top of the"
            f" edge interval is {_cents(te.edge_ci.high)} per contract."
        )
    else:
        status = "suggested"
        if te.n < min_trades:
            reasons.append(
                f"The test period had only {te.n} trades (fewer than {min_trades}), too few"
                " to reject it."
            )
        else:
            reasons.append(f"The test period ({te.n} trades) did not clearly reject it.")
        reasons.append("This is a starting point for paper trading, not evidence of an edge.")
        if te.trades_needed is not None:
            reasons.append(
                f"About {te.trades_needed:,} trades would be needed to detect a"
                f" {100 * te.trades_needed_for:.1f}c edge at the test break-even."
            )
        else:
            reasons.append(
                "The test period had too little data to estimate how many trades it would"
                " take to detect an edge."
            )
    if tr.n < 10 * min_trades:
        reasons.append(f"The choice rests on {tr.n} train trades; treat it as rough.")
    return Suggestion(
        status=status,
        variant=variant,
        train=best.result,
        test=chosen_test,
        score=best.score,
        neighbours=best.neighbours,
        reasons=reasons,
        train_results=all_train,
    )


# --- text ------------------------------------------------------------------------------


def _row(label: str, r: VariantResult | None) -> str:
    if r is None:
        return f"    {label:<6}" + "  -" * 5
    st = r.stats
    ci = st.edge_ci
    ci_s = f"[{_cents(ci.low)}, {_cents(ci.high)}]" if ci else "-"
    return (
        f"    {label:<6}{st.n:>7}  {_pct(st.win_rate):>8}  {_pct(st.breakeven):>10}"
        f"  {_cents(st.edge):>7} {ci_s:<18}  {round(st.pnl, 2) + 0.0:>+10,.2f}"
    )


def _cell(r: VariantResult | None, chosen: Variant | None) -> str:
    if r is None:
        return "-"
    mark = "*" if chosen is not None and r.variant.name == chosen.name else " "
    return f"{_cents(r.stats.edge)} n={r.stats.n}{mark}"


def _grid_table(grid: Grid, s: Suggestion) -> list[str]:
    by_index = {r.variant.index: r for r in s.train_results}
    axes = list(grid.items())
    if len(axes) == 1:
        path, values = axes[0]
        labels = [_value(v) for v in values]
        w = max(len(path), *(len(x) for x in labels))
        lines = [f"    {path:<{w}}  train edge"]
        lines += [
            f"    {label:<{w}}  {_cell(by_index.get((i,)), s.variant)}"
            for i, label in enumerate(labels)
        ]
        return lines
    (row_path, row_values), (col_path, col_values) = axes
    cells = [
        [_cell(by_index.get((i, j)), s.variant) for j in range(len(col_values))]
        for i in range(len(row_values))
    ]
    row_labels = [_value(v) for v in row_values]
    col_labels = [_value(v) for v in col_values]
    w0 = max(len(row_path), *(len(x) for x in row_labels))
    widths = [
        max(len(col_labels[j]), *(len(cells[i][j]) for i in range(len(row_values))))
        for j in range(len(col_values))
    ]
    lines = [
        f"    {'':<{w0}}  {col_path}",
        f"    {row_path:<{w0}}  "
        + "  ".join(f"{c:>{w}}" for c, w in zip(col_labels, widths, strict=True)),
    ]
    for label, row in zip(row_labels, cells, strict=True):
        lines.append(
            f"    {label:<{w0}}  "
            + "  ".join(f"{c:>{w}}" for c, w in zip(row, widths, strict=True))
        )
    return lines


def render_suggestion(s: Suggestion, grid: Grid) -> str:
    headline = {
        "suggested": "suggested starting settings (paper trading only)",
        "rejected": "rejected: no settings worth paper trading from this sweep",
        "insufficient": "insufficient data to suggest settings",
    }[s.status]
    lines = [f"Suggestion: {headline}"]
    if s.variant is not None:
        lines.append(f"  variant     {s.variant.name}")
        for k, v in s.variant.overrides.items():
            lines.append(f"    {k} = {_value(v)}")
        if s.score is not None:
            lines.append(f"  stability   {_cents(s.score)} per contract (train, shrunk)")
        lines.append("")
        lines.append(
            f"    {'':<6}{'trades':>7}  {'win rate':>8}  {'break-even':>10}"
            f"  {'edge':>7} {'[95% interval]':<18}  {'P&L':>10}"
        )
        lines.append(_row("train", s.train))
        lines.append(_row("test", s.test))
    lines.append("")
    lines += [f"  - {r}" for r in s.reasons]
    if 1 <= len(grid) <= 2 and s.train_results:
        lines.append("")
        mark = " (* = chosen)" if s.variant is not None else ""
        lines.append(f"  train edge per contract and trades by variant{mark}")
        lines += _grid_table(grid, s)
    return "\n".join(lines)


# --- YAML ------------------------------------------------------------------------------

# Same order as manage.STRATEGY_TEMPLATE; anything else follows in model order.
_KEY_ORDER = ["name", "series", "status", "check_every", "taker_fee_rate", "signal", "gates",
              "sizing", "risk"]  # fmt: skip
_ALWAYS = {"name", "series", "status", "check_every", "signal", "sizing", "risk"}
_MAX_LINE = 100


def _plain(v: object) -> object:
    """Config values as YAML-native data. Durations become `5s`, which parse_duration
    reads back exactly."""
    if isinstance(v, timedelta):
        seconds = v.total_seconds()
        return f"{int(seconds) if seconds.is_integer() else seconds}s"
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in cast(dict[object, object], v).items()}
    if isinstance(v, list | tuple):
        return [_plain(x) for x in cast(Sequence[object], v)]
    return v


def _flow(v: object) -> str:
    text = yaml.safe_dump([v], default_flow_style=True, sort_keys=False, width=1_000_000)
    return text.strip()[1:-1]


def _emit(key: str, v: object, indent: int) -> list[str]:
    pad = " " * indent
    one_line = f"{pad}{key}: {_flow(v)}"
    # Lists (gates) always go one item per line, like the template, so they're easy to edit.
    if not isinstance(v, dict | list) or (isinstance(v, dict) and len(one_line) <= _MAX_LINE):
        return [one_line]
    lines = [f"{pad}{key}:"]
    if isinstance(v, dict):
        for k, x in cast(dict[object, object], v).items():
            lines += _emit(str(k), x, indent + 2)
    else:
        lines += [f"{pad}  - {_flow(x)}" for x in cast(list[object], v)]
    return lines


def strategy_yaml(s: Suggestion, base_name: str) -> str:
    """The suggested strategy as a list item to append under `strategies:` (two-space
    indent, like `nem strategy add`)."""
    if s.status != "suggested" or s.variant is None or s.train is None or s.test is None:
        raise ValueError(f"nothing to write: the suggestion was {s.status}")
    name = f"{base_name}_suggested"
    if not re.fullmatch(NAME_PATTERN, name):
        raise ValueError(f"{name!r} is not a valid strategy name")
    config = s.variant.config.model_copy(update={"name": name, "status": "active"})
    full = config.model_dump()
    trimmed = config.model_dump(exclude_defaults=True)
    data = {k: full[k] for k in full if k in _ALWAYS or k in trimmed}
    # null is every risk limit's default (disabled), so leaving nulls out round-trips.
    data["risk"] = config.risk.model_dump(exclude_none=True)
    keys = [k for k in _KEY_ORDER if k in data] + [k for k in data if k not in _KEY_ORDER]
    plain = {k: _plain(data[k]) for k in keys}

    tr, te = s.train, s.test
    lines = ["", f"  - name: {_flow(plain.pop('name'))}"]
    lines += [
        f"    # {path} = {_value(v)}: suggested from {tr.stats.n} train trades"
        f" ({_period(tr)}), checked on {te.stats.n} test trades ({_period(te)})"
        for path, v in s.variant.overrides.items()
    ]
    for k, v in plain.items():
        lines += _emit(k, v, 4)
    return "\n".join(lines) + "\n"
