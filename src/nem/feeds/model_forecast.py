"""Live forecast feeds from numerical weather models, for the forecast gates
(`source_agreement`, `bracket_distance`, `sky_conditions`; `nem.gates.forecast`).

Both feeds fetch the same products as the forecast backfill
(`nem.backfill.sources`) and parse them with the same code, so a live value and a backfilled
one mean the same thing: same per-climate-day windows, units and `known_at` rule. Sources,
terms and independence: docs/data-provenance.md, "Live model forecast feeds".

- `iem_mos`: NWS MOS text guidance for one station as issued, via the Iowa Environmental
  Mesonet (public domain). `model: GFS` (GFS MOS, MAV) or `NBS` (National Blend of Models
  text, which blends GFS, ECMWF, GEM and others, so it is not independent of them).
- `open_meteo_run`: one raw model run at the station's grid point from Open-Meteo's
  single-runs API: `ecmwf_ifs`, `gem_global` (Canada), `icon_global` (DWD) or
  `gfs_global`. Open-Meteo's free API is for **non-commercial use only**, so this feed
  requires `terms: non-commercial` in the config as an explicit acknowledgment.

Keys `YYYY-MM-DD/{field}` (CLI climate day, local standard time), fields as in the
backfill: `high` / `low` (F), `cloud` (mean daytime cover %, 07-19 LST), `pop` (max 6-h
precipitation chance %; MOS only), `qpf_in` (inches) or `qpf_cat` (GFS MOS category).

`known_at` = model run time + the source's assumed release lag (`nem.backfill.SOURCES`),
never later than now: a run is emitted only once that time has passed, even if the server
already has it, so live values are never fresher than a backfill of the same run would be.
A value is emitted only if the run started before the window it summarizes (no nowcasts)
and covers the whole window. Each fetch emits every qualifying run of the last
`lookback` (default 30 h), so a feed started mid-day still has the morning runs that
forecast today's high; the store ignores repeats. Fetches that would find nothing new
(the newest releasable run was already read) make no request.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import model_validator

from nem.backfill.model import SOURCES, BackfillRow, horizon_errors
from nem.backfill.sources import (
    COORDS,
    IEM_MOS,
    OPEN_METEO,
    OPEN_METEO_FIRST_RUN,
    ArchiveError,
    check_mos_csv,
    mos_params,
    open_meteo_cycle,
    open_meteo_params,
    parse_mos_csv,
    parse_open_meteo,
)
from nem.core.config import Duration
from nem.core.registry import PluginParams, register
from nem.feeds import http
from nem.feeds.base import Feed, Observation
from nem.settle.stations import STATIONS, Station

MOS_SOURCE = {"GFS": "iem_mos_gfs", "NBS": "iem_mos_nbs"}
MOS_CYCLE = timedelta(hours=6)  # GFS MOS and NBS: 00/06/12/18Z (NBS since 2026-05-05)
OPEN_METEO_SOURCE = {
    "ecmwf_ifs": "openmeteo_ecmwf_ifs",
    "gem_global": "openmeteo_gem_global",
    "icon_global": "openmeteo_icon_global",
    "gfs_global": "openmeteo_gfs_global",
}


def latest_releasable(now: datetime, lag: timedelta, cycle: timedelta) -> datetime:
    """The newest run on a `cycle`-hour schedule (from 00Z) whose run time + `lag` <= now."""
    t = (now - lag).astimezone(UTC)
    midnight = t.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + cycle * ((t - midnight) // cycle)


def observations(rows: Sequence[BackfillRow], station: Station, now: datetime) -> list[Observation]:
    """Parsed rows -> (key, value, known_at), keeping only rows knowable by now that pass
    the backfill's horizon rules."""
    tz = ZoneInfo(station.tz)
    return [
        (r.key, r.value, r.known_at)
        for r in rows
        if r.known_at <= now and not horizon_errors(r, tz)
    ]


class ModelFeedParams(PluginParams):
    station: str  # NWS CLI id, e.g. CLINYC (nem.settle.stations)
    # null = $NEM_USER_AGENT, else a generic one; put your contact in the env, not here
    user_agent: str | None = None
    lookback: Duration = timedelta(hours=30)

    @model_validator(mode="after")
    def _station(self) -> Self:
        s = STATIONS.get(self.station)
        if s is None:
            raise ValueError(f"unknown station {self.station!r}; use a CLI id, e.g. CLINYC")
        if s.icao not in COORDS:
            raise ValueError(f"no coordinates for {s.icao} in nem.backfill.sources.COORDS")
        if not timedelta(hours=6) <= self.lookback <= timedelta(hours=72):
            raise ValueError("lookback must be between 6h and 72h")
        return self


@register("feed", "iem_mos")
class IemMos(Feed):
    class Params(ModelFeedParams):
        model: Literal["GFS", "NBS"]

    @classmethod
    def forecast_model(cls, params: PluginParams) -> str | None:
        assert isinstance(params, cls.Params)
        return SOURCES[MOS_SOURCE[params.model]].model

    def __init__(self, params: Params) -> None:
        self.p = params
        self.source = MOS_SOURCE[params.model]
        self.station = STATIONS[params.station]
        self._newest: datetime | None = None  # newest run already read

    def fetch(self, now: datetime) -> Sequence[Observation]:
        lag = SOURCES[self.source].lag
        if self._newest is not None and self._newest >= latest_releasable(now, lag, MOS_CYCLE):
            return []
        r = http.get(
            IEM_MOS,
            mos_params(self.source, self.station, now - self.p.lookback, now + timedelta(hours=1)),
            http.user_agent(self.p.user_agent),
        )
        r.raise_for_status()
        check_mos_csv(r.text, self.source, self.station)
        rows = parse_mos_csv(r.text, self.source, self.station, now)
        runs = [x.run_time for x in rows if x.known_at <= now]
        if runs:
            self._newest = max(runs)
        return observations(rows, self.station, now)


@register("feed", "open_meteo_run")
class OpenMeteoRun(Feed):
    class Params(ModelFeedParams):
        model: Literal["ecmwf_ifs", "gem_global", "icon_global", "gfs_global"]
        # Open-Meteo's free API is non-commercial only (open-meteo.com/en/terms); say so here
        terms: Literal["non-commercial"]

    @classmethod
    def forecast_model(cls, params: PluginParams) -> str | None:
        assert isinstance(params, cls.Params)
        return SOURCES[OPEN_METEO_SOURCE[params.model]].model

    def __init__(self, params: Params) -> None:
        self.p = params
        self.source = OPEN_METEO_SOURCE[params.model]
        self.station = STATIONS[params.station]
        self._done: set[datetime] = set()  # runs already read

    def runs(self, now: datetime) -> list[datetime]:
        """Releasable runs within `lookback` not read yet, newest first."""
        lag, cycle = SOURCES[self.source].lag, open_meteo_cycle(self.source)
        run = latest_releasable(now, lag, cycle)
        oldest = max(now - self.p.lookback, OPEN_METEO_FIRST_RUN[self.source])
        out: list[datetime] = []
        while run >= oldest:
            if run not in self._done:
                out.append(run)
            run -= cycle
        return out

    def fetch(self, now: datetime) -> Sequence[Observation]:
        self._done = {r for r in self._done if r >= now - self.p.lookback - timedelta(days=1)}
        out: list[Observation] = []
        for run in self.runs(now):
            r = http.get(
                OPEN_METEO,
                open_meteo_params(self.source, self.station, run),
                http.user_agent(self.p.user_agent),
            )
            if r.status_code == 400:  # run not (yet) available: try again next time
                continue
            r.raise_for_status()
            body: dict[str, Any] = r.json()
            try:
                rows = parse_open_meteo(body, self.source, self.station, run, now)
            except ArchiveError:
                continue  # e.g. {"error": true, "reason": "...not available"}
            self._done.add(run)
            out.extend(observations(rows, self.station, now))
        return out
