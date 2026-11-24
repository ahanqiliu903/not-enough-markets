"""Mark-to-market: what an open position would bring if sold now, as a taker.

Liquidation value = walk the recorded resting bids of the position's own side (best first)
for its contract count, minus the Kalshi taker fee per level, in the latest recorded
snapshot of its market (the same walk as a stop, nem.execution.exits.walk_bids). Only
recorded depth counts. Contracts the recorded bids don't cover (an empty bid side covers
none) are valued at $0: nobody would buy them now, so the position is worth no more than
what the bids take (e.g. a market all but resolved against us). The value is therefore a
floor that is never above what a sale would bring. The mid ((best bid + best ask) / 2 on
the position's side) is a reference only, never used for P&L.

No mark at all (None) when the trade has settled or exited, there is no snapshot, the
snapshot is older than `MARK_MAX_AGE` (a stale book is not a price), the market has closed
(it trades no more; the result is pending), or the snapshot recorded no depth for the
position's side (top of book only: unknown, not empty). Definitions:
docs/exits-and-fills.md.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from nem.core.types import MarketSnapshot, Trade
from nem.execution.exits import EPS, side_ask, walk_bids

# A fixed bound rather than a multiple of the poll interval: reports run as their own job
# (`nem report`), at any time, and 15 minutes is both a few polls of a daily market and the
# length of a 15-minute crypto window. Older than this, the mark is left empty.
MARK_MAX_AGE = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class Mark:
    marked_at: datetime  # time of the snapshot priced
    contracts: int  # covered by the recorded bids (<= the position's)
    no_bid: int  # contracts the bids don't cover, valued at $0
    bid: float | None  # average sale price per covered contract; None when none covered
    mid: float | None  # reference only: (best bid + best ask) / 2 on the held side
    value: float  # proceeds of the covered contracts after the taker fee; $0 for the rest
    unrealized_pnl: float  # value - entry cost incl. entry fee


def mark_position(
    trade: Trade,
    snap: MarketSnapshot | None,
    taker_fee_rate: float,
    now: datetime,
    max_age: timedelta = MARK_MAX_AGE,
) -> Mark | None:
    if trade.settled or snap is None or snap.ticker != trade.ticker:
        return None
    if snap.ts > now or now - snap.ts > max_age or now >= snap.close_time:
        return None
    if trade.side not in snap.depth:
        return None
    bids = sorted(snap.depth[trade.side], key=lambda lv: -lv[0])
    available = sum(q for _, q in bids)
    covered = min(trade.qty, math.floor(available + EPS))
    best_bid = bids[0][0] if bids else snap.yes_bid if trade.side == "yes" else snap.no_bid
    ask = side_ask(snap, trade.side)
    mid = None if best_bid is None or ask is None else round((best_bid + ask) / 2, 6)
    no_bid = trade.qty - covered
    if covered == 0:
        return Mark(snap.ts, 0, no_bid, None, mid, 0.0, round(-trade.cost, 6))
    sale = walk_bids(bids, covered, taker_fee_rate)
    value = round(sale.gross - sale.fee, 6)
    avg = round(sale.gross / covered, 6)
    return Mark(snap.ts, covered, no_bid, avg, mid, value, round(value - trade.cost, 6))
