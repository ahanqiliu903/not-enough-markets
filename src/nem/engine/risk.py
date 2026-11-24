"""Risk limits, checked for the strategy and then its portfolio before every order.

These fail closed: if a check can't be evaluated, the trade is blocked.

`new_cost` is the proposed order's full cost: contracts at their fill prices plus the
entry fee (the engine passes the broker's quote). Money limits are inclusive: a cost that
lands exactly on a limit passes; anything beyond it, after float noise, is blocked.
"""

from collections.abc import Sequence
from datetime import datetime

from nem.core.clock import EXCHANGE_TZ
from nem.core.config import RiskConfig
from nem.core.context import Context
from nem.core.types import Decision, Trade
from nem.settle.stations import lookup
from nem.stats.metrics import max_drawdown as _max_drawdown

EPS = 1e-9  # float noise, in dollars; far below a cent


def max_drawdown(trades: Sequence[Trade]) -> float:
    """Largest peak-to-trough drop of cumulative realized P&L (positive dollars)."""
    return _max_drawdown([t.realized_pnl or 0.0 for t in trades])


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
    if risk.max_allocation is not None and allocated + new_cost > risk.max_allocation + EPS:
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
    counted = [t for t in settled if not t.excluded]  # callers pass none; be safe
    losing = sum(t.realized_pnl or 0.0 for t in counted) < 0
    n = risk.stop_if_losing_after
    if n is not None and len(counted) >= n and losing:
        return block("stop_if_losing_after")
    return Decision.ok()


def _city(ticker: str) -> str | None:
    found = lookup(ticker)
    return found.station.icao if found is not None else None


def check_city_limit(
    risk: RiskConfig,
    scope: str,
    open_trades: Sequence[Trade],
    ticker: str | None,
    new_cost: float,
) -> Decision:
    """Open cost per station (city) plus this order must stay within `max_city_allocation`.
    Fails closed when the new market's city is unknown: an unmapped series can't be capped."""
    cap = risk.max_city_allocation
    if cap is None:
        return Decision.ok()
    city = _city(ticker) if ticker is not None else None
    if city is None:
        return Decision.skip(f"risk:{scope}:max_city_allocation:unknown_city")
    held = sum(t.cost for t in open_trades if _city(t.ticker) == city)
    if held + new_cost > cap + EPS:
        return Decision.skip(f"risk:{scope}:max_city_allocation:{city}")
    return Decision.ok()


def check_risk(ctx: Context, new_cost: float, ticker: str | None = None) -> Decision:
    try:
        now = ctx.now()
        if new_cost > ctx.cash() + EPS:
            return Decision.skip("risk:portfolio:insufficient_cash")
        mine = ctx.open_trades()
        settled = ctx.settled_positions()  # excluded trades are left out
        d = check_limits(ctx.strategy.risk, "strategy", mine, settled, new_cost, now)
        if d.take:
            d = check_city_limit(ctx.strategy.risk, "strategy", mine, ticker, new_cost)
        if not d.take:
            return d
        everyone = ctx.open_trades(portfolio_wide=True)
        d = check_limits(
            ctx.portfolio.risk,
            "portfolio",
            everyone,
            ctx.settled_positions(portfolio_wide=True),
            new_cost,
            now,
        )
        if not d.take:
            return d
        return check_city_limit(ctx.portfolio.risk, "portfolio", everyone, ticker, new_cost)
    except Exception as e:  # fail closed
        return Decision.skip(f"risk:error:{type(e).__name__}")
