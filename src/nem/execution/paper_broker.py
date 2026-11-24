"""Paper broker: fills against the recorded orderbook, as an immediate-or-cancel order would.

Buying YES at limit L lifts resting NO bids priced at 1 - ask, best first, until the order
is filled or the next level costs more than L. Whatever can't fill is cancelled. The fill
price can therefore never exceed the limit (a live bot once filled at 0.928 against a 0.91
cap; here it's structurally impossible and asserted).

Only recorded depth is liquidity. A side recorded empty never fills. A side not recorded at
all (top of book only) doesn't fill either, unless the portfolio opts in to
`fills.missing_depth: quote`, which assumes the quoted ask covers the whole order; results
under that assumption are labelled (docs/exits-and-fills.md).

`quote` returns the fill `submit` would make, without placing anything. A paper fill is a
pure function of the order and the snapshot, so the quote is exact (same levels, same
per-level fee rounding); the engine checks risk against the quote's cost, fees included.
"""

import math
from datetime import datetime
from typing import Literal

from nem.core.types import Fill, Level, MarketSnapshot, MissingDepth, Order, Side
from nem.execution.fees import taker_fee

EPS = 1e-9

# Why an order didn't fill (logged as the no_fill reason).
NO_DEPTH_RECORDED = "no_depth_recorded"  # top of book only: size unknown
BOOK_EMPTY = "book_empty"  # recorded empty, or taken by earlier fills this tick
NO_LIQUIDITY_AT_LIMIT = "no_liquidity_at_limit"
NO_QUOTE = "no_quote"  # quote mode, and no ask quoted either


def _opposite(side: Side) -> Side:
    return "no" if side == "yes" else "yes"


def _asks(order: Order, snap: MarketSnapshot, missing_depth: MissingDepth) -> list[Level]:
    """Asks for the order's side, best (cheapest) first."""
    opposite = _opposite(order.side)
    if opposite in snap.depth:
        levels = sorted(snap.depth[opposite], key=lambda lv: -lv[0])
        return [(round(1 - p, 4), q) for p, q in levels]
    if missing_depth != "quote":
        return []
    ask = snap.yes_ask if order.side == "yes" else snap.no_ask
    return [] if ask is None else [(ask, float(order.qty))]


class PaperBroker:
    mode: Literal["paper"] = "paper"

    def __init__(self, taker_fee_rate: float, missing_depth: MissingDepth = "none") -> None:
        self.taker_fee_rate = taker_fee_rate
        self.missing_depth: MissingDepth = missing_depth

    def quote(self, order: Order, snap: MarketSnapshot, ts: datetime) -> Fill | None:
        """The fill `submit` would make now. Side-effect free."""
        return self.submit(order, snap, ts)

    def submit(self, order: Order, snap: MarketSnapshot, ts: datetime) -> Fill | None:
        takeable = [
            (p, q)
            for p, q in _asks(order, snap, self.missing_depth)
            if p <= order.limit_price + EPS
        ]
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

    def no_fill_reason(self, order: Order, snap: MarketSnapshot) -> str:
        """Why `submit` returned None for this order and book."""
        opposite = _opposite(order.side)
        if opposite in snap.depth:
            return BOOK_EMPTY if not snap.depth[opposite] else NO_LIQUIDITY_AT_LIMIT
        if self.missing_depth != "quote":
            return NO_DEPTH_RECORDED
        ask = snap.yes_ask if order.side == "yes" else snap.no_ask
        return NO_QUOTE if ask is None else NO_LIQUIDITY_AT_LIMIT
