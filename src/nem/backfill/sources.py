"""Archive adapters for the forecast backfill: fetch, cache, parse into `BackfillRow`s.

Two archives, both checked 2026-11-21 (details and terms in docs/data-provenance.md):

- IEM MOS archive (`/cgi-bin/request/mos.py`): the NWS MOS text guidance as issued,
  one row per (run, forecast hour), `runtime` = the model cycle in UTC. Models used:
  GFS (MAV, 00/06/12/18Z), NAM (MET, 00/12Z, retired 2026-11-03) and NBS (NBM text,
  01/07/13/19Z until 2026-05-05, then 00/06/12/18Z). Public domain, rate limited per IP.
- Open-Meteo single-runs API: one archived model run selected by its initialization time
  (`run=`), hourly values on Open-Meteo's grid. ECMWF IFS from 2024-03-14, GFS and ICON
  from 2026-04-02, GEM (00/12Z only) from 2026-05-28. CC BY 4.0, free tier for
  non-commercial use only.

Parsing turns a run into per-climate-day values for a station (CLI day, local standard
time, `nem.settle.rules`): `high` / `low`, `cloud` (mean daytime cover %, 07-19 LST),
`pop` (max 6-h precipitation chance %, periods overlapping 07-19 LST), `qpf_in` (inches)
or `qpf_cat` (MOS QPF category). A value is kept only when the run started before the
window it summarizes and the run covers the whole window (no partial days).

The live feeds in `nem.feeds.model_forecast` reuse the query builders and parsers here.
"""

import csv
import hashlib
import io
import json
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from statistics import fmean
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from nem.backfill.model import SOURCES, BackfillRow, Field
from nem.feeds import http
from nem.settle.rules import climate_day_window, standard_offset
from nem.settle.stations import Station

IEM_MOS = "https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py"
OPEN_METEO = "https://single-runs-api.open-meteo.com/v1/forecast"
# Generic on purpose: no personal contact details in requests to third parties.
USER_AGENT = "not-enough-markets-forecast-backfill/1.0"

# station coordinates (api.weather.gov/stations/<ICAO>, as in docs/kalshi-tickers.txt)
COORDS: dict[str, tuple[float, float]] = {
    "KATL": (33.6403, -84.4269), "KAUS": (30.183, -97.6799), "KBOS": (42.3606, -71.0106),
    "KDCA": (38.8483, -77.0342), "KDEN": (39.8466, -104.6562), "KDFW": (32.8974, -97.022),
    "KEWR": (40.6825, -74.1694), "KSAN": (32.7336, -117.1831), "KTTN": (40.2764, -74.8164),
    "KHOU": (29.6375, -95.2825), "KLAS": (36.0719, -115.1634), "KLAX": (33.9381, -118.3889),
    "KMDW": (41.7842, -87.7553), "KMIA": (25.7906, -80.3164), "KMSP": (44.8831, -93.2289),
    "KMSY": (29.9928, -90.2508), "KNYC": (40.7833, -73.9667), "KOKC": (35.3886, -97.6003),
    "KPHL": (39.8733, -75.2268), "KPHX": (33.4278, -112.0035), "KSAT": (29.5328, -98.4636),
    "KSDF": (38.1741, -85.7365), "KSEA": (47.4447, -122.3136), "KSFO": (37.6196, -122.3656),
}  # fmt: skip

MOS_MODELS = {"iem_mos_gfs": "GFS", "iem_mos_nam": "NAM", "iem_mos_nbs": "NBS"}
OPEN_METEO_MODELS = {
    "openmeteo_ecmwf_ifs": "ecmwf_ifs",
    "openmeteo_gfs_global": "gfs_global",
    "openmeteo_gem_global": "gem_global",
    "openmeteo_icon_global": "icon_global",
}
# first run each Open-Meteo model has in the single-runs archive (their docs, and for GEM /
# ICON the first run the API answered for, both checked 2026-11-21)
OPEN_METEO_FIRST_RUN = {
    "openmeteo_ecmwf_ifs": datetime(2024, 3, 14, tzinfo=UTC),
    "openmeteo_gfs_global": datetime(2026, 4, 2, tzinfo=UTC),
    "openmeteo_gem_global": datetime(2026, 5, 28, tzinfo=UTC),
    "openmeteo_icon_global": datetime(2026, 4, 2, tzinfo=UTC),
}
# hours between runs (00/06/12/18Z unless listed; GEM global runs only at 00/12Z)
OPEN_METEO_CYCLE = {"openmeteo_gem_global": timedelta(hours=12)}
DEFAULT_SOURCES = ("iem_mos_gfs", "iem_mos_nam", "iem_mos_nbs", "openmeteo_ecmwf_ifs")

# MOS sky cover category -> % (midpoint of its okta range): clear, few, scattered, broken,
# overcast
MOS_CLOUD = {"CL": 0.0, "FW": 19.0, "SC": 44.0, "BK": 75.0, "OV": 100.0}

DAY_START, DAY_END = timedelta(hours=7), timedelta(hours=19)  # daytime window, LST
SIX_HOURS = timedelta(hours=6)


def open_meteo_cycle(source: str) -> timedelta:
    return OPEN_METEO_CYCLE.get(source, SIX_HOURS)


class ArchiveError(RuntimeError):
    pass


# --- local standard time helpers ---------------------------------------------------------


def _lst(t: datetime, tz: ZoneInfo) -> datetime:
    """UTC instant -> naive local standard time (no DST shift), as the CLI day uses."""
    u = t.astimezone(UTC).replace(tzinfo=None)
    return u + standard_offset(tz, u.date())


def _utc(day: date, offset: timedelta, tz: ZoneInfo) -> datetime:
    """`offset` after local standard midnight of `day`, as a UTC instant."""
    return climate_day_window(day, tz)[0] + offset


# --- IEM MOS -----------------------------------------------------------------------------


def _num(raw: str | None) -> float | None:
    try:
        return float((raw or "").strip())
    except ValueError:
        return None


def _iem_time(raw: str) -> datetime:
    return datetime.fromisoformat(raw.strip()).replace(tzinfo=UTC)


def parse_mos_csv(
    text: str, source: str, station: Station, fetched_at: datetime
) -> list[BackfillRow]:
    """IEM `mos.py?format=csv` -> rows. Max/min: the X/N (GFS, NAM) or TXN (NBS) value at
    an evening forecast hour is that day's daytime max (07-19 LST), at a morning hour the
    overnight min ending that morning (19-08 LST)."""
    if source not in MOS_MODELS:
        raise ValueError(f"not a MOS source: {source}")
    tz = ZoneInfo(station.tz)
    runs: dict[datetime, list[dict[str, str]]] = defaultdict(list)
    for rec in csv.DictReader(io.StringIO(text)):
        if rec.get("runtime") and rec.get("ftime"):
            runs[_iem_time(rec["runtime"])].append(rec)
    out: list[BackfillRow] = []
    for run, recs in sorted(runs.items()):
        out.extend(_mos_run(source, station, tz, run, recs, fetched_at))
    return out


def _mos_run(
    source: str,
    station: Station,
    tz: ZoneInfo,
    run: datetime,
    recs: Sequence[dict[str, str]],
    fetched_at: datetime,
) -> list[BackfillRow]:
    model = MOS_MODELS[source]
    run_id = f"{model} MOS {run:%Y-%m-%dT%HZ}"
    lag = SOURCES[source].lag

    def row(day: date, field: Field, value: float, valid_from: datetime) -> BackfillRow:
        return BackfillRow(
            source=source, station=station.cli, target_date=day, field=field, value=value,
            run_time=run, valid_from=valid_from, known_at=run + lag, run_id=run_id,
            fetched_at=fetched_at,
        )  # fmt: skip

    out: list[BackfillRow] = []
    by_time = [(_iem_time(r["ftime"]), r) for r in recs]
    for ft, r in by_time:
        value = _num(r.get("txn") or r.get("n_x"))
        if value is None:
            continue
        local = _lst(ft, tz)
        day = local.date()
        if local.hour >= 12:
            field, valid_from = "high", _utc(day, DAY_START, tz)
        else:
            field, valid_from = "low", _utc(day, DAY_END - timedelta(days=1), tz)
        if run <= valid_from:
            out.append(row(day, field, value, valid_from))

    days = sorted({_lst(ft, tz).date() for ft, _ in by_time})
    for day in days:
        start, end = _utc(day, DAY_START, tz), _utc(day, DAY_END, tz)
        if run > start:
            continue
        sky = [
            (ft, v)
            for ft, r in by_time
            if start <= ft <= end
            and (v := _num(r.get("sky")) if model == "NBS" else MOS_CLOUD.get(r.get("cld", "")))
            is not None
        ]
        if (
            sky
            and min(ft for ft, _ in sky) <= start + timedelta(hours=3)
            and max(ft for ft, _ in sky) >= end - timedelta(hours=3)
        ):
            out.append(row(day, "cloud", round(fmean(v for _, v in sky), 1), start))
        # 6-h periods (ending at `ft`) that overlap the daytime window
        periods = [(ft, r) for ft, r in by_time if ft > start and ft - SIX_HOURS < end]
        pops = [(ft, v) for ft, r in periods if (v := _num(r.get("p06"))) is not None]
        if _covers([ft for ft, _ in pops], start, end):
            first = min(ft for ft, _ in pops) - SIX_HOURS
            if run <= first:
                out.append(row(day, "pop", max(v for _, v in pops), first))
        qpfs = [(ft, v) for ft, r in periods if (v := _num(r.get("q06"))) is not None]
        if _covers([ft for ft, _ in qpfs], start, end):
            first = min(ft for ft, _ in qpfs) - SIX_HOURS
            if run <= first:
                if model == "NBS":  # hundredths of an inch
                    out.append(row(day, "qpf_in", round(sum(v for _, v in qpfs) / 100, 2), first))
                else:  # MOS QPF category (0 = none, 1 = 0.01-0.09", 2 = 0.10-0.24", ...)
                    out.append(row(day, "qpf_cat", max(v for _, v in qpfs), first))
    return out


def _covers(period_ends: Sequence[datetime], start: datetime, end: datetime) -> bool:
    """Contiguous 6-h periods spanning [start, end]."""
    if not period_ends:
        return False
    ends = sorted(set(period_ends))
    if ends[0] - SIX_HOURS > start or ends[-1] < end:
        return False
    return all(b - a == SIX_HOURS for a, b in pairwise(ends))


# --- Open-Meteo single runs --------------------------------------------------------------


def parse_open_meteo(
    body: dict[str, Any], source: str, station: Station, run: datetime, fetched_at: datetime
) -> list[BackfillRow]:
    """One single-runs response (hourly, GMT, °F) -> rows for each complete climate day."""
    if body.get("error"):
        raise ArchiveError(str(body.get("reason", "Open-Meteo error")))
    if body.get("utc_offset_seconds", 0) != 0:
        raise ArchiveError("expected timezone=GMT")
    tz = ZoneInfo(station.tz)
    hourly: dict[str, list[Any]] = body.get("hourly", {})
    times = [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in hourly.get("time", [])]

    def series(name: str) -> dict[datetime, float]:
        vals = hourly.get(name) or []
        return {
            t: float(v) for t, v in zip(times, vals, strict=False) if isinstance(v, int | float)
        }

    temp, cloud = series("temperature_2m"), series("cloud_cover")
    precip, pop = series("precipitation"), series("precipitation_probability")
    run_id = f"{OPEN_METEO_MODELS[source]} {run:%Y-%m-%dT%HZ}"
    known_at = run + SOURCES[source].lag

    def row(day: date, field: Field, value: float, valid_from: datetime) -> BackfillRow:
        return BackfillRow(
            source=source, station=station.cli, target_date=day, field=field,
            value=round(value, 2), run_time=run, valid_from=valid_from, known_at=known_at,
            run_id=run_id, fetched_at=fetched_at,
        )  # fmt: skip

    def hours(start: datetime, n: int) -> list[datetime]:
        return [start + timedelta(hours=i) for i in range(n)]

    out: list[BackfillRow] = []
    for day in sorted({_lst(t, tz).date() for t in times}):
        start, _ = climate_day_window(day, tz)
        if run > start:
            continue
        day_hours = hours(start, 24)  # instantaneous values 00:00..23:00 LST
        if all(h in temp for h in day_hours):
            out.append(row(day, "high", max(temp[h] for h in day_hours), start))
            out.append(row(day, "low", min(temp[h] for h in day_hours), start))
        lit = hours(start + DAY_START, 13)  # 07:00..19:00 LST
        if all(h in cloud for h in lit):
            out.append(row(day, "cloud", fmean(cloud[h] for h in lit), lit[0]))
        if all(h in pop for h in lit):
            out.append(row(day, "pop", max(pop[h] for h in lit), lit[0]))
        wet = hours(start + DAY_START + timedelta(hours=1), 12)  # hourly sums ending 08..19
        if all(h in precip for h in wet):
            out.append(row(day, "qpf_in", sum(precip[h] for h in wet) / 25.4, lit[0]))
    return out


# --- fetching ----------------------------------------------------------------------------


class ArchiveClient:
    """GET with an optional on-disk cache of raw responses, a pause between network
    requests per host, and backoff on 429/5xx (IEM limits requests per IP)."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        pause: dict[str, float] | None = None,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 60.0,
    ) -> None:
        self._http = httpx.Client(transport=http.TRANSPORT, timeout=timeout)
        self._cache = cache_dir
        self._pause = pause or {"mesonet.agron.iastate.edu": 3.0, "open-meteo.com": 0.3}
        self._max_retries = max_retries
        self._sleep = sleep
        self.requests = 0
        self.cache_hits = 0

    def close(self) -> None:
        self._http.close()

    def _cache_path(self, url: str) -> Path | None:
        if self._cache is None:
            return None
        return self._cache / f"{hashlib.sha256(url.encode()).hexdigest()[:32]}.txt"

    def _host_pause(self, url: str) -> float:
        host = httpx.URL(url).host
        return next((p for h, p in self._pause.items() if host.endswith(h)), 1.0)

    def get_text(self, base: str, params: dict[str, str]) -> str:
        url = f"{base}?{urlencode(params)}"
        cached = self._cache_path(url)
        if cached is not None and cached.exists():
            self.cache_hits += 1
            return cached.read_text()
        problem = ""
        for attempt in range(self._max_retries + 1):
            if self.requests:
                self._sleep(self._host_pause(url) * 2**attempt)
            self.requests += 1
            try:
                r = self._http.get(url, headers={"User-Agent": USER_AGENT})
            except httpx.TransportError as e:
                problem = repr(e)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                problem = f"HTTP {r.status_code}"
                continue
            if r.is_error and r.status_code != 400:  # Open-Meteo: 400 = run not available
                raise ArchiveError(f"GET {url}: HTTP {r.status_code}")
            if cached is not None and not r.is_error:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text(r.text)
            return r.text
        raise ArchiveError(f"GET {url}: gave up after {self._max_retries + 1} tries ({problem})")


@dataclass(frozen=True, slots=True)
class Target:
    """One market's forecast needs: the station and climate day, and the latest decision
    time that matters (the last entry; None = any run before the day)."""

    station: Station
    day: date
    until: datetime | None = None


def _merge_days(days: Iterable[date], lookback: int, max_span: int = 31) -> list[tuple[date, date]]:
    """[start, end) date ranges covering [day - lookback, day + 1) for every day."""
    spans: list[tuple[date, date]] = []
    for d in sorted(set(days)):
        lo, hi = d - timedelta(days=lookback), d + timedelta(days=1)
        if spans and lo <= spans[-1][1] and (hi - spans[-1][0]).days <= max_span:
            spans[-1] = (spans[-1][0], max(spans[-1][1], hi))
        else:
            spans.append((lo, hi))
    return spans


def mos_params(source: str, station: Station, sts: datetime, ets: datetime) -> dict[str, str]:
    """IEM `mos.py` query for the runs of `source` started in [sts, ets) (shared with the
    live `iem_mos` feed, so live and backfill read the same product the same way)."""
    return {
        "station": station.icao, "model": MOS_MODELS[source], "format": "csv",
        "sts": f"{sts.astimezone(UTC):%Y-%m-%dT%H:%MZ}",
        "ets": f"{ets.astimezone(UTC):%Y-%m-%dT%H:%MZ}",
    }  # fmt: skip


def check_mos_csv(text: str, source: str, station: Station) -> None:
    if not text.startswith("runtime"):
        raise ArchiveError(f"IEM MOS {station.icao} {source}: {text[:120]!r}")


def open_meteo_params(source: str, station: Station, run: datetime) -> dict[str, str]:
    """Single-runs query for one run of `source` at the station's coordinates (shared with
    the live `open_meteo_run` feed)."""
    lat, lon = COORDS[station.icao]
    return {
        "latitude": f"{lat}", "longitude": f"{lon}", "models": OPEN_METEO_MODELS[source],
        "run": f"{run.astimezone(UTC):%Y-%m-%dT%H:%M}", "forecast_days": "5",
        "timezone": "GMT", "temperature_unit": "fahrenheit", "precipitation_unit": "mm",
        "hourly": "temperature_2m,cloud_cover,precipitation,precipitation_probability",
    }  # fmt: skip


def fetch_mos(
    client: ArchiveClient,
    source: str,
    targets: Sequence[Target],
    fetched_at: datetime,
    lookback: int = 2,
) -> list[BackfillRow]:
    """MOS runs from `lookback` days before each target day through that day."""
    by_station: dict[str, list[Target]] = defaultdict(list)
    for t in targets:
        by_station[t.station.cli].append(t)
    out: list[BackfillRow] = []
    for group in by_station.values():
        station = group[0].station
        for lo, hi in _merge_days((t.day for t in group), lookback):
            text = client.get_text(IEM_MOS, mos_params(
                source, station, datetime.combine(lo, datetime.min.time(), UTC),
                datetime.combine(hi, datetime.min.time(), UTC),
            ))  # fmt: skip
            check_mos_csv(text, source, station)
            out.extend(parse_mos_csv(text, source, station, fetched_at))
    return out


def open_meteo_runs(source: str, target: Target, lookback: int = 2) -> list[datetime]:
    """The 00/06/12/18Z runs worth fetching for a target: from `lookback` days before,
    up to the start of its climate day, and (if known) public by the last entry."""
    tz = ZoneInfo(target.station.tz)
    start, _ = climate_day_window(target.day, tz)
    first = datetime.combine(target.day - timedelta(days=lookback), datetime.min.time(), UTC)
    first = max(first, OPEN_METEO_FIRST_RUN[source])
    lag = SOURCES[source].lag
    out: list[datetime] = []
    run = first
    while run <= start:
        if target.until is None or run + lag <= target.until:
            out.append(run)
        run += open_meteo_cycle(source)
    return out


def fetch_open_meteo(
    client: ArchiveClient,
    source: str,
    targets: Sequence[Target],
    fetched_at: datetime,
    lookback: int = 2,
) -> tuple[list[BackfillRow], list[str]]:
    """(rows, problems): a run the archive doesn't have is a problem line, not a failure."""
    wanted: dict[tuple[str, datetime], Station] = {}
    for t in targets:
        for run in open_meteo_runs(source, t, lookback):
            wanted[(t.station.cli, run)] = t.station
    out: list[BackfillRow] = []
    problems: list[str] = []
    for (_, run), station in sorted(wanted.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        text = client.get_text(OPEN_METEO, open_meteo_params(source, station, run))
        try:
            body: dict[str, Any] = json.loads(text)
            out.extend(parse_open_meteo(body, source, station, run, fetched_at))
        except (ArchiveError, ValueError) as e:
            problems.append(f"{source} {station.cli} run {run:%Y-%m-%dT%HZ}: {e}")
    return out, problems
