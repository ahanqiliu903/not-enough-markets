"""Forecast backfill. Offline: synthetic archive responses in the recorded
formats (IEM MOS CSV, Open-Meteo single-runs JSON) with made-up values."""

import csv
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from nem import cli
from nem.backfill.coverage import MIN_SOURCES, Entry, coverage, entries_from_lots, write_csv
from nem.backfill.model import (
    SOURCES,
    BackfillRow,
    LookaheadError,
    assert_known_before,
    horizon_errors,
    known_before,
    validate,
)
from nem.backfill.run import backfill, targets_for, zones
from nem.backfill.sources import (
    COORDS,
    ArchiveClient,
    Target,
    open_meteo_runs,
    parse_mos_csv,
    parse_open_meteo,
)
from nem.feeds import http
from nem.settle.model import SettlementCheck
from nem.settle.stations import STATIONS
from nem.stats.provenance import BACKFILL_LABEL, cap_verdict, label
from nem.store import Store

NYC = STATIONS["CLINYC"]
ET = ZoneInfo(NYC.tz)
FETCHED = datetime(2026, 11, 21, tzinfo=UTC)
DAY = date(2026, 1, 14)  # climate day: 05:00Z Jan 14 .. 05:00Z Jan 15


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)  # type: ignore[misc]


def row(
    run: datetime,
    value: float,
    source: str = "iem_mos_gfs",
    field: str = "high",
    day: date = DAY,
    valid_from: datetime | None = None,
) -> BackfillRow:
    return BackfillRow(
        source=source, station="CLINYC", target_date=day, field=field,  # type: ignore[arg-type]
        value=value, run_time=run, valid_from=valid_from or utc(2026, 1, 14, 12),
        known_at=run + SOURCES[source].lag, run_id=f"test {run:%dT%HZ}", fetched_at=FETCHED,
    )  # fmt: skip


# --- no look-ahead -----------------------------------------------------------------------


def test_known_before_excludes_a_later_issued_forecast() -> None:
    early = row(utc(2026, 1, 13, 12), 20)  # known 17:00Z Jan 13
    late = row(utc(2026, 1, 14, 6), 17)  # known 11:00Z Jan 14
    trade = utc(2026, 1, 14, 10, 59)
    assert known_before([late, early], trade) == [early]
    # once the later run is public it replaces the earlier one
    assert known_before([early, late], utc(2026, 1, 14, 11)) == [late]
    assert known_before([early, late], utc(2026, 1, 13, 16)) == []


def test_known_before_keeps_one_row_per_source_and_field() -> None:
    rows = [
        row(utc(2026, 1, 13, 0), 19),
        row(utc(2026, 1, 13, 6), 18),
        row(utc(2026, 1, 13, 1), 18, source="iem_mos_nbs"),
        row(utc(2026, 1, 13, 0), 40, field="cloud"),
    ]
    got = known_before(rows, utc(2026, 1, 14))
    assert {(r.source, r.field, r.value) for r in got} == {
        ("iem_mos_gfs", "high", 18), ("iem_mos_nbs", "high", 18), ("iem_mos_gfs", "cloud", 40),
    }  # fmt: skip


def test_assert_known_before_fails_on_a_late_row() -> None:
    late = row(utc(2026, 1, 14, 6), 17)
    with pytest.raises(LookaheadError, match="known after"):
        assert_known_before([late], utc(2026, 1, 14, 10))
    with pytest.raises(ValueError, match="naive"):
        known_before([late], datetime(2026, 1, 14))


def test_horizon_rules() -> None:
    ok = row(utc(2026, 1, 13, 12), 20)
    assert horizon_errors(ok, ET) == []
    assert "known_at is not run_time" in horizon_errors(
        replace(ok, known_at=ok.run_time), ET
    )[0]  # fmt: skip
    after = replace(ok, run_time=utc(2026, 1, 14, 13), known_at=utc(2026, 1, 14, 18))
    assert "run started after the window it forecasts" in horizon_errors(after, ET)
    too_late = replace(
        ok, run_time=utc(2026, 1, 15, 0), known_at=utc(2026, 1, 15, 5),
        valid_from=utc(2026, 1, 15, 0),
    )  # fmt: skip
    assert "known_at is not before the end of the target climate day" in horizon_errors(
        too_late, ET
    )
    assert "known_at is after the fetch time" in horizon_errors(
        replace(ok, fetched_at=utc(2026, 1, 13, 13)), ET
    )
    assert any("provenance" in e for e in horizon_errors(replace(ok, provenance="live"), ET))
    assert any(
        "outside" in e for e in horizon_errors(replace(ok, target_date=DAY.replace(day=26)), ET)
    )
    assert validate([replace(ok, station="CLIXXX")], zones()) == [
        "iem_mos_gfs CLIXXX 2026-01-14/high run test 13T12Z: station has no time zone"
    ]


# --- IEM MOS ---------------------------------------------------------------------------

MOS_HEADER = "runtime,ftime,model,n_x,tmp,dpt,cld,wdr,wsp,p06,p12,q06,q12,station"


def mos_line(
    run: str, ftime: str, n_x: str = "", cld: str = "SC", p06: str = "", q06: str = ""
) -> str:
    return f"{run},{ftime},GFS,{n_x},30,10,{cld},270,8,{p06},,{q06},,KNYC"


def gfs_fixture(run: str = "2026-01-14 00:00:00") -> str:
    """Fake GFS MOS run: 3-hourly sky, 6-hourly PoP/QPF, X/N at 00Z (max) / 12Z (min)."""
    lines = [MOS_HEADER]
    t0 = datetime.fromisoformat(run)
    for h in range(6, 63, 3):
        ft = t0 + timedelta(hours=h)
        n_x = {datetime(2026, 1, 15, 0): "18", datetime(2026, 1, 15, 12): "9"}.get(ft, "")
        six = ft.hour % 6 == 0
        lines.append(mos_line(
            run, f"{ft:%Y-%m-%d %H:%M:%S}", n_x, "OV" if ft.hour == 18 else "SC",
            f"{ft.hour + 5}" if six else "", "1" if six else "",
        ))  # fmt: skip
    return "\n".join(lines) + "\n"


def test_parse_gfs_mos() -> None:
    rows = parse_mos_csv(gfs_fixture(), "iem_mos_gfs", NYC, FETCHED)
    by = {(r.target_date, r.field): r for r in rows}
    high = by[(DAY, "high")]  # X/N at 00Z Jan 15 = 19:00 EST Jan 14: that day's max
    assert (high.value, high.run_time, high.known_at) == (18, utc(2026, 1, 14), utc(2026, 1, 14, 5))
    assert high.valid_from == utc(2026, 1, 14, 12)  # 07:00 EST
    assert high.run_id == "GFS MOS 2026-01-14T00Z" and high.provenance == "backfill"
    low = by[(date(2026, 1, 15), "low")]  # X/N at 12Z = 07:00 EST: the overnight min
    assert low.value == 9 and low.valid_from == utc(2026, 1, 15, 0)  # 19:00 EST Jan 14
    # daytime sky 12Z..00Z: SC (44) except OV (100) at 18Z
    assert by[(DAY, "cloud")].value == pytest.approx((44 * 4 + 100) / 5, abs=0.1)
    assert by[(DAY, "pop")].value == 5 + 18  # max PoP of the 6-h periods overlapping daytime
    assert by[(DAY, "qpf_cat")].value == 1
    assert validate(rows, zones()) == []


def test_mos_run_after_the_window_starts_is_not_a_forecast() -> None:
    rows = parse_mos_csv(gfs_fixture("2026-01-14 18:00:00"), "iem_mos_gfs", NYC, FETCHED)
    assert (DAY, "high") not in {(r.target_date, r.field) for r in rows}
    assert all(r.run_time <= r.valid_from for r in rows)


def test_parse_nbs_mos_sky_and_qpf() -> None:
    header = "runtime,ftime,model,tmp,dpt,p06,q06,sky,station,txn"
    run = "2026-01-14 01:00:00"
    lines = [header]
    for h in range(5, 50, 3):
        ft = datetime(2026, 1, 14, 1) + timedelta(hours=h)
        txn = "17" if ft == datetime(2026, 1, 15, 0) else ""
        six = ft.hour % 6 == 0
        lines.append(f"{run},{ft:%Y-%m-%d %H:%M:%S},NBS,30,10,{'20' if six else ''},"
                     f"{'12' if six else ''},60,KNYC,{txn}")  # fmt: skip
    rows = parse_mos_csv("\n".join(lines), "iem_mos_nbs", NYC, FETCHED)
    by = {(r.target_date, r.field): r.value for r in rows}
    assert by[(DAY, "high")] == 17 and by[(DAY, "cloud")] == 60
    assert by[(DAY, "qpf_in")] == 0.24  # 12-18Z and 18-00Z overlap 07-19 EST: 2 x 0.12"
    assert all(r.known_at == r.run_time + timedelta(hours=2) for r in rows)


# --- Open-Meteo --------------------------------------------------------------------------


def om_body(run: datetime, hours: int = 96) -> dict[str, Any]:
    times = [run + timedelta(hours=i) for i in range(hours)]
    return {
        "utc_offset_seconds": 0,
        "hourly": {
            "time": [f"{t:%Y-%m-%dT%H:%M}" for t in times],
            "temperature_2m": [10.0 + (t.hour % 24) / 2 for t in times],
            "cloud_cover": [50 for _ in times],
            "precipitation": [0.254 for _ in times],
            "precipitation_probability": [None for _ in times],
        },
    }


def test_parse_open_meteo_whole_days_only() -> None:
    run = utc(2026, 1, 13, 0)
    rows = parse_open_meteo(om_body(run), "openmeteo_ecmwf_ifs", NYC, run, FETCHED)
    by = {(r.target_date, r.field): r for r in rows}
    high = by[(DAY, "high")]
    assert high.value == 10 + 23 / 2 and by[(DAY, "low")].value == 10
    assert high.known_at == run + timedelta(hours=8) and high.valid_from == utc(2026, 1, 14, 5)
    assert by[(DAY, "cloud")].value == 50
    assert by[(DAY, "qpf_in")].value == pytest.approx(0.12)
    assert (DAY, "pop") not in by  # IFS has no precipitation probability
    assert (date(2026, 1, 26), "high") not in by  # run ends before that day does
    assert validate(rows, zones()) == []
    later = utc(2026, 1, 14, 6)  # after the climate day began: no value for it
    assert (DAY, "high") not in {
        (r.target_date, r.field)
        for r in parse_open_meteo(om_body(later), "openmeteo_ecmwf_ifs", NYC, later, FETCHED)
    }


def test_open_meteo_runs_respect_entry_time_and_archive_start() -> None:
    t = Target(NYC, DAY, until=utc(2026, 1, 13, 20))
    runs = open_meteo_runs("openmeteo_ecmwf_ifs", t)
    assert runs[0] == utc(2026, 1, 12) and runs[-1] == utc(2026, 1, 13, 12)  # 12Z + 8h = 20Z
    assert open_meteo_runs("openmeteo_gfs_global", t) == []  # archive starts 2026-04-02
    assert open_meteo_runs("openmeteo_ecmwf_ifs", Target(NYC, DAY))[-1] == utc(2026, 1, 14)


# --- fetching (mocked) -------------------------------------------------------------------


@pytest.fixture
def archive(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "mesonet.agron.iastate.edu":
            return httpx.Response(200, text=gfs_fixture())
        run = datetime.fromisoformat(request.url.params["run"]).replace(tzinfo=UTC)
        if run.hour == 18:
            return httpx.Response(400, json={"error": True, "reason": "not available"})
        return httpx.Response(200, json=om_body(run))

    monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(handler))
    yield seen


def test_backfill_fetches_validates_and_caches(
    archive: list[httpx.Request], tmp_path: Path
) -> None:
    targets = [Target(NYC, DAY, until=utc(2026, 1, 14, 18, 30))]
    client = ArchiveClient(tmp_path / "cache", sleep=lambda _: None)
    res = backfill(client, targets, ["iem_mos_gfs", "openmeteo_ecmwf_ifs"], FETCHED)
    assert res.invalid == [] and len(res.problems) == 2  # the two 18Z runs
    assert {r.source for r in res.rows} == {"iem_mos_gfs", "openmeteo_ecmwf_ifs"}
    assert all("User-Agent" in r.headers and "@" not in r.headers["User-Agent"] for r in archive)
    n = len(archive)
    backfill(client, targets, ["iem_mos_gfs", "openmeteo_ecmwf_ifs"], FETCHED)
    assert len(archive) == n + 2  # only the uncached 400s are asked again
    client.close()


def test_targets_merge_markets_of_one_station_day() -> None:
    e = [
        Entry("KXHIGHNY-26JAN14-B16.5", "no", utc(2026, 1, 14, 19)),
        Entry("KXHIGHNY-26JAN14-B18.5", "no", utc(2026, 1, 14, 20)),
    ]
    got = targets_for(["KXHIGHNY-26JAN14-B16.5", "KXHIGHNY-26JAN14-B18.5", "KXBTC-X"], e)
    assert got == [Target(NYC, DAY, until=utc(2026, 1, 14, 20))]


# --- store, coverage, CLI ----------------------------------------------------------------


def test_store_round_trip_and_provenance_check() -> None:
    r = row(utc(2026, 1, 13, 12), 20)
    with Store() as s:
        s.save_backfill([r, r])
        assert s.backfill_rows("CLINYC", DAY) == [r]
        assert s.backfill_rows("CLIDCA") == []
        with pytest.raises(sqlite3.IntegrityError):
            s.save_backfill([replace(r, provenance="live")])


def check(ticker: str) -> SettlementCheck:
    return SettlementCheck(
        ticker=ticker, series="KXHIGHNY", market_date=DAY, measure="high", station="CLINYC",
        rules_source="nws_cli", strike_type="between", floor_strike=16, cap_strike=17,
        observed_value=17, source="iem_cli", source_ref="x", derived_result="yes",
        kalshi_result="yes", kalshi_value=17, kalshi_settlement_value=1, status="agree",
        note="", checked_at=FETCHED,
    )  # fmt: skip


def test_coverage_counts_only_sources_known_before_entry(tmp_path: Path) -> None:
    t = "KXHIGHNY-26JAN14-B16.5"
    rows = [
        row(utc(2026, 1, 13, 12), 18),
        row(utc(2026, 1, 13, 13), 17, source="iem_mos_nbs"),
        row(utc(2026, 1, 13, 0), 17, source="openmeteo_ecmwf_ifs"),
        row(utc(2026, 1, 14, 6), 30, source="iem_mos_nam"),  # known 10:00Z: after the entry
    ]
    entries = entries_from_lots([
        (t, "no", utc(2026, 1, 14, 9, 0, 5)), (t, "no", utc(2026, 1, 14, 9, 0, 40)),
    ])  # fmt: skip
    assert len(entries) == 1
    got = coverage(entries, {("CLINYC", DAY): rows}, {t: check(t)})
    c = got[0]
    assert c.n_sources == MIN_SOURCES and "iem_mos_nam" not in c.highs
    assert (c.spread, c.median, c.implied_result()) == (1, 17, "yes")
    out = tmp_path / "cov.csv"
    write_csv(out, got, sorted(SOURCES))
    rec = next(csv.DictReader(out.open()))
    assert rec["provenance"] == BACKFILL_LABEL and rec["yes_if"] == "16-17"
    assert rec["lt3_sources"] == "no" and rec["iem_mos_nam_value"] == ""


def test_cli_coverage_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "nem.db"
    with Store(db) as s:
        s.save_backfill([row(utc(2026, 1, 13, 12), 18)])
    assert (
        cli.main(["backfill", "coverage", "--db", str(db), "--report", str(tmp_path / "c.csv")])
        == 1
    )
    assert "no manual entries" in capsys.readouterr().out


def test_coords_match_the_ticker_doc() -> None:
    doc = (Path(__file__).parents[2] / "docs" / "kalshi-tickers.txt").read_text()
    for icao, (lat, lon) in COORDS.items():
        assert re.search(rf"\b{icao}\s+{re.escape(str(lat))}\s+{re.escape(str(lon))}\b", doc), icao
    assert {s.icao for s in STATIONS.values()} <= set(COORDS)


# --- verdict cap ---------------------------------------------------------------------------


def test_cap_verdict() -> None:
    assert cap_verdict("edge", uses_backfill=True) == "undecided"
    assert cap_verdict("edge", uses_backfill=True, forward_verdict="undecided") == "undecided"
    assert cap_verdict("edge", uses_backfill=True, forward_verdict="edge") == "edge"
    assert cap_verdict("no_edge", uses_backfill=True) == "no_edge"  # rejecting is allowed
    assert cap_verdict("edge", uses_backfill=False) == "edge"
    with pytest.raises(ValueError):
        cap_verdict("great", uses_backfill=True)
    assert label("NYC", uses_backfill=True) == f"[{BACKFILL_LABEL}] NYC"
    assert label("NYC", uses_backfill=False) == "NYC"
