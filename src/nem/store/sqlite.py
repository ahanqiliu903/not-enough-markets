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
    Fill,
    MarketSnapshot,
    Mode,
    Order,
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
    return {side: [(float(p), int(q)) for p, q in levels] for side, levels in raw.items()}


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
        depth = {side: [list(level) for level in levels] for side, levels in snap.depth.items()}
        with self._conn:
            self._conn.execute(
                "INSERT INTO snapshots (ts, series, window_id, ticker, open_time, close_time,"
                " yes_bid, yes_ask, no_bid, no_ask, depth_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
                    json.dumps(depth),
                ),
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
                    " qty, fee, mode, opened_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
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

    def open_trades(self, portfolio: str | None = None) -> list[Trade]:
        sql = "SELECT * FROM trades WHERE settled_at IS NULL"
        args: tuple[str, ...] = ()
        if portfolio is not None:
            sql += " AND portfolio = ?"
            args = (portfolio,)
        return [_trade(r) for r in self._conn.execute(sql + " ORDER BY opened_at", args)]

    def settled_trades(
        self,
        strategies: Sequence[StrategyKey] | None = None,
        before: datetime | None = None,
        limit: int | None = None,
    ) -> list[Trade]:
        """Settled trades, oldest first. `before` is strict: `settled_at < before`.
        `limit` keeps the most recent N."""
        sql = "SELECT * FROM trades WHERE settled_at IS NOT NULL"
        args: list[str | int] = []
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
        won=None if r["won"] is None else bool(r["won"]),
        realized_pnl=r["realized_pnl"],
        settled_at=None if r["settled_at"] is None else _dt(r["settled_at"]),
    )
