"""The history database: a separate SQLite file with the normal schema plus two tables.

- `snapshots` / `settlements` (normal schema): one snapshot per minute **whose top of book
  changed** (candles with the same bid and ask as the minute before are not repeated, to
  keep the file small), never any depth. Replay it with `HistorySource`, which fills the
  unchanged minutes back in; the plain `ReplaySource` would see a market only in minutes
  its quote moved.
- `history_markets`: one row per imported market (provenance `historical_candles`), written
  last, so a market with a row is complete and an interrupted import resumes after it.
- `trade_prints`: every trade print Kalshi archived for those markets.

The tables are created here, not in `nem/store/schema.sql`: they exist only in history
files, and the engine database (`data/nem.db`) and its schema version are untouched.
"""

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from nem.core.types import MarketSnapshot, Side
from nem.store import Store

PROVENANCE = "historical_candles"
LABEL = "historical candles, no depth"

DDL = f"""
CREATE TABLE IF NOT EXISTS history_markets (
    ticker       TEXT PRIMARY KEY,
    series       TEXT NOT NULL,
    event_ticker TEXT NOT NULL,
    market_date  TEXT NOT NULL,        -- YYYY-MM-DD from the event ticker
    open_time    TEXT NOT NULL,
    close_time   TEXT NOT NULL,
    strike_type  TEXT,
    floor_strike REAL,
    cap_strike   REAL,
    result       TEXT,                 -- Kalshi's result as archived (yes / no / other)
    settled_at   TEXT,
    rules_primary TEXT NOT NULL DEFAULT '',
    candles      INTEGER NOT NULL,     -- per-minute candles received
    snapshots    INTEGER NOT NULL,     -- of which stored (top of book changed)
    prints       INTEGER NOT NULL,
    provenance   TEXT NOT NULL DEFAULT '{PROVENANCE}' CHECK (provenance = '{PROVENANCE}'),
    fetched_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trade_prints (
    trade_id     TEXT PRIMARY KEY,
    ticker       TEXT NOT NULL,
    created_time TEXT NOT NULL,
    yes_price    REAL NOT NULL,
    no_price     REAL NOT NULL,
    count        REAL NOT NULL CHECK (count > 0),
    taker_side   TEXT,                 -- outcome side the taker bought, if given
    provenance   TEXT NOT NULL DEFAULT '{PROVENANCE}' CHECK (provenance = '{PROVENANCE}')
);
CREATE INDEX IF NOT EXISTS trade_prints_ticker ON trade_prints (ticker, created_time);
"""


def _ts(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class Print:
    ticker: str
    trade_id: str
    created_time: datetime
    yes_price: float
    no_price: float
    count: float
    taker_side: Side | None

    def price(self, side: Side) -> float:
        return self.yes_price if side == "yes" else self.no_price


@dataclass(frozen=True, slots=True)
class HistoryMarket:
    ticker: str
    series: str
    event_ticker: str
    market_date: date
    open_time: datetime
    close_time: datetime
    strike_type: str | None
    floor_strike: float | None
    cap_strike: float | None
    result: str | None
    settled_at: datetime | None
    rules_primary: str


def is_history_db(path: str | Path) -> bool:
    """Whether `path` is a history file (has `history_markets`), without creating it."""
    p = Path(path)
    if not p.exists():
        return False
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'history_markets'"
        ).fetchone()
    finally:
        conn.close()
    return row is not None


class HistoryDb:
    """A history file: the normal `Store` (snapshots, settlements, forecast_backfill, ...)
    plus the history tables, through a second connection."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.store = Store(self.path)
        self._conn = sqlite3.connect(self.path)
        # several imports may write one file at once (one per series)
        self._conn.execute("PRAGMA busy_timeout = 120000")
        self._conn.executescript(DDL)

    def close(self) -> None:
        self._conn.close()
        self.store.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def has_market(self, ticker: str) -> bool:
        q = "SELECT 1 FROM history_markets WHERE ticker = ?"
        return self._conn.execute(q, (ticker,)).fetchone() is not None

    def save_market(
        self,
        m: HistoryMarket,
        snapshots: Sequence[MarketSnapshot],
        candles: int,
        prints: Sequence[Print],
        fetched_at: datetime,
    ) -> None:
        """Replace whatever a previous, interrupted import left for this market and write
        it in one transaction, so a market is either complete or absent."""
        with self._conn:
            self._conn.execute("DELETE FROM snapshots WHERE ticker = ?", (m.ticker,))
            self._conn.execute("DELETE FROM trade_prints WHERE ticker = ?", (m.ticker,))
            self._conn.execute("DELETE FROM history_markets WHERE ticker = ?", (m.ticker,))
            # same columns as Store.insert_snapshots; history has no depth
            self._conn.executemany(
                "INSERT INTO snapshots (ts, series, window_id, ticker, open_time, close_time,"
                " yes_bid, yes_ask, no_bid, no_ask, depth_json, strike_type, floor_strike,"
                " cap_strike) VALUES (?,?,?,?,?,?,?,?,?,?,'{}',?,?,?)",
                [
                    (_ts(x.ts), x.series, x.window_id, x.ticker, _ts(x.open_time),
                     _ts(x.close_time), x.yes_bid, x.yes_ask, x.no_bid, x.no_ask,
                     x.strike_type, x.floor_strike, x.cap_strike)
                    for x in snapshots
                ],
            )  # fmt: skip
            if m.result in ("yes", "no") and m.settled_at is not None:
                self._conn.execute(
                    "INSERT OR IGNORE INTO settlements (ticker, series, window_id, result,"
                    " settled_at) VALUES (?,?,?,?,?)",
                    (m.ticker, m.series, m.event_ticker, m.result, _ts(m.settled_at)),
                )
            self._conn.executemany(
                "INSERT OR IGNORE INTO trade_prints (trade_id, ticker, created_time, yes_price,"
                " no_price, count, taker_side) VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        p.trade_id,
                        p.ticker,
                        _ts(p.created_time),
                        p.yes_price,
                        p.no_price,
                        p.count,
                        p.taker_side,
                    )
                    for p in prints
                ],
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO history_markets (ticker, series, event_ticker, market_date,"
                " open_time, close_time, strike_type, floor_strike, cap_strike, result,"
                " settled_at, rules_primary, candles, snapshots, prints, fetched_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    m.ticker, m.series, m.event_ticker, m.market_date.isoformat(),
                    _ts(m.open_time), _ts(m.close_time), m.strike_type, m.floor_strike,
                    m.cap_strike, m.result,
                    None if m.settled_at is None else _ts(m.settled_at), m.rules_primary,
                    candles, len(snapshots), len(prints), _ts(fetched_at),
                ),
            )  # fmt: skip

    def markets(
        self, series: Sequence[str] | None = None, start: date | None = None,
        end: date | None = None,
    ) -> list[HistoryMarket]:  # fmt: skip
        """Imported markets, by date then ticker; `start`/`end` inclusive market dates."""
        where: list[str] = []
        args: list[str] = []
        if series is not None:
            where.append(f"series IN ({','.join('?' * len(series))})")
            args += list(series)
        if start is not None:
            where.append("market_date >= ?")
            args.append(start.isoformat())
        if end is not None:
            where.append("market_date <= ?")
            args.append(end.isoformat())
        sql = "SELECT * FROM history_markets" + (" WHERE " + " AND ".join(where) if where else "")
        self._conn.row_factory = sqlite3.Row
        try:
            rows = self._conn.execute(sql + " ORDER BY market_date, ticker", args).fetchall()
        finally:
            self._conn.row_factory = None
        return [
            HistoryMarket(
                r["ticker"], r["series"], r["event_ticker"], date.fromisoformat(r["market_date"]),
                datetime.fromisoformat(r["open_time"]), datetime.fromisoformat(r["close_time"]),
                r["strike_type"], r["floor_strike"], r["cap_strike"], r["result"],
                None if r["settled_at"] is None else datetime.fromisoformat(r["settled_at"]),
                r["rules_primary"],
            )
            for r in rows
        ]  # fmt: skip

    def prints(self, tickers: Iterable[str]) -> dict[str, list[Print]]:
        """Trade prints per ticker, oldest first."""
        out: dict[str, list[Print]] = {}
        for t in sorted(set(tickers)):
            rows = self._conn.execute(
                "SELECT trade_id, created_time, yes_price, no_price, count, taker_side"
                " FROM trade_prints WHERE ticker = ? ORDER BY created_time, trade_id",
                (t,),
            ).fetchall()
            out[t] = [
                Print(
                    t, tid, datetime.fromisoformat(ct), yp, np_, n,
                    "yes" if ts == "yes" else "no" if ts == "no" else None,
                )
                for tid, ct, yp, np_, n, ts in rows
            ]  # fmt: skip
        return out

    def counts(self) -> dict[str, int]:
        q = {
            "markets": "SELECT COUNT(*) FROM history_markets",
            "settled": "SELECT COUNT(*) FROM settlements",
            "snapshots": "SELECT COUNT(*) FROM snapshots",
            "candles": "SELECT COALESCE(SUM(candles), 0) FROM history_markets",
            "prints": "SELECT COUNT(*) FROM trade_prints",
            "events": "SELECT COUNT(DISTINCT event_ticker) FROM history_markets",
        }
        return {k: int(self._conn.execute(v).fetchone()[0]) for k, v in q.items()}
