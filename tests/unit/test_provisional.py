"""Provisional ("pending / likely") results of open trades (nem.settle.provisional) and
their report lines and columns. Offline: NWS and Kalshi are mocked."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from nem.core.types import StrikeType
from nem.market.kalshi import KalshiMarket, parse_market
from nem.settle.provisional import (
    MISMATCH_F,
    Call,
    Observation,
    Provisional,
    ProvisionalChecker,
    combine,
    observed_call,
    parse_observations,
    price_call,
    running_extreme,
    six_hour_groups,
)
from nem.stats.report import (
    PENDING_NOTE,
    STRATEGY_COLUMNS,
    SUMMARY_COLUMNS,
    TRADE_COLUMNS,
    build_report,
    pending_totals,
    render_text,
    report_data,
    tables,
)
from nem.store import Store

from engine_helpers import portfolio
from factories import make_trade
from kalshi_mock import mock_client

SFO = "KXHIGHTSFO-30JAN23-T55"
# SFO climate day Jan 23: Pacific standard midnight to midnight = 08:00Z to 08:00Z
DAY_START = datetime(2030, 1, 23, 8, tzinfo=UTC)
NOW = datetime(2030, 1, 24, 2, tzinfo=UTC)


# --- decision table -----------------------------------------------------------------------

Row = tuple[str, StrikeType, float | None, float | None, int, Call | None]
LOCKED_NO, LIKELY_NO = Call("no", locked=True), Call("no")
LOCKED_YES, LIKELY_YES = Call("yes", locked=True), Call("yes")

TABLE: list[Row] = [
    # high, "less 55" (YES iff high <= 54): a high of 56 already decides NO, even at 55
    ("high", "less", None, 55, 56, LOCKED_NO),
    ("high", "less", None, 55, 55, LIKELY_NO),  # 1F back (54) would be YES
    ("high", "less", None, 55, 54, None),  # can still rise past the strike
    # high, "greater 61" (YES iff high >= 62)
    ("high", "greater", 61, None, 63, LOCKED_YES),
    ("high", "greater", 61, None, 62, LIKELY_YES),
    ("high", "greater", 61, None, 60, None),
    # high, between 55.5 and 56.5 (YES iff 56)
    ("high", "between", 55.5, 56.5, 58, LOCKED_NO),
    ("high", "between", 55.5, 56.5, 57, LIKELY_NO),
    ("high", "between", 55.5, 56.5, 56, None),  # could still rise out of the bracket
    ("high", "between", 55.5, 56.5, 50, None),  # below: both ends say NO, but 56 is reachable
    # low, "less 30" (YES iff low <= 29): the low only falls
    ("low", "less", None, 30, 28, LOCKED_YES),
    ("low", "less", None, 30, 29, LIKELY_YES),
    ("low", "less", None, 30, 31, None),
    # low, "greater 40" (YES iff low >= 41)
    ("low", "greater", 40, None, 39, LOCKED_NO),
    ("low", "greater", 40, None, 40, LIKELY_NO),
    ("low", "greater", 40, None, 41, None),
    # low, between 35.5 and 36.5 (YES iff 36)
    ("low", "between", 35.5, 36.5, 34, LOCKED_NO),
    ("low", "between", 35.5, 36.5, 35, LIKELY_NO),
    ("low", "between", 35.5, 36.5, 36, None),
    ("low", "between", 35.5, 36.5, 40, None),
]


@pytest.mark.parametrize(("measure", "strike_type", "floor", "cap", "extreme", "want"), TABLE)
def test_observed_decision_table(
    measure: Any,
    strike_type: StrikeType,
    floor: float | None,
    cap: float | None,
    extreme: int,
    want: Call | None,
) -> None:
    assert observed_call(measure, extreme, strike_type, floor, cap) == want


def test_margin_is_configurable() -> None:
    assert MISMATCH_F == 1
    assert observed_call("high", 62, "greater", 61, None, margin=0) == LOCKED_YES
    assert observed_call("high", 63, "greater", 61, None, margin=2) == LIKELY_YES
    assert observed_call("high", 64, "greater", 61, None, margin=2) == LOCKED_YES


def test_unknown_strike_decides_nothing() -> None:
    assert observed_call("high", 90, None, None, None) is None
    assert observed_call("high", 90, "greater", None, None) is None


# --- observations -------------------------------------------------------------------------

RAW_SFO = "KSFO 232356Z 29012KT 10SM FEW020 13/07 A3012 RMK AO2 SLP199 T01280072 10133 20111"


def test_six_hour_groups() -> None:
    assert six_hour_groups(RAW_SFO) == (13.3, 11.1)
    neg = "KMSP 241156Z 33010KT 10SM M01/M05 A3020 RMK AO2 SLP240 T10061050 11006 21017 51012"
    assert six_hour_groups(neg) == (-0.6, -1.7)
    # 24-hour group (4sTTTsTTT), T group and pressure tendency are not 6-hour groups
    assert six_hour_groups("KX 1 RMK AO2 401560089 T01330056 53012") == (None, None)
    assert six_hour_groups("KX 232356Z 10133 20111") == (None, None)  # no remarks section
    assert six_hour_groups("KX RMK AO2 10133=") == (13.3, None)


def obs(at: datetime, c: float | None = None, raw: str = "") -> dict[str, Any]:
    return {
        "properties": {
            "timestamp": at.isoformat(),
            "temperature": {"unitCode": "wmoUnit:degC", "value": c},
            "rawMessage": raw,
        }
    }


def test_running_extreme_uses_the_window() -> None:
    end = DAY_START + timedelta(days=1)
    body = {
        "features": [
            obs(DAY_START - timedelta(minutes=4), 20.0),  # previous climate day
            obs(
                DAY_START + timedelta(hours=3, minutes=56), 9.0, "K RMK T00900050 10222"
            ),  # 6h reaches back
            obs(DAY_START + timedelta(hours=6), 11.0),
            obs(DAY_START + timedelta(hours=15, minutes=56), 12.8, RAW_SFO),
            obs(DAY_START + timedelta(hours=20), None),  # missing value
        ]
    }
    got = parse_observations(body)
    assert len(got) == 5
    assert running_extreme(got, "high", DAY_START, end) == 56  # 13.3C = 55.9F
    assert running_extreme(got, "low", DAY_START, end) == 48  # 9.0C = 48.2F
    assert (
        running_extreme(got, "high", DAY_START, DAY_START + timedelta(hours=7)) == 51
    )  # 11 C whole: 10.5
    assert running_extreme(got[:1], "high", DAY_START, end) is None


def test_whole_degree_readings_never_overstate() -> None:
    at = DAY_START + timedelta(hours=8)
    body = {"features": [obs(at, 13.0), obs(at + timedelta(hours=1), 13.0, RAW_SFO)]}
    whole, metar = parse_observations(body)
    assert not whole.precise and metar.precise
    # 13 C without its tenths may be 12.5 (54.5F -> 55) for a high, 13.5 (56.3F) for a low
    assert running_extreme([whole], "high", DAY_START, NOW) == 55
    assert running_extreme([whole], "low", DAY_START, NOW) == 56
    assert running_extreme([metar], "high", DAY_START, NOW) == 56  # 6h max 13.3 C


def test_negative_six_hour_min() -> None:
    at = datetime(2030, 1, 24, 11, 56, tzinfo=UTC)
    got = [Observation(at, -1.0, None, -1.7)]
    assert running_extreme(got, "low", at - timedelta(hours=7), at) == 29  # 28.9F


# --- price --------------------------------------------------------------------------------


def market(**kw: Any) -> dict[str, Any]:
    m: dict[str, Any] = {
        "ticker": SFO,
        "event_ticker": "KXHIGHTSFO-30JAN23",
        "status": "active",
        "open_time": "2030-01-22T15:00:00Z",
        "close_time": "2030-01-24T07:59:00Z",
        "yes_bid_dollars": "0.4000",
        "yes_ask_dollars": "0.4200",
        "no_bid_dollars": "0.5800",
        "no_ask_dollars": "0.6000",
        "last_price_dollars": "0.4100",
        "strike_type": "less",
        "cap_strike": 55,
    }
    m.update(kw)
    return m


def km(**kw: Any) -> KalshiMarket:
    return parse_market(market(**kw))


def test_parse_market_last_price() -> None:
    assert km().last_price == 0.41
    assert km(last_price_dollars="0.0000").last_price is None
    assert km(last_price_dollars="").last_price is None
    assert (
        parse_market({k: v for k, v in market().items() if k != "last_price_dollars"}).last_price
        is None
    )


def test_price_rules() -> None:
    assert price_call(km()) is None
    assert price_call(km(yes_bid_dollars="0.9800")) == LIKELY_YES
    assert price_call(km(no_bid_dollars="0.9850")) == LIKELY_NO
    assert price_call(km(no_bid_dollars="0.9700")) is None
    closed = {"status": "closed", "yes_bid_dollars": "0", "no_bid_dollars": "0"}
    assert price_call(km(**closed, last_price_dollars="0.9900")) == LIKELY_YES
    assert price_call(km(**closed, last_price_dollars="0.0100")) == LIKELY_NO
    assert price_call(km(**closed, last_price_dollars="0.5000")) is None
    assert price_call(km(**closed, last_price_dollars="0")) is None
    # a closed market's bids are ignored: only the last price counts
    assert price_call(km(status="closed", yes_bid_dollars="0.99", last_price_dollars="0.5")) is None


# --- combining ----------------------------------------------------------------------------


def test_combine() -> None:
    no = make_trade(side="no", avg_price=0.40, qty=10, fee=0.17)
    win, loss = round(10 * 0.60 - 0.17, 6), -(10 * 0.40 + 0.17)
    assert combine(no, LOCKED_NO, None, 56) == Provisional("locked_win", "observed", win, 56)
    assert combine(no, LOCKED_NO, LIKELY_NO).basis == "both"
    # observed locked beats a disagreeing price
    assert combine(no, LOCKED_NO, LIKELY_YES).status == "locked_win"
    assert combine(no, LOCKED_YES, None).status == "locked_loss"
    assert combine(no, LOCKED_YES, None).pnl == pytest.approx(loss)
    assert combine(no, LIKELY_YES, None).status == "likely_loss"
    assert combine(no, None, LIKELY_NO) == Provisional("likely_win", "price", win, None)
    assert combine(no, LIKELY_NO, LIKELY_NO).basis == "both"
    assert combine(no, LIKELY_NO, LIKELY_YES) == Provisional("conflict", "both", None, None)
    assert combine(no, None, None, 50, "x") == Provisional("unknown", None, None, 50, "x")


def test_pnl_matches_settlement() -> None:
    from nem.engine.settlement import realized_pnl

    t = make_trade(side="yes", avg_price=0.93, qty=7, fee=0.03)
    assert combine(t, None, LIKELY_YES).pnl == realized_pnl(t, True)
    assert combine(t, None, LIKELY_NO).pnl == realized_pnl(t, False)


# --- fetching -----------------------------------------------------------------------------


class Routes:
    def __init__(self, nws: httpx.Response | None = None, **market_kw: Any) -> None:
        self.nws = nws or httpx.Response(
            200,
            json={"features": [obs(DAY_START + timedelta(hours=15, minutes=56), 12.8, RAW_SFO)]},
        )
        self.market_kw = market_kw
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.weather.gov":
            return self.nws
        ticker = request.url.path.rsplit("/", 1)[-1]
        if ticker.endswith("-T99"):
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"market": market(ticker=ticker, **self.market_kw)})

    def hosts(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host]


def checker(routes: Routes) -> ProvisionalChecker:
    return ProvisionalChecker(
        NOW, transport=httpx.MockTransport(routes), kalshi=mock_client(routes, max_retries=0)
    )


def sfo_trade(ticker: str = SFO, **kw: Any) -> Any:
    kw.setdefault("side", "no")
    kw.setdefault("avg_price", 0.60)
    return make_trade(window_id=ticker.rsplit("-", 1)[0], ticker=ticker, **kw)


def test_sfo_example_is_a_locked_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEM_USER_AGENT", "nem test (ops@example.invalid)")
    routes = Routes()
    with checker(routes) as c:
        p = c.check_all([sfo_trade()])[("fav90", "KXHIGHTSFO-30JAN23")]
    assert (p.status, p.basis, p.observed_extreme_f) == ("locked_win", "observed", 56)
    assert p.pnl == pytest.approx(5 * 0.40 - 0.02)
    (nws,) = routes.hosts("api.weather.gov")
    assert nws.url.path == "/stations/KSFO/observations"
    assert nws.url.params["start"] == "2030-01-23T08:00:00Z"
    assert nws.url.params["end"] == "2030-01-24T02:00:00Z"  # now, not the day's end
    assert nws.headers["User-Agent"] == "nem test (ops@example.invalid)"
    assert nws.headers["Accept"] == "application/geo+json"


def test_generic_user_agent_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEM_USER_AGENT", raising=False)
    routes = Routes()
    with checker(routes) as c:
        c.check_all([sfo_trade()])
    assert routes.hosts("api.weather.gov")[0].headers["User-Agent"].startswith("not-enough-markets")


def test_one_request_per_station_and_market() -> None:
    routes = Routes()
    trades = [
        sfo_trade(),
        sfo_trade(strategy="other"),  # same market, another strategy
        sfo_trade("KXHIGHTSFO-30JAN23-T57"),
        sfo_trade("KXLOWTSFO-30JAN22-T45"),  # another day at the same station
    ]
    with checker(routes) as c:
        got = c.check_all(trades)
    assert len(got) == 3  # keyed by (strategy, window_id): two SFO T5x share a window here
    (nws,) = routes.hosts("api.weather.gov")
    assert nws.url.params["start"] == "2030-01-22T08:00:00Z"  # spans both climate days
    assert len(routes.hosts("api.elections.kalshi.com")) == 3


def test_not_a_temperature_market_uses_price_only() -> None:
    routes = Routes(yes_bid_dollars="0.99", no_bid_dollars="0")
    t = make_trade()  # KXBTC15M
    with checker(routes) as c:
        p = c.check(t)
    assert (p.status, p.basis, p.observed_extreme_f) == ("likely_win", "price", None)
    assert routes.hosts("api.weather.gov") == []


def test_market_not_started_yet() -> None:
    routes = Routes()
    with checker(routes) as c:
        p = c.check(sfo_trade("KXHIGHTSFO-30JAN25-T55"))
    assert p.status == "unknown" and p.observed_extreme_f is None
    assert routes.hosts("api.weather.gov") == []


def test_nws_failure_is_unknown_with_a_note() -> None:
    routes = Routes(nws=httpx.Response(503))
    with checker(routes) as c:
        p = c.check(sfo_trade())
    assert p.status == "unknown" and "observations KSFO: HTTP 503" in p.note
    routes = Routes(nws=httpx.Response(503), no_bid_dollars="0.99")
    with checker(routes) as c:
        p = c.check(sfo_trade())
    assert (p.status, p.basis) == ("likely_win", "price")  # price still counts


def test_kalshi_failure_is_unknown_with_a_note() -> None:
    routes = Routes()
    with checker(routes) as c:
        p = c.check(sfo_trade("KXHIGHTSFO-30JAN23-T99"))
    # the extreme is known but without the market there is no strike to judge it by
    assert (p.status, p.observed_extreme_f) == ("unknown", 56)
    assert p.note.startswith("kalshi:")


def test_transport_error_never_raises() -> None:
    def broken(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    with ProvisionalChecker(
        NOW, transport=httpx.MockTransport(broken), kalshi=mock_client(broken, max_retries=0)
    ) as c:
        got = c.check_all([sfo_trade()])
    (p,) = got.values()
    assert p.status == "unknown" and "offline" in p.note


# --- report -------------------------------------------------------------------------------


def _report(provisional: dict[tuple[str, str], Provisional] | None) -> Any:
    store = Store()
    for i in range(4):
        store.open_trade(make_trade(window_id=f"W{i}", opened_at=NOW))
    ex = make_trade(window_id="WX", opened_at=NOW)
    store.open_trade(ex)
    store.set_excluded(ex, "dup", NOW)
    return build_report(store, portfolio(), NOW, provisional=provisional)


PROVISIONAL: dict[tuple[str, str], Provisional] = {
    ("fav90", "W0"): Provisional("locked_win", "observed", 0.48, 56),
    ("fav90", "W1"): Provisional("likely_win", "price", 0.48, None),
    ("fav90", "W2"): Provisional("likely_loss", "both", -4.52, 61),
    ("fav90", "W3"): Provisional("conflict", "both", None, 60),
    ("fav90", "WX"): Provisional("locked_win", "observed", 0.48, 56),  # excluded: not summed
}


def test_pending_totals() -> None:
    trades = [make_trade(window_id=f"W{i}") for i in range(4)]
    p = pending_totals(trades, PROVISIONAL)
    assert p is not None
    assert (p.locked, p.likely, p.conflict) == (1, 2, 1)
    assert p.locked_pnl == pytest.approx(0.48) and p.likely_pnl == pytest.approx(-4.04)
    assert pending_totals([], None) == pending_totals([], {})
    assert pending_totals(trades, None) is None


def test_report_lines_and_columns() -> None:
    r = _report(PROVISIONAL)
    text = render_text(r)
    line = (
        "pending     locked +0.48 (1 won, 0 lost), likely -4.04 (1 won, 1 lost),"
        f" 1 conflict (observed and price disagree); {PENDING_NOTE}"
    )
    assert f"  {line}" in text and f"    {line}" in text  # portfolio and strategy
    summary, strategies, trades = tables(r)
    assert summary.columns == SUMMARY_COLUMNS and strategies.columns == STRATEGY_COLUMNS
    assert trades.columns == TRADE_COLUMNS
    for table in (summary, strategies):
        row = dict(zip(table.columns, table.rows[0], strict=True))
        got = [row[c] for c in table.columns if c.startswith("pending_")]
        assert got == [0.48, -4.04, 1, 2, 1]
    rows = {r[3]: dict(zip(trades.columns, r, strict=True)) for r in trades.rows}
    w0, w3, wx = rows["W0"], rows["W3"], rows["WX"]
    assert (w0["provisional_result"], w0["provisional_basis"]) == ("locked_win", "observed")
    assert (w0["provisional_pnl_usd"], w0["observed_extreme_f"]) == (0.48, 56)
    assert (w3["provisional_result"], w3["provisional_pnl_usd"]) == ("conflict", None)
    assert wx["provisional_result"] == "locked_win"  # listed, though not in the totals
    data = report_data(r)
    assert data["summary"]["pending_locked"] == 1  # type: ignore[index]
    # nothing provisional reaches money
    assert r.realized == 0 and r.equity == r.start


def test_report_without_provisional() -> None:
    r = _report(None)
    summary, _, trades = tables(r)
    row = dict(zip(summary.columns, summary.rows[0], strict=True))
    assert all(row[c] is None for c in summary.columns if c.startswith("pending_"))
    assert all(t[TRADE_COLUMNS.index("provisional_result")] is None for t in trades.rows)
    assert "pending" not in render_text(r)


def test_report_with_nothing_decided_has_no_line() -> None:
    r = _report({("fav90", "W0"): Provisional("unknown", None, None, 50)})
    assert "pending " not in render_text(r)
    failed = _report({("fav90", "W0"): Provisional("unknown", None, None, None, "kalshi: x")})
    assert "1 not checked (fetch failed)" in render_text(failed)


def test_settled_trade_has_no_provisional_columns() -> None:
    store = Store()
    t = make_trade(opened_at=NOW)
    store.open_trade(t)
    store.settle_trade(("research", "fav90"), t.window_id, True, 0.48, NOW)
    r = build_report(store, portfolio(), NOW, provisional={})
    _, _, trades = tables(r)
    row = dict(zip(trades.columns, trades.rows[0], strict=True))
    assert row["provisional_result"] is None and row["result"] == "won"


def test_summary_no_provisional_skips_network(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import nem.settle.provisional
    from nem.cli import main

    calls: list[datetime] = []

    class Offline(ProvisionalChecker):
        def __init__(self, now: datetime, **_kw: Any) -> None:
            calls.append(now)
            raise RuntimeError("network down")

    monkeypatch.setattr(nem.settle.provisional, "ProvisionalChecker", Offline)
    d = tmp_path / "portfolios"
    assert main(["portfolio", "new", "research", "--dir", str(d)]) == 0
    assert (
        main(["strategy", "add", "research", "fav90", "--series", "KXBTC15M", "--dir", str(d)]) == 0
    )
    db = tmp_path / "nem.db"
    with Store(db) as store:
        store.open_trade(make_trade(opened_at=NOW))
    capsys.readouterr()
    assert main(["summary", "--no-provisional", "--db", str(db), "--dir", str(d)]) == 0
    assert calls == []
    # with it on, a failing checker still lets the report through
    assert main(["summary", "--json", "--db", str(db), "--dir", str(d)]) == 0
    assert len(calls) == 1
    assert '"pending_locked": null' in capsys.readouterr().out
