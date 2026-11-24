"""Live model forecast feeds (`iem_mos`, `open_meteo_run`): offline, mocked HTTP."""

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from nem.backfill.model import SOURCES, BackfillRow
from nem.backfill.sources import Target, open_meteo_runs, parse_mos_csv, parse_open_meteo
from nem.core.config import PluginSpec
from nem.core.registry import REGISTRY, PluginError
from nem.core.types import FeedValue
from nem.feeds import http
from nem.feeds.base import Feed
from nem.feeds.model_forecast import latest_releasable
from nem.settle.stations import STATIONS
from nem.store import Store

from engine_helpers import context, portfolio

NYC = STATIONS["CLINYC"]
DAY = date(2026, 11, 22)  # NYC climate day = 05Z Nov 22 .. 05Z Nov 23 (EST)
H = timedelta(hours=1)


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)  # type: ignore[misc]


Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """`state["handler"]` answers requests; `state["seen"]` / `state["slept"]` record."""
    state: dict[str, Any] = {"seen": [], "slept": []}

    def handle(request: httpx.Request) -> httpx.Response:
        state["seen"].append(request)
        handler: Handler = state["handler"]
        return handler(request)

    monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(handle))

    def sleep(seconds: float) -> None:
        state["slept"].append(seconds)

    monkeypatch.setattr(http, "SLEEP", sleep)
    monkeypatch.setattr(http, "_last_request", {})
    monkeypatch.delenv(http.USER_AGENT_ENV, raising=False)
    yield state


def serve(server: dict[str, Any], handler: Handler) -> None:
    server["handler"] = handler


def feed(**spec: object) -> Feed:
    return REGISTRY.build("feed", PluginSpec.model_validate(spec), Feed)


# --- synthetic products ------------------------------------------------------------------

MOS_HEADER = "runtime,ftime,model,n_x,tmp,cld,p06,q06,station"


def mos_csv(runs: dict[datetime, float]) -> str:
    """GFS MOS runs (run time -> Nov 22 max) at 12/15/18/21/00Z around Nov 22's daytime."""
    lines = [MOS_HEADER]
    for run, high in runs.items():
        for ft, n_x, cld, p06, q06 in (
            (utc(2026, 11, 22, 12), "41", "SC", "", ""),  # 07 LST: overnight min
            (utc(2026, 11, 22, 15), "", "BK", "", ""),
            (utc(2026, 11, 22, 18), "", "OV", "20", "0"),
            (utc(2026, 11, 22, 21), "", "OV", "", ""),
            (utc(2026, 11, 23, 0), f"{high:g}", "BK", "60", "2"),  # 19 LST: daytime max
        ):
            lines.append(
                f"{run:%Y-%m-%d %H:%M:%S},{ft:%Y-%m-%d %H:%M:%S},GFS,{n_x},50,{cld},"
                f"{p06},{q06},KNYC"
            )
    return "\n".join(lines) + "\n"


def open_meteo_body(run: datetime, hours: int = 72) -> dict[str, Any]:
    times = [run + i * H for i in range(hours)]
    return {
        "utc_offset_seconds": 0,
        "hourly": {
            "time": [f"{t:%Y-%m-%dT%H:%M}" for t in times],
            "temperature_2m": [40.0 + i for i in range(hours)],
            "cloud_cover": [50.0] * hours,
            "precipitation": [0.1] * hours,
            "precipitation_probability": [None] * hours,  # deterministic model: no PoP
        },
    }


# --- schedule ----------------------------------------------------------------------------


def test_latest_releasable() -> None:
    six, twelve = timedelta(hours=6), timedelta(hours=12)
    assert latest_releasable(utc(2026, 11, 22, 15), 5 * H, six) == utc(2026, 11, 22, 6)
    assert latest_releasable(utc(2026, 11, 22, 17), 5 * H, six) == utc(2026, 11, 22, 12)
    assert latest_releasable(utc(2026, 11, 22, 3), 5 * H, six) == utc(2026, 11, 21, 18)
    assert latest_releasable(utc(2026, 11, 22, 19, 59), 8 * H, twelve) == utc(2026, 11, 22, 0)
    assert latest_releasable(utc(2026, 11, 22, 20), 8 * H, twelve) == utc(2026, 11, 22, 12)


def test_backfill_gem_runs_follow_its_12h_cycle() -> None:
    runs = open_meteo_runs("openmeteo_gem_global", Target(NYC, DAY), lookback=1)
    assert runs == [utc(2026, 11, 21, 0), utc(2026, 11, 21, 12), utc(2026, 11, 22, 0)]


# --- iem_mos -----------------------------------------------------------------------------


def test_iem_mos_emits_only_runs_past_their_release_lag(
    server: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    run6, run12 = utc(2026, 11, 22, 6), utc(2026, 11, 22, 12)
    text = mos_csv({run6: 60, run12: 62})
    serve(server, lambda r: httpx.Response(200, text=text))
    monkeypatch.setenv(http.USER_AGENT_ENV, "nem test (contact@example.org)")
    f = feed(type="iem_mos", station="CLINYC", model="GFS")

    now = utc(2026, 11, 22, 15)  # 12Z run is on the server but not "public" until 17Z
    obs = {k: (v, t) for k, v, t in f.fetch(now)}
    known6 = run6 + SOURCES["iem_mos_gfs"].lag
    assert known6 == utc(2026, 11, 22, 11)
    assert obs["2026-11-22/high"] == (60.0, known6)
    assert obs["2026-11-22/cloud"] == (78.8, known6)  # mean of SC BK OV OV BK, 07-19 LST
    assert obs["2026-11-22/pop"] == (60.0, known6)
    assert obs["2026-11-22/qpf_cat"] == (2.0, known6)
    assert "2026-11-22/low" not in obs  # the overnight min started before the 06Z run
    assert all(t <= now for _, t in obs.values())

    (req,) = server["seen"]
    assert req.url.path == "/cgi-bin/request/mos.py"
    assert req.url.params["station"] == "KNYC" and req.url.params["model"] == "GFS"
    assert req.url.params["sts"] == "2026-11-21T09:00Z"  # lookback 30h
    assert req.headers["User-Agent"] == "nem test (contact@example.org)"

    assert f.fetch(utc(2026, 11, 22, 16)) == []  # nothing new is releasable: no request
    assert len(server["seen"]) == 1
    later = {k: (v, t) for k, v, t in f.fetch(utc(2026, 11, 22, 17))}
    assert later["2026-11-22/high"] == (62.0, utc(2026, 11, 22, 17))
    assert len(server["seen"]) == 2


def test_iem_mos_matches_backfill_parser(server: dict[str, Any]) -> None:
    run = utc(2026, 11, 22, 6)
    text = mos_csv({run: 60})
    serve(server, lambda r: httpx.Response(200, text=text))
    now = utc(2026, 11, 22, 20)
    live = sorted(feed(type="iem_mos", station="CLINYC", model="GFS").fetch(now))
    rows = parse_mos_csv(text, "iem_mos_gfs", NYC, now)
    assert live == sorted((r.key, r.value, r.known_at) for r in rows)


def test_iem_mos_retries_then_gives_up(server: dict[str, Any]) -> None:
    calls: list[int] = []

    def flaky(r: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, text=mos_csv({utc(2026, 11, 22, 6): 60}))

    serve(server, flaky)
    f = feed(type="iem_mos", station="CLINYC", model="GFS", user_agent="me (x@example.org)")
    assert f.fetch(utc(2026, 11, 22, 15))
    assert len(calls) == 2 and 7.0 in server["slept"]

    serve(server, lambda r: httpx.Response(503))
    g = feed(type="iem_mos", station="CLINYC", model="NBS")
    with pytest.raises(http.FeedHTTPError):
        g.fetch(utc(2026, 11, 22, 15))


def test_iem_mos_rejects_non_csv(server: dict[str, Any]) -> None:
    serve(server, lambda r: httpx.Response(200, text="ERROR: station unknown"))
    with pytest.raises(RuntimeError):
        feed(type="iem_mos", station="CLINYC", model="GFS").fetch(utc(2026, 11, 22, 15))


def test_requests_to_one_host_are_spaced(
    server: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(http, "MONOTONIC", lambda: 100.0)  # no time passes between calls
    serve(server, lambda r: httpx.Response(200, text=mos_csv({})))
    for model in ("GFS", "NBS"):
        feed(type="iem_mos", station="CLINYC", model=model).fetch(utc(2026, 11, 22, 15))
    assert server["slept"] == [http.HOST_PAUSE["mesonet.agron.iastate.edu"]]


@pytest.mark.parametrize(
    "spec",
    [
        {"station": "CLINOPE", "model": "GFS"},
        {"station": "CLINYC", "model": "NAM"},  # retired 2026-11-03
        {"station": "CLINYC", "model": "GFS", "lookback": "2h"},
    ],
)
def test_iem_mos_params(spec: dict[str, object]) -> None:
    with pytest.raises((PluginError, ValidationError)):
        feed(type="iem_mos", **spec)


# --- open_meteo_run ----------------------------------------------------------------------


def test_open_meteo_requires_terms_acknowledgment() -> None:
    with pytest.raises((PluginError, ValidationError)):
        feed(type="open_meteo_run", station="CLINYC", model="ecmwf_ifs")
    with pytest.raises((PluginError, ValidationError)):
        feed(type="open_meteo_run", station="CLINYC", model="ecmwf_ifs", terms="commercial")


def test_open_meteo_reads_each_releasable_run_once(server: dict[str, Any]) -> None:
    have = utc(2026, 11, 22, 0)

    def handler(r: httpx.Request) -> httpx.Response:
        run = datetime.fromisoformat(r.url.params["run"]).replace(tzinfo=UTC)
        if run != have:
            return httpx.Response(400, json={"error": True, "reason": "run not available"})
        return httpx.Response(200, json=open_meteo_body(run))

    serve(server, handler)
    f = feed(type="open_meteo_run", station="CLINYC", model="ecmwf_ifs", terms="non-commercial")
    now = utc(2026, 11, 22, 15)  # 06Z + 8h = 14Z is the newest releasable run
    obs = {k: (v, t) for k, v, t in f.fetch(now)}
    runs = [r.url.params["run"] for r in server["seen"]]
    assert runs == ["2026-11-22T06:00", "2026-11-22T00:00", "2026-11-21T18:00", "2026-11-21T12:00"]
    assert all(r.url.params["models"] == "ecmwf_ifs" for r in server["seen"])
    known = utc(2026, 11, 22, 8)
    # climate day Nov 22 = hours 5..28 of the run: 45F .. 68F
    assert obs["2026-11-22/high"] == (68.0, known)
    assert obs["2026-11-22/low"] == (45.0, known)
    assert obs["2026-11-22/cloud"] == (50.0, known)
    assert "2026-11-22/pop" not in obs

    f.fetch(now)  # 00Z already read: only the three missing runs are asked for again
    assert len(server["seen"]) == 7
    assert "2026-11-22T00:00" not in [r.url.params["run"] for r in server["seen"][4:]]


def test_open_meteo_matches_backfill_parser(server: dict[str, Any]) -> None:
    run = utc(2026, 11, 22, 0)
    serve(
        server,
        lambda r: (
            httpx.Response(200, json=open_meteo_body(run))
            if r.url.params["run"] == "2026-11-22T00:00"
            else httpx.Response(400)
        ),
    )
    now = utc(2026, 11, 22, 21)
    live = feed(type="open_meteo_run", station="CLINYC", model="gem_global", terms="non-commercial")
    got = sorted(live.fetch(now))
    rows = parse_open_meteo(open_meteo_body(run), "openmeteo_gem_global", NYC, run, now)
    assert got == sorted((r.key, r.value, r.known_at) for r in rows)
    # GEM runs only at 00/12Z
    assert {r.url.params["run"][11:] for r in server["seen"]} <= {"00:00", "12:00"}


# --- through ctx.forecasts ---------------------------------------------------------------


def test_three_live_feeds_reach_ctx_forecasts(server: dict[str, Any]) -> None:
    feeds: list[dict[str, object]] = [
        {"name": "gfs_mos", "type": "iem_mos", "station": "CLINYC", "model": "GFS",
         "every": "30m", "max_age": "24h"},
        {"name": "ecmwf", "type": "open_meteo_run", "station": "CLINYC",
         "model": "ecmwf_ifs", "terms": "non-commercial", "every": "30m", "max_age": "24h"},
        {"name": "gem", "type": "open_meteo_run", "station": "CLINYC",
         "model": "gem_global", "terms": "non-commercial", "every": "30m", "max_age": "24h"},
    ]  # fmt: skip
    run0 = utc(2026, 11, 22, 0)

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.host == "mesonet.agron.iastate.edu":
            return httpx.Response(200, text=mos_csv({utc(2026, 11, 22, 6): 60}))
        if r.url.params["run"] == "2026-11-22T00:00":
            return httpx.Response(200, json=open_meteo_body(run0))
        return httpx.Response(400)

    serve(server, handler)
    now = utc(2026, 11, 22, 16)
    store = Store()
    for spec in feeds:
        name = str(spec["name"])
        obs = feed(**{k: v for k, v in spec.items() if k not in ("name", "every", "max_age")})
        values = [FeedValue(name, k, v, t) for k, v, t in obs.fetch(now)]
        store.record_feed_values(values, recorded_at=now)
    ctx = context(portfolio(feeds=feeds), store, now)
    got = ctx.forecasts(DAY, "high", feeds=["gfs_mos", "ecmwf", "gem"])
    assert {(f.source, f.value, f.provenance) for f in got} == {
        ("gfs_mos", 60.0, "feed"), ("ecmwf", 68.0, "feed"), ("gem", 68.0, "feed"),
    }  # fmt: skip
    assert all(f.known_at <= now for f in got)


def test_example_builds_with_open_meteo_enabled(tmp_path: Path) -> None:
    from nem.core.config import load_portfolio
    from nem.engine.runtime import build_feeds, build_strategies

    text = (
        Path(__file__).resolve().parents[2] / "examples/weather/forecast_gates.yaml"
    ).read_text()
    text = text.replace("  # - {name:", "  - {name:").replace("  #    station:", "     station:")
    text = text.replace(
        "feeds: [gfs_mos_nyc, nbm_nyc, nws_nyc]",
        "feeds: [gfs_mos_nyc, nbm_nyc, nws_nyc, ecmwf_nyc, gem_nyc]",
    )
    path = tmp_path / "forecast_gates.yaml"
    path.write_text(text)
    p = load_portfolio(path)
    assert {f.type for f in p.feeds} == {"iem_mos", "nws_forecast", "open_meteo_run"}
    assert len(p.feeds) == 5
    build_strategies([p])
    assert len(build_feeds([p])) == 5


# --- one value per model in ctx.forecasts ------------------------------------------------

MODEL_FEEDS: list[dict[str, object]] = [
    {"name": "gfs_mos_nyc", "type": "iem_mos", "model": "GFS", "station": "CLINYC",
     "max_age": "24h"},
    {"name": "nbm_nyc", "type": "iem_mos", "model": "NBS", "station": "CLINYC",
     "max_age": "24h"},
    {"name": "ecmwf_nyc", "type": "open_meteo_run", "model": "ecmwf_ifs",
     "terms": "non-commercial", "station": "CLINYC", "max_age": "24h"},
    {"name": "nws_nyc", "type": "nws_forecast", "lat": 40.78, "lon": -73.97, "max_age": "12h"},
]  # fmt: skip
ALL_FEEDS = ["gfs_mos_nyc", "nbm_nyc", "ecmwf_nyc", "nws_nyc"]
ALL_BACKFILL = ["iem_mos_gfs", "iem_mos_nbs", "openmeteo_ecmwf_ifs", "openmeteo_gfs_global"]
NOW_D = utc(2026, 11, 22, 16)


def brow(source: str, value: float, run: datetime) -> BackfillRow:
    return BackfillRow(
        source=source, station="CLINYC", target_date=DAY, field="high", value=value,
        run_time=run, valid_from=utc(2026, 11, 22, 12), known_at=run + SOURCES[source].lag,
        run_id=f"{source} {run:%dT%HZ}", fetched_at=utc(2026, 12, 1),
    )  # fmt: skip


def forecasts(feed_values: dict[str, tuple[float, datetime]], rows: list[BackfillRow]) -> Any:
    store = Store()
    for name, (v, at) in feed_values.items():
        store.record_feed_values([FeedValue(name, f"{DAY.isoformat()}/high", v, at)], NOW_D)
    store.save_backfill(rows)
    ctx = context(portfolio(feeds=MODEL_FEEDS), store, NOW_D)
    got = ctx.forecasts(
        DAY, "high", feeds=ALL_FEEDS, station="CLINYC", backfill_sources=ALL_BACKFILL
    )
    return {(f.source, f.value, f.provenance, f.model) for f in got}


def test_feed_and_backfill_of_one_model_count_once_feed_preferred() -> None:
    rows = [
        brow("iem_mos_gfs", 50, utc(2026, 11, 22, 6)),  # newer than the feed's: still loses
        brow("iem_mos_nbs", 51, utc(2026, 11, 22, 6)),
        brow("openmeteo_ecmwf_ifs", 52, utc(2026, 11, 22, 0)),
    ]
    feeds = {
        "gfs_mos_nyc": (60.0, utc(2026, 11, 22, 5)),
        "nbm_nyc": (61.0, utc(2026, 11, 22, 8)),
        "ecmwf_nyc": (62.0, utc(2026, 11, 22, 8)),
        "nws_nyc": (63.0, utc(2026, 11, 22, 9)),
    }
    assert forecasts(feeds, rows) == {
        ("gfs_mos_nyc", 60.0, "feed", "gfs"), ("nbm_nyc", 61.0, "feed", "nbm"),
        ("ecmwf_nyc", 62.0, "feed", "ecmwf_ifs"), ("nws_nyc", 63.0, "feed", "feed:nws_nyc"),
    }  # fmt: skip


def test_backfill_fills_in_for_a_model_with_no_feed_value() -> None:
    rows = [
        brow("iem_mos_gfs", 50, utc(2026, 11, 22, 6)),
        brow("iem_mos_nbs", 51, utc(2026, 11, 22, 6)),
    ]
    feeds = {
        "nbm_nyc": (61.0, utc(2026, 11, 22, 8)),
        "gfs_mos_nyc": (60.0, NOW_D - timedelta(hours=25)),  # older than max_age: no value
        "ecmwf_nyc": (62.0, NOW_D + H),  # not known yet at now: invisible
    }
    assert forecasts(feeds, rows) == {
        ("iem_mos_gfs", 50.0, "backfill", "gfs"), ("nbm_nyc", 61.0, "feed", "nbm"),
    }  # fmt: skip


def test_two_routes_to_one_model_in_backfill_keep_the_newest() -> None:
    rows = [
        brow("iem_mos_gfs", 50, utc(2026, 11, 22, 0)),  # known 05Z
        brow("openmeteo_gfs_global", 53, utc(2026, 11, 22, 0)),  # known 06Z
    ]
    assert forecasts({}, rows) == {("openmeteo_gfs_global", 53.0, "backfill", "gfs")}
