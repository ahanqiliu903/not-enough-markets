"""Portfolio reports: build from the store, render as text, or flatten to tables for the
CSV and Google Sheets reporters. Organized portfolio -> strategy -> trade."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from nem.core.config import PortfolioConfig, StrategyConfig
from nem.core.types import Trade
from nem.stats.metrics import StrategyStats, cost_per_contract, strategy_stats
from nem.store import Store

Cell = str | int | float | None


@dataclass(frozen=True)
class Table:
    name: str  # e.g. "strategies"
    columns: list[str]
    rows: list[list[Cell]]


@dataclass(frozen=True)
class StrategyReport:
    config: StrategyConfig
    stats: StrategyStats
    trades: list[Trade]
    open_positions: int
    reasons: dict[str, int]


@dataclass(frozen=True)
class PortfolioReport:
    portfolio: PortfolioConfig
    as_of: datetime
    start: float
    realized: float
    interest: float
    open_cost: float
    open_positions: int
    started_at: datetime | None
    cash_benchmark: float | None  # what holding cash at `interest_apy` would have earned
    strategies: list[StrategyReport]
    target_edge: float

    @property
    def equity(self) -> float:
        return self.start + self.realized + self.interest

    @property
    def total_return(self) -> float:
        return (self.equity - self.start) / self.start if self.start else 0.0


def build_report(
    store: Store,
    portfolio: PortfolioConfig,
    now: datetime,
    target_edge: float = 0.02,
    alpha: float = 0.05,
    power: float = 0.8,
) -> PortfolioReport:
    strategies: list[StrategyReport] = []
    for s in portfolio.strategies:
        trades = store.settled_trades(strategies=[(portfolio.name, s.name)])
        p_by_window = store.p_models(portfolio.name, s.name)
        p_models = [p_by_window.get(t.window_id) for t in trades]
        strategies.append(
            StrategyReport(
                config=s,
                stats=strategy_stats(trades, p_models, target_edge, alpha, power),
                trades=trades,
                open_positions=len(store.open_trades(strategy=(portfolio.name, s.name))),
                reasons=store.signal_reasons(portfolio.name, s.name),
            )
        )
    start = portfolio.starting_balance or portfolio.budget or 0.0
    open_trades = store.open_trades(portfolio=portfolio.name)
    started = store.first_activity(portfolio.name)
    benchmark = None
    if portfolio.interest_apy and started is not None:
        days = (now - started).total_seconds() / 86400
        benchmark = start * portfolio.interest_apy * days / 365
    return PortfolioReport(
        portfolio=portfolio,
        as_of=now,
        start=start,
        realized=sum(t.realized_pnl or 0.0 for t in store.settled_trades(portfolio=portfolio.name)),
        interest=store.ledger_total(portfolio.name),
        open_cost=sum(t.cost for t in open_trades),
        open_positions=len(open_trades),
        started_at=started,
        cash_benchmark=benchmark,
        strategies=strategies,
        target_edge=target_edge,
    )


# --- text ------------------------------------------------------------------------------


def _pct(x: float | None, digits: int = 1) -> str:
    return "-" if x is None else f"{100 * x:.{digits}f}%"


def _cents(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:+.1f}c"


def verdict(stats: StrategyStats, target_edge: float) -> str:
    edge = f"{100 * target_edge:g}c"
    if stats.n == 0:
        return "no settled trades yet"
    if stats.sprt.decision == "edge":
        return f"evidence of a {edge}+ edge per contract (sequential test, {stats.n} trades)"
    if stats.sprt.decision == "no_edge":
        return f"evidence of no {edge} edge: consider pausing or retiring it"
    size = f"{100 * stats.trades_needed_for:.1f}c"
    which = "the observed" if stats.trades_needed_for != target_edge else "a"
    if stats.trades_needed is None:
        return f"undecided after {stats.n} trades"
    return (
        f"undecided after {stats.n} trades; ~{stats.trades_needed:,} trades to detect"
        f" {which} {size} edge"
    )


def render_text(report: PortfolioReport) -> str:
    p = report.portfolio
    lines = [
        f"Portfolio {p.name} ({p.mode})",
        f"  equity ${report.equity:,.2f}  ({_pct(report.total_return, 2)} on ${report.start:,.2f})"
        f"  realized {report.realized:+,.2f}  interest {report.interest:+,.2f}",
        f"  open positions {report.open_positions} (${report.open_cost:,.2f} at cost)",
    ]
    if report.cash_benchmark is not None:
        diff = report.realized + report.interest - report.cash_benchmark
        lines.append(
            f"  vs holding cash at {_pct(p.interest_apy, 2)} APY: {diff:+,.2f}"
            f" (cash alone would have earned {report.cash_benchmark:+,.2f})"
        )
    for sr in report.strategies:
        s, st = sr.config, sr.stats
        lines.append("")
        lines.append(
            f"  {s.name}  {s.series}  {s.status}  {st.n} trades ({st.wins} won),"
            f" {sr.open_positions} open"
        )
        if st.n:
            ci = st.win_rate_ci
            ci_s = f"[{_pct(ci.low)}, {_pct(ci.high)}]" if ci else ""
            z = (
                f"z {st.z_vs_breakeven:+.2f} (p {st.p_value:.2f})"
                if st.z_vs_breakeven is not None and st.p_value is not None
                else ""
            )
            be = _pct(st.breakeven)
            lines.append(f"    win rate    {_pct(st.win_rate)} {ci_s}   break-even {be}   {z}")
            eci = st.edge_ci
            eci_s = f"[{_cents(eci.low)}, {_cents(eci.high)}]" if eci else ""
            lines.append(f"    edge        {_cents(st.edge)} per contract {eci_s}")
            per = f"{st.pnl_per_trade:+.2f}/trade" if st.pnl_per_trade is not None else ""
            lines.append(
                f"    P&L         {st.pnl:+,.2f}  ({per}, ROI {_pct(st.roi)})"
                f"   max drawdown {st.max_drawdown:,.2f}"
            )
        lines.append(f"    verdict     {verdict(st, report.target_edge)}")
        if st.calibration:
            buckets = "; ".join(
                f"{_pct(b.low, 0)}-{_pct(b.high, 0)}: said {_pct(b.predicted)},"
                f" won {_pct(b.actual)} (n={b.n})"
                for b in st.calibration
            )
            brier = f"Brier {st.brier:.3f}" if st.brier is not None else ""
            lines.append(f"    calibration {brier}  {buckets}")
        skipped = {r: c for r, c in sr.reasons.items() if not r.startswith("ok")}
        if skipped:
            top = ", ".join(f"{c} {r}" for r, c in sorted(skipped.items(), key=lambda x: -x[1]))
            lines.append(f"    skipped     {top}")
    return "\n".join(lines)


def render_all(reports: Sequence[PortfolioReport]) -> str:
    body = "\n\n".join(render_text(r) for r in reports)
    as_of = reports[0].as_of if reports else None
    return body + (f"\n\nas of {as_of:%Y-%m-%d %H:%M} UTC" if as_of else "")


# --- tables (CSV, Sheets) ---------------------------------------------------------------


def _r(x: float | None, digits: int = 4) -> float | None:
    return None if x is None else round(x, digits)


def tables(report: PortfolioReport) -> list[Table]:
    p = report.portfolio
    summary = Table(
        "summary",
        ["portfolio", "mode", "as_of", "start", "realized", "interest", "equity", "return",
         "open_positions", "open_cost", "cash_benchmark"],
        [[p.name, p.mode, report.as_of.isoformat(), report.start, _r(report.realized, 2),
          _r(report.interest, 2), _r(report.equity, 2), _r(report.total_return),
          report.open_positions, _r(report.open_cost, 2), _r(report.cash_benchmark, 2)]],
    )  # fmt: skip
    strategy_rows: list[list[Cell]] = []
    trade_rows: list[list[Cell]] = []
    for sr in report.strategies:
        st = sr.stats
        strategy_rows.append(
            [p.name, sr.config.name, sr.config.series, sr.config.status, st.n, st.wins,
             _r(st.win_rate), _r(st.win_rate_ci.low if st.win_rate_ci else None),
             _r(st.win_rate_ci.high if st.win_rate_ci else None), _r(st.breakeven), _r(st.edge),
             _r(st.z_vs_breakeven, 3), _r(st.p_value), _r(st.pnl, 2), _r(st.roi),
             _r(st.max_drawdown, 2), st.trades_needed, st.sprt.decision, _r(st.brier),
             sr.open_positions]
        )  # fmt: skip
        for t in sr.trades:
            trade_rows.append(
                [p.name, t.strategy, t.window_id, t.ticker, t.side, t.qty, _r(t.avg_price),
                 _r(t.fee, 2), _r(cost_per_contract(t)), t.mode, t.opened_at.isoformat(),
                 t.settled_at.isoformat() if t.settled_at else None,
                 None if t.won is None else int(t.won), _r(t.realized_pnl, 2)]
            )  # fmt: skip
    strategies = Table(
        "strategies",
        ["portfolio", "strategy", "series", "status", "trades", "wins", "win_rate",
         "win_rate_low", "win_rate_high", "breakeven", "edge_per_contract", "z_vs_breakeven",
         "p_value", "pnl", "roi", "max_drawdown", "trades_needed", "sprt", "brier", "open"],
        strategy_rows,
    )  # fmt: skip
    trades = Table(
        "trades",
        ["portfolio", "strategy", "window_id", "ticker", "side", "qty", "avg_price", "fee",
         "cost_per_contract", "mode", "opened_at", "settled_at", "won", "pnl"],
        trade_rows,
    )  # fmt: skip
    return [summary, strategies, trades]
