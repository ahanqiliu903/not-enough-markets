"""Which station settles each daily temperature series, and where to read it independently.

Verified 2026-11-21. Where each value comes from:

- `cli` (the NWS Daily Climate Report product id, e.g. CLINYC): the station code in the
  rules of each series' open market on 2026-11-21 (Kalshi public API,
  `/markets?series_ticker=...&status=open`: "recorded at New York City (CLINYC)"). Older
  markets name the place instead ("Central Park, New York", "Philadelphia International
  Airport") and, for some series, link the NWS page for that CLI in `rules_secondary`.
- `icao`, `wfo`, `name`: the Iowa Environmental Mesonet's parsed CLI archive
  (`/json/cli.py?station=<icao>`); every product it returned for 2025-2026 carries the PIL
  in `cli` (corrections included, e.g. `CLIPHL-AAA`) from the office in `wfo`.
- `asos`, `network`: IEM's ASOS station and network ids; `/api/1/daily.json` answered
  for each one with the same station name.
- `tz`: the IANA zone of the station's location (not from an API). The CLI climate day is
  midnight to midnight local *standard* time in that zone (see `nem.settle.rules`).

The settlement *source* is not stored here: it changed over time (NWS CLI for older
markets, The Weather Company for newer ones) and is read from each market's own rules.
KXHIGHTSDF's title says "SATX" but its rules name Louisville (docs/kalshi-tickers.txt);
always trust the rules over the title.
"""

from dataclasses import dataclass
from typing import Literal

Measure = Literal["high", "low"]


@dataclass(frozen=True, slots=True)
class Station:
    cli: str  # NWS CLI product id (AFOS PIL)
    icao: str  # station id in IEM's CLI archive
    wfo: str  # NWS office issuing the CLI
    name: str  # as the CLI names it
    tz: str  # IANA zone; the climate day uses its standard-time offset
    asos: str  # IEM ASOS id (fallback observations)
    network: str  # IEM ASOS network


@dataclass(frozen=True, slots=True)
class SeriesStation:
    series: str
    measure: Measure  # maximum (KXHIGH*) or minimum (KXLOW*) temperature
    city: str  # as the rules name it
    station: Station


_E, _C, _M, _AZ, _P = (
    "America/New_York", "America/Chicago", "America/Denver", "America/Phoenix",
    "America/Los_Angeles",
)  # fmt: skip

STATIONS: dict[str, Station] = {
    s.cli: s
    for s in (
        Station("CLIATL", "KATL", "FFC", "ATLANTA INTL ARPT", _E, "ATL", "GA_ASOS"),
        Station("CLIAUS", "KAUS", "EWX", "Austin Bergstrom", _C, "AUS", "TX_ASOS"),
        Station("CLIBOS", "KBOS", "BOX", "BOSTON/LOGAN INTL", _E, "BOS", "MA_ASOS"),
        Station("CLIDCA", "KDCA", "LWX", "WASHINGTON/NATIONAL", _E, "DCA", "VA_ASOS"),
        Station("CLIDEN", "KDEN", "BOU", "DENVER INTNL ARPT", _M, "DEN", "CO_ASOS"),
        Station("CLIDFW", "KDFW", "FWD", "DALLAS/FT WORTH", _C, "DFW", "TX_ASOS"),
        Station("CLIEWR", "KEWR", "OKX", "NEWARK INTL AIRPORT", _E, "EWR", "NJ_ASOS"),
        Station("CLIHOU", "KHOU", "HGX", "HOUSTON/HOBBY AIRPORT", _C, "HOU", "TX_ASOS"),
        Station("CLILAS", "KLAS", "VEF", "LAS VEGAS/MCCARRAN", _P, "LAS", "NV_ASOS"),
        Station("CLILAX", "KLAX", "LOX", "LOS ANGELES INTL", _P, "LAX", "CA_ASOS"),
        Station("CLIMDW", "KMDW", "LOT", "CHICAGO", _C, "MDW", "IL_ASOS"),
        Station("CLIMIA", "KMIA", "MFL", "MIAMI INTL AIRPORT", _E, "MIA", "FL_ASOS"),
        Station("CLIMSP", "KMSP", "MPX", "MINNEAPOLIS", _C, "MSP", "MN_ASOS"),
        Station("CLIMSY", "KMSY", "LIX", "NEW ORLEANS", _C, "MSY", "LA_ASOS"),
        Station("CLINYC", "KNYC", "OKX", "NEW YORK CITY", _E, "NYC", "NY_ASOS"),
        Station("CLIOKC", "KOKC", "OUN", "OKLAHOMA CITY(AWOS)", _C, "OKC", "OK_ASOS"),
        Station("CLIPHL", "KPHL", "PHI", "PHILADELPHIA INTL", _E, "PHL", "PA_ASOS"),
        Station("CLIPHX", "KPHX", "PSR", "PHOENIX/SKY HARBOR", _AZ, "PHX", "AZ_ASOS"),
        Station("CLISAN", "KSAN", "SGX", "SAN DIEGO/LINDBERG", _P, "SAN", "CA_ASOS"),
        Station("CLISAT", "KSAT", "EWX", "SAN ANTONIO INTL", _C, "SAT", "TX_ASOS"),
        Station("CLISDF", "KSDF", "LMK", "LOUISVILLE/STANDIFO", _E, "SDF", "KY_ASOS"),
        Station("CLISEA", "KSEA", "SEW", "SEATTLE-TACOMA INTL", _P, "SEA", "WA_ASOS"),
        Station("CLISFO", "KSFO", "MTR", "SAN FRANCISCO INTL", _P, "SFO", "CA_ASOS"),
        Station("CLITTN", "KTTN", "PHI", "TRENTON/MERCER CO.", _E, "TTN", "NJ_ASOS"),
    )
}

# series -> (measure, city as the rules name it, CLI id from the rules)
_SERIES: dict[str, tuple[Measure, str, str]] = {
    "KXHIGHAUS": ("high", "Austin", "CLIAUS"),
    "KXHIGHCHI": ("high", "Chicago", "CLIMDW"),
    "KXHIGHDEN": ("high", "Denver", "CLIDEN"),
    "KXHIGHLAX": ("high", "Los Angeles", "CLILAX"),
    "KXHIGHMIA": ("high", "Miami", "CLIMIA"),
    "KXHIGHNY": ("high", "New York City", "CLINYC"),
    "KXHIGHPHIL": ("high", "Philadelphia", "CLIPHL"),
    "KXHIGHTATL": ("high", "Atlanta", "CLIATL"),
    "KXHIGHTBOS": ("high", "Boston", "CLIBOS"),
    "KXHIGHTDAL": ("high", "Dallas", "CLIDFW"),
    "KXHIGHTDC": ("high", "Washington DC", "CLIDCA"),
    "KXHIGHTEWR": ("high", "Newark", "CLIEWR"),
    "KXHIGHTHOU": ("high", "Houston", "CLIHOU"),
    "KXHIGHTLV": ("high", "Las Vegas", "CLILAS"),
    "KXHIGHTMIN": ("high", "Minneapolis", "CLIMSP"),
    "KXHIGHTNOLA": ("high", "New Orleans", "CLIMSY"),
    "KXHIGHTOKC": ("high", "Oklahoma City", "CLIOKC"),
    "KXHIGHTPHX": ("high", "Phoenix", "CLIPHX"),
    "KXHIGHTSAN": ("high", "San Diego", "CLISAN"),
    "KXHIGHTSATX": ("high", "San Antonio", "CLISAT"),
    "KXHIGHTSDF": ("high", "Louisville", "CLISDF"),
    "KXHIGHTSEA": ("high", "Seattle", "CLISEA"),
    "KXHIGHTSFO": ("high", "San Francisco", "CLISFO"),
    "KXHIGHTTTN": ("high", "Trenton", "CLITTN"),
    "KXLOWTATL": ("low", "Atlanta", "CLIATL"),
    "KXLOWTAUS": ("low", "Austin", "CLIAUS"),
    "KXLOWTBOS": ("low", "Boston", "CLIBOS"),
    "KXLOWTCHI": ("low", "Chicago", "CLIMDW"),
    "KXLOWTDAL": ("low", "Dallas", "CLIDFW"),
    "KXLOWTDC": ("low", "Washington DC", "CLIDCA"),
    "KXLOWTDEN": ("low", "Denver", "CLIDEN"),
    "KXLOWTEWR": ("low", "Newark", "CLIEWR"),
    "KXLOWTHOU": ("low", "Houston", "CLIHOU"),
    "KXLOWTLAX": ("low", "Los Angeles", "CLILAX"),
    "KXLOWTLV": ("low", "Las Vegas", "CLILAS"),
    "KXLOWTMIA": ("low", "Miami", "CLIMIA"),
    "KXLOWTMIN": ("low", "Minneapolis", "CLIMSP"),
    "KXLOWTNOLA": ("low", "New Orleans", "CLIMSY"),
    "KXLOWTNYC": ("low", "New York City", "CLINYC"),
    "KXLOWTOKC": ("low", "Oklahoma City", "CLIOKC"),
    "KXLOWTPHIL": ("low", "Philadelphia", "CLIPHL"),
    "KXLOWTPHX": ("low", "Phoenix", "CLIPHX"),
    "KXLOWTSAN": ("low", "San Diego", "CLISAN"),
    "KXLOWTSATX": ("low", "San Antonio", "CLISAT"),
    "KXLOWTSDF": ("low", "Louisville", "CLISDF"),
    "KXLOWTSEA": ("low", "Seattle", "CLISEA"),
    "KXLOWTSFO": ("low", "San Francisco", "CLISFO"),
    "KXLOWTTTN": ("low", "Trenton", "CLITTN"),
}

SERIES: dict[str, SeriesStation] = {
    series: SeriesStation(series, measure, city, STATIONS[cli])
    for series, (measure, city, cli) in _SERIES.items()
}


def series_of(ticker: str) -> str:
    """KXHIGHNY-26MAR03-B40.5 -> KXHIGHNY."""
    return ticker.split("-", 1)[0]


def lookup(ticker: str) -> SeriesStation | None:
    return SERIES.get(series_of(ticker))
