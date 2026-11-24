"""Paper exits: when a held position leaves before settlement, and at what price.

Pure functions of one recorded snapshot; the engine (`nem.engine.exits`) decides which
snapshots they see. Definitions, and what the recorded data can and cannot show, are in
docs/exits-and-fills.md.

What is recorded: per poll, the top of book and resting bids per side with sizes (`depth`).
There are no trade prints and no queue positions, and our own paper orders are never in
the recorded book. Everything below is inferred from resting bids at poll times only.

Simplifications, all stated in the docs:
- Exits are all-or-nothing: the whole position leaves in one fill or not at all this poll
  (the trades table has one quantity per position; partial exits are not modelled).
- Only recorded bids are liquidity, as for entries: a side recorded empty never fills, and
  a side not recorded at all (top of book only) fills only under the portfolio's opt-in
  `fills.missing_depth: quote`, where the top-of-book bid is assumed to cover the whole
  position (labelled results).
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from nem.core.config import ExitConfig, StopConfig, TakeProfitConfig
from nem.core.types import Level, MarketSnapshot, MissingDepth, Side, Trade
from nem.execution.fees import maker_fee, taker_fee

EPS = 1e-9

ExitKind = Literal["take_profit", "stop"]
# Status of the exit's row in `orders`; how reports tell an exit from a settlement.
EXIT_STATUS: dict[ExitKind, str] = {"take_profit": "exit_take_profit", "stop": "exit_stop"}

# Contracts of `side` traded at or above `price` in (after, until]: (ticker, side, price,
# after, until) -> contracts. Only sources with trade prints provide it (nem.history).
TradedVolume = Callable[[str, Side, float, datetime, datetime], float]

_EXIT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "nem/exit-order")


def exit_order_id(trade: Trade) -> str:
    """Deterministic id of a position's (single) exit order, distinct from entry ids."""
    key = f"{trade.portfolio}|{trade.strategy}|{trade.window_id}|{trade.side}|exit"
    return str(uuid.uuid5(_EXIT_NAMESPACE, key))


@dataclass(frozen=True, slots=True)
class ExitFill:
    kind: ExitKind
    ts: datetime
    price: float  # average per contract
    qty: int
    fee: float
    limit_price: float  # resting price (take-profit) or lowest bid walked (stop)


def _opposite(side: Side) -> Side:
    return "no" if side == "yes" else "yes"


def side_bids(
    snap: MarketSnapshot, side: Side, qty: int, missing_depth: MissingDepth = "none"
) -> list[Level]:
    """Resting bids for `side`, best (highest) first, as recorded (empty when recorded
    empty). Side not recorded: none, or under `quote` the top-of-book bid with enough size
    for `qty`."""
    if side in snap.depth:
        return sorted(snap.depth[side], key=lambda lv: -lv[0])
    bid = snap.yes_bid if side == "yes" else snap.no_bid
    return [] if bid is None or missing_depth != "quote" else [(bid, float(qty))]


def side_ask(snap: MarketSnapshot, side: Side, *, quote: bool = True) -> float | None:
    """Best ask for `side`: 1 - the best bid on the other side. Other side not recorded:
    the top-of-book ask when `quote`, else None. Recorded empty: None."""
    opposite = _opposite(side)
    if opposite in snap.depth:
        levels = snap.depth[opposite]
        return round(1 - max(p for p, _ in levels), 4) if levels else None
    if not quote:
        return None
    return snap.yes_ask if side == "yes" else snap.no_ask


@dataclass(frozen=True, slots=True)
class BidWalk:
    """A market sale walked down resting bids: what `contracts` would bring."""

    contracts: float  # sold (less than asked for when the bids run out)
    gross: float  # proceeds before fees, dollars
    fee: float  # taker fee, per level, total rounded to the cent
    lowest: float | None  # lowest bid touched; None when nothing sold


def walk_bids(bids: Sequence[Level], qty: float, taker_fee_rate: float) -> BidWalk:
    """Sell up to `qty` contracts into `bids` (best first) as a taker: each level's size at
    its price, taker fee per level. Shared by stops and mark-to-market (nem.execution.mark)."""
    remaining, gross, fee, lowest = float(qty), 0.0, 0.0, None
    for price, size in bids:
        if remaining <= EPS:
            break
        take = min(remaining, size)
        if take <= 0:
            continue
        gross += take * price
        fee += taker_fee(price, take, taker_fee_rate)
        remaining -= take
        lowest = price
    return BidWalk(qty - max(remaining, 0.0), gross, round(fee, 2), lowest)


def take_profit_fill(
    trade: Trade,
    tp: TakeProfitConfig,
    snap: MarketSnapshot,
    traded: TradedVolume | None = None,
    missing_depth: MissingDepth = "none",
) -> ExitFill | None:
    """Would our resting sell of `trade.side` at `tp.price` have filled by this snapshot?

    - optimistic: the market reached our price: best ask >= price (our order would be the
      best offer, at the front of the queue) or best bid >= price, and we assume a buyer
      took it before the next poll. An upper bound; it is not evidence of a trade.
    - realistic: resting bids at >= price cover the whole position. A buyer can only rest
      at or above our ask after every sell at or below it was taken; had our order been
      there, it would have been taken too, out of that leftover size.
    - pessimistic: resting bids strictly above price cover the whole position: the book
      traded through our level. At 99c this can never happen (Kalshi's top price), so a
      99c take-profit never fills and the position is held to settlement.
    - trade_through: trade prints of the held side at or above price, after the order was
      placed and up to this snapshot, add up to the whole position (`traded`). An upper
      bound: it assumes every such print would have hit our order first, ignoring the
      queue at our price and that a buyer at a higher price could have been someone
      lifting a better offer. Needs prints (historical archive only).

    Book sides not recorded count only under `missing_depth: quote` (see `side_bids`).
    Fills at exactly `tp.price` (a resting order gets no price improvement), maker fee.
    """
    if snap.ts <= trade.opened_at:
        return None  # the order rests from the entry; only later books can fill it
    price, qty = tp.price, trade.qty
    bids = side_bids(snap, trade.side, qty, missing_depth)
    match tp.fill_model:
        case "optimistic":
            ask = side_ask(snap, trade.side, quote=missing_depth == "quote")
            filled = (bool(bids) and bids[0][0] >= price - EPS) or (
                ask is not None and ask >= price - EPS
            )
        case "realistic":
            filled = sum(q for p, q in bids if p >= price - EPS) >= qty - EPS
        case "pessimistic":
            filled = sum(q for p, q in bids if p > price + EPS) >= qty - EPS
        case "trade_through":
            if traded is None:
                raise ValueError("trade_through needs trade prints; this source has none")
            volume = traded(trade.ticker, trade.side, price, trade.opened_at, snap.ts)
            filled = volume >= qty - EPS
    if not filled:
        return None
    return ExitFill("take_profit", snap.ts, price, qty, maker_fee(price, qty, tp.maker_rate), price)


def stop_fill(
    trade: Trade,
    stop: StopConfig,
    snap: MarketSnapshot,
    taker_fee_rate: float,
    missing_depth: MissingDepth = "none",
) -> ExitFill | None:
    """Sell at market once the best bid for the held side is at or below the threshold:
    walk the recorded bids best first, taker fee per level. If the bids can't absorb the
    whole position, nothing fills this poll and the stop tries again on the next one.
    No bids at all also means no fill (there is nothing to sell into), and so does a bid
    side that wasn't recorded, unless `missing_depth` is `quote`."""
    if snap.ts <= trade.opened_at:
        return None
    qty = trade.qty
    bids = side_bids(snap, trade.side, qty, missing_depth)
    if not bids or bids[0][0] > stop.bid_at_or_below + EPS:
        return None
    if sum(q for _, q in bids) < qty - EPS:
        return None
    sale = walk_bids(bids, qty, taker_fee_rate)
    lowest = bids[0][0] if sale.lowest is None else sale.lowest
    return ExitFill("stop", snap.ts, round(sale.gross / qty, 6), qty, sale.fee, lowest)


def decide_exit(
    trade: Trade,
    cfg: ExitConfig,
    snap: MarketSnapshot,
    taker_fee_rate: float,
    traded: TradedVolume | None = None,
    missing_depth: MissingDepth = "none",
) -> ExitFill | None:
    """Take-profit is checked first: a resting order fills before a stop could react. With
    validated configs both can trigger on one snapshot only under `optimistic`."""
    if cfg.take_profit is not None:
        fill = take_profit_fill(trade, cfg.take_profit, snap, traded, missing_depth)
        if fill is not None:
            return fill
    if cfg.stop is not None:
        return stop_fill(trade, cfg.stop, snap, taker_fee_rate, missing_depth)
    return None
