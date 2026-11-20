"""Portfolio reports: build from the store, render as text, or flatten to tables for the
CSV and Google Sheets reporters. Organized portfolio -> strategy -> trade."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

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
    trades: list[Trade]  # settled, oldest first
    open_trades: list[Trade]
    reasons: dict[str, int]

    @property
    def open_positions(self) -> int:
        return len(self.open_trades)


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
                open_trades=store.open_trades(strategy=(portfolio.name, s.name)),
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


# Version of the CSV / Sheets / JSON report layout (contract/README.md). Bump the minor
# for added columns, the major for renamed or removed ones.
SCHEMA_VERSION = "1.0"

SUMMARY_COLUMNS = [
    "schema_version", "portfolio", "mode", "as_of", "started_at", "starting_balance_usd",
    "realized_pnl_usd", "interest_usd", "equity_usd", "return_rate", "open_positions",
    "open_cost_usd", "interest_apy", "cash_benchmark_usd", "pnl_vs_cash_usd",
]  # fmt: skip
STRATEGY_COLUMNS = [
    "schema_version", "portfolio", "strategy", "series", "status", "trades", "wins", "open",
    "win_rate", "win_rate_lo", "win_rate_hi", "break_even_rate", "edge_cents", "edge_lo_cents",
    "edge_hi_cents", "z", "p", "pnl_usd", "pnl_per_trade_usd", "roi", "max_drawdown_usd",
    "trades_to_detect", "trades_to_detect_edge_cents", "verdict", "brier",
]  # fmt: skip
TRADE_COLUMNS = [
    "schema_version", "portfolio", "strategy", "window_id", "ticker", "side", "contracts",
    "entry_price_cents", "fee_usd", "cost_per_contract_cents", "mode", "opened_at",
    "close_time", "settled_at", "result", "pnl_usd",
]  # fmt: skip


def _r(x: float | None, digits: int = 4) -> float | None:
    return None if x is None else round(x, digits)


def _c(x: float | None) -> float | None:
    """Dollars per contract -> cents, to a tenth of a cent (Kalshi's finest tick)."""
    return None if x is None else round(100 * x, 2)


def iso(t: datetime | None) -> str | None:
    """UTC ISO 8601 with a Z, the contract's one timestamp format."""
    return None if t is None else t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _row(columns: list[str], values: dict[str, Cell]) -> list[Cell]:
    return [values[c] for c in columns]  # KeyError if a column is forgotten


def _trade_row(t: Trade) -> list[Cell]:
    result = "open" if t.won is None else "won" if t.won else "lost"
    return _row(TRADE_COLUMNS, {
        "schema_version": SCHEMA_VERSION, "portfolio": t.portfolio, "strategy": t.strategy,
        "window_id": t.window_id, "ticker": t.ticker, "side": t.side, "contracts": t.qty,
        "entry_price_cents": _c(t.avg_price), "fee_usd": _r(t.fee, 2),
        "cost_per_contract_cents": _c(cost_per_contract(t)), "mode": t.mode,
        "opened_at": iso(t.opened_at), "close_time": iso(t.close_time),
        "settled_at": iso(t.settled_at), "result": result, "pnl_usd": _r(t.realized_pnl, 2),
    })  # fmt: skip


def stats_fields(st: StrategyStats) -> dict[str, Cell]:
    """A strategy's statistics in contract units; shared by reports and backtest runs."""
    return {
        "trades": st.n, "wins": st.wins, "win_rate": _r(st.win_rate),
        "win_rate_lo": _r(st.win_rate_ci.low if st.win_rate_ci else None),
        "win_rate_hi": _r(st.win_rate_ci.high if st.win_rate_ci else None),
        "break_even_rate": _r(st.breakeven), "edge_cents": _c(st.edge),
        "edge_lo_cents": _c(st.edge_ci.low if st.edge_ci else None),
        "edge_hi_cents": _c(st.edge_ci.high if st.edge_ci else None),
        "z": _r(st.z_vs_breakeven, 3), "p": _r(st.p_value), "pnl_usd": _r(st.pnl, 2),
        "pnl_per_trade_usd": _r(st.pnl_per_trade), "roi": _r(st.roi),
        "max_drawdown_usd": _r(st.max_drawdown, 2), "trades_to_detect": st.trades_needed,
        "trades_to_detect_edge_cents": _c(st.trades_needed_for),
        "verdict": st.sprt.decision, "brier": _r(st.brier),
    }  # fmt: skip


def tables(report: PortfolioReport) -> list[Table]:
    """The report as the three flat tables of the data contract (contract/README.md).
    Units are in the column names; missing values are None, never 0."""
    p = report.portfolio
    gain = report.equity - report.start
    summary = Table("summary", SUMMARY_COLUMNS, [_row(SUMMARY_COLUMNS, {
        "schema_version": SCHEMA_VERSION, "portfolio": p.name, "mode": p.mode,
        "as_of": iso(report.as_of), "started_at": iso(report.started_at),
        "starting_balance_usd": report.start, "realized_pnl_usd": _r(report.realized, 2),
        "interest_usd": _r(report.interest, 2), "equity_usd": _r(report.equity, 2),
        "return_rate": _r(report.total_return), "open_positions": report.open_positions,
        "open_cost_usd": _r(report.open_cost, 2), "interest_apy": p.interest_apy,
        "cash_benchmark_usd": _r(report.cash_benchmark, 2),
        "pnl_vs_cash_usd": None if report.cash_benchmark is None
        else _r(gain - report.cash_benchmark, 2),
    })])  # fmt: skip
    strategy_rows: list[list[Cell]] = []
    trade_rows: list[list[Cell]] = []
    for sr in report.strategies:
        strategy_rows.append(_row(STRATEGY_COLUMNS, {
            "schema_version": SCHEMA_VERSION, "portfolio": p.name, "strategy": sr.config.name,
            "series": sr.config.series, "status": sr.config.status, "open": sr.open_positions,
            **stats_fields(sr.stats),
        }))  # fmt: skip
        trade_rows += [_trade_row(t) for t in [*sr.trades, *sr.open_trades]]
    return [
        summary,
        Table("strategies", STRATEGY_COLUMNS, strategy_rows),
        Table("trades", TRADE_COLUMNS, trade_rows),
    ]


def report_data(report: PortfolioReport) -> dict[str, object]:
    """`nem summary --json`: the summary and strategy tables as objects, plus each
    strategy's skip reasons and calibration (contract/schema/summary_report.schema.json)."""
    summary, strategies, _ = tables(report)
    rows: list[dict[str, object]] = [
        dict(zip(strategies.columns, r, strict=True)) for r in strategies.rows
    ]
    for row, sr in zip(rows, report.strategies, strict=True):
        row["signal_reasons"] = dict(sorted(sr.reasons.items()))
        row["calibration"] = [
            {"lo_rate": b.low, "hi_rate": b.high, "n": b.n,
             "predicted_rate": _r(b.predicted), "observed_rate": _r(b.actual)}
            for b in sr.stats.calibration
        ]  # fmt: skip
    return {
        "schema_version": SCHEMA_VERSION,
        "summary": dict(zip(summary.columns, summary.rows[0], strict=True)),
        "strategies": rows,
    }
