"""Portfolio reports: build from the store, render as text, or flatten to tables for the
CSV and Google Sheets reporters. Organized portfolio -> strategy -> trade."""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from statistics import fmean, stdev
from zoneinfo import ZoneInfo

from nem.core.config import PluginSpec, PortfolioConfig, RiskConfig, StrategyConfig
from nem.core.types import ManualExit, ManualLot, Trade
from nem.execution.fees import DEFAULT_TAKER_RATE
from nem.execution.mark import Mark, mark_position
from nem.stats.exits import ExitBreakdown, ExitLabel, by_exit_kind
from nem.stats.held import HeldToSettlement, held_to_settlement
from nem.stats.metrics import Interval, StrategyStats, cost_per_contract, strategy_stats, wilson
from nem.stats.provenance import cap_verdict, label, uses_backfill
from nem.store import Store

Cell = str | int | float | None


@dataclass(frozen=True)
class Table:
    name: str  # e.g. "strategies"
    columns: list[str]
    rows: list[list[Cell]]


MarkKey = tuple[str, str]  # (strategy, window_id): one open trade within a portfolio


@dataclass(frozen=True)
class OpenMarks:
    """Open positions at liquidation value (nem.execution.mark), summed over the trades with
    a fresh recorded book; contracts its bids don't cover count at $0 (`no_bid`). The rest
    are `unmarked` and left out of the sums (stale or missing snapshot, closed market, no
    depth recorded); with open positions but none marked, the sums are None. With no open
    positions, 0."""

    value: float | None  # proceeds at best bids after the exit fee, $0 for uncovered
    unrealized_pnl: float | None  # value - entry cost incl. entry fee
    unmarked: int
    no_bid: int | None  # contracts of marked trades the bids don't cover, valued at $0


NO_OPEN = OpenMarks(0.0, 0.0, 0, 0)


def open_marks(trades: Sequence[Trade], marks: dict[MarkKey, Mark]) -> OpenMarks:
    if not trades:
        return NO_OPEN
    marked = [m for t in trades if (m := marks.get((t.strategy, t.window_id)))]
    if not marked:
        return OpenMarks(None, None, len(trades), None)
    return OpenMarks(
        sum(m.value for m in marked),
        sum(m.unrealized_pnl for m in marked),
        len(trades) - len(marked),
        sum(m.no_bid for m in marked),
    )


def mark_trades(
    store: Store, trades: Sequence[Trade], rates: dict[str, float], now: datetime
) -> dict[MarkKey, Mark]:
    """Marks of open trades against each market's latest snapshot at `now`, at the
    strategy's taker rate (0.07 for a strategy no longer in the portfolio file)."""
    out: dict[MarkKey, Mark] = {}
    for t in trades:
        snap = store.latest_snapshot(t.ticker, now)
        mark = mark_position(t, snap, rates.get(t.strategy, DEFAULT_TAKER_RATE), now)
        if mark is not None:
            out[(t.strategy, t.window_id)] = mark
    return out


@dataclass(frozen=True)
class StrategyReport:
    config: StrategyConfig
    stats: StrategyStats  # over settled trades not excluded from statistics
    trades: list[Trade]  # settled, oldest first, excluded ones included (they're listed)
    open_trades: list[Trade]  # excluded ones included (they're listed)
    reasons: dict[str, int]
    # imported manual lots only: the hold-to-settlement counterfactual, None until their
    # markets are checked (nem settle verify)
    held: HeldToSettlement | None = None
    # engine trades split by how they ended; empty unless the strategy exits or has exited
    exits: dict[ExitLabel, ExitBreakdown] = field(default_factory=dict[ExitLabel, ExitBreakdown])
    marks: dict[MarkKey, Mark] = field(default_factory=dict[MarkKey, Mark])  # open trades only

    @property
    def counted_open(self) -> list[Trade]:
        """Open trades that count (not excluded): the ones in money totals and marks."""
        return [t for t in self.open_trades if not t.excluded]

    @property
    def open(self) -> OpenMarks:
        return open_marks(self.counted_open, self.marks)

    @property
    def excluded(self) -> list[Trade]:
        """Trades excluded from statistics (`nem trade exclude`), settled and open."""
        return [t for t in (*self.trades, *self.open_trades) if t.excluded]

    @property
    def open_positions(self) -> int:
        return len(self.counted_open)

    @property
    def backfilled(self) -> bool:
        """A gate reads backfilled forecasts: the verdict is capped (nem.stats.provenance)."""
        return uses_backfill(self.config)

    @property
    def verdict(self) -> str:
        """The sequential test's decision as reported: "edge" on backfilled forecasts is
        capped to "undecided" (there is no separate forward-recorded verdict to confirm it)."""
        return cap_verdict(self.stats.sprt.decision, uses_backfill=self.backfilled)


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
    manual: "ManualBaseline | None" = None  # set for imported manual trading (read-only)
    open: OpenMarks = NO_OPEN  # open positions at liquidation value
    # excluded trades, incl. strategies no longer in the portfolio file; left out of
    # realized, equity and open positions as if never taken
    excluded: list[Trade] = field(default_factory=list[Trade])

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
    open_trades = store.open_trades(portfolio=portfolio.name, include_excluded=False)
    rates = {s.name: s.taker_rate for s in portfolio.strategies}
    marks = mark_trades(store, open_trades, rates, now)
    strategies: list[StrategyReport] = []
    for s in portfolio.strategies:
        trades = store.settled_trades(strategies=[(portfolio.name, s.name)])
        counted = [t for t in trades if not t.excluded]  # statistics leave excluded out
        p_by_window = store.p_models(portfolio.name, s.name)
        p_models = [p_by_window.get(t.window_id) for t in counted]
        strategies.append(
            StrategyReport(
                config=s,
                stats=strategy_stats(counted, p_models, target_edge, alpha, power),
                trades=trades,
                open_trades=store.open_trades(strategy=(portfolio.name, s.name)),
                reasons=store.signal_reasons(portfolio.name, s.name),
                exits=_exits(store, s, counted),
                marks={k: m for k, m in marks.items() if k[0] == s.name},
            )
        )
    start = portfolio.starting_balance or portfolio.budget or 0.0
    started = store.first_activity(portfolio.name)
    benchmark = None
    if portfolio.interest_apy and started is not None:
        days = (now - started).total_seconds() / 86400
        benchmark = start * portfolio.interest_apy * days / 365
    return PortfolioReport(
        portfolio=portfolio,
        as_of=now,
        start=start,
        realized=sum(
            t.realized_pnl or 0.0
            for t in store.settled_trades(portfolio=portfolio.name, include_excluded=False)
        ),
        interest=store.ledger_total(portfolio.name),
        open_cost=sum(t.cost for t in open_trades),
        open_positions=len(open_trades),
        started_at=started,
        cash_benchmark=benchmark,
        strategies=strategies,
        target_edge=target_edge,
        open=open_marks(open_trades, marks),
        excluded=store.excluded_trades(portfolio.name),
    )


def _exits(
    store: Store, s: StrategyConfig, trades: Sequence[Trade]
) -> dict[ExitLabel, ExitBreakdown]:
    kinds = by_exit_kind(store, trades)
    exits_now = s.exit is not None and not s.exit.holds
    return kinds if exits_now or set(kinds) - {"settlement"} else {}


@dataclass(frozen=True)
class ManualBaseline:
    """Totals of an imported manual portfolio (nem import kalshi-pnl). Money is Kalshi's
    realized P&L after fees, summed; nothing is recomputed from prices."""

    lots: int
    notional: float  # entry price x contracts, summed over lots
    fees: float
    pnl: float  # after fees
    first_close: datetime
    last_close: datetime
    exits: dict[ManualExit, tuple[int, float]]  # exit kind -> (lots, P&L after fees)
    # Per-lot (unweighted) numbers: position size changed a lot over the period, so dollar
    # totals mix sizing with edge. Each lot counts once here, whatever its size.
    contracts: float
    wins: int  # lots with P&L after fees > 0
    win_ci: Interval | None  # Wilson 95%
    mean_entry: float  # mean entry price per lot
    edge: float  # mean P&L after fees per contract, per lot (dollars)
    edge_ci: Interval | None  # normal 95% interval on that mean
    months: list["ManualMonth"]


@dataclass(frozen=True)
class ManualMonth:
    month: str  # YYYY-MM of the close, US Eastern (Kalshi's export time zone)
    lots: int
    contracts: float
    notional: float
    pnl: float
    edge: float  # mean P&L after fees per contract, per lot (dollars)


EXPORT_TZ = ZoneInfo("America/New_York")


def _per_contract(lots: Sequence[ManualLot]) -> list[float]:
    return [lot.realized_pnl / lot.qty for lot in lots]


def manual_baseline(lots: Sequence[ManualLot]) -> ManualBaseline:
    exits: dict[ManualExit, tuple[int, float]] = {}
    by_month: dict[str, list[ManualLot]] = {}
    for lot in lots:
        n, pnl = exits.get(lot.exit, (0, 0.0))
        exits[lot.exit] = (n + 1, pnl + lot.realized_pnl)
        by_month.setdefault(f"{lot.closed_at.astimezone(EXPORT_TZ):%Y-%m}", []).append(lot)
    per = _per_contract(lots)
    edge = fmean(per)
    edge_ci = None
    if len(per) > 1:
        half = 1.96 * stdev(per) / math.sqrt(len(per))
        edge_ci = Interval(edge - half, edge + half)
    wins = sum(1 for lot in lots if lot.won)
    return ManualBaseline(
        lots=len(lots),
        notional=sum(lot.notional for lot in lots),
        fees=sum(lot.fee for lot in lots),
        pnl=sum(lot.realized_pnl for lot in lots),
        first_close=min(lot.closed_at for lot in lots),
        last_close=max(lot.closed_at for lot in lots),
        exits=exits,
        contracts=sum(lot.qty for lot in lots),
        wins=wins,
        win_ci=wilson(wins, len(lots)),
        mean_entry=fmean(lot.entry_price for lot in lots),
        edge=edge,
        edge_ci=edge_ci,
        months=[
            ManualMonth(
                month=month,
                lots=len(group),
                contracts=sum(lot.qty for lot in group),
                notional=sum(lot.notional for lot in group),
                pnl=sum(lot.realized_pnl for lot in group),
                edge=fmean(_per_contract(group)),
            )
            for month, group in sorted(by_month.items())
        ],
    )


def _series_label(lots: Sequence[ManualLot]) -> str:
    series = {lot.series for lot in lots}
    if len(series) == 1:
        return series.pop()
    return "KXHIGH" if all(s.startswith("KXHIGH") for s in series) else "MIXED"


def build_manual_report(
    store: Store,
    portfolio: str,
    now: datetime,
    target_edge: float = 0.02,
    alpha: float = 0.05,
    power: float = 0.8,
) -> PortfolioReport | None:
    """Report for an imported manual portfolio, None if it has no lots. The portfolio is
    real-money history, so `mode` is live; its `budget` is the lots' entry notional, which
    makes the return the P&L on the money put in. Strategies are `retired`: never traded.
    Win = lot P&L after fees > 0; break-even = mean (entry + fees) per contract. Each
    strategy also gets the hold-to-settlement counterfactual on checked results (held.py)."""
    names = [s for p, s in store.manual_strategies() if p == portfolio]
    by_strategy = {name: store.manual_lots(portfolio, name) for name in names}
    lots = [lot for group in by_strategy.values() for lot in group]
    if not lots:
        return None
    checks = {c.ticker: c for c in store.settlement_checks()}
    manual = PluginSpec(type="manual_import")  # not a plugin: nothing is ever built
    strategies: list[StrategyReport] = []
    configs: list[StrategyConfig] = []
    for name, group in by_strategy.items():
        config = StrategyConfig(
            name=name, status="retired", series=_series_label(group), signal=manual,
            sizing=manual, risk=RiskConfig(),
        )  # fmt: skip
        configs.append(config)
        strategies.append(
            StrategyReport(
                config=config,
                stats=strategy_stats(group, (), target_edge, alpha, power),
                trades=[],  # lots are not engine trades (fractional sizes, early exits)
                open_trades=[],
                reasons={},
                held=held_to_settlement(group, checks, target_edge, alpha, power),
            )
        )
    baseline = manual_baseline(lots)
    config = PortfolioConfig(
        name=portfolio, mode="live", budget=baseline.notional, strategies=configs
    )
    return PortfolioReport(
        portfolio=config,
        as_of=now,
        start=baseline.notional,
        realized=baseline.pnl,
        interest=0.0,
        open_cost=0.0,
        open_positions=0,
        started_at=min(lot.opened_at for lot in lots),
        cash_benchmark=None,
        strategies=strategies,
        target_edge=target_edge,
        manual=baseline,
    )


def manual_reports(
    store: Store,
    now: datetime,
    skip: Sequence[str] = (),
    target_edge: float = 0.02,
    alpha: float = 0.05,
    power: float = 0.8,
) -> list[PortfolioReport]:
    """Every imported manual portfolio, except names in `skip` (portfolio files win)."""
    names = sorted({p for p, _ in store.manual_strategies()} - set(skip))
    reports = [build_manual_report(store, n, now, target_edge, alpha, power) for n in names]
    return [r for r in reports if r is not None]


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


MANUAL_VERDICT = (
    "not applicable: these lots exited before settlement (99c sells, stop-outs), so the"
    " sequential test's $1/$0 payout doesn't hold for the actual exits; use the per-lot edge"
    " above. The hold-to-settlement verdict, on checked results, is below"
)
HELD_VERDICT = "held to settlement (counterfactual, checked results):"
HELD_UNCHECKED = "held to settlement: run `nem settle verify` first"


BACKFILL_CAPPED = (
    "undecided: the sequential test says edge, but these trades rest on backfilled forecasts,"
    " which can reject an idea, not confirm it; confirm on forward-recorded data (nem run)"
)


def _strategy_verdict(report: "PortfolioReport", sr: StrategyReport) -> str:
    if report.manual is not None:
        return MANUAL_VERDICT
    if sr.verdict != sr.stats.sprt.decision:
        return label(BACKFILL_CAPPED, uses_backfill=True)
    return label(verdict(sr.stats, report.target_edge), uses_backfill=sr.backfilled)


_EXIT_LABELS: dict[ManualExit, str] = {
    "sold": "sold below 99c",
    "settled_won": "settled at 1.00",
    "settled_lost": "settled at 0.00",
}


def _manual_header(name: str, m: ManualBaseline) -> list[str]:
    at99, at99_pnl = m.exits.get("sold_at_99c", (0, 0.0))
    detail = ", ".join(
        f"{label} {m.exits[k][0]} {m.exits[k][1]:+,.2f}"
        for k, label in _EXIT_LABELS.items()
        if k in m.exits
    )
    return [
        f"Portfolio {name} (imported manual trading: real money, read-only, never traded)",
        f"  lots {m.lots}   entry notional ${m.notional:,.2f}   P&L after fees {m.pnl:+,.2f}"
        f"   fees {m.fees:,.2f}   closed {m.first_close:%Y-%m-%d} to {m.last_close:%Y-%m-%d}",
        f"  exits at 99c {at99} lots {at99_pnl:+,.2f}; other exits {m.lots - at99} lots"
        f" {m.pnl - at99_pnl:+,.2f}" + (f" ({detail})" if detail else ""),
        "  win = lot P&L after fees > 0. The strategy lines below are the engine's usual stats,"
        " which assume a hold-to-settlement payout ($1 or $0 vs entry + fees); early exits"
        " (99c sells, stop-outs) break that, so read the P&L-based edge here for money",
        "  per lot (each lot counts once; size changed over the period, so dollars mix sizing"
        " with edge):",
        f"    win rate    {_pct(m.wins / m.lots)} {_ci_pct(m.win_ci)}   ({m.wins}/{m.lots})"
        f"   mean entry {_pct(m.mean_entry)}",
        f"    edge        {_cents(m.edge)} per contract after fees {_ci_cents(m.edge_ci)}"
        f"   ({m.contracts:,.2f} contracts)",
        "  by month (close, US Eastern)   lots   contracts     notional        P&L   edge/contract",
        *(
            f"    {mo.month}                  {mo.lots:>5} {mo.contracts:>11,.2f}"
            f" {mo.notional:>12,.2f} {mo.pnl:>+10,.2f}   {_cents(mo.edge):>8}"
            for mo in m.months
        ),
    ]


_HELD_EXITS: dict[ManualExit, str] = {
    "sold_at_99c": "99c sells", "sold": "stops", "settled_won": "settled won",
    "settled_lost": "settled lost",
}  # fmt: skip


def _held_lines(h: HeldToSettlement | None, target_edge: float) -> list[str]:
    if h is None:
        return [f"    {HELD_UNCHECKED}"]
    st, mst = h.stats, h.market_stats
    lines = [
        "    held to settlement (counterfactual: each lot pays $1 if its side matches the"
        " checked result, else $0; actual open fee, no close fee at settlement)",
        f"      lots        {h.lots} used, {h.excluded} excluded ({h.unchecked} not checked,"
        f" {h.no_result} without a yes/no result)",
    ]
    if st.n:
        z = (
            f"z {st.z_vs_breakeven:+.2f} (p {st.p_value:.2f})"
            if st.z_vs_breakeven is not None and st.p_value is not None
            else ""
        )
        diff = h.held_pnl - h.actual_pnl
        parts = "; ".join(
            f"{label} {d.held - d.actual:+,.2f} over {d.lots} lots"
            + (f", {d.held_wins} would have won" if k == "sold" else "")
            for k, label in _HELD_EXITS.items()
            if (d := h.by_exit.get(k)) is not None
        )
        lines += [
            f"      win rate    {_pct(st.win_rate)} {_ci_pct(st.win_rate_ci)}   ({st.wins}/{st.n})"
            f"   break-even {_pct(st.breakeven)}   {z}",
            f"      edge        {_cents(st.edge)} per contract {_ci_cents(st.edge_ci)}",
            f"      P&L         held {h.held_pnl:+,.2f} vs actual exits {h.actual_pnl:+,.2f}"
            f" on these lots: {diff:+,.2f} ({_pct(diff / h.notional if h.notional else None)}"
            f" of ${h.notional:,.2f} notional)",
            f"      by exit     {parts}",
            f"      markets     {h.markets} distinct, {h.markets_with_loss} with a loss;"
            f" won {_pct(h.market_win_rate)} {_ci_pct(h.market_win_ci)}"
            f"   ({h.markets_won}/{h.markets} markets with no losing lot)"
            f"   break-even {_pct(h.market_breakeven)}   {_z(mst)}",
            "      clustering  lots on the same market share one outcome and are not"
            " independent: the per-lot z, Wilson interval and sequential test count each lot"
            " as independent and overstate the evidence; the per-market verdict (one bet per"
            " market at its lots' mean cost per contract) is the one to read",
        ]
    lines += [
        f"    verdict     {HELD_VERDICT}",
        f"      per market  {verdict(mst, target_edge).replace('trades', 'markets')}",
        f"      per lot     {verdict(st, target_edge).replace('trades', 'lots')}"
        + (" (overstates: lots cluster)" if st.n else ""),
    ]
    return lines


def _z(st: StrategyStats) -> str:
    if st.z_vs_breakeven is None or st.p_value is None:
        return ""
    return f"z {st.z_vs_breakeven:+.2f} (p {st.p_value:.2f})"


_EXIT_ORDER: tuple[ExitLabel, ...] = ("settlement", "take_profit", "stop")


def _exit_lines(exits: dict[ExitLabel, ExitBreakdown]) -> list[str]:
    if not exits:
        return []
    parts: list[str] = []
    for kind in _EXIT_ORDER:
        if (e := exits.get(kind)) is None:
            continue
        part = f"{kind} {e.n} {e.pnl:+,.2f} ({e.wins} with P&L > 0"
        if kind != "settlement":
            part += f"; side won at settlement {e.won_at_settlement}"
            part += f", {e.pending} pending" if e.pending else ""
        parts.append(part + ")")
    return [
        f"    by exit     {'; '.join(parts)}",
        "                (win rate above: P&L > 0; learned win counts use the settlement"
        " outcome of every trade, exits included, once its market settles)",
    ]


def _ci_pct(ci: Interval | None) -> str:
    return f"[{_pct(ci.low)}, {_pct(ci.high)}]" if ci else ""


def _ci_cents(ci: Interval | None) -> str:
    return f"[{_cents(ci.low)}, {_cents(ci.high)}]" if ci else ""


def render_text(report: PortfolioReport) -> str:
    p = report.portfolio
    if report.manual is not None:
        return _render_strategies(report, _manual_header(p.name, report.manual))
    lines = [
        f"Portfolio {p.name} ({p.mode})",
        f"  equity ${report.equity:,.2f}  ({_pct(report.total_return, 2)} on ${report.start:,.2f})"
        f"  realized {report.realized:+,.2f}  interest {report.interest:+,.2f}",
        f"  open positions {report.open_positions} (${report.open_cost:,.2f} at cost)",
        *_open_lines(report.open, report.open_positions, "  unrealized  "),
        *_excluded_lines(report.excluded, "  "),
    ]
    if report.cash_benchmark is not None:
        diff = report.realized + report.interest - report.cash_benchmark
        lines.append(
            f"  vs holding cash at {_pct(p.interest_apy, 2)} APY: {diff:+,.2f}"
            f" (cash alone would have earned {report.cash_benchmark:+,.2f})"
        )
    return _render_strategies(report, lines)


MARK_LABEL = "liquidation value at best bids, after exit fee"


def _open_lines(o: OpenMarks, n_open: int, prefix: str) -> list[str]:
    """Unrealized P&L of open positions (engine marks, nem.execution.mark)."""
    if n_open == 0:
        return []
    if o.value is None or o.unrealized_pnl is None:
        return [f"{prefix}- ({MARK_LABEL}: none of {n_open} open has a fresh book)"]
    marked = n_open - o.unmarked
    rest = f"; {o.unmarked} unmarked, left out" if o.unmarked else ""
    if o.no_bid:
        rest += f"; {o.no_bid} contracts with no bids valued at $0 (pending loss)"
    return [
        f"{prefix}{o.unrealized_pnl:+,.2f} on {marked} of {n_open} open"
        f" (${o.value:,.2f} {MARK_LABEL}{rest})"
    ]


EXCLUDED_NOTE = "left out of P&L, equity and risk limits too"


def _excluded_lines(trades: Sequence[Trade], prefix: str) -> list[str]:
    """Trades excluded from statistics (nem trade exclude), when there are any."""
    if not trades:
        return []
    settled = [t for t in trades if t.settled]
    pnl = sum(t.realized_pnl or 0.0 for t in settled)
    detail = f"{len(settled)} settled, P&L {pnl:+,.2f} not counted" if settled else "all open"
    return [
        f"{prefix}{len(trades)} excluded ({detail}; {EXCLUDED_NOTE}; nem trade list --excluded)"
    ]


def _render_strategies(report: PortfolioReport, lines: list[str]) -> str:
    for sr in report.strategies:
        s, st = sr.config, sr.stats
        lines.append("")
        head = f"{s.name}  {s.series}  {s.status}  {st.n} trades ({st.wins} won)"
        lines.append(f"  {label(head, uses_backfill=sr.backfilled)}, {sr.open_positions} open")
        lines += _open_lines(sr.open, sr.open_positions, "    unrealized  ")
        lines += _excluded_lines(sr.excluded, "    ")
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
        lines += _exit_lines(sr.exits)
        lines.append(f"    verdict     {_strategy_verdict(report, sr)}")
        if report.manual is not None:
            lines += _held_lines(sr.held, report.target_edge)
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
    # 1.1: open positions at liquidation value (OpenMarks)
    "open_value_usd", "unrealized_pnl_usd", "open_unmarked", "open_no_bid_contracts",
    "excluded_trades",  # 1.1: nem trade exclude
]  # fmt: skip
STRATEGY_COLUMNS = [
    "schema_version", "portfolio", "strategy", "series", "status", "trades", "wins", "open",
    "win_rate", "win_rate_lo", "win_rate_hi", "break_even_rate", "edge_cents", "edge_lo_cents",
    "edge_hi_cents", "z", "p", "pnl_usd", "pnl_per_trade_usd", "roi", "max_drawdown_usd",
    "trades_to_detect", "trades_to_detect_edge_cents", "verdict", "brier",
    "open_value_usd", "unrealized_pnl_usd", "open_unmarked", "open_no_bid_contracts",  # 1.1
    "excluded_trades",  # 1.1: trades left out of the statistics above (nem trade exclude)
]  # fmt: skip
TRADE_COLUMNS = [
    "schema_version", "portfolio", "strategy", "window_id", "ticker", "side", "contracts",
    "entry_price_cents", "fee_usd", "cost_per_contract_cents", "mode", "opened_at",
    "close_time", "settled_at", "result", "pnl_usd",
    # 1.1: mark-to-market of an open trade (nem.execution.mark); empty when settled or stale
    "mark_bid_cents", "mark_mid_cents", "mark_contracts", "mark_value_usd",
    "unrealized_pnl_usd", "marked_at",
    # 1.1: excluded from statistics (nem trade exclude); "true"/"false" in CSV
    "excluded", "exclude_reason",
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


def _open_fields(o: OpenMarks) -> dict[str, Cell]:
    return {
        "open_value_usd": _r(o.value, 2), "unrealized_pnl_usd": _r(o.unrealized_pnl, 2),
        "open_unmarked": o.unmarked, "open_no_bid_contracts": o.no_bid,
    }  # fmt: skip


def _trade_row(t: Trade, m: Mark | None = None) -> list[Cell]:
    result = "open" if t.won is None else "won" if t.won else "lost"
    return _row(TRADE_COLUMNS, {
        "mark_bid_cents": _c(m.bid) if m else None, "mark_mid_cents": _c(m.mid) if m else None,
        "mark_contracts": m.contracts if m else None,
        "mark_value_usd": _r(m.value, 2) if m else None,
        "unrealized_pnl_usd": _r(m.unrealized_pnl, 2) if m else None,
        "marked_at": iso(m.marked_at) if m else None,
        "schema_version": SCHEMA_VERSION, "portfolio": t.portfolio, "strategy": t.strategy,
        "window_id": t.window_id, "ticker": t.ticker, "side": t.side, "contracts": t.qty,
        "entry_price_cents": _c(t.avg_price), "fee_usd": _r(t.fee, 2),
        "cost_per_contract_cents": _c(cost_per_contract(t)), "mode": t.mode,
        "opened_at": iso(t.opened_at), "close_time": iso(t.close_time),
        "settled_at": iso(t.settled_at), "result": result, "pnl_usd": _r(t.realized_pnl, 2),
        "excluded": "true" if t.excluded else "false", "exclude_reason": t.exclude_reason,
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


_MANUAL_OVERRIDES: dict[str, Cell] = {
    "verdict": "undecided", "trades_to_detect": None,
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
        **_open_fields(report.open), "excluded_trades": len(report.excluded),
    })])  # fmt: skip
    strategy_rows: list[list[Cell]] = []
    trade_rows: list[list[Cell]] = []
    for sr in report.strategies:
        strategy_rows.append(_row(STRATEGY_COLUMNS, {
            "schema_version": SCHEMA_VERSION, "portfolio": p.name, "strategy": sr.config.name,
            "series": sr.config.series, "status": sr.config.status, "open": sr.open_positions,
            **stats_fields(sr.stats), "verdict": sr.verdict,  # capped on backfilled data
            # contract 1.0 has no "not applicable" verdict: imported manual lots stay undecided
            # (see MANUAL_VERDICT) with no trades-to-detect count
            **(_MANUAL_OVERRIDES if report.manual is not None else {}),
            **_open_fields(sr.open), "excluded_trades": len(sr.excluded),
        }))  # fmt: skip
        trade_rows += [_trade_row(t) for t in sr.trades]
        trade_rows += [
            _trade_row(t, sr.marks.get((t.strategy, t.window_id))) for t in sr.open_trades
        ]
    return [
        summary,
        Table("strategies", STRATEGY_COLUMNS, strategy_rows),
        Table("trades", TRADE_COLUMNS, trade_rows),
    ]


_HELD_FIELDS = (
    "win_rate", "win_rate_lo", "win_rate_hi", "break_even_rate", "edge_cents", "edge_lo_cents",
    "edge_hi_cents", "verdict",
)  # fmt: skip


# per-market statistics (one bet per market), as market_<field>; market_verdict is the
# headline when lots cluster on a market
_HELD_MARKET_FIELDS = ("break_even_rate", "edge_cents", "edge_lo_cents", "edge_hi_cents", "z",
                       "p", "verdict")  # fmt: skip


def _held_fields(h: HeldToSettlement) -> dict[str, Cell]:
    """Optional `held_to_settlement` object on an imported manual strategy (contract 1.1,
    additive: strategy rows allow additional properties). The row's own fields stay the
    actual exits'."""
    fields, market = stats_fields(h.stats), stats_fields(h.market_stats)
    ci = h.market_win_ci
    return {
        "lots": h.lots, "excluded": h.excluded, **{k: fields[k] for k in _HELD_FIELDS},
        "markets": h.markets, "markets_with_loss": h.markets_with_loss,
        "market_win_rate": _r(h.market_win_rate), "market_win_rate_lo": _r(ci.low if ci else None),
        "market_win_rate_hi": _r(ci.high if ci else None),
        **{f"market_{k}": market[k] for k in _HELD_MARKET_FIELDS},
        "markets_to_detect": market["trades_to_detect"],
    }  # fmt: skip


def _exit_fields(exits: dict[ExitLabel, ExitBreakdown]) -> dict[str, object]:
    """Optional `exits` object on an engine strategy row (contract 1.1, additive): settled
    trades per exit kind. `wins` is P&L > 0; `won_at_settlement` whether the side won when
    the market settled (`learning_pending` exits not yet known)."""
    return {
        kind: {
            "trades": e.n, "wins": e.wins, "contracts": e.contracts, "pnl_usd": _r(e.pnl, 2),
            "won_at_settlement": e.won_at_settlement, "learning_pending": e.pending,
        }
        for kind in _EXIT_ORDER
        if (e := exits.get(kind)) is not None
    }  # fmt: skip


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
        if sr.held is not None:
            row["held_to_settlement"] = _held_fields(sr.held)
        if sr.exits:
            row["exits"] = _exit_fields(sr.exits)
        if sr.backfilled:
            # contract 1.1, additive: a gate read backfilled forecasts, so `verdict` is
            # capped (never "edge" without forward-recorded confirmation)
            row["backfilled_forecasts"] = True
    return {
        "schema_version": SCHEMA_VERSION,
        "summary": dict(zip(summary.columns, summary.rows[0], strict=True)),
        "strategies": rows,
    }
