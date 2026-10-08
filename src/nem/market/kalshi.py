"""Kalshi REST client (read side).

Wire-format quirks are handled here and nowhere else (verified 2026-10; these drift):
- Prices are dollar strings (`"0.9320"`), sizes are fractional strings (`"817.01"`).
- A bid of 0 means "no bid" and an ask of 1 means "no ask"; both become None.
- Orderbook ladders are bids per side in ascending price order; we return best first.
- Above 90c (and below 10c) prices tick in 0.1c.
- Public market data needs no auth. The `elections` host serves all markets.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import httpx

from nem.core.types import Depth, Level, Side
from nem.market.auth import Signer

Env = Literal["prod", "demo"]

BASE_URLS: dict[Env, str] = {
    "prod": "https://api.elections.kalshi.com/trade-api/v2",
    "demo": "https://external-api.demo.kalshi.co/trade-api/v2",
}
ORDERBOOK_BATCH = 50  # tickers per batch orderbook request (limit not documented; stay modest)


class KalshiError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class KalshiMarket:
    ticker: str
    event_ticker: str  # = window_id for 15-minute series
    status: str
    open_time: datetime
    close_time: datetime
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    result: Side | None  # None until determined
    settled_at: datetime | None


def _price(s: str | None) -> float | None:
    return None if s is None or s == "" else float(s)


def _bid(s: str | None) -> float | None:
    p = _price(s)
    return None if p is None or p <= 0 else p


def _ask(s: str | None) -> float | None:
    p = _price(s)
    return None if p is None or p >= 1 else p


def _time(s: str) -> datetime:
    return datetime.fromisoformat(s)


def parse_market(m: dict[str, Any]) -> KalshiMarket:
    result = m.get("result") or None
    settlement_ts = m.get("settlement_ts")
    return KalshiMarket(
        ticker=m["ticker"],
        event_ticker=m["event_ticker"],
        status=m["status"],
        open_time=_time(m["open_time"]),
        close_time=_time(m["close_time"]),
        yes_bid=_bid(m.get("yes_bid_dollars")),
        yes_ask=_ask(m.get("yes_ask_dollars")),
        no_bid=_bid(m.get("no_bid_dollars")),
        no_ask=_ask(m.get("no_ask_dollars")),
        result=result if result in ("yes", "no") else None,
        settled_at=_time(settlement_ts) if settlement_ts else None,
    )


def parse_orderbook(book: dict[str, Any]) -> Depth:
    """`{"yes_dollars": [["0.77", "9015.74"], ...], "no_dollars": [...]}` -> best-first."""

    def side(levels: list[list[str]] | None) -> list[Level]:
        parsed = [(float(p), float(q)) for p, q in levels or []]
        return sorted(parsed, key=lambda lv: lv[0], reverse=True)

    return {"yes": side(book.get("yes_dollars")), "no": side(book.get("no_dollars"))}


class KalshiClient:
    def __init__(
        self,
        env: Env = "prod",
        signer: Signer | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = httpx.Client(base_url=BASE_URLS[env], timeout=timeout, transport=transport)
        self._signer = signer
        self._max_retries = max_retries
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: Any = None) -> dict[str, Any]:
        """GET with retries on 429, 5xx and transport errors (exponential backoff)."""
        problem = ""
        for attempt in range(self._max_retries + 1):
            if attempt:
                self._sleep(0.5 * 2 ** (attempt - 1))
            headers: dict[str, str] = {}
            if self._signer is not None:
                full_path = self._http.base_url.path.rstrip("/") + path
                headers = self._signer.headers("GET", full_path, int(time.time() * 1000))
            try:
                r = self._http.get(path, params=params, headers=headers)
            except httpx.TransportError as e:
                problem = repr(e)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                problem = f"HTTP {r.status_code}"
                continue
            if r.is_error:
                raise KalshiError(f"GET {path}: HTTP {r.status_code}: {r.text[:200]}")
            body: dict[str, Any] = r.json()
            return body
        raise KalshiError(f"GET {path}: gave up after {self._max_retries + 1} tries ({problem})")

    def exchange_active(self) -> bool:
        return bool(self._get("/exchange/status").get("trading_active"))

    def markets(self, series: str, status: str = "open") -> list[KalshiMarket]:
        out: list[KalshiMarket] = []
        cursor = ""
        while True:
            params = {"series_ticker": series, "status": status, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            body = self._get("/markets", params)
            out.extend(parse_market(m) for m in body.get("markets", []))
            cursor = body.get("cursor") or ""
            if not cursor:
                return out

    def market(self, ticker: str) -> KalshiMarket:
        return parse_market(self._get(f"/markets/{ticker}")["market"])

    def orderbooks(self, tickers: Sequence[str]) -> dict[str, Depth]:
        out: dict[str, Depth] = {}
        for i in range(0, len(tickers), ORDERBOOK_BATCH):
            chunk = tickers[i : i + ORDERBOOK_BATCH]
            body = self._get("/markets/orderbooks", [("tickers", t) for t in chunk])
            for ob in body.get("orderbooks", []):
                out[ob["ticker"]] = parse_orderbook(ob.get("orderbook_fp") or {})
        return out
