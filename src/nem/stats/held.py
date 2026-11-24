"""Hold-to-settlement counterfactual for imported manual lots: what each lot would have
scored had it been held to settlement instead of sold at 99c or stopped out.

Every lot becomes a $1/$0 bet on its market's checked result (`nem settle verify`):
- result: `derived_result` whenever the check has one (`agree`, `disagree`, or
  `kalshi_missing`, e.g. Kalshi settled the market `scalar`, at a price), as `nem settle
  verify` replays it; else Kalshi's result if it is yes or no; anything else (no check,
  no derived value and no yes/no from Kalshi) excludes the lot, counted;
- cost: entry price plus the lot's actual open fee; no close fee, since Kalshi charges
  none at settlement.

Each lot is one trade whatever its size, as for engine trades, so a fractional quantity
only changes the per-contract cost and the dollar P&L, never a lot's weight. Lots on the
same market share its one outcome, so they are not independent: the per-lot statistics
overstate the evidence. So each market is also scored once (`HeldMarket`): won when none
of its lots lost, at the mean over its lots of their cost per contract (entry + open fee),
in market-date order. Those per-market statistics are the same engine statistics on one
bet per market, and they are the headline when lots cluster.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from nem.core.types import ManualExit, ManualLot, Side
from nem.settle.model import SettlementCheck
from nem.stats.metrics import Interval, StrategyStats, cost_per_contract, strategy_stats


def held_result(check: SettlementCheck) -> Side | None:
    """The result a held lot settles on, None if the check has no usable yes/no answer."""
    if check.status in ("agree", "disagree", "kalshi_missing") and check.derived_result:
        return check.derived_result
    if check.kalshi_result == "yes":
        return "yes"
    if check.kalshi_result == "no":
        return "no"
    return None


@dataclass(frozen=True, slots=True)
class HeldLot:
    """A manual lot re-scored as held to settlement (a `Bet` for the statistics)."""

    lot: ManualLot
    result: Side
    settles_on: date  # the market's day, for settlement order

    @property
    def qty(self) -> float:
        return self.lot.qty

    @property
    def cost(self) -> float:
        return self.lot.notional + self.lot.open_fees

    @property
    def won(self) -> bool:
        return self.lot.side == self.result

    @property
    def realized_pnl(self) -> float:
        return (self.qty if self.won else 0.0) - self.cost


@dataclass(frozen=True, slots=True)
class HeldMarket:
    """One market's held lots as a single one-contract bet (a `Bet` for the statistics):
    won when none of its lots lost, costing the mean of its lots' cost per contract."""

    ticker: str
    settles_on: date
    lots: int
    cost: float  # = cost per contract, since qty is 1
    won: bool

    @property
    def qty(self) -> float:
        return 1.0

    @property
    def realized_pnl(self) -> float:
        return (1.0 if self.won else 0.0) - self.cost


def held_markets(held: Sequence[HeldLot]) -> list[HeldMarket]:
    """`held` grouped by market, in (market date, first lot opened) order."""
    groups: dict[str, list[HeldLot]] = {}
    for h in held:
        groups.setdefault(h.lot.ticker, []).append(h)
    markets = [
        HeldMarket(
            ticker=ticker,
            settles_on=min(h.settles_on for h in group),
            lots=len(group),
            cost=sum(cost_per_contract(h) for h in group) / len(group),
            won=all(h.won for h in group),
        )
        for ticker, group in groups.items()
    ]
    first = {t: min(h.lot.opened_at for h in g) for t, g in groups.items()}
    return sorted(markets, key=lambda m: (m.settles_on, first[m.ticker]))


@dataclass(frozen=True, slots=True)
class ExitDelta:
    """Lots of one actual exit kind: their actual P&L vs held to settlement."""

    lots: int
    actual: float  # Kalshi's realized P&L after fees
    held: float
    held_wins: int  # lots that would have won held to settlement


@dataclass(frozen=True, slots=True)
class HeldToSettlement:
    lots: int  # used
    unchecked: int  # excluded: the market has no settlement check
    no_result: int  # excluded: checked, but no usable yes/no result
    notional: float  # entry notional of the used lots
    stats: StrategyStats
    by_exit: dict[ManualExit, ExitDelta]
    # one bet per market (HeldMarket), market-date order: n = distinct markets, wins =
    # markets with no losing lot, breakeven = mean over markets of their cost per contract
    market_stats: StrategyStats

    @property
    def markets(self) -> int:
        return self.market_stats.n

    @property
    def markets_won(self) -> int:
        return self.market_stats.wins

    @property
    def markets_with_loss(self) -> int:
        return self.markets - self.markets_won

    @property
    def market_win_rate(self) -> float | None:
        return self.market_stats.win_rate

    @property
    def market_win_ci(self) -> Interval | None:  # Wilson 95% on markets won / markets
        return self.market_stats.win_rate_ci

    @property
    def market_breakeven(self) -> float | None:
        return self.market_stats.breakeven

    @property
    def excluded(self) -> int:
        return self.unchecked + self.no_result

    @property
    def actual_pnl(self) -> float:
        return sum(d.actual for d in self.by_exit.values())

    @property
    def held_pnl(self) -> float:
        return sum(d.held for d in self.by_exit.values())


def held_to_settlement(
    lots: Sequence[ManualLot],
    checks: Mapping[str, SettlementCheck],
    target_edge: float = 0.02,
    alpha: float = 0.05,
    power: float = 0.8,
) -> HeldToSettlement | None:
    """None when none of the lots' markets has been checked (run `nem settle verify`)."""
    if not any(lot.ticker in checks for lot in lots):
        return None
    held: list[HeldLot] = []
    unchecked = no_result = 0
    for lot in lots:
        check = checks.get(lot.ticker)
        if check is None:
            unchecked += 1
            continue
        result = held_result(check)
        if result is None:
            no_result += 1
            continue
        day = check.market_date or lot.closed_at.date()
        held.append(HeldLot(lot, result, day))
    # settlement order, as the engine feeds settled trades to the sequential test
    held.sort(key=lambda h: (h.settles_on, h.lot.opened_at))
    by_exit: dict[ManualExit, ExitDelta] = {}
    for h in held:
        d = by_exit.get(h.lot.exit, ExitDelta(0, 0.0, 0.0, 0))
        by_exit[h.lot.exit] = ExitDelta(
            d.lots + 1, d.actual + h.lot.realized_pnl, d.held + h.realized_pnl,
            d.held_wins + h.won,
        )  # fmt: skip
    return HeldToSettlement(
        lots=len(held),
        unchecked=unchecked,
        no_result=no_result,
        notional=sum(h.lot.notional for h in held),
        stats=strategy_stats(held, (), target_edge, alpha, power),
        by_exit=by_exit,
        market_stats=strategy_stats(held_markets(held), (), target_edge, alpha, power),
    )
