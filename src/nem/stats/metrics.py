"""Pure statistics over settled trades. No I/O.

Every trade is a binary bet held to settlement: pay c per contract (price + fees), get $1
if it wins. So the break-even win rate of a trade is exactly c, and the natural question
is whether the strategy wins more often than its prices imply.

Comparisons are per trade (each trade counts once, whatever its size), so win rate and
break-even use the same weighting. Dollar P&L is reported separately.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist, fmean

from nem.core.types import Trade

Z = NormalDist()


@dataclass(frozen=True, slots=True)
class Interval:
    low: float
    high: float


def cost_per_contract(t: Trade) -> float:
    """Price plus fees per contract: the trade's break-even win probability."""
    return t.cost / t.qty


def wilson(wins: int, n: int, confidence: float = 0.95) -> Interval | None:
    """Wilson score interval for a binomial proportion. Behaves well near 0% and 100%,
    where most of these strategies live (unlike the textbook p +/- z*se)."""
    if n == 0:
        return None
    z = Z.inv_cdf(0.5 + confidence / 2)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return Interval(max(0.0, centre - half), min(1.0, centre + half))


def trades_needed(
    breakeven: float, edge: float, alpha: float = 0.05, power: float = 0.8
) -> int | None:
    """Trades needed for a one-sided test at `alpha` to detect a true win rate of
    `breakeven + edge` with probability `power`. None if the edge is impossible."""
    p1 = breakeven + edge
    if edge <= 0 or not 0 < breakeven < 1 or p1 >= 1:
        return None
    za, zb = Z.inv_cdf(1 - alpha), Z.inv_cdf(power)
    n = (za * math.sqrt(breakeven * (1 - breakeven)) + zb * math.sqrt(p1 * (1 - p1))) / edge
    return math.ceil(n * n)


@dataclass(frozen=True, slots=True)
class Sprt:
    """Wald's sequential probability ratio test, checked after every trade.

    H0: each trade wins exactly as often as its price implies (no edge).
    H1: each trade wins `edge` more often than its price implies.
    Stop as soon as the evidence crosses a boundary; until then, keep collecting.
    """

    llr: float  # log-likelihood ratio, H1 vs H0
    upper: float  # cross it: evidence of an edge
    lower: float  # cross it: evidence of no edge
    decision: str  # "edge" | "no_edge" | "undecided"


def sprt(
    trades: Sequence[Trade], edge: float = 0.02, alpha: float = 0.05, power: float = 0.8
) -> Sprt:
    beta = 1 - power
    upper, lower = math.log((1 - beta) / alpha), math.log(beta / (1 - alpha))
    llr, decision = 0.0, "undecided"
    for t in trades:
        p0 = min(max(cost_per_contract(t), 1e-6), 1 - 1e-6)
        p1 = min(p0 + edge, 1 - 1e-6)
        llr += math.log(p1 / p0) if t.won else math.log((1 - p1) / (1 - p0))
        if llr >= upper:
            decision = "edge"
            break
        if llr <= lower:
            decision = "no_edge"
            break
    return Sprt(llr, upper, lower, decision)


@dataclass(frozen=True, slots=True)
class CalibrationBucket:
    low: float
    high: float
    n: int
    predicted: float  # mean p_model
    actual: float  # observed win rate


def calibration(
    pairs: Sequence[tuple[float, bool]], width: float = 0.02
) -> list[CalibrationBucket]:
    """Group (p_model, won) by predicted probability: does "93%" actually win 93%?"""
    buckets: dict[int, list[tuple[float, bool]]] = {}
    for p, won in pairs:
        buckets.setdefault(min(int(p / width), int(1 / width) - 1), []).append((p, won))
    return [
        CalibrationBucket(
            low=round(k * width, 4),
            high=round((k + 1) * width, 4),
            n=len(items),
            predicted=fmean(p for p, _ in items),
            actual=sum(w for _, w in items) / len(items),
        )
        for k, items in sorted(buckets.items())
    ]


def brier(pairs: Sequence[tuple[float, bool]]) -> float | None:
    """Mean squared error of p_model vs outcome (lower is better; 0 is perfect)."""
    return fmean((p - w) ** 2 for p, w in pairs) if pairs else None


def max_drawdown(pnls: Sequence[float]) -> float:
    """Largest peak-to-trough drop of cumulative P&L (positive dollars)."""
    peak = equity = worst = 0.0
    for x in pnls:
        equity += x
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


@dataclass(frozen=True, slots=True)
class StrategyStats:
    n: int
    wins: int
    win_rate: float | None
    win_rate_ci: Interval | None
    breakeven: float | None  # mean cost per contract
    edge: float | None  # mean (won - cost) per contract, per trade
    edge_ci: Interval | None
    z_vs_breakeven: float | None  # >1.64 ~ significant at 5% one-sided
    p_value: float | None
    pnl: float
    pnl_per_trade: float | None
    roi: float | None  # P&L / capital spent
    max_drawdown: float
    trades_needed: int | None  # to detect `trades_needed_for` at alpha/power
    trades_needed_for: float  # the observed edge if positive and plausible, else the target
    sprt: Sprt
    brier: float | None
    calibration: list[CalibrationBucket]


def strategy_stats(
    trades: Sequence[Trade],
    p_models: Sequence[float | None] = (),
    target_edge: float = 0.02,
    alpha: float = 0.05,
    power: float = 0.8,
) -> StrategyStats:
    """`trades` must be settled, oldest first. `p_models` aligns with `trades`."""
    n = len(trades)
    wins = sum(1 for t in trades if t.won)
    costs = [cost_per_contract(t) for t in trades]
    returns = [(1.0 if t.won else 0.0) - c for t, c in zip(trades, costs, strict=True)]
    pnls = [t.realized_pnl or 0.0 for t in trades]
    spent = sum(t.cost for t in trades)

    breakeven = fmean(costs) if n else None
    edge = fmean(returns) if n else None
    win_ci = wilson(wins, n)
    # Edge interval from the win-rate interval. A normal interval on the returns would
    # collapse to almost nothing after a few straight wins, which is exactly when it
    # matters most not to be overconfident.
    edge_ci = (
        Interval(win_ci.low - breakeven, win_ci.high - breakeven)
        if win_ci is not None and breakeven is not None
        else None
    )

    # Wins vs expected wins under "no edge" (each trade wins with probability = its cost)
    var = sum(c * (1 - c) for c in costs)
    z = (wins - sum(costs)) / math.sqrt(var) if var > 0 else None
    p_value = 1 - Z.cdf(z) if z is not None else None

    # How many trades to detect the observed edge (if positive and plausible), else the
    # target edge, at the requested alpha and power
    needed_for, needed = target_edge, None
    if breakeven is not None:
        observed = (wins / n) - breakeven
        if observed > 0 and breakeven + observed < 0.999:
            needed_for = observed
        needed = trades_needed(breakeven, needed_for, alpha, power)

    pairs = [(p, bool(t.won)) for t, p in zip(trades, p_models, strict=False) if p is not None]
    return StrategyStats(
        n=n,
        wins=wins,
        win_rate=wins / n if n else None,
        win_rate_ci=win_ci,
        breakeven=breakeven,
        edge=edge,
        edge_ci=edge_ci,
        z_vs_breakeven=z,
        p_value=p_value,
        pnl=sum(pnls),
        pnl_per_trade=fmean(pnls) if n else None,
        roi=sum(pnls) / spent if spent else None,
        max_drawdown=max_drawdown(pnls),
        trades_needed=needed,
        trades_needed_for=needed_for,
        sprt=sprt(trades, target_edge, alpha, power),
        brier=brier(pairs),
        calibration=calibration(pairs),
    )
