"""Settlement truth. Offline: IEM and Kalshi answers are small synthetic
fixtures, not anyone's trading data."""

import csv
import re
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx
import pytest

from nem.cli import main
from nem.feeds import http
from nem.market import kalshi as kalshi_module
from nem.market.kalshi import KalshiClient, KalshiNotFound
from nem.settle.observed import Iem, Reading, SourceError, parse_asos_csv
from nem.settle.rules import (
    climate_day,
    climate_day_window,
    derive_result,
    rules_measure,
    rules_source,
    rules_stations,
    standard_offset,
    status_of,
    whole_degrees,
)
from nem.settle.stations import SERIES, STATIONS, lookup
from nem.settle.verify import (
    check_market,
    needs_review,
    pnl_by_ticker,
    render_counts,
    tickers_from_file,
    verify,
    write_report,
)
from nem.store import Store

from kalshi_mock import Recording, mock_client

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
NY = ZoneInfo("America/New_York")
NWS_RULES = (
    "If the highest temperature recorded in Central Park, New York for March 03, 2026 as "
    "reported by the National Weather Service's Climatological Report (Daily), is between "
    "40-41°, then the market resolves to Yes."
)
TWC_RULES = (
    "If the maximum temperature recorded at New York City (CLINYC) for Oct 9, 2026, is "
    "greater than 57° fahrenheit according to The Weather Company, then the market resolves "
    "to Yes."
)


def market(**kw: Any) -> dict[str, Any]:
    """A settled Kalshi market (raw API shape), B40.5 on 2026-03-03, YES won at 41."""
    m: dict[str, Any] = {
        "ticker": "KXHIGHNY-26MAR03-B40.5", "strike_type": "between", "floor_strike": 40,
        "cap_strike": 41, "result": "yes", "expiration_value": "41.00",
        "settlement_value_dollars": "1.0000", "rules_primary": NWS_RULES, "rules_secondary": "",
    }  # fmt: skip
    m.update(kw)
    return m


def cli_row(valid: str, high: object, low: object, pil: str = "CLINYC") -> dict[str, Any]:
    return {"station": "KNYC", "valid": valid, "high": high, "low": low,
            "product": f"202603040630-KOKX-CDUS41-{pil}"}  # fmt: skip


ASOS_CSV = "station,valid,tmpf\nNYC,2026-03-05 05:51,38.00\nNYC,2026-03-05 18:51,44.60\nNYC,x,M\n"


@pytest.fixture
def iem_routes(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Path -> handler result for IEM requests; records every request."""
    table: dict[str, Any] = {
        "/json/cli.py": {
            "results": [cli_row("2026-03-03", 41, 33), cli_row("2026-03-05", "M", "M")]
        },
        "/cgi-bin/request/asos.py": ASOS_CSV,
    }
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = table.get(request.url.path)
        if callable(body):
            return cast(Callable[[httpx.Request], httpx.Response], body)(request)
        if isinstance(body, str):
            return httpx.Response(200, text=body)
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404)

    monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(handler))
    table["_seen"] = seen
    yield table


def no_sleep(_s: float) -> None:
    pass


def iem() -> Iem:
    return Iem(pause=0, sleep=no_sleep)


# --- mapping ------------------------------------------------------------------------


def test_every_series_maps_to_a_station_of_the_right_kind() -> None:
    for name, s in SERIES.items():
        assert s.series == name
        assert s.measure == ("low" if name.startswith("KXLOW") else "high")
        assert s.station is STATIONS[s.station.cli]
        assert re.fullmatch(r"CLI[A-Z]{3}", s.station.cli)
        assert s.station.icao == "K" + s.station.cli[3:]  # holds for every station here
        assert s.station.network.endswith("_ASOS")
        ZoneInfo(s.station.tz)  # a real zone
    assert lookup("KXHIGHTDC-26MAR03-T60") is SERIES["KXHIGHTDC"]
    assert lookup("KXBTC15M-26OCT081430") is None


def test_mapping_matches_the_tickers_doc() -> None:
    """docs/kalshi-tickers.txt lists the same ICAO station for every mapped series."""
    doc = (Path(__file__).parents[2] / "docs" / "kalshi-tickers.txt").read_text()
    listed = dict(re.findall(r"^  (KX[A-Z]+)\s+.+?\s+(K[A-Z]{3})\s+-?\d", doc, re.MULTILINE))
    for name, s in SERIES.items():
        assert listed.get(name) == s.station.icao, name


def test_every_listed_temperature_series_is_mapped() -> None:
    doc = (Path(__file__).parents[2] / "docs" / "kalshi-tickers.txt").read_text()
    listed = set(re.findall(r"^  (KX(?:HIGH|LOW)[A-Z]+)\s+.+?\s+K[A-Z]{3}\s+-?\d", doc, re.M))
    assert len(listed) == 48
    assert listed - SERIES.keys() == set()


def test_louisville_and_phoenix() -> None:
    assert SERIES["KXLOWTSDF"].station.cli == "CLISDF"  # rules say Louisville
    phx = ZoneInfo(STATIONS["CLIPHX"].tz)
    assert standard_offset(phx, date(2026, 7, 1)) == timedelta(hours=-7)


# --- climate day and rounding -------------------------------------------------------


def test_climate_day_is_local_standard_time() -> None:
    # winter: midnight EST = 05:00 UTC
    assert climate_day_window(date(2026, 1, 24), NY) == (
        datetime(2026, 1, 24, 5, tzinfo=UTC),
        datetime(2026, 1, 25, 5, tzinfo=UTC),
    )
    # summer: still midnight EST, which is 01:00 EDT
    start, end = climate_day_window(date(2026, 7, 1), NY)
    assert start == datetime(2026, 7, 1, 5, tzinfo=UTC)
    assert start.astimezone(NY).hour == 1
    assert end - start == timedelta(days=1)


@pytest.mark.parametrize(
    ("local", "day"),
    [
        ("2026-01-24T23:59:59-05:00", "2026-01-24"),  # last second of the day
        ("2026-01-25T00:00:00-05:00", "2026-01-25"),  # midnight starts the next one
        ("2026-07-02T00:30:00-04:00", "2026-07-01"),  # 00:30 EDT is 23:30 EST the day before
        ("2026-07-02T00:59:59-04:00", "2026-07-01"),
        ("2026-07-02T01:00:00-04:00", "2026-07-02"),
        ("2026-03-08T01:59:00-05:00", "2026-03-08"),  # just before clocks spring forward
        ("2026-03-08T03:00:00-04:00", "2026-03-08"),  # just after
        ("2026-03-09T00:30:00-04:00", "2026-03-08"),  # first night of DST
        ("2026-11-01T01:30:00-04:00", "2026-11-01"),  # fall back: first 01:30 (EDT)
        ("2026-11-01T01:30:00-05:00", "2026-11-01"),  # second 01:30 (EST)
        ("2026-11-01T00:30:00-04:00", "2026-10-31"),  # last DST night still shifted
    ],
)
def test_climate_day_edges(local: str, day: str) -> None:
    assert climate_day(datetime.fromisoformat(local), NY) == date.fromisoformat(day)


def test_dst_change_days_are_24_hours() -> None:
    for d in (date(2026, 3, 8), date(2026, 11, 1)):
        start, end = climate_day_window(d, NY)
        assert end - start == timedelta(hours=24)
        assert start == datetime(d.year, d.month, d.day, 5, tzinfo=UTC)


def test_phoenix_has_no_shift() -> None:
    phx = ZoneInfo("America/Phoenix")
    assert climate_day(datetime(2026, 7, 2, 0, 30, tzinfo=phx), phx) == date(2026, 7, 2)


def test_climate_day_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive"):
        climate_day(datetime(2026, 1, 1), NY)


@pytest.mark.parametrize(
    ("x", "whole"), [(16.4, 16), (16.5, 17), (16.0, 16), (-0.5, 0), (-0.6, -1), (44.6, 45)]
)
def test_whole_degrees_round_half_up(x: float, whole: int) -> None:
    assert whole_degrees(x) == whole


# --- strikes and rules --------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "lo", "hi", "value", "result"),
    [
        ("less", None, 53, 52, "yes"),  # T53 "less than 53": 52 or below
        ("less", None, 53, 53, "no"),
        ("greater", 30, None, 30, "no"),  # T30 "greater than 30": 31 or above
        ("greater", 30, None, 31, "yes"),
        ("between", 16, 17, 15, "no"),  # B16.5: 16 or 17, both ends included
        ("between", 16, 17, 16, "yes"),
        ("between", 16, 17, 17, "yes"),
        ("between", 16, 17, 18, "no"),
        ("between", 16, 17, 17.4, "yes"),  # rounds to 17
        ("between", 16, 17, 17.5, "no"),  # rounds to 18
        ("less", None, None, 50, None),  # no strike: unknown
    ],
)
def test_derive_result(
    kind: Any, lo: float | None, hi: float | None, value: float, result: str | None
) -> None:
    assert derive_result(value, kind, lo, hi) == result


def test_rules_parsing() -> None:
    assert rules_source(NWS_RULES) == "nws_cli"
    assert rules_source(TWC_RULES) == "twc"
    assert rules_source("... according to Weather Underground for KDCA ...") == "wu"
    assert rules_source("The Weather Company or the Climatological Report") == "unverified"
    assert rules_source("") == "unverified"
    assert rules_measure(NWS_RULES) == "high"
    assert rules_measure("If the minimum temperature recorded at Seattle ...") == "low"
    assert rules_measure("temperature") is None
    assert rules_stations(TWC_RULES, "Data for CLINYC can be found") == {"CLINYC"}
    assert rules_stations(NWS_RULES) == set()


def test_status_of() -> None:
    assert status_of("yes", "yes") == "agree"
    assert status_of("yes", "no") == "disagree"
    assert status_of("no", "scalar") == "kalshi_missing"
    assert status_of("no", None) == "kalshi_missing"
    assert status_of(None, "yes") == "source_missing"


# --- IEM ----------------------------------------------------------------------------


def test_cli_reading_is_cached_per_station_year(iem_routes: dict[str, Any]) -> None:
    src = iem()
    station = STATIONS["CLINYC"]
    got = src.cli(station, date(2026, 3, 3), "high")
    assert got == Reading(41, "iem_cli", "202603040630-KOKX-CDUS41-CLINYC", approximate=False)
    assert src.cli(station, date(2026, 3, 3), "low") == Reading(
        33, "iem_cli", "202603040630-KOKX-CDUS41-CLINYC", False
    )
    assert src.cli(station, date(2026, 3, 4), "high") is None  # no report that day
    assert len(iem_routes["_seen"]) == 1
    req = iem_routes["_seen"][0]
    assert req.url.params["station"] == "KNYC" and req.url.params["year"] == "2026"
    assert "@" not in req.headers["User-Agent"]  # generic, no contact details


def test_missing_cli_falls_back_to_metars_in_the_standard_time_day(
    iem_routes: dict[str, Any],
) -> None:
    got = iem().reading(STATIONS["CLINYC"], date(2026, 3, 5), "high")
    assert got is not None
    assert (got.value, got.source, got.approximate) == (45, "iem_asos_obs", True)
    asos = iem_routes["_seen"][-1]
    assert asos.url.params["sts"] == "2026-03-05T05:00Z"  # midnight EST
    assert asos.url.params["ets"] == "2026-03-06T05:00Z"
    low = iem().reading(STATIONS["CLINYC"], date(2026, 3, 5), "low")
    assert low is not None and low.value == 38


def test_cli_product_must_be_the_stations(iem_routes: dict[str, Any]) -> None:
    iem_routes["/json/cli.py"] = {"results": [cli_row("2026-03-03", 41, 33, pil="CLIPHL")]}
    with pytest.raises(SourceError, match="not a CLINYC product"):
        iem().cli(STATIONS["CLINYC"], date(2026, 3, 3), "high")


def status(code: int) -> Callable[[httpx.Request], httpx.Response]:
    def respond(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(code)

    return respond


def test_iem_backs_off_on_rate_limit(iem_routes: dict[str, Any]) -> None:
    calls: list[int] = []

    def limited(_r: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429) if len(calls) < 3 else httpx.Response(200, text=ASOS_CSV)

    iem_routes["/cgi-bin/request/asos.py"] = limited
    slept: list[float] = []
    src = Iem(pause=1.0, sleep=slept.append)
    assert src.asos_obs(STATIONS["CLINYC"], date(2026, 3, 5), "high") is not None
    assert slept == [2.0, 4.0]
    iem_routes["/cgi-bin/request/asos.py"] = status(503)
    with pytest.raises(SourceError, match="gave up"):
        Iem(pause=0, max_retries=1, sleep=no_sleep).asos_obs(
            STATIONS["CLINYC"], date(2026, 3, 5), "high"
        )
    iem_routes["/cgi-bin/request/asos.py"] = status(400)
    with pytest.raises(SourceError, match="HTTP 400"):
        iem().asos_obs(STATIONS["CLINYC"], date(2026, 3, 5), "high")


def test_parse_asos_csv_skips_missing() -> None:
    assert parse_asos_csv(ASOS_CSV) == [38.0, 44.6]
    assert parse_asos_csv("station,valid,tmpf\n") == []


# --- checking one market ------------------------------------------------------------


def reading_of(value: int | None, approximate: bool = False) -> Callable[[], Reading | None]:
    def f() -> Reading | None:
        if value is None:
            return None
        return Reading(value, "iem_asos_obs" if approximate else "iem_cli", "ref", approximate)

    return f


def test_agree() -> None:
    c = check_market("KXHIGHNY-26MAR03-B40.5", market(), reading_of(41), NOW)
    assert (c.status, c.derived_result, c.kalshi_result, c.observed_value) == (
        "agree", "yes", "yes", 41.0,
    )  # fmt: skip
    assert (c.station, c.measure, c.rules_source, c.market_date) == (
        "CLINYC", "high", "nws_cli", date(2026, 3, 3),
    )  # fmt: skip
    assert c.note == "" and not needs_review(c)


def test_disagree_and_value_mismatch_are_surfaced() -> None:
    c = check_market("KXHIGHNY-26MAR03-B40.5", market(), reading_of(42), NOW)
    assert c.status == "disagree" and c.derived_result == "no"
    assert "value mismatch: kalshi 41, observed 42" in c.note
    same_side = check_market("KXHIGHNY-26MAR03-B40.5", market(), reading_of(40), NOW)
    assert same_side.status == "agree" and needs_review(same_side)  # values differ


def test_scalar_settlement_is_kalshi_missing() -> None:
    m = market(result="scalar", expiration_value="", settlement_value_dollars="0.0000")
    c = check_market("KXHIGHNY-26MAR03-B40.5", m, reading_of(35, approximate=True), NOW)
    assert c.status == "kalshi_missing" and c.derived_result == "no"
    assert c.kalshi_settlement_value == 0.0 and c.kalshi_value is None
    assert "kalshi result 'scalar'" in c.note and "approximate source" in c.note
    assert "within 1F" not in c.note


def test_approximate_reading_near_a_strike_is_not_conclusive() -> None:
    c = check_market("KXHIGHNY-26MAR03-B40.5", market(), reading_of(42, approximate=True), NOW)
    assert "within 1F of a strike: not conclusive" in c.note


def test_source_missing() -> None:
    c = check_market("KXHIGHNY-26MAR03-B40.5", market(), reading_of(None), NOW)
    assert c.status == "source_missing" and c.observed_value is None
    assert "no independent reading" in c.note


def test_unmapped_series_and_unknown_rules() -> None:
    c = check_market("KXHIGHXYZ-26MAR03-T50", market(rules_primary="?"), reading_of(41), NOW)
    assert c.status == "source_missing" and c.station == ""
    assert "series not in nem.settle.stations" in c.note
    assert "settlement source not recognised" in c.note


def test_newer_twc_rules_are_flagged_as_cross_check_only() -> None:
    m = market(rules_primary=TWC_RULES.replace("greater than 57", "between 40-41"))
    c = check_market("KXHIGHNY-26MAR03-B40.5", m, reading_of(41), NOW)
    assert c.rules_source == "twc" and c.status == "agree"
    assert "cross-check only" in c.note and not needs_review(c)


def test_rules_naming_another_station_is_flagged() -> None:
    m = market(rules_secondary="Data for CLIPHL can be found ...")
    c = check_market("KXHIGHNY-26MAR03-B40.5", m, reading_of(41), NOW)
    assert "rules name CLIPHL, mapping says CLINYC" in c.note and needs_review(c)


def test_rules_for_the_wrong_measure_are_flagged() -> None:
    m = market(rules_primary=NWS_RULES.replace("highest", "lowest"))
    c = check_market("KXHIGHNY-26MAR03-B40.5", m, reading_of(41), NOW)
    assert "rules measure low != series high" in c.note


def test_source_error_is_a_missing_source() -> None:
    def broken() -> Reading | None:
        raise SourceError("down")

    c = check_market("KXHIGHNY-26MAR03-B40.5", market(), broken, NOW)
    assert c.status == "source_missing" and "source: down" in c.note


def test_verify_handles_kalshi_failures(iem_routes: dict[str, Any]) -> None:
    def fetch(ticker: str) -> dict[str, Any]:
        if ticker.endswith("T99"):
            raise KalshiNotFound("GET /historical/markets/x: HTTP 404")
        return market(ticker=ticker)

    checks = verify(
        ["KXHIGHNY-26MAR03-B40.5", "KXHIGHNY-26MAR03-T99", "KXHIGHNY-26MAR03-B40.5"],
        fetch, iem(), NOW,
    )  # fmt: skip
    assert [c.status for c in checks] == ["agree", "kalshi_missing"]
    assert checks[1].kalshi_result is None and "HTTP 404" in checks[1].note
    assert "2 markets checked" in render_counts(checks)


def test_kalshi_market_detail_falls_back_to_historical() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/trade-api/v2/historical/"):
            return httpx.Response(200, json={"market": market()})
        return httpx.Response(404, json={"error": "not found"})

    rec = Recording(handler)
    assert mock_client(rec).market_detail("KXHIGHNY-26MAR03-B40.5")["result"] == "yes"
    assert [r.url.path for r in rec.requests] == [
        "/trade-api/v2/markets/KXHIGHNY-26MAR03-B40.5",
        "/trade-api/v2/historical/markets/KXHIGHNY-26MAR03-B40.5",
    ]


# --- storage, report, CLI -----------------------------------------------------------


def test_store_round_trip_and_replace() -> None:
    s = Store()
    first = check_market("KXHIGHNY-26MAR03-B40.5", market(), reading_of(42), NOW)
    no_date = check_market("KXHIGHNY-BAD", market(), reading_of(41), NOW)
    s.save_settlement_checks([first, no_date])
    assert s.settlement_checks() == [first, no_date]
    again = check_market("KXHIGHNY-26MAR03-B40.5", market(), reading_of(41), NOW)
    s.save_settlement_checks([again])
    assert s.settlement_checks("agree") == [again]
    assert s.settlement_checks("disagree") == []


def test_report_and_pnl(tmp_path: Path) -> None:
    pnl_csv = tmp_path / "pnl.csv"
    pnl_csv.write_text(
        "market_ticker,realized_pnl_with_fees_dollars\n"
        "KXHIGHNY-26MAR03-B40.5,-1.25\nKXHIGHNY-26MAR03-B40.5,0.50\nKXBTC15M-X,2\n,1\nX,abc\n"
    )
    pnl = pnl_by_ticker([pnl_csv])
    assert pnl == {"KXHIGHNY-26MAR03-B40.5": -0.75, "KXBTC15M-X": 2.0}
    checks = [
        check_market("KXHIGHNY-26MAR03-B40.5", market(), reading_of(42), NOW),
        check_market("KXHIGHNY-26MAR04-B40.5", market(), reading_of(41), NOW),
    ]
    out = tmp_path / "r" / "d.csv"
    assert write_report(out, checks, pnl) == 1
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 1
    assert rows[0]["status"] == "disagree" and rows[0]["realized_pnl_usd"] == "-0.75"
    assert rows[0]["derived_result"] == "no" and rows[0]["replay_result"] == "no"
    assert rows[0]["market_date"] == "2026-03-03"


def test_tickers_file(tmp_path: Path) -> None:
    f = tmp_path / "t.txt"
    f.write_text("# mine\nKXHIGHNY-26MAR03-B40.5\n\n  KXLOWTDC-26MAR03-T40  # low\n")
    assert tickers_from_file(f) == ["KXHIGHNY-26MAR03-B40.5", "KXLOWTDC-26MAR03-T40"]


def test_cli_settle_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    iem_routes: dict[str, Any],
) -> None:
    def kalshi_handler(request: httpx.Request) -> httpx.Response:
        ticker = request.url.path.rsplit("/", 1)[-1]
        result = "no" if ticker.endswith("B42.5") else "yes"
        return httpx.Response(200, json={"market": market(ticker=ticker, result=result)})

    def client(*_a: Any, **_k: Any) -> KalshiClient:
        return mock_client(kalshi_handler)

    monkeypatch.setattr(kalshi_module, "KalshiClient", client)
    tickers = tmp_path / "t.txt"
    tickers.write_text("KXHIGHNY-26MAR03-B40.5\nKXHIGHNY-26MAR03-B42.5\nKXBTC15M-26MAR031400\n")
    db, report = tmp_path / "nem.db", tmp_path / "rep.csv"
    args = ["settle", "verify", "--tickers", str(tickers), "--db", str(db)]
    assert main([*args, "--report", str(report), "--pause", "0"]) == 0
    out = capsys.readouterr().out
    assert "2 markets checked" in out and "disagree        1" in out
    with Store(db) as s:
        assert [c.status for c in s.settlement_checks()] == ["agree", "disagree"]
    rows = list(csv.DictReader(report.open()))
    assert [r["ticker"] for r in rows] == ["KXHIGHNY-26MAR03-B42.5"]

    empty = tmp_path / "empty.db"
    assert main(["settle", "verify", "--db", str(empty), "--report", str(report)]) == 1
    assert "no KXHIGH*/KXLOW* tickers" in capsys.readouterr().out
