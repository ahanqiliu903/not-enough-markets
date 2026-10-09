"""Builders for engine tests: a portfolio, a context, and scripted market windows."""

from datetime import datetime, timedelta
from typing import Any

from nem.builtins import load_builtins
from nem.core.clock import ManualClock
from nem.core.config import PortfolioConfig
from nem.core.context import Context
from nem.core.types import MarketSnapshot, Settlement
from nem.market.source import Tick
from nem.store import Store

from factories import portfolio_dict

load_builtins()


def strategy(**kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "name": "fav90",
        "series": "KXBTC15M",
        "signal": {
            "type": "extreme_favorite",
            "threshold": 0.9,
            "min_edge": 0.01,
            "priors": {"yes": {"wins": 95, "n": 100}, "no": {"wins": 95, "n": 100}},
        },
        "gates": [{"type": "max_entry_price", "value": 0.95}],
        "sizing": {"type": "fixed", "contracts": 5},
    }
    d.update(kw)
    return d


def portfolio(**kw: Any) -> PortfolioConfig:
    kw.setdefault("strategies", [strategy()])
    return PortfolioConfig.model_validate(portfolio_dict(**kw))


def context(p: PortfolioConfig, store: Store, now: datetime, name: str = "fav90") -> Context:
    return Context(p, p.strategy(name), ManualClock(now), store)


def window(
    open_time: datetime,
    series: str = "KXBTC15M",
    yes_ask: float = 0.90,
    every: timedelta = timedelta(seconds=5),
    minutes: int = 15,
    depth: float = 50,
) -> list[MarketSnapshot]:
    """One market window, quoted every `every`, YES favorite at `yes_ask` with depth."""
    close = open_time + timedelta(minutes=minutes)
    code = f"{close:%y}OCT{close:%d%H%M}"
    out: list[MarketSnapshot] = []
    t = open_time
    while t < close:
        out.append(
            MarketSnapshot(
                ts=t,
                series=series,
                window_id=f"{series}-{code}",
                ticker=f"{series}-{code}-00",
                open_time=open_time,
                close_time=close,
                yes_bid=round(yes_ask - 0.01, 4),
                yes_ask=yes_ask,
                no_bid=round(1 - yes_ask, 4),
                no_ask=round(1 - yes_ask + 0.01, 4),
                depth={
                    "yes": [(round(yes_ask - 0.01, 4), depth)],
                    "no": [(round(1 - yes_ask, 4), depth)],
                },
            )
        )
        t += every
    return out


def ticks(*windows: list[MarketSnapshot]) -> list[Tick]:
    by_ts: dict[datetime, list[MarketSnapshot]] = {}
    for w in windows:
        for s in w:
            by_ts.setdefault(s.ts, []).append(s)
    return [Tick(ts, snaps) for ts, snaps in sorted(by_ts.items())]


def settle(w: list[MarketSnapshot], result: str) -> Settlement:
    s = w[0]
    return Settlement(s.ticker, s.series, s.window_id, result, s.close_time + timedelta(seconds=3))  # type: ignore[arg-type]
