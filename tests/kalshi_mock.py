"""Offline Kalshi API: serves captured responses from tests/fixtures/kalshi/."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from nem.market.kalshi import KalshiClient

FIXTURES = Path(__file__).parent / "fixtures" / "kalshi"

Handler = Callable[[httpx.Request], httpx.Response]


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


def default_routes(request: httpx.Request) -> httpx.Response:
    path = request.url.path.removeprefix("/trade-api/v2")
    if path == "/markets":
        return httpx.Response(200, json=fixture("markets_open"))
    if path == "/markets/orderbooks":
        return httpx.Response(200, json=fixture("orderbooks"))
    if path == "/exchange/status":
        return httpx.Response(200, json=fixture("exchange_status"))
    if path.startswith("/markets/"):  # a settled market, relabelled as the one asked for
        body = fixture("market_settled")
        ticker = path.removeprefix("/markets/")
        body["market"].update(ticker=ticker, event_ticker=ticker.rsplit("-", 1)[0])
        return httpx.Response(200, json=body)
    return httpx.Response(404, json={"error": "not found"})


class Recording:
    """Wraps a handler and remembers every request."""

    def __init__(self, handler: Handler = default_routes) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def _no_sleep(_seconds: float) -> None:
    pass


def mock_client(handler: Handler = default_routes, **kw: Any) -> KalshiClient:
    kw.setdefault("sleep", _no_sleep)
    return KalshiClient("prod", transport=httpx.MockTransport(handler), **kw)
