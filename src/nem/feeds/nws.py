"""US temperature and precipitation forecasts from the National Weather Service
(api.weather.gov, free, no key).

Kalshi's daily temperature markets settle on The Weather Company's reading for a specific
station (e.g. Central Park, CLINYC, for KXHIGHNY), not on NWS. NWS is a free forecast for
the same place: a reasonable predictor, but expect occasional disagreement with the
settlement value. Pass the station's coordinates; anyone with a Weather Company API key
can add a feed for it the same way.

Keys, one per forecast date (exchange-local date, ISO format):
- `YYYY-MM-DD/high`: daytime high, F
- `YYYY-MM-DD/low`: overnight low ending that morning, F (from the previous night's period)
- `YYYY-MM-DD/precip`: highest chance of precipitation among that day's periods, %

`known_at` is the forecast's update time, so replay only sees forecasts that were issued
before each decision. NWS asks clients to identify themselves and not poll too often;
`every: 30m` is plenty since forecasts update a few times a day.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import Field

from nem.core.registry import PluginParams, register
from nem.feeds import http
from nem.feeds.base import Feed, Observation


def _fahrenheit(value: float, unit: str) -> float:
    return value * 9 / 5 + 32 if unit.upper() == "C" else value


@register("feed", "nws_forecast")
class NwsForecast(Feed):
    class Params(PluginParams):
        lat: float = Field(ge=-90, le=90)
        lon: float = Field(ge=-180, le=180)
        user_agent: str = http.USER_AGENT  # NWS asks for contact info; put yours here

    def __init__(self, params: Params) -> None:
        self.p = params
        self._forecast_url: str | None = None

    def _url(self) -> str:
        if self._forecast_url is None:
            points = http.get_json(
                f"https://api.weather.gov/points/{self.p.lat:.4f},{self.p.lon:.4f}",
                self.p.user_agent,
            )
            self._forecast_url = str(points["properties"]["forecast"])
        return self._forecast_url

    def fetch(self, now: datetime) -> Sequence[Observation]:
        props: dict[str, Any] = http.get_json(self._url(), self.p.user_agent)["properties"]
        issued = props.get("updateTime") or props.get("generatedAt")
        known_at = min(datetime.fromisoformat(issued), now) if issued else now
        highs: dict[date, float] = {}
        lows: dict[date, float] = {}
        precip: dict[date, float] = {}
        periods: list[dict[str, Any]] = props.get("periods", [])
        for period in periods:
            start = datetime.fromisoformat(period["startTime"])  # local time with offset
            day = start.date()
            temp = period.get("temperature")
            if isinstance(temp, int | float):
                temp = _fahrenheit(float(temp), period.get("temperatureUnit", "F"))
                if period.get("isDaytime"):
                    highs.setdefault(day, temp)
                else:
                    lows.setdefault(day + timedelta(days=1), temp)  # the night before
            pop: dict[str, Any] = period.get("probabilityOfPrecipitation") or {}
            chance = pop.get("value")
            if isinstance(chance, int | float):
                precip[day] = max(precip.get(day, 0.0), float(chance))
        out: list[Observation] = []
        for name, values in (("high", highs), ("low", lows), ("precip", precip)):
            out.extend((f"{d.isoformat()}/{name}", v, known_at) for d, v in sorted(values.items()))
        return out
