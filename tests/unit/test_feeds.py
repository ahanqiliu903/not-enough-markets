from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest

from nem.core.clock import ManualClock
from nem.core.config import PluginSpec
from nem.core.context import Context, DataUnavailable
from nem.core.registry import REGISTRY, PluginError
from nem.core.types import FeedValue, Side
from nem.engine.runner import Runner
from nem.feeds import http
from nem.feeds.base import Feed
from nem.gates.base import Gate
from nem.gates.feeds import market_date, resolve_key
from nem.market.kalshi import parse_market
from nem.market.source import FakeSource
from nem.store import Store

from engine_helpers import portfolio, settle, strategy, ticks, window
from factories import make_signal, make_snapshot
from kalshi_mock import fixture

NOW = datetime(2026, 10, 9, 1, 35, tzinfo=UTC)


@pytest.fixture
def routes(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """URL path -> JSON body for feed HTTP calls."""
    table: dict[str, Any] = {}
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = table.get(request.url.path)
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404)

    monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(handler))
    table["_seen"] = seen
    yield table


def feed(**spec: object) -> Feed:
    return REGISTRY.build("feed", PluginSpec.model_validate(spec), Feed)


# --- strikes ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "lo", "hi", "value", "yes"),
    [
        ("greater", 76, None, 76, False),
        ("greater", 76, None, 77, True),
        ("greater_or_equal", 100.5, None, 100.5, True),
        ("less", None, 69, 69, False),
        ("less_or_equal", None, 69, 69, True),
        ("between", 75, 76, 75, True),
        ("between", 75, 76, 76.5, False),
        ("greater", None, None, 1, None),  # strike missing
        (None, 1, 2, 1, None),
    ],
)
def test_resolves_yes(kind: Any, lo: Any, hi: Any, value: float, yes: bool | None) -> None:
    snap = make_snapshot(strike_type=kind, floor_strike=lo, cap_strike=hi)
    assert snap.resolves_yes(value) is yes


def test_kalshi_strike_parsed() -> None:
    m = parse_market(fixture("markets_open")["markets"][0])
    assert m.strike_type == "greater_or_equal"
    assert m.floor_strike is not None and m.cap_strike is None


# --- feeds ----------------------------------------------------------------------------


def test_coinbase_spot(routes: dict[str, Any]) -> None:
    routes["/products/ETH-USD/ticker"] = {
        "price": "2500.5", "bid": "2500.4", "ask": "2500.6", "time": "2026-10-09T01:34:42.8Z"
    }  # fmt: skip
    obs = feed(type="coinbase_spot", product="ETH-USD").fetch(NOW)
    assert [(k, v) for k, v, _ in obs] == [("value", 2500.5), ("bid", 2500.4), ("ask", 2500.6)]
    assert obs[0][2] == datetime(2026, 10, 9, 1, 34, 42, 800000, tzinfo=UTC)


def test_coinbase_never_claims_future_knowledge(routes: dict[str, Any]) -> None:
    routes["/products/BTC-USD/ticker"] = {"price": "1", "time": "2030-01-01T00:00:00Z"}
    [(_, _, known_at)] = feed(type="coinbase_spot").fetch(NOW)
    assert known_at == NOW


def test_coinbase_product_validated() -> None:
    with pytest.raises(PluginError):
        feed(type="coinbase_spot", product="btc")


NWS_FORECAST = {
    "properties": {
        "updateTime": "2026-10-08T23:47:25+00:00",
        "periods": [
            {"startTime": "2026-10-08T18:00:00-04:00", "isDaytime": False, "temperature": 57,
             "temperatureUnit": "F", "probabilityOfPrecipitation": {"value": 10}},
            {"startTime": "2026-10-09T06:00:00-04:00", "isDaytime": True, "temperature": 71,
             "temperatureUnit": "F", "probabilityOfPrecipitation": {"value": 40}},
            {"startTime": "2026-10-09T18:00:00-04:00", "isDaytime": False, "temperature": 15,
             "temperatureUnit": "C", "probabilityOfPrecipitation": {"value": 70}},
        ],
    }
}  # fmt: skip


def test_nws_forecast(routes: dict[str, Any]) -> None:
    routes["/points/40.7789,-73.9692"] = {
        "properties": {"forecast": "https://api.weather.gov/gridpoints/OKX/34,45/forecast"}
    }
    routes["/gridpoints/OKX/34,45/forecast"] = NWS_FORECAST
    f = feed(type="nws_forecast", lat=40.7789, lon=-73.9692, user_agent="me@example.com")
    obs = {k: (v, t) for k, v, t in f.fetch(NOW)}
    issued = datetime(2026, 10, 8, 23, 47, 25, tzinfo=UTC)
    assert obs["2026-10-09/high"] == (71.0, issued)
    assert obs["2026-10-09/low"] == (57.0, issued)  # the night of Oct 8 -> Oct 9's low
    assert obs["2026-10-10/low"] == (59.0, issued)  # 15C converted
    assert obs["2026-10-09/precip"] == (70.0, issued)  # max over the day's periods
    f.fetch(NOW)
    seen: list[httpx.Request] = routes["_seen"]
    assert [r.url.path for r in seen].count("/points/40.7789,-73.9692") == 1  # cached
    assert seen[0].headers["User-Agent"] == "me@example.com"


# --- gates ------------------------------------------------------------------------------


def test_market_date_and_key_templates() -> None:
    assert market_date("KXHIGHNY-26OCT09") == date(2026, 10, 9)
    assert market_date("KXBTC15M-26OCT081430") == date(2026, 10, 8)
    assert market_date("NODATE") is None
    snap = make_snapshot(window_id="KXHIGHNY-26OCT09")
    assert resolve_key("{date}/high", snap) == "2026-10-09/high"
    assert resolve_key("value", snap) == "value"
    with pytest.raises(DataUnavailable):
        resolve_key("{date}/high", make_snapshot(window_id="X-NODATE"))


FEEDS = [
    {"name": "spot", "type": "coinbase_spot", "max_age": "1m"},
    {"name": "nyc", "type": "nws_forecast", "lat": 40.78, "lon": -73.97, "max_age": "12h"},
]


def ctx_with(values: dict[str, dict[str, float]], now: datetime = NOW) -> Context:
    store = Store()
    for name, kv in values.items():
        store.record_feed_values(
            [FeedValue(name, k, v, now - timedelta(seconds=5)) for k, v in kv.items()], now
        )
    p = portfolio(feeds=FEEDS)
    return Context(p, p.strategy("fav90"), ManualClock(now), store)


def gate(**spec: object) -> Gate:
    return REGISTRY.build("gate", PluginSpec.model_validate(spec), Gate)


BTC: dict[str, Any] = {"strike_type": "greater_or_equal", "floor_strike": 80000.0}


@pytest.mark.parametrize(
    ("spot", "side", "margin_pct", "take"),
    [
        (80500, "yes", 0, True),
        (79500, "yes", 0, False),
        (79500, "no", 0, True),
        (80050, "yes", 0.1, False),  # within 0.1% of the strike: too close to call
        (80100, "yes", 0.1, True),
    ],
)
def test_feed_agrees_spot(spot: float, side: Side, margin_pct: float, take: bool) -> None:
    g = gate(type="feed_agrees", feed="spot", margin_pct=margin_pct)
    d = g.check(make_signal(side=side), make_snapshot(**BTC), ctx_with({"spot": {"value": spot}}))
    assert d.take is take
    if not take:
        assert d.reason == "feed_agrees:spot"


@pytest.mark.parametrize(
    ("high", "side", "take"),
    # 76.5 +/- 0.4 is clear of the bracket; 76.3 +/- 0.4 reaches into it
    [(75.5, "yes", True), (80, "yes", False), (76.5, "no", True), (76.3, "no", False)],
)
def test_feed_agrees_forecast_bracket(high: float, side: Side, take: bool) -> None:
    # KXHIGHNY "75-76F" bracket, 0.4F margin
    snap = make_snapshot(
        window_id="KXHIGHNY-26OCT09", strike_type="between", floor_strike=75, cap_strike=76
    )
    g = gate(type="feed_agrees", feed="nyc", key="{date}/high", margin=0.4)
    ctx = ctx_with({"nyc": {"2026-10-09/high": high}})
    assert g.check(make_signal(side=side), snap, ctx).take is take


def test_feed_agrees_rejects_range_straddling_a_bracket() -> None:
    snap = make_snapshot(strike_type="between", floor_strike=75, cap_strike=76)
    g = gate(type="feed_agrees", feed="spot", margin=5)
    # 74 +/- 5 covers the whole 75-76 bracket: NO isn't safe even though 69, 74, 79 are all NO
    assert not g.check(make_signal(side="no"), snap, ctx_with({"spot": {"value": 74}})).take


def test_feed_agrees_without_strike_is_unavailable() -> None:
    g = gate(type="feed_agrees", feed="spot")
    with pytest.raises(DataUnavailable, match="no usable strike"):
        g.check(make_signal(), make_snapshot(), ctx_with({"spot": {"value": 1}}))


def test_feed_agrees_stale_feed_is_unavailable() -> None:
    g = gate(type="feed_agrees", feed="spot")
    ctx = ctx_with({"spot": {"value": 80500}}, now=NOW)
    ctx.clock.set(NOW + timedelta(minutes=2))  # type: ignore[attr-defined]
    with pytest.raises(DataUnavailable):
        g.check(make_signal(), make_snapshot(**BTC), ctx)


@pytest.mark.parametrize(("precip", "reason"), [(30, "ok"), (70, "feed_threshold:nyc:above_max")])
def test_feed_threshold(precip: float, reason: str) -> None:
    g = gate(type="feed_threshold", feed="nyc", key="{date}/precip", max=60)
    snap = make_snapshot(window_id="KXHIGHNY-26OCT09")
    ctx = ctx_with({"nyc": {"2026-10-09/precip": precip}})
    assert g.check(make_signal(), snap, ctx).reason == reason
    low = gate(type="feed_threshold", feed="nyc", key="{date}/precip", min=50)
    assert low.check(make_signal(), snap, ctx).take is (precip >= 50)


def test_feed_threshold_needs_a_bound() -> None:
    with pytest.raises(PluginError, match="set min, max"):
        gate(type="feed_threshold", feed="x")
    with pytest.raises(PluginError, match="min must be"):
        gate(type="feed_threshold", feed="x", min=5, max=1)


# --- end to end: live feed fetching + gate in the runner -------------------------------------


def test_runner_fetches_feed_and_gate_uses_it(routes: dict[str, Any]) -> None:
    open_ = datetime(2026, 10, 7, 19, 0, tzinfo=UTC)
    w = window(open_)
    w = [replace(snap, **BTC) for snap in w]
    routes["/products/BTC-USD/ticker"] = {"price": "79000", "time": open_.isoformat()}
    gates = [{"type": "feed_agrees", "feed": "spot"}]
    store = Store()
    p = portfolio(feeds=[FEEDS[0]], strategies=[strategy(gates=gates)])
    runner = Runner([p], FakeSource(ticks(w), [settle(w, "yes")]), store, ManualClock(open_))
    runner.run(ticks(w[:3]))
    assert store.open_trades() == []  # spot 79,000 is below the 80,000 strike: no YES
    assert store.signal_reasons("research", "fav90") == {"feed_agrees:spot": 1}
    assert len(store.feed_values("spot", "value", as_of=w[2].ts)) == 1  # every 1m by default


def test_feeds_example_portfolio_builds() -> None:
    from pathlib import Path

    from nem.core.config import load_portfolios
    from nem.engine.runtime import build_feeds, build_strategies

    examples = Path(__file__).parents[2] / "examples" / "feeds"
    portfolios = load_portfolios(examples)
    assert len(build_strategies(portfolios)) == 2
    assert {f.spec.name for f in build_feeds(portfolios)} == {"btc_spot", "nyc"}
