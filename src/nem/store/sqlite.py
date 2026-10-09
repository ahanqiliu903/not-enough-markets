"""SQLite store: snapshots, signals, orders, fills, trades, stats, heartbeats.

Rows are keyed by (portfolio, strategy). File databases use WAL, so a separate reporter
process can read while the runner writes.
"""

import json
import sqlite3
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from nem.core.types import (
    DecisionKind,
    Depth,
    FeedValue,
    Fill,
    MarketSnapshot,
    Mode,
    Order,
    Settlement,
    Side,
    Signal,
    StrategyKey,
    Trade,
)

SCHEMA_VERSION = 1


class StoreError(RuntimeError):
    pass


def _ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime: {dt!r}")
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _depth_from_json(s: str) -> Depth:
    raw: dict[Side, list[list[Any]]] = json.loads(s)
    return {side: [(float(p), float(q)) for p, q in levels] for side, levels in raw.items()}


def _strategies_filter(strategies: Sequence[StrategyKey]) -> tuple[str, list[str]]:
    clause = " OR ".join(["(portfolio = ? AND strategy = ?)"] * len(strategies)) or "0"
    return f"({clause})", [part for key in strategies for part in key]


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if str(path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        version: int = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version > SCHEMA_VERSION:
            raise StoreError(f"database schema v{version} is newer than code (v{SCHEMA_VERSION})")
        schema = files("nem.store").joinpath("schema.sql").read_text()
        self._conn.executescript(f"BEGIN; {schema} PRAGMA user_version = {SCHEMA_VERSION}; COMMIT;")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- snapshots ---------------------------------------------------------

    def insert_snapshot(self, snap: MarketSnapshot) -> None:
        self.insert_snapshots([snap])

    def insert_snapshots(self, snaps: Sequence[MarketSnapshot]) -> None:
        """Insert in one transaction (one poll's worth)."""
        with self._conn:
            self._conn.executemany(
                "INSERT INTO snapshots (ts, series, window_id, ticker, open_time, close_time,"
                " yes_bid, yes_ask, no_bid, no_ask, depth_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        _ts(snap.ts),
                        snap.series,
                        snap.window_id,
                        snap.ticker,
                        _ts(snap.open_time),
                        _ts(snap.close_time),
                        snap.yes_bid,
                        snap.yes_ask,
                        snap.no_bid,
                        snap.no_ask,
                        json.dumps(
                            {side: [list(lv) for lv in lvs] for side, lvs in snap.depth.items()}
                        ),
                    )
                    for snap in snaps
                ],
            )

    def iter_snapshots(
        self,
        series: Sequence[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Iterator[MarketSnapshot]:
        """Snapshots in time order, `start <= ts < end`."""
        where: list[str] = []
        args: list[str] = []
        if series is not None:
            where.append(f"series IN ({','.join('?' * len(series))})")
            args.extend(series)
        if start is not None:
            where.append("ts >= ?")
            args.append(_ts(start))
        if end is not None:
            where.append("ts < ?")
            args.append(_ts(end))
        sql = "SELECT * FROM snapshots"
        if where:
            sql += " WHERE " + " AND ".join(where)
        for r in self._conn.execute(sql + " ORDER BY ts, id", args):
            yield MarketSnapshot(
                ts=_dt(r["ts"]),
                series=r["series"],
                window_id=r["window_id"],
                ticker=r["ticker"],
                open_time=_dt(r["open_time"]),
                close_time=_dt(r["close_time"]),
                yes_bid=r["yes_bid"],
                yes_ask=r["yes_ask"],
                no_bid=r["no_bid"],
                no_ask=r["no_ask"],
                depth=_depth_from_json(r["depth_json"]),
            )

    # --- settlements --------------------------------------------------------

    def record_settlement(self, settlement: Settlement) -> None:
        """Idempotent: recording the same market twice keeps the first row."""
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO settlements (ticker, series, window_id, result, settled_at)"
                " VALUES (?,?,?,?,?)",
                (
                    settlement.ticker,
                    settlement.series,
                    settlement.window_id,
                    settlement.result,
                    _ts(settlement.settled_at),
                ),
            )

    def settlement(self, ticker: str, as_of: datetime | None = None) -> Settlement | None:
        """The market's outcome, or None if unknown (or not yet settled as of `as_of`)."""
        sql = "SELECT * FROM settlements WHERE ticker = ?"
        args = [ticker]
        if as_of is not None:
            sql += " AND settled_at <= ?"
            args.append(_ts(as_of))
        r = self._conn.execute(sql, args).fetchone()
        if r is None:
            return None
        return Settlement(
            r["ticker"], r["series"], r["window_id"], r["result"], _dt(r["settled_at"])
        )

    def unsettled_tickers(self, now: datetime) -> list[str]:
        """Recorded markets that have closed but have no settlement row yet."""
        rows = self._conn.execute(
            "SELECT DISTINCT ticker FROM snapshots WHERE close_time <= ?"
            " AND ticker NOT IN (SELECT ticker FROM settlements) ORDER BY ticker",
            (_ts(now),),
        )
        return [r[0] for r in rows]

    # --- signals, orders, fills -------------------------------------------

    def log_signal(
        self,
        sig: Signal,
        ts: datetime,
        decision: DecisionKind,
        reason: str,
        edge: float | None = None,
    ) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO signals (ts, portfolio, strategy, window_id, ticker, side, price,"
                " p_model, edge, decision, reason, meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    _ts(ts),
                    sig.portfolio,
                    sig.strategy,
                    sig.window_id,
                    sig.ticker,
                    sig.side,
                    sig.limit_price,
                    sig.p_model,
                    edge,
                    decision,
                    reason,
                    json.dumps(dict(sig.meta), default=str),
                ),
            )
        return _lastrowid(cur)

    def signal_reasons(self, portfolio: str, strategy: str) -> dict[str, int]:
        """Count of signals per reason, e.g. how often each gate blocked."""
        rows = self._conn.execute(
            "SELECT reason, COUNT(*) FROM signals WHERE portfolio = ? AND strategy = ?"
            " GROUP BY reason",
            (portfolio, strategy),
        )
        return {r[0]: r[1] for r in rows}

    def record_order(self, order: Order, ts: datetime, mode: Mode, status: str) -> int:
        try:
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO orders (ts, portfolio, strategy, client_order_id, window_id,"
                    " ticker, side, limit_price, qty, mode, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        _ts(ts),
                        order.portfolio,
                        order.strategy,
                        order.client_order_id,
                        order.window_id,
                        order.ticker,
                        order.side,
                        order.limit_price,
                        order.qty,
                        mode,
                        status,
                    ),
                )
        except sqlite3.IntegrityError as e:
            raise StoreError(f"duplicate client_order_id {order.client_order_id}") from e
        return _lastrowid(cur)

    def set_order_status(self, client_order_id: str, status: str) -> None:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE orders SET status = ? WHERE client_order_id = ?", (status, client_order_id)
            )
        if cur.rowcount != 1:
            raise StoreError(f"no order {client_order_id}")

    def order_status(self, client_order_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT status FROM orders WHERE client_order_id = ?", (client_order_id,)
        ).fetchone()
        return None if row is None else row[0]

    def order_attempts(self, key: StrategyKey, window_id: str, side: Side) -> int:
        """Orders already sent for this strategy, window and side (next attempt number)."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM orders WHERE portfolio = ? AND strategy = ? AND window_id = ?"
            " AND side = ?",
            (*key, window_id, side),
        ).fetchone()
        return int(row[0])

    def record_fill(self, fill: Fill) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO fills (client_order_id, ts, price, qty, fee) VALUES (?,?,?,?,?)",
                (fill.client_order_id, _ts(fill.ts), fill.price, fill.qty, fill.fee),
            )

    # --- trades -------------------------------------------------------------

    def open_trade(self, trade: Trade) -> None:
        if trade.settled:
            raise StoreError("open_trade takes an unsettled trade")
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO trades (portfolio, strategy, window_id, ticker, side, avg_price,"
                    " qty, fee, mode, opened_at, close_time) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        trade.portfolio,
                        trade.strategy,
                        trade.window_id,
                        trade.ticker,
                        trade.side,
                        trade.avg_price,
                        trade.qty,
                        trade.fee,
                        trade.mode,
                        _ts(trade.opened_at),
                        _ts(trade.close_time),
                    ),
                )
        except sqlite3.IntegrityError as e:
            raise StoreError(
                f"trade already open for {trade.portfolio}/{trade.strategy} {trade.window_id}"
            ) from e

    def settle_trade(
        self,
        key: StrategyKey,
        window_id: str,
        won: bool,
        realized_pnl: float,
        settled_at: datetime,
    ) -> None:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE trades SET won = ?, realized_pnl = ?, settled_at = ?"
                " WHERE portfolio = ? AND strategy = ? AND window_id = ? AND settled_at IS NULL",
                (int(won), realized_pnl, _ts(settled_at), *key, window_id),
            )
        if cur.rowcount != 1:
            raise StoreError(f"no open trade for {key[0]}/{key[1]} {window_id}")

    def open_trades(
        self, portfolio: str | None = None, strategy: StrategyKey | None = None
    ) -> list[Trade]:
        sql = "SELECT * FROM trades WHERE settled_at IS NULL"
        args: list[str] = []
        if portfolio is not None:
            sql += " AND portfolio = ?"
            args.append(portfolio)
        if strategy is not None:
            sql += " AND portfolio = ? AND strategy = ?"
            args.extend(strategy)
        return [_trade(r) for r in self._conn.execute(sql + " ORDER BY opened_at", args)]

    def trades_due(self, now: datetime) -> list[Trade]:
        """Open trades whose market has closed, so their result may be available."""
        rows = self._conn.execute(
            "SELECT * FROM trades WHERE settled_at IS NULL AND close_time <= ? ORDER BY close_time",
            (_ts(now),),
        )
        return [_trade(r) for r in rows]

    def has_trade(self, key: StrategyKey, window_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM trades WHERE portfolio = ? AND strategy = ? AND window_id = ?",
            (*key, window_id),
        ).fetchone()
        return row is not None

    def settled_trades(
        self,
        strategies: Sequence[StrategyKey] | None = None,
        before: datetime | None = None,
        limit: int | None = None,
        portfolio: str | None = None,
    ) -> list[Trade]:
        """Settled trades, oldest first. `before` is strict: `settled_at < before`.
        `limit` keeps the most recent N."""
        sql = "SELECT * FROM trades WHERE settled_at IS NOT NULL"
        args: list[str | int] = []
        if portfolio is not None:
            sql += " AND portfolio = ?"
            args.append(portfolio)
        if strategies is not None:
            clause, keys = _strategies_filter(strategies)
            sql += f" AND {clause}"
            args.extend(keys)
        if before is not None:
            sql += " AND settled_at < ?"
            args.append(_ts(before))
        sql += " ORDER BY settled_at DESC, portfolio, strategy, window_id"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return [_trade(r) for r in reversed(self._conn.execute(sql, args).fetchall())]

    def p_models(self, portfolio: str, strategy: str) -> dict[str, float]:
        """window_id -> the p_model of the signal that opened that trade (for calibration)."""
        rows = self._conn.execute(
            "SELECT window_id, p_model FROM signals WHERE portfolio = ? AND strategy = ?"
            " AND decision = 'take' ORDER BY ts",
            (portfolio, strategy),
        )
        return {r[0]: r[1] for r in rows}

    def first_activity(self, portfolio: str) -> datetime | None:
        """Earliest trade or ledger entry, i.e. roughly when the portfolio started."""
        row = self._conn.execute(
            "SELECT MIN(t) FROM (SELECT MIN(opened_at) AS t FROM trades WHERE portfolio = ?"
            " UNION ALL SELECT MIN(ts) FROM ledger WHERE portfolio = ?)",
            (portfolio, portfolio),
        ).fetchone()
        return None if row[0] is None else _dt(row[0])

    # --- ledger (interest and other non-trade cash) -----------------------------

    def add_ledger(self, portfolio: str, ts: datetime, kind: str, amount: float) -> bool:
        """Idempotent per (portfolio, kind, ts). Returns False if it was already there."""
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO ledger (portfolio, ts, kind, amount) VALUES (?,?,?,?)",
                (portfolio, _ts(ts), kind, amount),
            )
        return cur.rowcount == 1

    def ledger_total(self, portfolio: str, before: datetime | None = None) -> float:
        sql = "SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE portfolio = ?"
        args = [portfolio]
        if before is not None:
            sql += " AND ts < ?"
            args.append(_ts(before))
        return float(self._conn.execute(sql, args).fetchone()[0])

    def last_ledger(self, portfolio: str, kind: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT MAX(ts) FROM ledger WHERE portfolio = ? AND kind = ?", (portfolio, kind)
        ).fetchone()
        return None if row[0] is None else _dt(row[0])

    # --- feeds ------------------------------------------------------------------

    def record_feed_values(self, values: Sequence[FeedValue], recorded_at: datetime) -> None:
        """Idempotent per (feed, key, known_at)."""
        with self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO feed_values (feed, key, value, known_at, recorded_at)"
                " VALUES (?,?,?,?,?)",
                [(v.feed, v.key, v.value, _ts(v.known_at), _ts(recorded_at)) for v in values],
            )

    def feed_values(
        self,
        feed: str,
        key: str,
        as_of: datetime,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[FeedValue]:
        """Values known by `as_of` (inclusive), oldest first. `limit` keeps the newest N."""
        sql = "SELECT * FROM feed_values WHERE feed = ? AND key = ? AND known_at <= ?"
        args: list[str | int] = [feed, key, _ts(as_of)]
        if since is not None:
            sql += " AND known_at >= ?"
            args.append(_ts(since))
        sql += " ORDER BY known_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        rows = self._conn.execute(sql, args).fetchall()
        return [
            FeedValue(r["feed"], r["key"], r["value"], _dt(r["known_at"])) for r in reversed(rows)
        ]

    # --- stats, heartbeats ---------------------------------------------------

    def bump_stat(self, portfolio: str, strategy: str, key: str, won: bool) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO stats (portfolio, strategy, key, wins, n) VALUES (?, ?, ?, ?, 1)"
                " ON CONFLICT (portfolio, strategy, key)"
                " DO UPDATE SET wins = wins + excluded.wins, n = n + 1",
                (portfolio, strategy, key, int(won)),
            )

    def stat(self, portfolio: str, strategy: str, key: str) -> tuple[int, int]:
        """(wins, n); (0, 0) if never recorded."""
        row = self._conn.execute(
            "SELECT wins, n FROM stats WHERE portfolio = ? AND strategy = ? AND key = ?",
            (portfolio, strategy, key),
        ).fetchone()
        return (0, 0) if row is None else (row[0], row[1])

    def heartbeat(
        self,
        ts: datetime,
        process: str,
        status: str,
        portfolio: str | None = None,
        strategy: str | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO heartbeats (ts, process, portfolio, strategy, status)"
                " VALUES (?,?,?,?,?)",
                (_ts(ts), process, portfolio, strategy, status),
            )

    def last_heartbeat(self, process: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT MAX(ts) FROM heartbeats WHERE process = ?", (process,)
        ).fetchone()
        return None if row[0] is None else _dt(row[0])


def _lastrowid(cur: sqlite3.Cursor) -> int:
    if cur.lastrowid is None:
        raise StoreError("insert returned no row id")
    return cur.lastrowid


def _trade(r: sqlite3.Row) -> Trade:
    return Trade(
        portfolio=r["portfolio"],
        strategy=r["strategy"],
        window_id=r["window_id"],
        ticker=r["ticker"],
        side=r["side"],
        avg_price=r["avg_price"],
        qty=r["qty"],
        fee=r["fee"],
        mode=r["mode"],
        opened_at=_dt(r["opened_at"]),
        close_time=_dt(r["close_time"]),
        won=None if r["won"] is None else bool(r["won"]),
        realized_pnl=r["realized_pnl"],
        settled_at=None if r["settled_at"] is None else _dt(r["settled_at"]),
    )
