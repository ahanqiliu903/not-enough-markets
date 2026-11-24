"""Fetch settled markets from Kalshi's public historical API (no key) into a `HistoryDb`.

Endpoints (docs.kalshi.com, checked 2026-11-21):

- `GET /historical/markets?series_ticker=S&limit=1000&cursor=...`: every archived market of
  the series. There is no close-time filter; the window is applied here, by the date in
  the event ticker.
- `GET /historical/markets/{ticker}/candlesticks?start_ts&end_ts&period_interval=1`: one
  candle per minute (at most 5,000 per request), OHLC of the best YES bid and ask, trade
  price, volume and open interest. No depth, no sizes at the top of book.
- `GET /historical/trades?ticker=T&limit=1000&cursor=...`: trade prints, newest first.

Requests are cached raw on disk (`--cache`), paced (`pause` seconds between network
requests) and retried with backoff on 429 / 5xx (`nem.backfill.sources.ArchiveClient`).
"""

import json
import logging
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from nem.backfill.sources import ArchiveClient, ArchiveError
from nem.core.types import MarketSnapshot, Side, StrikeType
from nem.gates.feeds import market_date
from nem.history.db import HistoryDb, HistoryMarket, Print

log = logging.getLogger(__name__)

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
MAX_CANDLES = 5000  # per request (docs)
PAGE = 1000
STRIKE_TYPES: tuple[StrikeType, ...] = (
    "greater", "greater_or_equal", "less", "less_or_equal", "between",
)  # fmt: skip


class HistoryError(RuntimeError):
    pass


type Json = dict[str, Any]


class HistoryClient:
    """JSON GETs against the historical API through a caching, paced `ArchiveClient`."""

    def __init__(self, archive: ArchiveClient, base_url: str = BASE_URL) -> None:
        self._archive = archive
        self._base = base_url

    @classmethod
    def create(cls, cache: Path | None, pause: float = 0.25) -> "HistoryClient":
        return cls(ArchiveClient(cache, pause={"kalshi.com": pause}))

    @property
    def requests(self) -> int:
        return self._archive.requests

    @property
    def cache_hits(self) -> int:
        return self._archive.cache_hits

    def close(self) -> None:
        self._archive.close()

    def get(self, path: str, params: dict[str, str]) -> Json:
        try:
            text = self._archive.get_text(self._base + path, params)
        except ArchiveError as e:
            raise HistoryError(str(e)) from e
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise HistoryError(f"GET {path}: not JSON ({text[:100]!r})") from e
        if not isinstance(data, dict):
            raise HistoryError(f"GET {path} {params}: not an object")
        obj = cast(Json, data)
        if "error" in obj:
            raise HistoryError(f"GET {path} {params}: {str(obj['error'])[:200]}")
        return obj

    def _pages(self, path: str, params: dict[str, str], key: str) -> list[Json]:
        out: list[Json] = []
        cursor = ""
        while True:
            data = self.get(path, {**params, "limit": str(PAGE), "cursor": cursor})
            items = cast(list[Json], data.get(key) or [])
            out += items
            cursor = str(data.get("cursor") or "")
            if not items or not cursor:
                return out

    def series_markets(self, series: str) -> list[Json]:
        return self._pages("/historical/markets", {"series_ticker": series}, "markets")

    def candles(self, ticker: str, start: datetime, end: datetime) -> list[Json]:
        out: list[Json] = []
        lo = int(start.timestamp())
        hi = int(end.timestamp())
        step = (MAX_CANDLES - 1) * 60
        while lo <= hi:
            top = min(hi, lo + step)
            data = self.get(
                f"/historical/markets/{ticker}/candlesticks",
                {"start_ts": str(lo), "end_ts": str(top), "period_interval": "1"},
            )
            out += cast(list[Json], data.get("candlesticks") or [])
            lo = top + 1
        return out

    def trades(self, ticker: str) -> list[Json]:
        return self._pages("/historical/trades", {"ticker": ticker}, "trades")


# --- parsing ------------------------------------------------------------------------------


def _time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _num(v: object) -> float | None:
    if isinstance(v, bool) or v is None or v == "":
        return None
    if isinstance(v, int | float | str):
        return float(v)
    return None


def _bid(v: object) -> float | None:
    p = _num(v)
    return None if p is None or p <= 0 else round(p, 4)


def _ask(v: object) -> float | None:
    p = _num(v)
    return None if p is None or p >= 1 else round(p, 4)


def parse_market(m: Json, series: str) -> HistoryMarket | None:
    """None when the ticker has no date (not a daily market)."""
    day = market_date(str(m["event_ticker"]))
    if day is None:
        return None
    st = m.get("strike_type")
    settled = m.get("settlement_ts")
    return HistoryMarket(
        ticker=str(m["ticker"]),
        series=series,
        event_ticker=str(m["event_ticker"]),
        market_date=day,
        open_time=_time(str(m["open_time"])),
        close_time=_time(str(m["close_time"])),
        strike_type=st if st in STRIKE_TYPES else None,
        floor_strike=_num(m.get("floor_strike")),
        cap_strike=_num(m.get("cap_strike")),
        result=str(m.get("result") or "") or None,
        settled_at=_time(str(settled)) if settled else None,
        rules_primary=str(m.get("rules_primary") or ""),
    )


def candle_snapshots(m: HistoryMarket, candles: Sequence[Json]) -> list[MarketSnapshot]:
    """One snapshot per candle whose closing best bid / ask differ from the previous
    candle's, stamped at the candle's end (the quote known at that minute). Candles after
    the market's close are dropped. NO quotes are the YES quotes mirrored."""
    out: list[MarketSnapshot] = []
    last: tuple[float | None, float | None] | None = None
    strike = cast(StrikeType | None, m.strike_type)
    for c in sorted(candles, key=lambda c: int(c["end_period_ts"])):
        ts = datetime.fromtimestamp(int(c["end_period_ts"]), UTC)
        if ts < m.open_time or ts >= m.close_time:
            continue
        yb = _bid(cast(Json, c.get("yes_bid") or {}).get("close"))
        ya = _ask(cast(Json, c.get("yes_ask") or {}).get("close"))
        if (yb, ya) == last:
            continue
        last = (yb, ya)
        out.append(
            MarketSnapshot(
                ts=ts, series=m.series, window_id=m.event_ticker, ticker=m.ticker,
                open_time=m.open_time, close_time=m.close_time, yes_bid=yb, yes_ask=ya,
                no_bid=None if ya is None else round(1 - ya, 4),
                no_ask=None if yb is None else round(1 - yb, 4),
                strike_type=strike, floor_strike=m.floor_strike, cap_strike=m.cap_strike,
            )
        )  # fmt: skip
    return out


def parse_print(t: Json) -> Print | None:
    count = _num(t.get("count_fp") if t.get("count_fp") is not None else t.get("count"))
    yes = _num(t.get("yes_price_dollars"))
    no = _num(t.get("no_price_dollars"))
    if count is None or count <= 0 or yes is None or no is None:
        return None
    side = t.get("taker_outcome_side") or t.get("taker_side")
    taker: Side | None = "yes" if side == "yes" else "no" if side == "no" else None
    return Print(
        str(t["ticker"]), str(t["trade_id"]), _time(str(t["created_time"])), yes, no, count,
        taker,
    )  # fmt: skip


# --- import -------------------------------------------------------------------------------


@dataclass
class ImportResult:
    markets_listed: int = 0
    markets_in_window: int = 0
    imported: int = 0
    skipped_existing: int = 0
    unsettled: int = 0  # no yes/no result archived (stored, but replay can't settle them)
    candles: int = 0
    snapshots: int = 0
    prints: int = 0
    problems: list[str] = field(default_factory=list[str])


def import_history(
    client: HistoryClient,
    db: HistoryDb,
    series: Sequence[str],
    start: date,
    end: date,
    now: datetime,
    progress: Callable[[str], None] | None = None,
    strike_types: Collection[str] | None = None,
) -> ImportResult:
    """Import every archived market of `series` whose market date is in [start, end]
    (and, if `strike_types` is given, whose strike type is one of them, e.g. only the
    lowest bracket `less`). Markets already imported are skipped (resume). A market that
    fails to fetch is reported and left out; it can be retried by running the import
    again."""
    if strike_types is not None:
        unknown = sorted(set(strike_types) - set(STRIKE_TYPES))
        if unknown:
            raise ValueError(f"unknown strike types {unknown}; known: {list(STRIKE_TYPES)}")
    res = ImportResult()
    for s in series:
        listed = client.series_markets(s)
        res.markets_listed += len(listed)
        markets = [
            hm
            for raw in listed
            if (hm := parse_market(raw, s)) is not None
            and start <= hm.market_date <= end
            and (strike_types is None or hm.strike_type in strike_types)
        ]
        res.markets_in_window += len(markets)
        for i, m in enumerate(sorted(markets, key=lambda m: (m.market_date, m.ticker))):
            if db.has_market(m.ticker):
                res.skipped_existing += 1
                continue
            try:
                candles = client.candles(m.ticker, m.open_time, m.close_time + timedelta(minutes=1))
                prints = [p for t in client.trades(m.ticker) if (p := parse_print(t)) is not None]
            except HistoryError as e:
                res.problems.append(f"{m.ticker}: {e}")
                continue
            snaps = candle_snapshots(m, candles)
            db.save_market(m, snaps, len(candles), prints, now)
            res.imported += 1
            res.candles += len(candles)
            res.snapshots += len(snaps)
            res.prints += len(prints)
            if m.result not in ("yes", "no"):
                res.unsettled += 1
            if progress is not None and (i + 1) % 50 == 0:
                progress(f"{s}: {i + 1}/{len(markets)} markets ({client.requests} requests)")
    return res
