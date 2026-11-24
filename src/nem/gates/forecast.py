"""Gates on several weather forecasts at once, for daily temperature markets.

- `source_agreement`: at least `min_sources` forecasts of the market's measure (high or
  low), and (a fraction of) them imply our side wins under the market's own strike.
- `bracket_distance`: the forecast (median, or each source) is at least `min_distance`
  degrees from the region where our side loses.
- `sky_conditions`: veto when cloud cover / precipitation forecasts are above thresholds.

Forecasts come only through `ctx.forecasts` (known at or before now, newest per source):
from forecast feeds recorded live (`feeds`, e.g. `nws_forecast`), and/or from the
`forecast_backfill` table (`backfill_sources`) for replays of periods with no
recorded feeds. Backfill is approximate: any strategy that sets `backfill_sources` has its
verdict capped (`nem.stats.provenance.uses_backfill` / `cap_verdict`).

The market's day is the date in its event ticker (the CLI climate day); its station and
measure come from `nem.settle.stations`, or from `station` / `field` for series that
aren't mapped. A series that can't be resolved is blocked (config problem, not missing
data). Too few sources raises `DataUnavailable`, so `fail_open` decides; it defaults to
false for all three: with no forecast there is no evidence the trade is safe.

Outcomes use whole degrees, as settlement does (`nem.settle.rules`): a forecast of 17.4
counts as 17.
"""

import math
from collections.abc import Sequence
from datetime import date, timedelta
from statistics import median
from typing import Literal, Self

from pydantic import Field, model_validator

from nem.backfill.coverage import MIN_SOURCES
from nem.backfill.model import SOURCES
from nem.core.config import Duration
from nem.core.context import Context, DataUnavailable, Forecast
from nem.core.registry import register
from nem.core.types import Decision, MarketSnapshot, Signal
from nem.gates.base import Gate, GateParams
from nem.gates.feeds import market_date
from nem.settle.rules import whole_degrees
from nem.settle.stations import STATIONS, lookup

Measure = Literal["high", "low"]


class ForecastGateParams(GateParams):
    feeds: list[str] = Field(default_factory=list[str])  # forecast feeds (live / recorded)
    backfill_sources: list[str] = Field(default_factory=list[str])  # replay only, approximate
    backfill_max_age: Duration = timedelta(hours=48)  # older backfilled runs don't count
    station: str | None = None  # NWS CLI id; null = from the series' station mapping

    @model_validator(mode="after")
    def _sources(self) -> Self:
        if not self.feeds and not self.backfill_sources:
            raise ValueError("set feeds and/or backfill_sources")
        for what, names in (("feeds", self.feeds), ("backfill_sources", self.backfill_sources)):
            if len(set(names)) != len(names):
                raise ValueError(f"duplicate {what}")
        unknown = sorted(set(self.backfill_sources) - SOURCES.keys())
        if unknown:
            raise ValueError(f"unknown backfill_sources {unknown}; known: {sorted(SOURCES)}")
        if set(self.feeds) & set(self.backfill_sources):
            raise ValueError("a name can't be both a feed and a backfill source")
        if self.station is not None and self.station not in STATIONS:
            raise ValueError(f"unknown station {self.station!r}; use a CLI id, e.g. CLINYC")
        return self


class ForecastGate(Gate):
    """Shared plumbing: resolve the market's station-day and read forecasts via ctx."""

    def __init__(self, params: ForecastGateParams) -> None:
        super().__init__(params)
        self.fp = params

    def feeds_used(self) -> Sequence[str]:
        return list(self.fp.feeds)

    def _station(self, snap: MarketSnapshot) -> str | None:
        if self.fp.station is not None:
            return self.fp.station
        found = lookup(snap.ticker)
        return found.station.cli if found else None

    def _day(self, snap: MarketSnapshot) -> date:
        d = market_date(snap.window_id)
        if d is None:
            raise DataUnavailable(f"no date in {snap.window_id!r}")
        return d

    def _read(
        self, ctx: Context, snap: MarketSnapshot, field: str, station: str | None
    ) -> list[Forecast]:
        return ctx.forecasts(
            self._day(snap),
            field,
            feeds=self.fp.feeds,
            station=station,
            backfill_sources=self.fp.backfill_sources,
            backfill_max_age=self.fp.backfill_max_age,
        )

    def _needs_station(self) -> bool:
        return bool(self.fp.backfill_sources)


class TemperatureGateParams(ForecastGateParams):
    field: Measure | None = None  # null = the series' measure (KXHIGH* high, KXLOW* low)
    min_sources: int = Field(default=1, ge=1)


class TemperatureGate(ForecastGate):
    def __init__(self, params: TemperatureGateParams) -> None:
        super().__init__(params)
        self.tp = params

    def _temperatures(self, snap: MarketSnapshot, ctx: Context) -> list[Forecast] | Decision:
        found = lookup(snap.ticker)
        measure = self.tp.field or (found.measure if found else None)
        station = self._station(snap)
        if measure is None or (station is None and self._needs_station()):
            return Decision.skip(f"{self.name}:unknown_series")
        got = self._read(ctx, snap, measure, station)
        if len(got) < self.tp.min_sources:
            raise DataUnavailable(
                f"{len(got)} of {self.tp.min_sources} {measure} forecasts for {snap.ticker}"
            )
        return got


def _wins(snap: MarketSnapshot, signal: Signal, value: float) -> bool:
    yes = snap.resolves_yes(whole_degrees(value))
    if yes is None:
        raise DataUnavailable(f"{snap.ticker} has no usable strike")
    return yes is (signal.side == "yes")


def distance_to_loss(snap: MarketSnapshot, signal: Signal, value: float, limit: float) -> float:
    """Degrees from `value` to the nearest temperature at which our side loses, capped at
    `limit`; 0 if it loses at `value`. Settlement rounds half up, so whole degree n covers
    [n - 0.5, n + 0.5): selling NO on a 16-17 bracket, the losing region starts at 17.5."""
    if not _wins(snap, signal, value):
        return 0.0
    best = limit
    for n in range(math.floor(value - limit - 0.5), math.ceil(value + limit + 0.5) + 1):
        if _wins(snap, signal, n):
            continue
        lo, hi = n - 0.5, n + 0.5
        d = lo - value if value < lo else value - hi if value >= hi else 0.0
        best = min(best, d)
    return best


@register("gate", "source_agreement")
class SourceAgreement(TemperatureGate):
    """At least `min_sources` forecasts, `min_agree_fraction` of them on our side of the
    strike, and optionally no more than `max_spread` degrees between highest and lowest."""

    class Params(TemperatureGateParams):
        min_sources: int = Field(default=MIN_SOURCES, ge=1)
        min_agree_fraction: float = Field(default=1.0, gt=0, le=1)
        max_spread: float | None = Field(default=None, gt=0)

    def __init__(self, params: Params) -> None:
        super().__init__(params)
        self.p = params

    def check(self, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
        got = self._temperatures(snap, ctx)
        if isinstance(got, Decision):
            return got
        agree = sum(_wins(snap, signal, f.value) for f in got)
        if agree < self.p.min_agree_fraction * len(got) - 1e-9:
            return Decision.skip(f"{self.name}:disagree")
        values = [f.value for f in got]
        if self.p.max_spread is not None and max(values) - min(values) > self.p.max_spread:
            return Decision.skip(f"{self.name}:spread")
        return Decision.ok()


@register("gate", "bracket_distance")
class BracketDistance(TemperatureGate):
    """The median forecast (or each source's, `use: each`) is at least `min_distance`
    degrees from where our side loses. Never sells the bracket the forecast sits in."""

    class Params(TemperatureGateParams):
        min_distance: float = Field(default=2.0, ge=0)
        use: Literal["median", "each"] = "median"

    def __init__(self, params: Params) -> None:
        super().__init__(params)
        self.p = params

    def check(self, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
        got = self._temperatures(snap, ctx)
        if isinstance(got, Decision):
            return got
        values = [f.value for f in got]
        points = [median(values)] if self.p.use == "median" else values
        d = self.p.min_distance
        if any(
            not _wins(snap, signal, v) or distance_to_loss(snap, signal, v, d + 1) < d
            for v in points
        ):
            return Decision.skip(f"{self.name}:too_close")
        return Decision.ok()


SkyField = Literal["cloud", "pop", "qpf_in", "qpf_cat"]


@register("gate", "sky_conditions")
class SkyConditions(ForecastGate):
    """Veto when a sky forecast is above its threshold (null = not checked). Each checked
    field needs `min_sources` sources, else `DataUnavailable`.

    `cloud` is %: NBM and Open-Meteo give %, GFS/NAM MOS give a category mapped to its
    okta midpoint (CL 0, FW 19, SC 44, BK 75, OV 100), so with MOS a threshold acts as a
    category cutoff (75 vetoes overcast but not broken). nws_forecast has no cloud key.
    `pop` is the precipitation chance % (nws_forecast's `precip`). `qpf_in` inches;
    `qpf_cat` the MOS QPF category (0 none, 1 = 0.01-0.09", 2 = 0.10-0.24", ...).
    """

    class Params(ForecastGateParams):
        max_cloud: float | None = Field(default=None, ge=0, le=100)
        max_pop: float | None = Field(default=50, ge=0, le=100)
        max_qpf_in: float | None = Field(default=None, ge=0)
        max_qpf_cat: float | None = Field(default=None, ge=0, le=6)
        aggregate: Literal["max", "median"] = "max"  # max: any source flagging it vetoes
        min_sources: int = Field(default=1, ge=1)

        @model_validator(mode="after")
        def _some(self) -> Self:
            if all(v is None for v in self.thresholds().values()):
                raise ValueError("set at least one of max_cloud, max_pop, max_qpf_in, max_qpf_cat")
            return self

        def thresholds(self) -> dict[SkyField, float | None]:
            return {
                "cloud": self.max_cloud, "pop": self.max_pop,
                "qpf_in": self.max_qpf_in, "qpf_cat": self.max_qpf_cat,
            }  # fmt: skip

    def __init__(self, params: Params) -> None:
        super().__init__(params)
        self.p = params

    def check(self, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
        station = self._station(snap)
        if station is None and self._needs_station():
            return Decision.skip(f"{self.name}:unknown_series")
        for field, limit in self.p.thresholds().items():
            if limit is None:
                continue
            values = [f.value for f in self._read(ctx, snap, field, station)]
            if len(values) < self.p.min_sources:
                raise DataUnavailable(
                    f"{len(values)} of {self.p.min_sources} {field} forecasts for {snap.ticker}"
                )
            value = max(values) if self.p.aggregate == "max" else median(values)
            if value > limit:
                return Decision.skip(f"{self.name}:{field}")
        return Decision.ok()
