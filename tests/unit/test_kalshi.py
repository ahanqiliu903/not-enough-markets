import base64
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from nem.market.auth import Signer
from nem.market.kalshi import KalshiError, parse_market, parse_orderbook

from kalshi_mock import Recording, default_routes, fixture, mock_client


def test_parse_open_market() -> None:
    m = parse_market(fixture("markets_open")["markets"][0])
    assert m.ticker == "KXBTC15M-26OCT081430-30"
    assert m.event_ticker == "KXBTC15M-26OCT081430"
    assert (m.yes_bid, m.yes_ask, m.no_bid, m.no_ask) == (0.73, 0.74, 0.26, 0.27)
    assert m.close_time == datetime(2026, 10, 8, 18, 30, tzinfo=UTC)
    assert m.result is None
    assert m.settled_at is None


def test_parse_settled_market() -> None:
    m = parse_market(fixture("market_settled")["market"])
    assert m.result == "no"
    assert m.settled_at == datetime(2026, 10, 8, 18, 15, 3, 694230, tzinfo=UTC)
    # bid 0 / ask 1 on a dead book mean "no quote"
    assert (m.yes_bid, m.yes_ask) == (None, None)


def test_parse_orderbook_best_first() -> None:
    book = parse_orderbook(fixture("orderbooks")["orderbooks"][0]["orderbook_fp"])
    assert book["yes"][0] == (0.82, 1784.35)
    assert book["no"][0] == (0.17, 12988.71)
    for side in ("yes", "no"):
        prices = [p for p, _ in book[side]]
        assert prices == sorted(prices, reverse=True)


def test_parse_orderbook_empty() -> None:
    assert parse_orderbook({"yes_dollars": None}) == {"yes": [], "no": []}


def test_markets_follows_cursor() -> None:
    page = fixture("markets_open")
    m2 = dict(page["markets"][0], ticker="SECOND")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "abc":
            return httpx.Response(200, json={"markets": [m2], "cursor": ""})
        return httpx.Response(200, json={"markets": page["markets"], "cursor": "abc"})

    rec = Recording(handler)
    markets = mock_client(rec).markets("KXBTC15M")
    assert [m.ticker for m in markets] == ["KXBTC15M-26OCT081430-30", "SECOND"]
    assert rec.requests[0].url.params["series_ticker"] == "KXBTC15M"
    assert rec.requests[0].url.params["status"] == "open"


def test_orderbooks_batches_tickers() -> None:
    rec = Recording()
    mock_client(rec).orderbooks([f"T{i}" for i in range(120)])
    sizes = [len(r.url.params.get_list("tickers")) for r in rec.requests]
    assert sizes == [50, 50, 20]


def test_market_and_exchange_status() -> None:
    client = mock_client()
    assert client.market("KXBTC15M-26OCT081415-15").result == "no"
    assert client.exchange_active() is True


def test_retries_rate_limit_then_succeeds() -> None:
    sleeps: list[float] = []
    calls = iter([429, 503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(calls)
        return default_routes(request) if code == 200 else httpx.Response(code)

    assert mock_client(handler, sleep=sleeps.append).exchange_active()
    assert sleeps == [0.5, 1.0]


def test_retries_transport_errors_then_gives_up() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(KalshiError, match="gave up after 4 tries"):
        mock_client(handler).exchange_active()


def test_client_errors_are_not_retried() -> None:
    rec = Recording(lambda _r: httpx.Response(404, json={"error": "nope"}))
    with pytest.raises(KalshiError, match="HTTP 404"):
        mock_client(rec).market("NOPE")
    assert len(rec.requests) == 1


def test_signed_requests_sign_full_path_without_query() -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    rec = Recording()
    mock_client(rec, signer=Signer("kid", key)).markets("KXBTC15M")
    req = rec.requests[0]
    ts = req.headers["KALSHI-ACCESS-TIMESTAMP"]
    sig = base64.b64decode(req.headers["KALSHI-ACCESS-SIGNATURE"])
    key.public_key().verify(sig, f"{ts}GET/trade-api/v2/markets".encode())


def test_unsigned_by_default() -> None:
    rec = Recording()
    mock_client(rec).exchange_active()
    assert "KALSHI-ACCESS-KEY" not in rec.requests[0].headers
