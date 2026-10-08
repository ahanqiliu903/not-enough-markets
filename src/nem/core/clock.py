"""Clocks and Kalshi window-code math.

15-minute event tickers look like `SERIES-YYMONDDHHMM` (e.g. `KXBTC15M-26OCT071530`), in
exchange time (US Eastern). The code part is shared across series for the same window,
which is how cross-asset gates line windows up. Verify against current Kalshi docs.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

EXCHANGE_TZ = ZoneInfo("America/New_York")

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_CODE_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})$")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """Clock for replay and tests. Only moves forward."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def set(self, t: datetime) -> None:
        if t < self._now:
            raise ValueError(f"clock cannot move backwards ({t} < {self._now})")
        self._now = t

    def advance(self, delta: timedelta) -> None:
        self.set(self._now + delta)


def split_window_id(window_id: str) -> tuple[str, str]:
    """`"KXBTC15M-26OCT071530"` -> `("KXBTC15M", "26OCT071530")`."""
    series, sep, code = window_id.partition("-")
    if not sep or not series or not _CODE_RE.match(code):
        raise ValueError(f"not a window id: {window_id!r}")
    return series, code


def window_code_to_datetime(code: str) -> datetime:
    m = _CODE_RE.match(code)
    if not m or m.group(2) not in _MONTHS:
        raise ValueError(f"not a window code: {code!r}")
    yy, mon, dd, hh, mm = m.groups()
    return datetime(
        2000 + int(yy), _MONTHS.index(mon) + 1, int(dd), int(hh), int(mm), tzinfo=EXCHANGE_TZ
    )


def datetime_to_window_code(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("dt must be timezone-aware")
    t = dt.astimezone(EXCHANGE_TZ)
    return f"{t:%y}{_MONTHS[t.month - 1]}{t:%d%H%M}"


def shift_window_code(code: str, n: int, minutes: int = 15) -> str:
    """Code of the window `n` windows away (negative = earlier). Uses absolute time, so it
    stays correct across DST changes; wall-clock codes repeat during the fall-back hour."""
    t = window_code_to_datetime(code).astimezone(UTC) + timedelta(minutes=n * minutes)
    return datetime_to_window_code(t)
