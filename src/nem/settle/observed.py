"""Independent daily high/low readings from the Iowa Environmental Mesonet (IEM).

IEM materials are public domain ("may be used freely by anyone for any lawful purpose",
mesonet.agron.iastate.edu/disclaimer.php, read 2026-11-21), without warranty; attribution
appreciated. The underlying NWS products are US government works.

1. `iem_cli` (preferred): IEM's parse of the NWS Daily Climate Report (CLI), the official
   climate record for the station, one request per station-year
   (`/json/cli.py?station=KNYC&year=2026`). `source_ref` is the CLI product id; its raw
   text is at `/api/1/nwstext/<product id>`. Values are whole degrees F; "M" is missing.
2. `iem_asos_obs` (fallback, approximate): the highest / lowest temperature among the
   station's METAR reports (routine and special) inside the CLI climate day, local
   standard time (`/cgi-bin/request/asos.py`). It can miss the true extreme between
   reports (the CLI also uses continuous 1-minute data), so a reading within a degree or
   so of a strike is not conclusive. That endpoint is rate limited per IP (HTTP 429);
   we back off and retry.

Weather Underground / The Weather Company (the settlement source of newer markets) is a
manual cross-check only: their terms forbid bots and scrapers without written permission
and there is no free public API for station history.
"""

import csv
import io
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from nem.feeds import http
from nem.settle.rules import climate_day_window, whole_degrees
from nem.settle.stations import Measure, Station

IEM = "https://mesonet.agron.iastate.edu"
# Generic on purpose: no personal contact details in requests to third parties.
USER_AGENT = "not-enough-markets-settlement-check/1.0"


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Reading:
    value: int  # whole degrees F
    source: str  # iem_cli / iem_asos_obs
    ref: str  # CLI product id, or the observation count and window
    approximate: bool


class Iem:
    """Fetches and caches IEM data; `pause` seconds between requests (be polite)."""

    def __init__(
        self,
        *,
        pause: float = 1.0,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 60.0,
    ) -> None:
        self._http = httpx.Client(transport=http.TRANSPORT, timeout=timeout)
        self._pause = pause
        self._max_retries = max_retries
        self._sleep = sleep
        self._cli: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
        self.requests = 0

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict[str, Any] | list[tuple[str, str]]) -> httpx.Response:
        url = f"{IEM}{path}?{urlencode(params)}"
        problem = ""
        for attempt in range(self._max_retries + 1):
            if self.requests:
                self._sleep(self._pause * 2**attempt)
            self.requests += 1
            try:
                r = self._http.get(url, headers={"User-Agent": USER_AGENT})
            except httpx.TransportError as e:
                problem = repr(e)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                problem = f"HTTP {r.status_code}"
                continue
            if r.is_error:
                raise SourceError(f"GET {url}: HTTP {r.status_code}")
            return r
        raise SourceError(f"GET {url}: gave up after {self._max_retries + 1} tries ({problem})")

    def cli_rows(self, station: Station, year: int) -> dict[str, dict[str, Any]]:
        key = (station.icao, year)
        if key not in self._cli:
            body: dict[str, Any] = self._get(
                "/json/cli.py", {"station": station.icao, "year": year}
            ).json()
            rows: list[dict[str, Any]] = body.get("results", [])
            self._cli[key] = {str(r["valid"]): r for r in rows}
        return self._cli[key]

    def cli(self, station: Station, day: date, measure: Measure) -> Reading | None:
        row = self.cli_rows(station, day.year).get(day.isoformat())
        if row is None:
            return None
        product = str(row.get("product", ""))
        if station.cli not in product:
            raise SourceError(f"{product!r} is not a {station.cli} product")
        value = row.get(measure)
        if not isinstance(value, int | float) or isinstance(value, bool):
            return None  # "M": the report has no value for that day
        return Reading(whole_degrees(float(value)), "iem_cli", product, approximate=False)

    def asos_obs(self, station: Station, day: date, measure: Measure) -> Reading | None:
        start, end = climate_day_window(day, ZoneInfo(station.tz))
        params = [
            ("station", station.asos), ("data", "tmpf"), ("tz", "Etc/UTC"),
            ("format", "onlycomma"), ("latlon", "no"), ("missing", "M"),
            ("report_type", "3"), ("report_type", "4"),  # routine + special METARs
            ("sts", start.strftime("%Y-%m-%dT%H:%MZ")), ("ets", end.strftime("%Y-%m-%dT%H:%MZ")),
        ]  # fmt: skip
        values = parse_asos_csv(self._get("/cgi-bin/request/asos.py", params).text)
        if not values:
            return None
        pick = max(values) if measure == "high" else min(values)
        ref = f"{len(values)} METARs {start:%Y-%m-%dT%H:%MZ}/{end:%H:%MZ}"
        return Reading(whole_degrees(pick), "iem_asos_obs", ref, approximate=True)

    def reading(self, station: Station, day: date, measure: Measure) -> Reading | None:
        """CLI first; METAR observations only when the CLI has no value."""
        return self.cli(station, day, measure) or self.asos_obs(station, day, measure)


def parse_asos_csv(text: str) -> list[float]:
    """`station,valid,tmpf` rows (UTC) -> temperatures, skipping missing ones."""
    out: list[float] = []
    for row in csv.DictReader(io.StringIO(text)):
        raw = (row.get("tmpf") or "").strip()
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out
