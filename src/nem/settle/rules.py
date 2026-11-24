"""Settlement rules for daily temperature markets: the climate day, rounding, what the
market's rules name as source, and which side wins for an observed value.

Climate day. The NWS Daily Climate Report (CLI) covers midnight to midnight local
*standard* time; its tables say "TIME (LST)". During daylight saving time that is
01:00 to 00:59 local clock time, so a reading at 00:30 EDT on Jul 2 belongs to Jul 1. The
day is always 24 hours, including on the days clocks change. Phoenix (no DST) is the
same in both. Kalshi's own trading ends at 11:59 PM (ET or local), so in summer the
market stops trading while up to an hour of its climate day is still to come.

Rounding. CLI values are whole degrees F. Fallback readings with decimals are rounded
half up (x.5 -> x+1, -0.5 -> 0) before being compared with a strike, because the market
settles on the whole-degree value.

Strikes, as Kalshi defines them (`strike_type`, `floor_strike`, `cap_strike`; checked on
every traded market): `T` tickers are one-sided, either `less` with `cap_strike` = the
number in the ticker ("less than 53", YES if the value is 52 or below) or `greater` with
`floor_strike` = that number ("greater than 30", YES at 31 or above). `B` tickers are
`between` with floor = ticker - 0.5 and cap = ticker + 0.5, both inclusive (B16.5 = 16 or
17). The ticker letter alone doesn't say which way a `T` market points: always use the
strike type.
"""

import math
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from nem.core.types import MarketSnapshot, Side, StrikeType

RulesSource = Literal["nws_cli", "twc", "wu", "unverified"]
Status = Literal["agree", "disagree", "kalshi_missing", "source_missing"]
STATUSES: tuple[Status, ...] = ("agree", "disagree", "kalshi_missing", "source_missing")


def standard_offset(tz: ZoneInfo, day: date) -> timedelta:
    """The zone's UTC offset without daylight saving on `day` (EST = -5h all year)."""
    noon = datetime.combine(day, time(12), tz)
    offset, dst = noon.utcoffset(), noon.dst()
    assert offset is not None
    return offset - (dst or timedelta(0))


def climate_day_window(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start, end) in UTC of the CLI climate day `day`: local standard midnight to midnight."""
    start = datetime.combine(day, time(0), UTC) - standard_offset(tz, day)
    return start, start + timedelta(days=1)


def climate_day(instant: datetime, tz: ZoneInfo) -> date:
    """The CLI climate day an instant belongs to."""
    if instant.tzinfo is None:
        raise ValueError(f"naive datetime: {instant!r}")
    local_day = instant.astimezone(tz).date()
    for day in (local_day - timedelta(days=1), local_day):
        start, end = climate_day_window(day, tz)
        if start <= instant < end:
            return day
    return local_day


def whole_degrees(value: float) -> int:
    """Round half up to a whole degree, as the market settles on whole degrees."""
    return math.floor(value + 0.5)


def rules_source(rules_primary: str) -> RulesSource:
    """What the market's rules name as the settlement source."""
    text = rules_primary.lower()
    found: list[RulesSource] = []
    if "climatological report" in text:
        found.append("nws_cli")
    if "weather company" in text:
        found.append("twc")
    if "weather underground" in text or "wunderground" in text:
        found.append("wu")
    return found[0] if len(found) == 1 else "unverified"


def rules_measure(rules_primary: str) -> Literal["high", "low"] | None:
    text = rules_primary.lower()
    high = "maximum temperature" in text or "highest temperature" in text
    low = "minimum temperature" in text or "lowest temperature" in text
    return "high" if high and not low else "low" if low and not high else None


_CLI_CODE = re.compile(r"\b(CLI[A-Z]{3})\b")


def rules_stations(*texts: str) -> set[str]:
    """CLI ids named anywhere in the rules, e.g. {"CLIDCA"}."""
    return {m for t in texts for m in _CLI_CODE.findall(t)}


def derive_result(
    value: float,
    strike_type: StrikeType | None,
    floor_strike: float | None,
    cap_strike: float | None,
) -> Side | None:
    """The winning side for an observed value, from the market's own strike definition.
    None when the strike is unknown."""
    snap = _strike_only(strike_type, floor_strike, cap_strike)
    yes = snap.resolves_yes(whole_degrees(value))
    return None if yes is None else "yes" if yes else "no"


def _strike_only(
    strike_type: StrikeType | None, floor_strike: float | None, cap_strike: float | None
) -> MarketSnapshot:
    # reuse the engine's strike semantics (one definition of what YES means)
    t = datetime(2000, 1, 1, tzinfo=UTC)
    return MarketSnapshot(
        ts=t, series="", window_id="", ticker="", open_time=t, close_time=t + timedelta(1),
        yes_bid=None, yes_ask=None, no_bid=None, no_ask=None,
        strike_type=strike_type, floor_strike=floor_strike, cap_strike=cap_strike,
    )  # fmt: skip


def status_of(derived: Side | None, kalshi_result: str | None) -> Status:
    """`kalshi_missing` covers anything that isn't a yes/no result (unsettled, a fetch
    failure, or "scalar": settled at a price instead of to one side)."""
    if derived is None:
        return "source_missing"
    if kalshi_result not in ("yes", "no"):
        return "kalshi_missing"
    return "agree" if derived == kalshi_result else "disagree"
