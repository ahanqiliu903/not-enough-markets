"""Risk limits, checked for the strategy and then its portfolio before every order.

These fail closed: if a check can't be evaluated, the trade is blocked.
"""

from collections.abc import Sequence
from datetime import datetime

from nem.core.clock import EXCHANGE_TZ
from nem.core.config import RiskConfig
from nem.core.context import Context
from nem.core.types import Decision, Trade


def max_drawdown(trades: Sequence[Trade]) -> float:
    """Largest peak-to-trough drop of cumulative realized P&L (positive dollars)."""
    peak = equity = worst = 0.0
    for t in trades:
        equity += t.realized_pnl or 0.0
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _same_exchange_day(a: datetime, b: datetime) -> bool:
    return a.astimezone(EXCHANGE_TZ).date() == b.astimezone(EXCHANGE_TZ).date()


def check_limits(
    risk: RiskConfig,
    scope: str,
    open_trades: Sequence[Trade],
    settled: Sequence[Trade],
    new_cost: float,
    now: datetime,
) -> Decision:
    def block(limit: str) -> Decision:
        return Decision.skip(f"risk:{scope}:{limit}")

    if risk.max_open_positions is not None and len(open_trades) >= risk.max_open_positions:
        return block("max_open_positions")
    allocated = sum(t.cost for t in open_trades)
    if risk.max_allocation is not None and allocated + new_cost > risk.max_allocation:
        return block("max_allocation")
    if risk.daily_loss_cap is not None:
        today = sum(
            t.realized_pnl or 0.0
            for t in settled
            if t.settled_at and _same_exchange_day(t.settled_at, now)
        )
        if -today >= risk.daily_loss_cap:
            return block("daily_loss_cap")
    if risk.max_drawdown is not None and max_drawdown(settled) >= risk.max_drawdown:
        return block("max_drawdown")
    losing = sum(t.realized_pnl or 0.0 for t in settled) < 0
    n = risk.stop_if_losing_after
    if n is not None and len(settled) >= n and losing:
        return block("stop_if_losing_after")
    return Decision.ok()


def check_risk(ctx: Context, new_cost: float) -> Decision:
    try:
        now = ctx.now()
        if new_cost > ctx.cash():
            return Decision.skip("risk:portfolio:insufficient_cash")
        d = check_limits(
            ctx.strategy.risk,
            "strategy",
            ctx.open_trades(),
            ctx.settled_trades(),
            new_cost,
            now,
        )
        if not d.take:
            return d
        return check_limits(
            ctx.portfolio.risk,
            "portfolio",
            ctx.open_trades(portfolio_wide=True),
            ctx.portfolio_settled_trades(),
            new_cost,
            now,
        )
    except Exception as e:  # fail closed
        return Decision.skip(f"risk:error:{type(e).__name__}")
