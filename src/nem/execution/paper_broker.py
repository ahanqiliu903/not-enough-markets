"""Paper broker: fills against the recorded orderbook, as an immediate-or-cancel order would.

Buying YES at limit L lifts resting NO bids priced at 1 - ask, best first, until the order
is filled or the next level costs more than L. Whatever can't fill is cancelled. The fill
price can therefore never exceed the limit (a live bot once filled at 0.928 against a 0.91
cap; here it's structurally impossible and asserted).

If a snapshot has no depth, the order fills in full at the top-of-book ask. That ignores
liquidity, so record with depth for realistic results.
"""

import math
from datetime import datetime
from typing import Literal

from nem.core.types import Fill, Level, MarketSnapshot, Order
from nem.execution.fees import taker_fee

EPS = 1e-9


def _asks(order: Order, snap: MarketSnapshot) -> list[Level]:
    """Asks for the order's side, best (cheapest) first."""
    opposite = "no" if order.side == "yes" else "yes"
    levels = snap.depth.get(opposite)
    if levels:
        return [(round(1 - p, 4), q) for p, q in levels]
    ask = snap.yes_ask if order.side == "yes" else snap.no_ask
    return [] if ask is None else [(ask, float(order.qty))]


class PaperBroker:
    mode: Literal["paper"] = "paper"

    def __init__(self, taker_fee_rate: float) -> None:
        self.taker_fee_rate = taker_fee_rate

    def submit(self, order: Order, snap: MarketSnapshot, ts: datetime) -> Fill | None:
        takeable = [(p, q) for p, q in _asks(order, snap) if p <= order.limit_price + EPS]
        available = sum(q for _, q in takeable)
        qty = min(order.qty, math.floor(available + EPS))
        if qty <= 0:
            return None
        remaining, cost, fee = float(qty), 0.0, 0.0
        for price, size in takeable:
            take = min(remaining, size)
            if take <= 0:
                break
            cost += take * price
            fee += taker_fee(price, take, self.taker_fee_rate)
            remaining -= take
        avg = round(cost / qty, 6)
        assert avg <= order.limit_price + EPS, f"fill {avg} above limit {order.limit_price}"
        return Fill(order.client_order_id, ts, avg, qty, round(fee, 2))
