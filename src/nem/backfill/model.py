"""Backfilled forecast rows, the no-look-ahead selector, and the horizon rules every row
must satisfy (table `forecast_backfill`, see docs/data-provenance.md, Forecast backfill).

A backfilled row is a forecast fetched *after the fact* from an archive. Its `known_at` is
the model run time plus a conservative publication lag for that source (`SOURCES`), never
the target date and never the fetch time. Consumers must only see rows with
`known_at <= decision time`: use `known_before`, which also checks its own output.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from nem.settle.rules import climate_day_window

Field = Literal["high", "low", "cloud", "pop", "qpf_in", "qpf_cat"]
FIELDS: tuple[Field, ...] = ("high", "low", "cloud", "pop", "qpf_in", "qpf_cat")
PROVENANCE = "backfill"


@dataclass(frozen=True, slots=True)
class SourceInfo:
    name: str
    lag: timedelta  # run time -> when the product was public (assumed, conservative)
    fidelity: str  # one line for reports
    # Which forecast model this is (`ctx.forecasts` counts each model once): the same
    # model reached two ways (GFS MOS and raw GFS; a live feed and its backfill) shares one.
    model: str


# Lags are assumptions, not measurements: the archives keep the model cycle, not the minute
# the product went public. Each is set later than the product's usual availability, so a
# row can only look *staler* than it really was, never fresher.
SOURCES: dict[str, SourceInfo] = {
    s.name: s
    for s in (
        SourceInfo(
            "iem_mos_gfs", timedelta(hours=5), "GFS MOS (MAV) text as issued, via IEM", "gfs"
        ),
        SourceInfo(
            "iem_mos_nam", timedelta(hours=4), "NAM MOS (MET) text as issued, via IEM", "nam"
        ),
        SourceInfo("iem_mos_nbs", timedelta(hours=2), "NBM text (NBS) as issued, via IEM", "nbm"),
        SourceInfo(
            "openmeteo_ecmwf_ifs",
            timedelta(hours=8),
            "raw ECMWF IFS run on Open-Meteo's grid (single-runs API), no station correction",
            "ecmwf_ifs",
        ),
        SourceInfo(
            "openmeteo_gfs_global",
            timedelta(hours=6),
            "raw GFS run on Open-Meteo's grid (single-runs API, from 2026-04-02 only)",
            "gfs",
        ),
        # 12Z runs were public on Open-Meteo about 5.8 h (GEM) and 4 h (ICON) after the
        # cycle on 2026-11-21 (their meta.json `last_run_availability_time`)
        SourceInfo(
            "openmeteo_gem_global",
            timedelta(hours=8),
            "raw Canadian GEM global run on Open-Meteo's grid (single-runs API, 00/12Z,"
            " from 2026-05-28 only)",
            "gem",
        ),
        SourceInfo(
            "openmeteo_icon_global",
            timedelta(hours=6),
            "raw DWD ICON global run on Open-Meteo's grid (single-runs API, from 2026-04-02 only)",
            "icon",
        ),
    )
}


@dataclass(frozen=True, slots=True)
class BackfillRow:
    source: str  # a key of SOURCES
    station: str  # NWS CLI id, e.g. CLINYC
    target_date: date  # CLI climate day the value is about
    field: Field
    value: float
    run_time: datetime  # model initialization, UTC
    valid_from: datetime  # start of the window the value summarizes
    known_at: datetime  # run_time + SOURCES[source].lag
    run_id: str
    fetched_at: datetime
    provenance: str = PROVENANCE

    @property
    def key(self) -> str:
        """Same key shape as live forecast feeds (nws_forecast): `YYYY-MM-DD/high`."""
        return f"{self.target_date.isoformat()}/{self.field}"


class LookaheadError(AssertionError):
    """A backfilled row would be visible before it was knowable."""


def known_before(rows: Iterable[BackfillRow], at: datetime) -> list[BackfillRow]:
    """For a decision at `at` (e.g. a trade's open time): the newest row per (source,
    station, target date, field) with `known_at <= at`. Later-issued runs are excluded."""
    if at.tzinfo is None:
        raise ValueError(f"naive datetime: {at!r}")
    best: dict[tuple[str, str, date, str], BackfillRow] = {}
    for r in rows:
        if r.known_at > at:
            continue
        k = (r.source, r.station, r.target_date, r.field)
        if k not in best or r.known_at > best[k].known_at:
            best[k] = r
    out = sorted(best.values(), key=lambda r: (r.source, r.station, r.target_date, r.field))
    assert_known_before(out, at)
    return out


def assert_known_before(rows: Iterable[BackfillRow], at: datetime) -> None:
    late = [r for r in rows if r.known_at > at]
    if late:
        r = late[0]
        raise LookaheadError(
            f"{len(late)} backfilled rows known after {at.isoformat()}, e.g. {r.source}"
            f" {r.station} {r.key} known_at {r.known_at.isoformat()}"
        )


def horizon_errors(row: BackfillRow, tz: ZoneInfo) -> list[str]:
    """The horizon rules (empty list = valid). `tz` is the station's zone.

    1. provenance is `backfill` and the source is known;
    2. known_at = run_time + the source's lag (so known_at >= run_time);
    3. the run started no later than the window the value summarizes (`valid_from`),
       i.e. it is a forecast, not a nowcast of a window already under way;
    4. valid_from lies inside the target climate day or the evening before it (overnight
       lows start at 19:00 LST the day before);
    5. known_at is before the end of the target climate day (local standard midnight);
    6. known_at is not after the fetch time.
    """
    errors: list[str] = []
    info = SOURCES.get(row.source)
    if row.provenance != PROVENANCE:
        errors.append(f"provenance {row.provenance!r} is not {PROVENANCE!r}")
    if info is None:
        errors.append(f"unknown source {row.source!r}")
    elif row.known_at != row.run_time + info.lag:
        errors.append("known_at is not run_time + the source's lag")
    if row.run_time > row.valid_from:
        errors.append("run started after the window it forecasts")
    start, end = climate_day_window(row.target_date, tz)
    if not start - timedelta(hours=6) <= row.valid_from < end:
        errors.append("valid_from is outside the target climate day")
    if row.known_at >= end:
        errors.append("known_at is not before the end of the target climate day")
    if row.known_at > row.fetched_at:
        errors.append("known_at is after the fetch time")
    return errors


def validate(rows: Sequence[BackfillRow], zones: dict[str, ZoneInfo]) -> list[str]:
    """Every horizon-rule violation, one line each. `zones`: CLI id -> station zone."""
    out: list[str] = []
    for r in rows:
        tz = zones.get(r.station)
        problems = ["station has no time zone"] if tz is None else horizon_errors(r, tz)
        out.extend(f"{r.source} {r.station} {r.key} run {r.run_id}: {p}" for p in problems)
    return out
