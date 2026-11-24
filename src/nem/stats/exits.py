"""Split settled trades by how they ended: settlement, take-profit or stop.

An exit leaves an order row whose status names its kind (see `nem.execution.exits`);
a trade without one was held to settlement. An exited trade's `won` is its P&L > 0; whether
its side won at settlement (what learning uses) is in `exit_learning`, once known.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from nem.core.types import StrategyKey, Trade
from nem.execution.exits import EXIT_STATUS, exit_order_id
from nem.store import Store

ExitLabel = Literal["settlement", "take_profit", "stop"]
_BY_STATUS: dict[str, ExitLabel] = {status: kind for kind, status in EXIT_STATUS.items()}


def exit_kind(store: Store, trade: Trade) -> ExitLabel | None:
    """How a settled trade ended; None while it's still open."""
    if not trade.settled:
        return None
    status = store.order_status(exit_order_id(trade))
    return _BY_STATUS.get(status or "", "settlement")


@dataclass(frozen=True, slots=True)
class ExitBreakdown:
    kind: ExitLabel
    n: int
    wins: int  # realized_pnl > 0
    contracts: int
    pnl: float
    # side won at settlement (for `settlement`, the same as `wins`); exits whose market
    # hasn't settled yet are `pending` and counted in neither
    won_at_settlement: int
    pending: int


def by_exit_kind(store: Store, trades: Iterable[Trade]) -> dict[ExitLabel, ExitBreakdown]:
    """Count and realized P&L (after all fees) per exit kind, over settled trades only."""
    groups: dict[ExitLabel, list[Trade]] = {}
    for t in trades:
        kind = exit_kind(store, t)
        if kind is not None:
            groups.setdefault(kind, []).append(t)
    outcomes: dict[StrategyKey, dict[str, bool | None]] = {}

    def at_settlement(t: Trade, kind: ExitLabel) -> bool | None:
        if kind == "settlement":
            return t.won
        key = (t.portfolio, t.strategy)
        if key not in outcomes:
            outcomes[key] = store.exit_outcomes(key)
        return outcomes[key].get(t.window_id)

    out: dict[ExitLabel, ExitBreakdown] = {}
    for kind, ts in groups.items():
        settled = [at_settlement(t, kind) for t in ts]
        out[kind] = ExitBreakdown(
            kind=kind,
            n=len(ts),
            wins=sum(1 for t in ts if (t.realized_pnl or 0.0) > 0),
            contracts=sum(t.qty for t in ts),
            pnl=round(sum(t.realized_pnl or 0.0 for t in ts), 6),
            won_at_settlement=sum(1 for w in settled if w),
            pending=sum(1 for w in settled if w is None),
        )
    return out
