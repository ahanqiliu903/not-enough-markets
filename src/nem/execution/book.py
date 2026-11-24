"""Depth taken by paper fills within one execution scope and one tick.

A recorded book is shown to every strategy of a portfolio in the same tick. Without
bookkeeping, two strategies wanting 5 contracts each would both fill from a 5-contract book.
A `BookTaken` belongs to one scope (one portfolio, which shares one pool of capital) for one
tick: each fill, entry or exit, takes what it walked out of the recorded depth, and later
strategies in that tick see only what is left (`view`). Order is deterministic: the runner
processes exits, then entries, each in strategy order (as in the portfolio file).

Assumptions (docs/exits-and-fills.md):
- Depth replenishes between ticks: the next poll's book is recorded afresh, and our paper
  orders are never in it, so nothing taken carries over.
- Separate scopes never share: two portfolios, or two sweep variants (each its own
  portfolio), each see the whole recorded book, as they would if run alone.
- Only recorded depth is taken. Quote-only fills (`fills.missing_depth: quote`) have no
  recorded size to take, so they are not limited across strategies; they are labelled.
"""

from dataclasses import replace

from nem.core.types import Level, MarketSnapshot, Side

EPS = 1e-9


def _key(price: float) -> float:
    return round(price, 6)


class BookTaken:
    def __init__(self) -> None:
        # (ticker, side of the resting bids, price) -> contracts taken this tick
        self._taken: dict[tuple[str, Side, float], float] = {}

    def view(self, snap: MarketSnapshot) -> MarketSnapshot:
        """`snap` with this tick's fills removed from its recorded depth. A side that was
        recorded stays recorded (possibly empty); a side that wasn't stays absent."""
        if not any(k[0] == snap.ticker for k in self._taken):
            return snap
        depth: dict[Side, list[Level]] = {}
        for side, levels in snap.depth.items():
            left: list[Level] = []
            for price, size in levels:
                rest = size - self._taken.get((snap.ticker, side, _key(price)), 0.0)
                if rest > EPS:
                    left.append((price, rest))
            depth[side] = left
        return replace(snap, depth=depth)

    def take(
        self, snap: MarketSnapshot, side: Side, qty: float, *, at_or_above: float = 0.0
    ) -> None:
        """Record that a fill took `qty` contracts of `side`'s resting bids in `snap` (a
        `view`), best first, from levels priced at or above `at_or_above`. An entry buying
        YES takes NO bids (and vice versa); an exit selling YES takes YES bids."""
        remaining = float(qty)
        for price, size in sorted(snap.depth.get(side, ()), key=lambda lv: -lv[0]):
            if remaining <= EPS or price < at_or_above - EPS:
                break
            got = min(remaining, size)
            key = (snap.ticker, side, _key(price))
            self._taken[key] = self._taken.get(key, 0.0) + got
            remaining -= got
