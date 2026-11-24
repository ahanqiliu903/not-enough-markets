"""Provisional ("pending / likely") results of open trades, for reports only.

Some open trades' outcomes are practically known before Kalshi settles them. This module
says which, from two bases, so a report can show what is coming:

- `observed` (daily temperature series): the climate day's running extreme can only move
  one way (the high only rises, the low only falls), so once every value it can still
  reach resolves the market the same way, the outcome is decided. It is `locked` only if
  that also holds 1°F back toward the strike (`MISMATCH_F`): the observations here are
  ASOS reports from api.weather.gov, while settlement is The Weather Company / the NWS
  CLI, and the two have differed by a degree. Holding only at the exact extreme is
  `likely`.
- `price` (Kalshi public market data): a side bid at 98c or more, or a closed market whose
  last trade was at 98c+ / 2c-, is `likely`, never `locked`: a price is belief, not
  settlement (a market once traded at 1c for a side that then won).

Nothing here reaches the engine: no statistic, equity, risk limit, signal or gate sees
it (no look-ahead; it never goes through Context). Kalshi's settlement stays the only
truth; reports label these numbers "not counted".
"""

import logging
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx

from nem.core.types import Side, StrikeType, Trade
from nem.engine.settlement import realized_pnl
from nem.feeds.http import USER_AGENT_ENV
from nem.gates.feeds import market_date
from nem.market.kalshi import KalshiClient, KalshiError, KalshiMarket
from nem.settle.observed import USER_AGENT
from nem.settle.rules import climate_day_window, derive_result, whole_degrees
from nem.settle.stations import Measure, Station, lookup

log = logging.getLogger(__name__)

Status = Literal["locked_win", "locked_loss", "likely_win", "likely_loss", "conflict", "unknown"]
Basis = Literal["observed", "price", "both"]
STATUSES: tuple[Status, ...] = (
    "locked_win", "locked_loss", "likely_win", "likely_loss", "conflict", "unknown",
)  # fmt: skip

MISMATCH_F = 1  # degrees F the settlement source may differ from the observations
LIKELY_PRICE = 0.98  # a side bid (or closed market's last price) this high: likely
HIGH_BOUND_F = 200  # beyond any reachable temperature: the end of "can still rise to"
LOW_BOUND_F = -100
SIX_HOURS = timedelta(hours=6)
NWS = "https://api.weather.gov"


@dataclass(frozen=True, slots=True)
class Call:
    """One basis's view of a market: which side wins and how sure."""

    side: Side
    locked: bool = False


@dataclass(frozen=True, slots=True)
class Provisional:
    status: Status
    basis: Basis | None  # None when unknown
    pnl: float | None  # P&L if the status holds (win/loss only), as settlement would book it
    observed_extreme_f: int | None  # running high/low so far, whole degrees F
    note: str = ""  # why unknown, fetch failures ("; "-separated)

    @property
    def decided(self) -> bool:
        return self.status not in ("conflict", "unknown")


UNKNOWN = Provisional("unknown", None, None, None)


# --- decisions (pure) -------------------------------------------------------------------


def _one_side(
    values: Iterable[int],
    strike_type: StrikeType | None,
    floor_strike: float | None,
    cap_strike: float | None,
) -> Side | None:
    """The side every value resolves to, None if they differ or the strike is unknown."""
    sides: set[Side | None] = {
        derive_result(v, strike_type, floor_strike, cap_strike) for v in values
    }
    return sides.pop() if len(sides) == 1 else None


def _reachable(measure: Measure, extreme: int, back: int) -> range:
    """Whole degrees the final value can still take, widened `back` degrees toward the
    strike side the extreme has already passed."""
    if measure == "high":
        return range(extreme - back, HIGH_BOUND_F + 1)
    return range(LOW_BOUND_F, extreme + back + 1)


def observed_call(
    measure: Measure,
    extreme_f: int,
    strike_type: StrikeType | None,
    floor_strike: float | None,
    cap_strike: float | None,
    margin: int = MISMATCH_F,
) -> Call | None:
    """Decided when every value still reachable resolves to one side (checked degree by
    degree, so `between` strikes are right too); locked when that also holds `margin`
    degrees back toward the strike."""
    exact = _one_side(_reachable(measure, extreme_f, 0), strike_type, floor_strike, cap_strike)
    if exact is None:
        return None
    wide = _one_side(_reachable(measure, extreme_f, margin), strike_type, floor_strike, cap_strike)
    return Call(exact, locked=wide == exact)


def price_call(market: KalshiMarket) -> Call | None:
    """Likely (never locked) from Kalshi prices. Active: a side bid at LIKELY_PRICE or
    more. Closed (anything not active): the last trade at LIKELY_PRICE+ (YES) or at
    1 - LIKELY_PRICE or less (NO)."""
    if market.status == "active":
        if market.yes_bid is not None and market.yes_bid >= LIKELY_PRICE:
            return Call("yes")
        if market.no_bid is not None and market.no_bid >= LIKELY_PRICE:
            return Call("no")
        return None
    last = market.last_price
    if last is None:
        return None
    if last >= LIKELY_PRICE:
        return Call("yes")
    if last <= 1 - LIKELY_PRICE:
        return Call("no")
    return None


def combine(
    trade: Trade, observed: Call | None, price: Call | None, extreme_f: int | None = None,
    note: str = "",
) -> Provisional:  # fmt: skip
    """Observed locked wins over everything; otherwise two disagreeing bases are a
    conflict (shown, never totalled)."""
    basis: Basis
    if observed is not None and observed.locked:
        side, sure = observed.side, "locked"
        basis = "both" if price is not None and price.side == side else "observed"
    elif observed is not None and price is not None:
        if observed.side != price.side:
            return Provisional("conflict", "both", None, extreme_f, note)
        side, sure, basis = observed.side, "likely", "both"
    elif observed is not None:
        side, sure, basis = observed.side, "likely", "observed"
    elif price is not None:
        side, sure, basis = price.side, "likely", "price"
    else:
        return Provisional("unknown", None, None, extreme_f, note)
    won = side == trade.side
    status: Status
    if sure == "locked":
        status = "locked_win" if won else "locked_loss"
    else:
        status = "likely_win" if won else "likely_loss"
    return Provisional(status, basis, realized_pnl(trade, won), extreme_f, note)


# --- NWS observations -------------------------------------------------------------------

# METAR remarks: 6-hour maximum (1sTTT) / minimum (2sTTT), s=1 for below zero, tenths °C
_SIX_HOUR = re.compile(r"(?<=\s)([12])([01])(\d{3})(?=\s|=|$)")


@dataclass(frozen=True, slots=True)
class Observation:
    at: datetime
    temp_c: float | None  # temperature.value
    max6_c: float | None  # 6-hour maximum ending at `at`
    min6_c: float | None  # 6-hour minimum ending at `at`
    # temp_c to a tenth (a METAR with its T group); otherwise a whole °C, e.g. the 5-minute
    # reports without a rawMessage, so the true value may be up to 0.5 °C either side
    precise: bool = True


def six_hour_groups(raw: str) -> tuple[float | None, float | None]:
    """(max, min) °C from a METAR's remarks, None where absent."""
    _, sep, remarks = raw.partition(" RMK ")
    if not sep:
        return None, None
    found: dict[str, float] = {}
    for kind, sign, tenths in _SIX_HOUR.findall(f" {remarks}"):
        found.setdefault(kind, (-1 if sign == "1" else 1) * int(tenths) / 10)
    return found.get("1"), found.get("2")


_T_GROUP = re.compile(r"(?<=\s)T[01]\d{3}[01]\d{3}(?=\s|=|$)")


def _number(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def parse_observations(body: dict[str, Any]) -> list[Observation]:
    """api.weather.gov `/stations/{id}/observations` GeoJSON -> observations."""
    out: list[Observation] = []
    features: list[dict[str, Any]] = body.get("features") or []
    for feature in features:
        props: dict[str, Any] = feature.get("properties") or {}
        stamp = props.get("timestamp")
        if not isinstance(stamp, str):
            continue
        temp: dict[str, Any] = props.get("temperature") or {}
        raw = str(props.get("rawMessage") or "")
        hi, lo = six_hour_groups(raw)
        precise = _T_GROUP.search(f" {raw.partition(' RMK ')[2]} ") is not None
        at = datetime.fromisoformat(stamp)
        out.append(Observation(at, _number(temp.get("value")), hi, lo, precise))
    return out


def to_f(c: float) -> float:
    return c * 9 / 5 + 32


def running_extreme(
    observations: Sequence[Observation], measure: Measure, start: datetime, end: datetime
) -> int | None:
    """Highest (high) / lowest (low) temperature in [start, end), whole degrees F: every
    reading taken in the window, and every 6-hour max/min group whose whole period lies in
    it (one reaching back before `start` could carry the previous day's extreme). A whole
    °C reading counts as the least extreme value it may stand for (13 °C as 12.5 for a high),
    so rounding never overstates the extreme."""
    values: list[float] = []
    for o in observations:
        if start <= o.at < end and o.temp_c is not None:
            slack = 0.0 if o.precise else 0.5
            values.append(o.temp_c - slack if measure == "high" else o.temp_c + slack)
        six = o.max6_c if measure == "high" else o.min6_c
        if six is not None and start <= o.at - SIX_HOURS and o.at <= end:
            values.append(six)
    if not values:
        return None
    pick = max(values) if measure == "high" else min(values)
    return whole_degrees(to_f(pick))


# --- fetching ---------------------------------------------------------------------------


class FetchError(RuntimeError):
    pass


class ProvisionalChecker:
    """Fetches what one report run needs, once: one NWS request per station (covering every
    open market's climate day there) and one Kalshi request per market. Create one per run.
    Every failure becomes `unknown` with a note; nothing here raises to the report."""

    def __init__(
        self,
        now: datetime,
        *,
        transport: httpx.BaseTransport | None = None,
        kalshi: KalshiClient | None = None,
        timeout: float = 15.0,
        user_agent: str | None = None,
    ) -> None:
        self.now = now
        self._http = httpx.Client(transport=transport, timeout=timeout)
        self._kalshi = kalshi or KalshiClient(transport=transport, timeout=timeout, max_retries=1)
        self._ua = user_agent or os.environ.get(USER_AGENT_ENV) or USER_AGENT
        self._markets: dict[str, KalshiMarket | str] = {}  # ticker -> market or error
        self._obs: dict[str, list[Observation] | str] = {}  # ICAO -> observations or error
        self._windows: dict[str, tuple[datetime, datetime]] = {}  # ICAO -> span to fetch
        self.requests = 0

    def close(self) -> None:
        self._http.close()
        self._kalshi.close()

    def __enter__(self) -> "ProvisionalChecker":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _window(self, ticker: str) -> tuple[Station, Measure, datetime, datetime] | None:
        """The climate-day window so far, None for non-temperature or not-yet-started."""
        info, day = lookup(ticker), market_date(ticker)
        if info is None or day is None:
            return None
        start, end = climate_day_window(day, ZoneInfo(info.station.tz))
        end = min(end, self.now)
        return (info.station, info.measure, start, end) if start < end else None

    def plan(self, trades: Sequence[Trade]) -> None:
        """Note every station window these trades need, so each station is fetched once."""
        for t in trades:
            w = self._window(t.ticker)
            if w is None:
                continue
            station, _, start, end = w
            lo, hi = self._windows.get(station.icao, (start, end))
            self._windows[station.icao] = (min(lo, start), max(hi, end))

    def market(self, ticker: str) -> KalshiMarket | str:
        if ticker not in self._markets:
            try:
                self.requests += 1
                self._markets[ticker] = self._kalshi.market(ticker)
            except (KalshiError, httpx.HTTPError, KeyError, ValueError) as e:
                self._markets[ticker] = f"kalshi: {e}"
        return self._markets[ticker]

    def observations(
        self, station: Station, start: datetime, end: datetime
    ) -> list[Observation] | str:
        icao = station.icao
        if icao not in self._obs:
            lo, hi = self._windows.get(icao, (start, end))
            lo, hi = min(lo, start), max(hi, end)
            try:
                self._obs[icao] = self._fetch_observations(icao, lo, hi)
            except (FetchError, httpx.HTTPError, KeyError, ValueError, TypeError) as e:
                self._obs[icao] = f"observations {icao}: {e}"
        return self._obs[icao]

    def _fetch_observations(self, icao: str, start: datetime, end: datetime) -> list[Observation]:
        self.requests += 1
        r = self._http.get(
            f"{NWS}/stations/{icao}/observations",
            params={"start": _z(start), "end": _z(end)},
            headers={"User-Agent": self._ua, "Accept": "application/geo+json"},
        )
        if r.is_error:
            raise FetchError(f"HTTP {r.status_code}")
        body: dict[str, Any] = r.json()
        return parse_observations(body)

    def check(self, trade: Trade) -> Provisional:
        notes: list[str] = []
        market = self.market(trade.ticker)
        price: Call | None = None
        if isinstance(market, str):
            notes.append(market)
        else:
            price = price_call(market)
        observed: Call | None = None
        extreme: int | None = None
        w = self._window(trade.ticker)
        if w is not None:
            station, measure, start, end = w
            obs = self.observations(station, start, end)
            if isinstance(obs, str):
                notes.append(obs)
            else:
                extreme = running_extreme(obs, measure, start, end)
            if extreme is not None and not isinstance(market, str):
                observed = observed_call(
                    measure, extreme, market.strike_type, market.floor_strike, market.cap_strike
                )
        return combine(trade, observed, price, extreme, "; ".join(notes))

    def check_all(self, trades: Sequence[Trade]) -> dict[tuple[str, str], Provisional]:
        """(strategy, window_id) -> Provisional for each open trade (excluded ones too; the
        report leaves them out of totals)."""
        self.plan(trades)
        out: dict[tuple[str, str], Provisional] = {}
        for t in trades:
            try:
                out[(t.strategy, t.window_id)] = self.check(t)
            except Exception as e:  # a report must never fail over this
                log.warning("provisional check of %s failed: %s", t.ticker, e)
                out[(t.strategy, t.window_id)] = Provisional(
                    "unknown", None, None, None, f"error: {e}"
                )
        return out


def _z(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
