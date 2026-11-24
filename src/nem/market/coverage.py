"""Kalshi series that match a prefix, have open markets, and no strategy trades yet.

Report only (`nem series uncovered`): adding a series stays a manual step, because its
settlement rules must be read first (titles can be wrong; docs/kalshi-tickers.txt).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from nem.market.kalshi import KalshiClient
from nem.settle.stations import SERIES


@dataclass(frozen=True, slots=True)
class Uncovered:
    series: str
    title: str
    open_markets: int
    settlement: str  # first settlement source Kalshi lists, "" if none
    mapped: bool  # station known to nem.settle.stations (forecast gates can resolve it)


def uncovered(
    client: KalshiClient, prefixes: Sequence[str], used: Iterable[str]
) -> list[Uncovered]:
    taken = set(used)
    out: list[Uncovered] = []
    for s in client.series_list():
        ticker = str(s.get("ticker", ""))
        if ticker in taken or not ticker.startswith(tuple(prefixes)):
            continue
        n = len(client.markets(ticker))
        if n == 0:
            continue
        sources: list[dict[str, Any]] = s.get("settlement_sources") or []
        out.append(Uncovered(
            series=ticker,
            title=str(s.get("title", "")),
            open_markets=n,
            settlement=str(sources[0].get("name", "")) if sources else "",
            mapped=ticker in SERIES,
        ))  # fmt: skip
    return sorted(out, key=lambda u: u.series)
