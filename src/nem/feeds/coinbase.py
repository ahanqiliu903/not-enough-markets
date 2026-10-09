"""Crypto spot price from Coinbase's public ticker (no API key).

Kalshi's crypto markets settle on CF Benchmarks' Real Time Index, a cross-exchange
average that isn't freely available. Coinbase spot tracks it closely, so it's a good proxy
for "is BTC above the target price right now?", but not the settlement value itself.
"""

from collections.abc import Sequence
from datetime import datetime

from pydantic import Field

from nem.core.registry import PluginParams, register
from nem.feeds import http
from nem.feeds.base import Feed, Observation


@register("feed", "coinbase_spot")
class CoinbaseSpot(Feed):
    """Keys: `value` (last trade price), `bid`, `ask`. Known at the ticker's own time."""

    class Params(PluginParams):
        product: str = Field(default="BTC-USD", pattern=r"^[A-Z0-9]+-[A-Z]+$")

    def __init__(self, params: Params) -> None:
        self.url = f"https://api.exchange.coinbase.com/products/{params.product}/ticker"

    def fetch(self, now: datetime) -> Sequence[Observation]:
        t = http.get_json(self.url)
        known_at = datetime.fromisoformat(t["time"]) if t.get("time") else now
        known_at = min(known_at, now)  # never claim to know something in the future
        return [
            (key, float(t[field]), known_at)
            for key, field in (("value", "price"), ("bid", "bid"), ("ask", "ask"))
            if t.get(field) not in (None, "")
        ]
