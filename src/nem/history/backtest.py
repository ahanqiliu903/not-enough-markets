"""Replays and sweeps over a history file, and their honest report.

- `replay`: every portfolio over the markets dated in a period (`HistorySource`), results
  in a fresh in-memory store.
- `sweep`: like `nem suggest`'s `run_sweep`, but a grid path applies to **every strategy of
  the portfolio** (one strategy per city, same gates), so variants are compared on the
  pooled trades of all cities. `nem.backtest.suggest.suggest` then chooses on train only
  and lets the later test period reject.
- `period_report`: per trade (one trade = one market: the engine trades at most one
  bracket per strategy per event), per city, per market date (cities on one date share the
  weather, so a date is won only when none of its trades lost), and by exit kind, with the
  per-contract edge from realized P&L (correct with early exits too).

Every verdict here goes through `cap_verdict` and every result is labeled: a backtest on
archived quotes without depth and backfilled forecasts can reject an idea, not confirm it.
The archive has no depth, so every replay here fills at the quote (`quote_fills`: the
portfolio's `fills.missing_depth` is set to `quote`) and is labeled so.
"""

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from statistics import fmean, stdev

from nem.backtest.sweep import SweepError, expand
from nem.backtest.types import Grid, Period, Variant, VariantResult
from nem.builtins import load_builtins
from nem.core.clock import ManualClock
from nem.core.config import PortfolioConfig, StrategyConfig
from nem.core.types import Trade
from nem.engine.runner import Runner
from nem.gates.feeds import market_date
from nem.history.db import LABEL, HistoryDb
from nem.history.source import HistorySource
from nem.stats.exits import ExitLabel, exit_kind
from nem.stats.metrics import Interval, StrategyStats, strategy_stats
from nem.stats.provenance import (
    BACKFILL_LABEL,
    QUOTE_FILLS_LABEL,
    cap_verdict,
    uses_backfill,
)
from nem.store import Store

# Kalshi settles daily weather markets the morning after; a week covers late revisions.
SETTLE_GRACE = timedelta(days=7)


def labels(portfolios: Iterable[PortfolioConfig]) -> str:
    backfill = any(uses_backfill(s) for p in portfolios for s in p.strategies)
    parts = ([BACKFILL_LABEL] if backfill else []) + [LABEL, QUOTE_FILLS_LABEL]
    return " ".join(f"[{x}]" for x in parts)


def quote_fills(portfolio: PortfolioConfig) -> PortfolioConfig:
    """`portfolio` with `fills.missing_depth: quote`. Historical candles record no depth, so
    nothing would fill otherwise; the assumption is explicit and in every label here."""
    if portfolio.fills.missing_depth == "quote":
        return portfolio
    fills = portfolio.fills.model_copy(update={"missing_depth": "quote"})
    return portfolio.model_copy(update={"fills": fills})


@dataclass(frozen=True, slots=True)
class DatePeriod:
    """Markets whose market date is in [start, end], inclusive."""

    start: date
    end: date

    def as_period(self) -> Period:
        # `Period` is a time range; the sweep machinery only uses it as a label here.
        return Period(
            datetime.combine(self.start, time(), UTC),
            datetime.combine(self.end + timedelta(days=1), time(), UTC),
        )


def replay(
    db: HistoryDb, portfolios: Sequence[PortfolioConfig], period: DatePeriod, results: Store,
    *, step: timedelta = timedelta(minutes=1),
) -> None:  # fmt: skip
    load_builtins()
    source = HistorySource(db, period.start, period.end, step=step)
    series = sorted({s.series for p in portfolios for s in p.strategies if s.status == "active"})
    markets = db.markets(series, period.start, period.end)
    if not markets:
        raise SweepError(f"no imported markets for {series} in {period.start}..{period.end}")
    clock = ManualClock(min(m.open_time for m in markets))
    quoted = [quote_fills(p) for p in portfolios]
    runner = Runner(quoted, source, results, clock, data=db.store, fetch_feeds=False)
    runner.run(source.ticks(runner.series))
    runner.settle_remaining(max(m.close_time for m in markets) + SETTLE_GRACE)


def _variant_portfolio(
    base: PortfolioConfig, name: str, configs: list[StrategyConfig]
) -> PortfolioConfig:
    return base.model_copy(update={"name": f"{base.name}_{name}", "strategies": configs,
                                   "reporting": []})  # fmt: skip


def expand_portfolio(base: PortfolioConfig, grid: Grid) -> list[tuple[Variant, PortfolioConfig]]:
    """One portfolio per grid combination; each path is applied to every active strategy,
    which keeps its own name."""
    active = [s for s in base.strategies if s.status == "active"]
    if not active:
        raise SweepError(f"{base.name} has no active strategies")
    per_strategy = [expand(s, grid) for s in active]
    out: list[tuple[Variant, PortfolioConfig]] = []
    for i, first in enumerate(per_strategy[0]):
        configs = [
            vs[i].config.model_copy(update={"name": s.name})
            for s, vs in zip(active, per_strategy, strict=True)
        ]
        out.append((first, _variant_portfolio(base, first.name, configs)))
    return out


def trades_of(results: Store, portfolio: PortfolioConfig) -> list[Trade]:
    keys = [(portfolio.name, s.name) for s in portfolio.strategies]
    return results.settled_trades(strategies=keys)


def sweep(
    db: HistoryDb, base: PortfolioConfig, grid: Grid, periods: Sequence[DatePeriod],
    *, step: timedelta = timedelta(minutes=1),
) -> dict[DatePeriod, list[VariantResult]]:  # fmt: skip
    """Replay each period once with every variant side by side (each its own portfolio,
    so cash and risk limits behave as they would alone); stats pool all its strategies."""
    variants = expand_portfolio(base, grid)
    out: dict[DatePeriod, list[VariantResult]] = {}
    for period in periods:
        with Store() as results:
            replay(db, [p for _, p in variants], period, results, step=step)
            rows: list[VariantResult] = []
            for v, p in variants:
                trades = trades_of(results, p)
                rows.append(VariantResult(v, period.as_period(), strategy_stats(trades)))
        out[period] = rows
    return out


# --- report -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DateBet:
    """All trades on one market date (every city): their P&L per contract, and whether
    none lost. Cities on one date share the weather, so dates, not trades, are the
    independent-ish unit."""

    day: date
    n_trades: int
    contracts: int
    realized_pnl: float
    won: bool  # no trade on the date lost at settlement

    @property
    def edge(self) -> float:
        return self.realized_pnl / self.contracts


def date_bets(trades: Sequence[Trade], won_at_settlement: dict[str, bool]) -> list[DateBet]:
    groups: dict[date, list[Trade]] = defaultdict(list)
    for t in trades:
        d = market_date(t.window_id)
        if d is not None:
            groups[d].append(t)
    return [
        DateBet(
            d, len(ts), sum(t.qty for t in ts), sum(t.realized_pnl or 0.0 for t in ts),
            all(won_at_settlement.get(t.window_id + t.strategy, bool(t.won)) for t in ts),
        )
        for d, ts in sorted(groups.items())
    ]  # fmt: skip


@dataclass(frozen=True, slots=True)
class DateStats:
    """Mean over dates of each date's realized P&L per contract, with a normal 95%
    interval across dates (clustered by date; no independence between cities assumed)."""

    n: int
    lost: int
    edge: float | None
    ci: Interval | None

    @property
    def decision(self) -> str:
        if self.ci is None or self.n < 2:
            return "undecided"
        if self.ci.high < 0:
            return "no_edge"
        return "edge" if self.ci.low > 0 else "undecided"


def date_stats(dates: Sequence[DateBet]) -> DateStats:
    edges = [d.edge for d in dates]
    lost = sum(1 for d in dates if not d.won)
    if not edges:
        return DateStats(0, 0, None, None)
    mean = fmean(edges)
    if len(edges) < 2:
        return DateStats(len(edges), lost, mean, None)
    half = 1.96 * stdev(edges) / math.sqrt(len(edges))
    return DateStats(len(edges), lost, mean, Interval(mean - half, mean + half))


@dataclass
class Group:
    name: str
    trades: list[Trade] = field(default_factory=list[Trade])

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def contracts(self) -> int:
        return sum(t.qty for t in self.trades)

    @property
    def pnl(self) -> float:
        return sum(t.realized_pnl or 0.0 for t in self.trades)

    @property
    def edge_pc(self) -> float | None:
        """Mean over trades of realized P&L per contract (after fees), dollars."""
        if not self.trades:
            return None
        return fmean((t.realized_pnl or 0.0) / t.qty for t in self.trades)

    @property
    def mean_entry(self) -> float | None:
        return fmean(t.avg_price for t in self.trades) if self.trades else None

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if (t.realized_pnl or 0.0) <= 0)

    @property
    def worst(self) -> float:
        return min((t.realized_pnl or 0.0 for t in self.trades), default=0.0)


@dataclass
class PeriodReport:
    title: str
    label: str
    period: DatePeriod
    trades: list[Trade]
    held: StrategyStats  # per trade, hold-to-settlement statistics (won at settlement)
    by_date: DateStats
    verdict: str  # capped
    verdict_by_date: str  # capped
    all_trades: Group
    by_city: list[Group]
    by_exit: dict[ExitLabel, Group]
    by_month: list[Group]
    open_trades: int


def _won_at_settlement(results: Store, trades: Sequence[Trade]) -> dict[str, bool]:
    """Key window_id + strategy -> our side won at settlement (exits: from learning)."""
    out: dict[str, bool] = {}
    cache: dict[tuple[str, str], dict[str, bool | None]] = {}
    for t in trades:
        kind = exit_kind(results, t)
        key = (t.portfolio, t.strategy)
        if kind == "settlement" or kind is None:
            out[t.window_id + t.strategy] = bool(t.won)
            continue
        if key not in cache:
            cache[key] = results.exit_outcomes(key)
        w = cache[key].get(t.window_id)
        if w is not None:
            out[t.window_id + t.strategy] = w
    return out


@dataclass(frozen=True, slots=True)
class _HeldBet:
    qty: float
    cost: float
    won: bool
    realized_pnl: float


def period_report(
    results: Store, portfolio: PortfolioConfig, period: DatePeriod, title: str
) -> PeriodReport:
    keys = [(portfolio.name, s.name) for s in portfolio.strategies]
    trades = results.settled_trades(strategies=keys)
    open_n = len(results.open_trades(portfolio=portfolio.name))
    won = _won_at_settlement(results, trades)
    # Held-to-settlement view: each trade as a $1/$0 bet on its side's settlement outcome
    # (for exited trades, what would have happened had it been held). The money view is the
    # realized-P&L edge in the groups below.
    held_bets = [
        _HeldBet(t.qty, t.cost, won.get(t.window_id + t.strategy, bool(t.won)),
                 t.qty * (1.0 if won.get(t.window_id + t.strategy, bool(t.won)) else 0.0)
                 - t.cost)
        for t in trades
    ]  # fmt: skip
    held = strategy_stats(held_bets)
    by_date = date_stats(date_bets(trades, won))
    by_city: dict[str, Group] = {}
    by_exit: dict[ExitLabel, Group] = {}
    by_month: dict[str, Group] = {}
    for t in trades:
        by_city.setdefault(t.strategy, Group(t.strategy)).trades.append(t)
        kind = exit_kind(results, t) or "settlement"
        by_exit.setdefault(kind, Group(kind)).trades.append(t)
        d = market_date(t.window_id)
        m = f"{d:%Y-%m}" if d else "?"
        by_month.setdefault(m, Group(m)).trades.append(t)
    return PeriodReport(
        title=title,
        label=labels([portfolio]),
        period=period,
        trades=trades,
        held=held,
        by_date=by_date,
        verdict=cap_verdict(held.sprt.decision, uses_backfill=True),
        verdict_by_date=cap_verdict(by_date.decision, uses_backfill=True),
        all_trades=Group("all", list(trades)),
        by_city=sorted(by_city.values(), key=lambda g: g.name),
        by_exit=by_exit,
        by_month=[by_month[k] for k in sorted(by_month)],
        open_trades=open_n,
    )


def _c(x: float | None) -> str:
    return "-" if x is None else f"{round(100 * x, 2) + 0.0:+.2f}c"


def _p(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.1f}%"


def _stats_line(name: str, st: StrategyStats) -> str:
    ci = st.win_rate_ci
    ci_s = f" [{_p(ci.low)}, {_p(ci.high)}]" if ci else ""
    return (
        f"{name}: n={st.n}, win rate {_p(st.win_rate)}{ci_s} vs break-even"
        f" {_p(st.breakeven)}, edge {_c(st.edge)}/contract, SPRT {st.sprt.decision}"
    )


def _group_row(g: Group) -> str:
    return (
        f"| {g.name} | {g.n} | {g.contracts} |"
        f" {'-' if g.mean_entry is None else f'{g.mean_entry:.3f}'} | {g.losses} |"
        f" {g.pnl:+.2f} | {_c(g.edge_pc)} | {g.worst:+.2f} |"
    )


GROUP_HEADER = (
    "| group | trades | contracts | mean entry | P&L<=0 | P&L $ | edge/contract (P&L) |"
    " worst $ |\n|---|---|---|---|---|---|---|---|"
)


def render_period(r: PeriodReport) -> str:
    lines = [
        f"### {r.label} {r.title} ({r.period.start} to {r.period.end})",
        "",
        f"- {_stats_line('Per trade (= per market), held-to-settlement outcome', r.held)};"
        f" verdict (capped) **{r.verdict}**",
        f"- Per market date (cities pooled, P&L per contract per date): {r.by_date.n} dates,"
        f" {r.by_date.lost} with a loss, edge {_c(r.by_date.edge)}/contract"
        f" [{_c(r.by_date.ci.low if r.by_date.ci else None)},"
        f" {_c(r.by_date.ci.high if r.by_date.ci else None)}]; verdict (capped)"
        f" **{r.verdict_by_date}**",
        f"- Money: P&L {r.all_trades.pnl:+.2f} on {r.all_trades.contracts} contracts, edge"
        f" {_c(r.all_trades.edge_pc)}/contract after fees (realized, incl. exits);"
        f" {r.open_trades} trades left open (no result archived)",
    ]
    if r.held.trades_needed is not None:
        lines.append(
            f"- About {r.held.trades_needed:,} trades needed to detect a"
            f" {100 * r.held.trades_needed_for:.1f}c edge at this break-even."
        )
    lines += ["", GROUP_HEADER]
    lines += [_group_row(g) for g in r.by_city]
    lines += [_group_row(g) for g in r.by_exit.values()]
    lines += [_group_row(g) for g in r.by_month]
    lines.append(_group_row(r.all_trades))
    return "\n".join(lines)


def describe_trades(trades: Sequence[Trade], won: dict[str, bool] | None = None) -> list[str]:
    """One line per losing trade, for the report (synthetic-safe: tickers are public)."""
    del won
    out: list[str] = []
    for t in sorted(trades, key=lambda t: t.opened_at):
        if (t.realized_pnl or 0.0) > 0:
            continue
        out.append(
            f"| {t.ticker} | {t.side} | {t.avg_price:.2f} | {t.qty} |"
            f" {t.opened_at:%Y-%m-%d %H:%M}Z | {(t.realized_pnl or 0.0):+.2f} |"
        )
    return out
