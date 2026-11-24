"""Only enter between two local clock times anchored on the market's date (the date in its
event ticker, e.g. KXHIGHNY-26JAN14 -> Jan 14).

`from_day` / `until_day` count days from the market date (-1 = the day before), and
`from_time` / `until_time` are local clock times (DST-aware) in `tz`, or in the zone of
the station that settles the series (`nem.settle.stations`) when `tz` is null. The window
is [from, until): e.g. "the day before only" is `until_day: 0, until_time: "00:00"`, and
"6 AM to noon on the day" is `from_day: 0, from_time: "06:00", until_day: 0,
until_time: "12:00"`. With
`from_day: null` there is no lower bound (from the market's open).

Times are "HH:MM" strings on purpose: YAML 1.1 reads an unquoted 10:00 as the number 600.

A market without a date in its ticker, or (with `tz: null`) without a station mapping, is
always blocked: that is a config problem, not missing data, so `fail_open` doesn't apply.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from nem.core.context import Context
from nem.core.registry import register
from nem.core.types import Decision, MarketSnapshot, Signal
from nem.gates.base import Gate, GateParams
from nem.gates.feeds import market_date
from nem.settle.stations import lookup

_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


def _clock(s: str) -> time:
    hours, minutes = s.split(":")
    return time(int(hours), int(minutes))


@register("gate", "market_day_window")
class MarketDayWindow(Gate):
    class Params(GateParams):
        tz: str | None = None  # IANA zone; null = the settling station's zone
        from_day: int | None = Field(default=None, ge=-30, le=30)  # null = no lower bound
        from_time: str = Field(default="00:00", pattern=_HHMM)
        until_day: int = Field(default=0, ge=-30, le=30)
        until_time: str = Field(default="00:00", pattern=_HHMM)  # exclusive

        @field_validator("tz")
        @classmethod
        def _zone(cls, v: str | None) -> str | None:
            if v is not None:
                try:
                    ZoneInfo(v)
                except (ZoneInfoNotFoundError, ValueError) as e:
                    raise ValueError(f"unknown time zone {v!r}") from e
            return v

        @model_validator(mode="after")
        def _ordered(self) -> "MarketDayWindow.Params":
            if self.from_day is not None and (self.from_day, _clock(self.from_time)) >= (
                self.until_day, _clock(self.until_time)
            ):  # fmt: skip
                raise ValueError("the window must start before it ends")
            return self

    def __init__(self, params: Params) -> None:
        super().__init__(params)
        self.p = params
        self.tz = ZoneInfo(params.tz) if params.tz is not None else None

    def _at(self, day: date, offset: int, clock: str, tz: ZoneInfo) -> datetime:
        return datetime.combine(day + timedelta(days=offset), _clock(clock), tz)

    def check(self, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
        day = market_date(snap.window_id)
        if day is None:
            return Decision.skip(f"{self.name}:no_date")
        tz = self.tz
        if tz is None:
            found = lookup(snap.ticker)
            if found is None:
                return Decision.skip(f"{self.name}:unknown_zone")
            tz = ZoneInfo(found.station.tz)
        now = ctx.now()
        if now >= self._at(day, self.p.until_day, self.p.until_time, tz):
            return Decision.skip(f"{self.name}:too_late")
        if self.p.from_day is not None and now < self._at(
            day, self.p.from_day, self.p.from_time, tz
        ):
            return Decision.skip(f"{self.name}:too_early")
        return Decision.ok()
