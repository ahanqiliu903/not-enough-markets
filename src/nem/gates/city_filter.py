"""Trade only some cities, e.g. exclude Washington DC until replay says it belongs.

Cities are named by the ICAO id of the station that settles them (KDCA, KNYC, ...), from
`nem.settle.stations`, so two series on the same station (a high and a low) are one city.
A market whose series has no station mapping is always blocked: the filter can't tell
whether it's allowed. That is a config problem, not missing data, so `fail_open` doesn't
apply to it.
"""

from pydantic import Field, model_validator

from nem.core.context import Context
from nem.core.registry import register
from nem.core.types import Decision, MarketSnapshot, Signal
from nem.gates.base import Gate, GateParams
from nem.settle.stations import STATIONS, lookup

_KNOWN = frozenset(s.icao for s in STATIONS.values())


@register("gate", "city_filter")
class CityFilter(Gate):
    class Params(GateParams):
        allow: list[str] | None = None  # only these stations; null = any mapped station
        exclude: list[str] = Field(default_factory=list[str])  # never these stations

        @model_validator(mode="after")
        def _known(self) -> "CityFilter.Params":
            if self.allow is None and not self.exclude:
                raise ValueError("set allow and/or exclude")
            unknown = sorted({*(self.allow or []), *self.exclude} - _KNOWN)
            if unknown:
                raise ValueError(f"unknown stations {unknown}; use ICAO ids, e.g. KNYC, KDCA")
            return self

    def __init__(self, params: Params) -> None:
        super().__init__(params)
        self.allow = frozenset(params.allow) if params.allow is not None else None
        self.exclude = frozenset(params.exclude)

    def check(self, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
        found = lookup(snap.ticker)
        if found is None:
            return Decision.skip(f"{self.name}:unknown_city")
        city = found.station.icao
        if city in self.exclude or (self.allow is not None and city not in self.allow):
            return Decision.skip(f"{self.name}:{city}")
        return Decision.ok()
