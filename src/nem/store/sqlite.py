"""SQLite store: snapshots, signals, orders, fills, trades, stats, heartbeats.

Rows are keyed by (portfolio, strategy). File databases use WAL, so a separate reporter
process can read while the runner writes.
"""

import json
import sqlite3
from bisect import bisect_right
from collections.abc import Generator, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from nem.core.types import (
    DecisionKind,
    Depth,
    FeedValue,
    Fill,
    ManualFill,
    ManualLot,
    MarketSnapshot,
    Mode,
    Order,
    OrderKind,
    OrderRecord,
    Settlement,
    Side,
    Signal,
    StrategyKey,
    Trade,
)

if TYPE_CHECKING:
    from nem.backfill.model import BackfillRow
    from nem.settle.model import SettlementCheck

SCHEMA_VERSION = 9

# Upgrades for existing databases: MIGRATIONS[v] takes a database from v-1 to v. A fresh
# database runs schema.sql (always the latest) instead.
MIGRATIONS: dict[int, str] = {
    2: """
    CREATE TABLE halts (
        scope   TEXT PRIMARY KEY,
        reason  TEXT NOT NULL,
        ts      TEXT NOT NULL
    );
    CREATE INDEX heartbeats_process_ts ON heartbeats (process, ts);
    """,
    3: """
    ALTER TABLE snapshots ADD COLUMN strike_type TEXT;
    ALTER TABLE snapshots ADD COLUMN floor_strike REAL;
    ALTER TABLE snapshots ADD COLUMN cap_strike REAL;
    """,
    # same DDL as schema.sql (comments there)
    4: """
    CREATE TABLE manual_lots (
        portfolio        TEXT NOT NULL,
        strategy         TEXT NOT NULL,
        lot_key          TEXT NOT NULL,
        provenance       TEXT NOT NULL DEFAULT 'manual_import' CHECK (provenance = 'manual_import'),
        ticker           TEXT NOT NULL,
        side             TEXT NOT NULL CHECK (side IN ('yes', 'no')),
        qty              REAL NOT NULL CHECK (qty > 0),
        entry_price      REAL NOT NULL,
        exit_price       REAL NOT NULL,
        open_fees        REAL NOT NULL,
        close_fees       REAL NOT NULL,
        pnl_without_fees REAL NOT NULL,
        realized_pnl     REAL NOT NULL,
        opened_at        TEXT NOT NULL,
        closed_at        TEXT NOT NULL,
        source_file      TEXT NOT NULL,
        source_line      INTEGER NOT NULL,
        imported_at      TEXT NOT NULL,
        PRIMARY KEY (portfolio, lot_key)
    );
    CREATE TABLE manual_fills (
        portfolio   TEXT NOT NULL,
        fill_key    TEXT NOT NULL,
        provenance  TEXT NOT NULL DEFAULT 'manual_import' CHECK (provenance = 'manual_import'),
        ticker      TEXT NOT NULL,
        side        TEXT NOT NULL CHECK (side IN ('yes', 'no')),
        qty         REAL NOT NULL CHECK (qty > 0),
        price       REAL NOT NULL,
        fee         REAL NOT NULL,
        liquidity   TEXT NOT NULL CHECK (liquidity IN ('maker', 'taker')),
        filled_at   TEXT NOT NULL,
        source_file TEXT NOT NULL,
        source_line INTEGER NOT NULL,
        imported_at TEXT NOT NULL,
        PRIMARY KEY (portfolio, fill_key)
    );
    """,
    5: """
    CREATE TABLE settlement_checks (
        ticker                  TEXT PRIMARY KEY,
        series                  TEXT NOT NULL,
        market_date             TEXT,     -- YYYY-MM-DD CLI climate day (local standard time)
        measure                 TEXT NOT NULL CHECK (measure IN ('high', 'low')),
        station                 TEXT NOT NULL,     -- NWS CLI id, e.g. CLINYC ('' if unmapped)
        rules_source            TEXT NOT NULL      -- what the market's own rules name
            CHECK (rules_source IN ('nws_cli', 'twc', 'wu', 'unverified')),
        strike_type             TEXT,
        floor_strike            REAL,
        cap_strike              REAL,
        observed_value          REAL,     -- whole degrees F; NULL when no source had it
        source                  TEXT,     -- iem_cli (NWS CLI via IEM) / iem_asos_obs (approximate)
        source_ref              TEXT,     -- e.g. the CLI product id
        derived_result          TEXT CHECK (derived_result IN ('yes', 'no')),  -- replay truth
        kalshi_result           TEXT,     -- as Kalshi reports it, incl. 'scalar'; NULL if unfetched
        kalshi_value            REAL,     -- Kalshi's expiration_value
        kalshi_settlement_value REAL,     -- dollars paid per YES contract
        status                  TEXT NOT NULL
            CHECK (status IN ('agree', 'disagree', 'kalshi_missing', 'source_missing')),
        note                    TEXT NOT NULL DEFAULT '',
        checked_at              TEXT NOT NULL
    );
    """,
    6: """
    CREATE TABLE forecast_backfill (
        source      TEXT NOT NULL,
        station     TEXT NOT NULL,
        target_date TEXT NOT NULL,
        field       TEXT NOT NULL,
        value       REAL NOT NULL,
        run_time    TEXT NOT NULL,
        valid_from  TEXT NOT NULL,
        known_at    TEXT NOT NULL,
        run_id      TEXT NOT NULL,
        provenance  TEXT NOT NULL DEFAULT 'backfill' CHECK (provenance = 'backfill'),
        fetched_at  TEXT NOT NULL,
        PRIMARY KEY (source, station, target_date, field, run_time)
    );
    CREATE INDEX forecast_backfill_target ON forecast_backfill (station, target_date);
    """,
    # Exits recorded before v7 never fed learning; queue them so the next settlement pass
    # learns from their markets' results, as it does for every exit from now on.
    7: """
    CREATE TABLE exit_learning (
        portfolio  TEXT NOT NULL,
        strategy   TEXT NOT NULL,
        window_id  TEXT NOT NULL,
        won        INTEGER,
        learned_at TEXT,
        PRIMARY KEY (portfolio, strategy, window_id),
        FOREIGN KEY (portfolio, strategy, window_id)
            REFERENCES trades (portfolio, strategy, window_id)
    );
    CREATE INDEX exit_learning_pending ON exit_learning (learned_at);
    INSERT INTO exit_learning (portfolio, strategy, window_id)
    SELECT DISTINCT t.portfolio, t.strategy, t.window_id FROM trades t JOIN orders o
        ON o.portfolio = t.portfolio AND o.strategy = t.strategy AND o.window_id = t.window_id
    WHERE t.settled_at IS NOT NULL AND o.status IN ('exit_take_profit', 'exit_stop');
    """,
    8: "",  # columns only: ADDED_COLUMNS
    # After v9's columns are added: derive what older rows imply. An order already past
    # `submitted` gets its filled quantity from its fills and the rest as cancelled (IOC);
    # one still `submitted` is left for reconciliation (nem.engine.reconcile). Each order's
    # first fill takes the order's id as its fill id (paper orders fill at most once).
    9: """
    UPDATE orders SET kind = 'exit' WHERE status IN ('exit_take_profit', 'exit_stop');
    UPDATE orders SET filled_qty = COALESCE((SELECT SUM(f.qty) FROM fills f
        WHERE f.client_order_id = orders.client_order_id), 0)
    WHERE status != 'submitted' AND filled_qty = 0 AND cancelled_qty = 0;
    UPDATE orders SET cancelled_qty = MAX(qty - filled_qty, 0)
    WHERE status != 'submitted' AND filled_qty + cancelled_qty < qty;
    UPDATE fills SET fill_id = client_order_id WHERE fill_id IS NULL AND id = (SELECT MIN(f.id)
        FROM fills f WHERE f.client_order_id = fills.client_order_id);
    UPDATE fills SET fill_id = client_order_id || '#' || id WHERE fill_id IS NULL;
    CREATE UNIQUE INDEX IF NOT EXISTS fills_fill_id ON fills (fill_id);
    CREATE INDEX IF NOT EXISTS orders_attempts ON orders (portfolio, strategy, window_id, side);
    """,
}

# Columns a version adds to an existing table, as (table, column, type). Added only when
# missing, so the upgrade is idempotent (SQLite has no ADD COLUMN IF NOT EXISTS).
ADDED_COLUMNS: dict[int, list[tuple[str, str, str]]] = {
    8: [("trades", "excluded_at", "TEXT"), ("trades", "exclude_reason", "TEXT")],
    9: [
        ("orders", "kind", "TEXT NOT NULL DEFAULT 'entry' CHECK (kind IN ('entry', 'exit'))"),
        ("orders", "filled_qty", "INTEGER NOT NULL DEFAULT 0"),
        ("orders", "cancelled_qty", "INTEGER NOT NULL DEFAULT 0"),
        ("orders", "close_time", "TEXT"),
        ("fills", "fill_id", "TEXT"),
    ],
}

# Order lifecycle. An entry order is recorded `submitted` before it is sent, then moves to
# exactly one terminal status; an exit order (all-or-nothing, decided and filled in one
# step) is recorded already terminal. Nothing moves out of a terminal status.
ENTRY_TERMINAL = frozenset({"filled", "partial", "no_fill", "rejected_over_risk", "cancelled"})
EXIT_STATUSES = frozenset({"exit_take_profit", "exit_stop"})

HEARTBEAT_RETENTION = timedelta(days=2)


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


# An entry order whose outcome isn't recorded: still submitted, or filled without a trade.
_UNRESOLVED_ENTRY = (
    "(o.status = 'submitted' OR (o.status IN ('filled', 'partial') AND NOT EXISTS"
    " (SELECT 1 FROM trades t WHERE t.portfolio = o.portfolio AND t.strategy = o.strategy"
    " AND t.window_id = o.window_id)))"
)


def _strategies_filter(strategies: Sequence[StrategyKey]) -> tuple[str, list[str]]:
    clause = " OR ".join(["(portfolio = ? AND strategy = ?)"] * len(strategies)) or "0"
    return f"({clause})", [part for key in strategies for part in key]


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(path)
        self._tx_depth = 0
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
        if version == 0:
            script = files("nem.store").joinpath("schema.sql").read_text()
        else:
            script = "".join(self._upgrade(v) for v in range(version + 1, SCHEMA_VERSION + 1))
        self._conn.executescript(f"BEGIN; {script} PRAGMA user_version = {SCHEMA_VERSION}; COMMIT;")

    def _upgrade(self, v: int) -> str:
        """DDL taking the database from v-1 to v."""
        added = ""
        for table, column, kind in ADDED_COLUMNS.get(v, []):
            have = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                added += f"ALTER TABLE {table} ADD COLUMN {column} {kind};\n"
        return added + MIGRATIONS[v]  # columns first: a version's DDL may use them

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Generator[Self]:
        """Every write inside commits together, or none does (the block raised). Nests: an
        inner block joins the outer one, so only the outermost commits."""
        if self._tx_depth:
            self._tx_depth += 1
            try:
                yield self
            finally:
                self._tx_depth -= 1
            return
        self._tx_depth = 1
        try:
            with self._conn:
                yield self
        finally:
            self._tx_depth = 0

    def _write(self) -> AbstractContextManager[object]:
        """One write method's own transaction, or nothing inside `transaction()`."""
        return nullcontext() if self._tx_depth else self._conn

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
        with self._write():
            self._conn.executemany(
                "INSERT INTO snapshots (ts, series, window_id, ticker, open_time, close_time,"
                " yes_bid, yes_ask, no_bid, no_ask, depth_json, strike_type, floor_strike,"
                " cap_strike) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                        snap.strike_type,
                        snap.floor_strike,
                        snap.cap_strike,
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
            yield _snapshot(r)

    def latest_snapshot(self, ticker: str, at_or_before: datetime) -> MarketSnapshot | None:
        """The market's most recent snapshot with `ts <= at_or_before` (mark-to-market)."""
        row = self._conn.execute(
            "SELECT * FROM snapshots WHERE ticker = ? AND ts <= ? ORDER BY ts DESC, id DESC"
            " LIMIT 1",
            (ticker, _ts(at_or_before)),
        ).fetchone()
        return None if row is None else _snapshot(row)

    def latest_snapshots(
        self, ticker: str, times: Sequence[datetime]
    ) -> list[MarketSnapshot | None]:
        """`latest_snapshot(ticker, t)` for each `t` in `times`, in a few queries instead of
        one per time (portfolio value over time marks every open trade at every hour)."""
        if not times:
            return []
        keys = [_ts(t) for t in times]
        rows = self._conn.execute(
            "SELECT ts, id FROM snapshots WHERE ticker = ? AND ts <= ? ORDER BY ts, id",
            (ticker, max(keys)),
        ).fetchall()
        stamps: list[str] = [r[0] for r in rows]
        picked: list[int | None] = []
        for k in keys:
            i = bisect_right(stamps, k)  # the last row with ts <= k (highest id on a tie)
            picked.append(rows[i - 1][1] if i else None)
        ids = sorted({p for p in picked if p is not None})
        found: dict[int, MarketSnapshot] = {}
        for n in range(0, len(ids), 500):
            chunk = ids[n : n + 500]
            sql = f"SELECT * FROM snapshots WHERE id IN ({','.join('?' * len(chunk))})"
            found.update((r["id"], _snapshot(r)) for r in self._conn.execute(sql, chunk))
        return [None if p is None else found[p] for p in picked]

    def market_close_time(self, ticker: str) -> datetime | None:
        """The close time recorded with the market's snapshots, or None if never seen."""
        row = self._conn.execute(
            "SELECT close_time FROM snapshots WHERE ticker = ? ORDER BY ts DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        return None if row is None else _dt(row[0])

    # --- settlements --------------------------------------------------------

    def record_settlement(self, settlement: Settlement) -> None:
        """Idempotent: recording the same market twice keeps the first row."""
        with self._write():
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
        with self._write():
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

    def record_order(
        self,
        order: Order,
        ts: datetime,
        mode: Mode,
        status: str,
        *,
        kind: OrderKind = "entry",
        close_time: datetime | None = None,
    ) -> int:
        """Record an order. An entry order starts `submitted` (written before it is sent, so
        a crash leaves a visible unknown outcome); an exit order is recorded with its
        terminal exit status and its whole quantity filled."""
        if kind == "entry" and status != "submitted":
            raise StoreError(f"an entry order starts submitted, not {status!r}")
        if kind == "exit" and status not in EXIT_STATUSES:
            raise StoreError(f"an exit order's status is one of {sorted(EXIT_STATUSES)}")
        filled = order.qty if kind == "exit" else 0
        try:
            with self._write():
                cur = self._conn.execute(
                    "INSERT INTO orders (ts, portfolio, strategy, client_order_id, window_id,"
                    " ticker, side, limit_price, qty, mode, status, kind, filled_qty,"
                    " cancelled_qty, close_time) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
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
                        kind,
                        filled,
                        None if close_time is None else _ts(close_time),
                    ),
                )
        except sqlite3.IntegrityError as e:
            raise StoreError(f"duplicate client_order_id {order.client_order_id}") from e
        return _lastrowid(cur)

    def set_order_status(
        self, client_order_id: str, status: str, filled_qty: int | None = None
    ) -> None:
        """Move a submitted entry order to its terminal status, with how much filled (the
        rest is cancelled: entries are IOC). `filled_qty` defaults to the whole order for
        `filled` and to 0 otherwise; `partial` needs it. Any other move (out of a terminal
        status, to an unknown one, or with a quantity that doesn't fit) is refused."""
        row = self._conn.execute(
            "SELECT status, kind, qty FROM orders WHERE client_order_id = ?", (client_order_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"no order {client_order_id}")
        current, kind, qty = str(row[0]), str(row[1]), int(row[2])
        if kind != "entry" or current != "submitted" or status not in ENTRY_TERMINAL:
            raise StoreError(f"order {client_order_id}: illegal transition {current} -> {status}")
        filled = (qty if status == "filled" else 0) if filled_qty is None else filled_qty
        legal = {
            "filled": filled == qty,
            "partial": 0 < filled < qty,
        }.get(status, filled == 0)
        if not legal:
            raise StoreError(f"order {client_order_id}: {status} with {filled} of {qty} filled")
        with self._write():
            self._conn.execute(
                "UPDATE orders SET status = ?, filled_qty = ?, cancelled_qty = ?"
                " WHERE client_order_id = ? AND status = 'submitted'",
                (status, filled, qty - filled, client_order_id),
            )

    def order_status(self, client_order_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT status FROM orders WHERE client_order_id = ?", (client_order_id,)
        ).fetchone()
        return None if row is None else row[0]

    def order(self, client_order_id: str) -> OrderRecord | None:
        """The stored order with its lifecycle state and fills, or None."""
        row = self._conn.execute(
            "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)
        ).fetchone()
        return None if row is None else self._order_record(row)

    def _order_record(self, r: sqlite3.Row) -> OrderRecord:
        fills = self._conn.execute(
            "SELECT * FROM fills WHERE client_order_id = ? ORDER BY id", (r["client_order_id"],)
        )
        return OrderRecord(
            order=Order(
                r["client_order_id"],
                r["portfolio"],
                r["strategy"],
                r["window_id"],
                r["ticker"],
                r["side"],
                r["limit_price"],
                r["qty"],
            ),
            ts=_dt(r["ts"]),
            mode=r["mode"],
            kind=r["kind"],
            status=r["status"],
            filled_qty=r["filled_qty"],
            cancelled_qty=r["cancelled_qty"],
            close_time=None if r["close_time"] is None else _dt(r["close_time"]),
            fills=tuple(
                Fill(f["client_order_id"], _dt(f["ts"]), f["price"], f["qty"], f["fee"])
                for f in fills
            ),
        )

    def order_attempts(self, key: StrategyKey, window_id: str, side: Side) -> int:
        """Orders already sent for this strategy, window and side (next attempt number).
        Only meaningful once `unresolved_attempt` is False: bump an attempt only after the
        previous one is known dead."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM orders WHERE portfolio = ? AND strategy = ? AND window_id = ?"
            " AND side = ? AND kind = 'entry'",
            (*key, window_id, side),
        ).fetchone()
        return int(row[0])

    def unresolved_attempt(self, key: StrategyKey, window_id: str, side: Side) -> bool:
        """True while an earlier entry for this strategy, window and side has an unknown
        outcome: still `submitted`, or filled without its trade. No new attempt until it is
        resolved (nem.engine.reconcile)."""
        row = self._conn.execute(
            "SELECT 1 FROM orders o WHERE o.portfolio = ? AND o.strategy = ?"
            " AND o.window_id = ? AND o.side = ? AND o.kind = 'entry'"
            f" AND {_UNRESOLVED_ENTRY} LIMIT 1",
            (*key, window_id, side),
        ).fetchone()
        return row is not None

    def unresolved_orders(self) -> list[OrderRecord]:
        """Orders whose outcome isn't fully recorded, oldest first: entries still
        `submitted` or filled without a trade, and exits without a fill or whose trade is
        still open. What a crash between writes (or a pre-v9 database) can leave behind."""
        rows = self._conn.execute(
            "SELECT * FROM orders o WHERE (o.kind = 'entry' AND"
            f" {_UNRESOLVED_ENTRY}) OR (o.kind = 'exit' AND (NOT EXISTS (SELECT 1 FROM fills f"
            " WHERE f.client_order_id = o.client_order_id) OR EXISTS (SELECT 1 FROM trades t"
            " WHERE t.portfolio = o.portfolio AND t.strategy = o.strategy"
            " AND t.window_id = o.window_id AND t.settled_at IS NULL)))"
            " ORDER BY o.ts, o.id"
        ).fetchall()
        return [self._order_record(r) for r in rows]

    def unbooked_entry_fills(self) -> list[tuple[StrategyKey, str, int, int]]:
        """Windows whose entry fills hold more contracts than their trade: (key, window_id,
        filled contracts, trade contracts). Only an interrupted pre-v9 write leaves these;
        the extra fills are in no position, so neither cash nor risk sees them."""
        rows = self._conn.execute(
            "SELECT o.portfolio, o.strategy, o.window_id, SUM(f.qty), t.qty FROM orders o"
            " JOIN fills f ON f.client_order_id = o.client_order_id JOIN trades t"
            " ON t.portfolio = o.portfolio AND t.strategy = o.strategy"
            " AND t.window_id = o.window_id WHERE o.kind = 'entry'"
            " GROUP BY o.portfolio, o.strategy, o.window_id HAVING SUM(f.qty) > t.qty"
            " ORDER BY o.portfolio, o.strategy, o.window_id"
        )
        return [((r[0], r[1]), r[2], int(r[3]), int(r[4])) for r in rows]

    def discard_order(self, client_order_id: str) -> None:
        """Delete an exit order that never filled (pre-v9 crash between its writes), so the
        position can exit again under the same deterministic id. Refused for anything else."""
        rec = self.order(client_order_id)
        if rec is None or rec.kind != "exit" or rec.fills:
            raise StoreError(f"order {client_order_id}: only an unfilled exit can be discarded")
        with self._write():
            self._conn.execute("DELETE FROM orders WHERE client_order_id = ?", (client_order_id,))

    def record_fill(self, fill: Fill) -> bool:
        """Record a fill once. Paper orders fill at most once (IOC entry, all-or-nothing
        exit), so the order's id is the fill's identity: the same fill again is a no-op
        (False), a different fill for the same order is refused."""
        existing = self._conn.execute(
            "SELECT ts, price, qty, fee FROM fills WHERE fill_id = ?", (fill.client_order_id,)
        ).fetchone()
        if existing is not None:
            same = (existing[0], existing[1], existing[2], existing[3]) == (
                _ts(fill.ts), fill.price, fill.qty, fill.fee
            )  # fmt: skip
            if same:
                return False
            raise StoreError(f"order {fill.client_order_id} already has a different fill")
        with self._write():
            self._conn.execute(
                "INSERT INTO fills (client_order_id, ts, price, qty, fee, fill_id)"
                " VALUES (?,?,?,?,?,?)",
                (
                    fill.client_order_id,
                    _ts(fill.ts),
                    fill.price,
                    fill.qty,
                    fill.fee,
                    fill.client_order_id,
                ),
            )
        return True

    # --- execution outcomes: each one transaction ----------------------------------

    def record_entry(self, fill: Fill, trade: Trade) -> bool:
        """One paper entry's outcome, all or nothing: the fill, the order's terminal status
        (`filled`, or `partial` with the IOC remainder cancelled) and the open trade. False
        (and nothing changes) for a fill already recorded: a duplicate can't debit twice."""
        with self.transaction():
            rec = self.order(fill.client_order_id)
            if rec is None:
                raise StoreError(f"no order {fill.client_order_id}")
            if not self.record_fill(fill):
                return False
            status = "filled" if fill.qty == rec.order.qty else "partial"
            self.set_order_status(fill.client_order_id, status, fill.qty)
            self.open_trade(trade)
        return True

    def record_exit(
        self, order: Order, fill: Fill, status: str, mode: Mode, won: bool, realized_pnl: float
    ) -> bool:
        """One exit's outcome, all or nothing: the exit order, its fill, the trade closed and
        queued for learning. False (and nothing changes) if this exit is already recorded."""
        with self.transaction():
            rec = self.order(order.client_order_id)
            if rec is not None and rec.fills:
                if rec.fills != (fill,):
                    raise StoreError(f"order {order.client_order_id} already has a different fill")
                return False
            if rec is not None:
                raise StoreError(f"exit order {order.client_order_id} has no fill; reconcile first")
            self.record_order(order, fill.ts, mode, status, kind="exit")
            self.record_fill(fill)
            key = (order.portfolio, order.strategy)
            self.exit_trade(key, order.window_id, won, realized_pnl, fill.ts)
        return True

    # --- trades -------------------------------------------------------------

    def open_trade(self, trade: Trade) -> None:
        if trade.settled:
            raise StoreError("open_trade takes an unsettled trade")
        try:
            with self._write():
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
        with self._write():
            cur = self._conn.execute(
                "UPDATE trades SET won = ?, realized_pnl = ?, settled_at = ?"
                " WHERE portfolio = ? AND strategy = ? AND window_id = ? AND settled_at IS NULL",
                (int(won), realized_pnl, _ts(settled_at), *key, window_id),
            )
        if cur.rowcount != 1:
            raise StoreError(f"no open trade for {key[0]}/{key[1]} {window_id}")

    def settle_and_learn(
        self, trade: Trade, won: bool, realized_pnl: float, settled_at: datetime
    ) -> bool:
        """Settle a held trade and count its outcome in its side's learned stat, in one
        transaction. False (and nothing changes) if it is already settled, so settling the
        same result twice can't count it twice."""
        key = (trade.portfolio, trade.strategy)
        with self.transaction():
            row = self._conn.execute(
                "SELECT settled_at FROM trades WHERE portfolio = ? AND strategy = ?"
                " AND window_id = ?",
                (*key, trade.window_id),
            ).fetchone()
            if row is None:
                raise StoreError(f"no trade {key[0]}/{key[1]} {trade.window_id}")
            if row[0] is not None:
                return False
            self.settle_trade(key, trade.window_id, won, realized_pnl, settled_at)
            self.bump_stat(*key, trade.side, won)
        return True

    def exit_trade(
        self,
        key: StrategyKey,
        window_id: str,
        won: bool,
        realized_pnl: float,
        settled_at: datetime,
    ) -> None:
        """Close a trade sold before settlement (`won` = P&L > 0) and queue it, in the same
        transaction, for learning once its market settles (`learning_due`)."""
        with self._write():
            cur = self._conn.execute(
                "UPDATE trades SET won = ?, realized_pnl = ?, settled_at = ?"
                " WHERE portfolio = ? AND strategy = ? AND window_id = ? AND settled_at IS NULL",
                (int(won), realized_pnl, _ts(settled_at), *key, window_id),
            )
            if cur.rowcount != 1:
                raise StoreError(f"no open trade for {key[0]}/{key[1]} {window_id}")
            self._conn.execute(
                "INSERT INTO exit_learning (portfolio, strategy, window_id) VALUES (?, ?, ?)",
                (*key, window_id),
            )

    def learning_due(self, now: datetime) -> list[Trade]:
        """Exited trades whose market has closed and whose settlement isn't learned yet."""
        rows = self._conn.execute(
            "SELECT t.* FROM trades t JOIN exit_learning e ON e.portfolio = t.portfolio"
            " AND e.strategy = t.strategy AND e.window_id = t.window_id"
            " WHERE e.learned_at IS NULL AND t.close_time <= ?"
            " ORDER BY t.close_time, t.portfolio, t.strategy, t.window_id",
            (_ts(now),),
        )
        return [_trade(r) for r in rows]

    def learn_exit(self, trade: Trade, won_at_settlement: bool, learned_at: datetime) -> bool:
        """Record an exited trade's settlement outcome and bump its side's learned count,
        once: False (and nothing changes) if it was already learned or never queued."""
        key = (trade.portfolio, trade.strategy)
        with self._write():
            cur = self._conn.execute(
                "UPDATE exit_learning SET won = ?, learned_at = ?"
                " WHERE portfolio = ? AND strategy = ? AND window_id = ? AND learned_at IS NULL",
                (int(won_at_settlement), _ts(learned_at), *key, trade.window_id),
            )
            if cur.rowcount != 1:
                return False
            self._conn.execute(
                "INSERT INTO stats (portfolio, strategy, key, wins, n) VALUES (?, ?, ?, ?, 1)"
                " ON CONFLICT (portfolio, strategy, key)"
                " DO UPDATE SET wins = wins + excluded.wins, n = n + 1",
                (*key, trade.side, int(won_at_settlement)),
            )
        return True

    def exit_outcomes(self, key: StrategyKey) -> dict[str, bool | None]:
        """window_id -> whether an exited trade's side won at settlement (None: pending)."""
        rows = self._conn.execute(
            "SELECT window_id, won FROM exit_learning WHERE portfolio = ? AND strategy = ?",
            key,
        )
        return {r[0]: None if r[1] is None else bool(r[1]) for r in rows}

    def open_trades(
        self,
        portfolio: str | None = None,
        strategy: StrategyKey | None = None,
        include_excluded: bool = True,
    ) -> list[Trade]:
        """Open trades, oldest first. `include_excluded=False` leaves out excluded trades
        (`nem trade exclude`), as every money and statistics total does."""
        sql = "SELECT * FROM trades WHERE settled_at IS NULL"
        if not include_excluded:
            sql += " AND excluded_at IS NULL"
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
            "SELECT * FROM trades WHERE settled_at IS NULL AND close_time <= ?"
            " ORDER BY close_time, portfolio, strategy, window_id",
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
        include_excluded: bool = True,
    ) -> list[Trade]:
        """Settled trades, oldest first. `before` is strict: `settled_at < before`.
        `limit` keeps the most recent N. `include_excluded=False` leaves out excluded trades
        (`nem trade exclude`), as every money and statistics total does; listings keep them."""
        sql = "SELECT * FROM trades WHERE settled_at IS NOT NULL"
        if not include_excluded:
            sql += " AND excluded_at IS NULL"
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

    # --- exclusions (nem trade exclude / include) -------------------------------

    def trades_for_ticker(
        self, ticker: str, portfolio: str | None = None, strategy: str | None = None
    ) -> list[Trade]:
        """Every trade (open or settled) on market `ticker`, optionally narrowed."""
        sql = "SELECT * FROM trades WHERE ticker = ?"
        args = [ticker]
        if portfolio is not None:
            sql += " AND portfolio = ?"
            args.append(portfolio)
        if strategy is not None:
            sql += " AND strategy = ?"
            args.append(strategy)
        rows = self._conn.execute(sql + " ORDER BY portfolio, strategy, window_id", args)
        return [_trade(r) for r in rows]

    def set_excluded(self, trade: Trade, reason: str | None, ts: datetime | None) -> None:
        """Exclude a trade (`reason` and `ts` set) or count it again (both None). An excluded
        trade is kept and listed, but left out of statistics, P&L, equity, cash and risk
        limits, as if it had never been taken."""
        if (reason is None) != (ts is None):
            raise StoreError("set_excluded takes both a reason and a time, or neither")
        with self._write():
            cur = self._conn.execute(
                "UPDATE trades SET excluded_at = ?, exclude_reason = ?"
                " WHERE portfolio = ? AND strategy = ? AND window_id = ?",
                (
                    None if ts is None else _ts(ts),
                    reason,
                    trade.portfolio,
                    trade.strategy,
                    trade.window_id,
                ),
            )
        if cur.rowcount != 1:
            raise StoreError(f"no trade {trade.portfolio}/{trade.strategy} {trade.window_id}")

    def excluded_trades(self, portfolio: str | None = None) -> list[Trade]:
        """Excluded trades, open or settled, in the order they were excluded."""
        sql = "SELECT * FROM trades WHERE excluded_at IS NOT NULL"
        args: list[str] = []
        if portfolio is not None:
            sql += " AND portfolio = ?"
            args.append(portfolio)
        rows = self._conn.execute(sql + " ORDER BY excluded_at, portfolio, strategy", args)
        return [_trade(r) for r in rows]

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
        with self._write():
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

    def ledger_entries(self, portfolio: str) -> list[tuple[datetime, float]]:
        """Every ledger entry of a portfolio as (ts, amount), oldest first."""
        rows = self._conn.execute(
            "SELECT ts, amount FROM ledger WHERE portfolio = ? ORDER BY ts, kind", (portfolio,)
        )
        return [(_dt(r[0]), float(r[1])) for r in rows]

    def last_ledger(self, portfolio: str, kind: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT MAX(ts) FROM ledger WHERE portfolio = ? AND kind = ?", (portfolio, kind)
        ).fetchone()
        return None if row[0] is None else _dt(row[0])

    # --- manual imports (nem import kalshi-pnl): history only, never traded -------------

    def add_manual_lots(self, lots: Sequence[ManualLot], imported_at: datetime) -> int:
        """Idempotent per (portfolio, lot_key). Returns how many lots were new."""
        with self._write():
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT OR IGNORE INTO manual_lots (portfolio, strategy, lot_key, ticker, side,"
                " qty, entry_price, exit_price, open_fees, close_fees, pnl_without_fees,"
                " realized_pnl, opened_at, closed_at, source_file, source_line, imported_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        lot.portfolio,
                        lot.strategy,
                        lot.lot_key,
                        lot.ticker,
                        lot.side,
                        lot.qty,
                        lot.entry_price,
                        lot.exit_price,
                        lot.open_fees,
                        lot.close_fees,
                        lot.pnl_without_fees,
                        lot.realized_pnl,
                        _ts(lot.opened_at),
                        _ts(lot.closed_at),
                        lot.source_file,
                        lot.source_line,
                        _ts(imported_at),
                    )
                    for lot in lots
                ],
            )
            return self._conn.total_changes - before

    def manual_lots(self, portfolio: str, strategy: str | None = None) -> list[ManualLot]:
        """Imported lots, oldest close first."""
        sql = "SELECT * FROM manual_lots WHERE portfolio = ?"
        args = [portfolio]
        if strategy is not None:
            sql += " AND strategy = ?"
            args.append(strategy)
        rows = self._conn.execute(sql + " ORDER BY closed_at, opened_at, lot_key", args)
        return [
            ManualLot(
                portfolio=r["portfolio"],
                strategy=r["strategy"],
                lot_key=r["lot_key"],
                ticker=r["ticker"],
                side=r["side"],
                qty=r["qty"],
                entry_price=r["entry_price"],
                exit_price=r["exit_price"],
                open_fees=r["open_fees"],
                close_fees=r["close_fees"],
                pnl_without_fees=r["pnl_without_fees"],
                realized_pnl=r["realized_pnl"],
                opened_at=_dt(r["opened_at"]),
                closed_at=_dt(r["closed_at"]),
                source_file=r["source_file"],
                source_line=r["source_line"],
            )
            for r in rows
        ]

    def manual_strategies(self) -> list[StrategyKey]:
        """(portfolio, strategy) pairs that hold imported manual lots."""
        rows = self._conn.execute(
            "SELECT DISTINCT portfolio, strategy FROM manual_lots ORDER BY portfolio, strategy"
        )
        return [(r[0], r[1]) for r in rows]

    def add_manual_fills(self, fills: Sequence[ManualFill], imported_at: datetime) -> int:
        """Idempotent per (portfolio, fill_key). Returns how many fills were new."""
        with self._write():
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT OR IGNORE INTO manual_fills (portfolio, fill_key, ticker, side, qty,"
                " price, fee, liquidity, filled_at, source_file, source_line, imported_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        f.portfolio,
                        f.fill_key,
                        f.ticker,
                        f.side,
                        f.qty,
                        f.price,
                        f.fee,
                        f.liquidity,
                        _ts(f.filled_at),
                        f.source_file,
                        f.source_line,
                        _ts(imported_at),
                    )
                    for f in fills
                ],
            )
            return self._conn.total_changes - before

    def manual_fills(self, portfolio: str) -> list[ManualFill]:
        rows = self._conn.execute(
            "SELECT * FROM manual_fills WHERE portfolio = ? ORDER BY filled_at, fill_key",
            (portfolio,),
        )
        return [
            ManualFill(
                portfolio=r["portfolio"],
                fill_key=r["fill_key"],
                ticker=r["ticker"],
                side=r["side"],
                qty=r["qty"],
                price=r["price"],
                fee=r["fee"],
                liquidity=r["liquidity"],
                filled_at=_dt(r["filled_at"]),
                source_file=r["source_file"],
                source_line=r["source_line"],
            )
            for r in rows
        ]

    # --- settlement checks (nem settle verify) -----------------------------------------

    def save_settlement_checks(self, checks: Sequence["SettlementCheck"]) -> None:
        """Insert or replace by ticker: a re-check overwrites the previous one."""
        with self._write():
            self._conn.executemany(
                f"INSERT OR REPLACE INTO settlement_checks ({', '.join(_CHECK_COLUMNS)})"
                f" VALUES ({', '.join('?' * len(_CHECK_COLUMNS))})",
                [_check_row(c) for c in checks],
            )

    def settlement_check_candidates(self) -> list[str]:
        """Weather markets this database knows: settled ones and imported manual lots."""
        rows = self._conn.execute(
            "SELECT ticker FROM settlements UNION SELECT ticker FROM manual_lots"
        ).fetchall()
        return sorted(t for (t,) in rows if t.startswith(("KXHIGH", "KXLOW")))

    def settlement_checks(self, status: str | None = None) -> list["SettlementCheck"]:
        sql = "SELECT * FROM settlement_checks"
        params: list[str] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        rows = self._conn.execute(sql + " ORDER BY ticker", params).fetchall()
        return [_check(r) for r in rows]

    # --- forecast backfill (nem backfill forecasts) ------------------------------------

    def save_backfill(self, rows: Sequence["BackfillRow"]) -> int:
        """Insert or replace by (source, station, target date, field, run): a re-fetch of
        the same model run overwrites it. Returns the number of rows written."""
        with self._write():
            self._conn.executemany(
                f"INSERT OR REPLACE INTO forecast_backfill ({', '.join(_BACKFILL_COLUMNS)})"
                f" VALUES ({', '.join('?' * len(_BACKFILL_COLUMNS))})",
                [_backfill_row(b) for b in rows],
            )
        return len(rows)

    def backfill_rows(
        self, station: str | None = None, target_date: date | None = None
    ) -> list["BackfillRow"]:
        """Every stored backfilled forecast (optionally one station / target day), ordered
        by known_at. Callers pick what was knowable with `nem.backfill.known_before`."""
        sql, args = "SELECT * FROM forecast_backfill WHERE 1", list[str]()
        if station is not None:
            sql += " AND station = ?"
            args.append(station)
        if target_date is not None:
            sql += " AND target_date = ?"
            args.append(target_date.isoformat())
        rows = self._conn.execute(sql + " ORDER BY known_at, source, field", args).fetchall()
        return [_backfill(r) for r in rows]

    # --- feeds ------------------------------------------------------------------

    def record_feed_values(self, values: Sequence[FeedValue], recorded_at: datetime) -> None:
        """Idempotent per (feed, key, known_at)."""
        with self._write():
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
        with self._write():
            self._conn.execute(
                "INSERT INTO stats (portfolio, strategy, key, wins, n) VALUES (?, ?, ?, ?, 1)"
                " ON CONFLICT (portfolio, strategy, key)"
                " DO UPDATE SET wins = wins + excluded.wins, n = n + 1",
                (portfolio, strategy, key, int(won)),
            )

    def stat(self, portfolio: str, strategy: str, key: str) -> tuple[int, int]:
        """(wins, n); (0, 0) if never recorded. Trades excluded from statistics are taken
        back out at read time (settlement bumps the per-side count of every trade, and an
        exclusion can come later or be undone): a held trade counts once settled, with its
        `won`; an exited one once learned, with its side's result at settlement."""
        row = self._conn.execute(
            "SELECT wins, n FROM stats WHERE portfolio = ? AND strategy = ? AND key = ?",
            (portfolio, strategy, key),
        ).fetchone()
        if row is None:
            return (0, 0)
        out = self._conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN e.window_id IS NULL THEN t.won ELSE e.won END), 0),"
            " COUNT(*) FROM trades t LEFT JOIN exit_learning e ON e.portfolio = t.portfolio"
            " AND e.strategy = t.strategy AND e.window_id = t.window_id"
            " WHERE t.portfolio = ? AND t.strategy = ? AND t.side = ?"
            " AND t.excluded_at IS NOT NULL AND (CASE WHEN e.window_id IS NULL"
            " THEN t.settled_at IS NOT NULL ELSE e.learned_at IS NOT NULL END)",
            (portfolio, strategy, key),
        ).fetchone()
        n = max(0, row[1] - out[1])
        return (min(n, max(0, row[0] - out[0])), n)

    def heartbeat(
        self,
        ts: datetime,
        process: str,
        status: str,
        portfolio: str | None = None,
        strategy: str | None = None,
    ) -> None:
        with self._write():
            self._conn.execute(
                "INSERT INTO heartbeats (ts, process, portfolio, strategy, status)"
                " VALUES (?,?,?,?,?)",
                (_ts(ts), process, portfolio, strategy, status),
            )
            # one row every few seconds adds up; keep a couple of days for `nem status`
            self._conn.execute(
                "DELETE FROM heartbeats WHERE process = ? AND ts < ?",
                (process, _ts(ts - HEARTBEAT_RETENTION)),
            )

    def last_heartbeat(self, process: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT MAX(ts) FROM heartbeats WHERE process = ?", (process,)
        ).fetchone()
        return None if row[0] is None else _dt(row[0])

    def heartbeat_status(self, process: str) -> tuple[datetime, str] | None:
        """(time, status) of the latest heartbeat, e.g. to show the last error."""
        row = self._conn.execute(
            "SELECT ts, status FROM heartbeats WHERE process = ? ORDER BY ts DESC LIMIT 1",
            (process,),
        ).fetchone()
        return None if row is None else (_dt(row[0]), row[1])

    def processes(self) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT DISTINCT process FROM heartbeats")]

    # --- halts (kill switch) ------------------------------------------------------

    def halt(self, scope: str, reason: str, ts: datetime) -> None:
        """Stop new entries. Scope: "*" (everything), "portfolio" or "portfolio/strategy"."""
        with self._write():
            self._conn.execute(
                "INSERT INTO halts (scope, reason, ts) VALUES (?,?,?)"
                " ON CONFLICT (scope) DO UPDATE SET reason = excluded.reason, ts = excluded.ts",
                (scope, reason, _ts(ts)),
            )

    def resume(self, scope: str) -> bool:
        with self._write():
            cur = self._conn.execute("DELETE FROM halts WHERE scope = ?", (scope,))
        return cur.rowcount == 1

    def halts(self) -> list[tuple[str, str, datetime]]:
        rows = self._conn.execute("SELECT scope, reason, ts FROM halts ORDER BY scope")
        return [(r[0], r[1], _dt(r[2])) for r in rows]

    def halted(self, portfolio: str, strategy: str) -> str | None:
        """The scope that halts this strategy, if any (most general first)."""
        scopes = ("*", portfolio, f"{portfolio}/{strategy}")
        row = self._conn.execute(
            "SELECT scope FROM halts WHERE scope IN (?,?,?) ORDER BY length(scope) LIMIT 1",
            scopes,
        ).fetchone()
        return None if row is None else row[0]

    def last_signal(self, portfolio: str, strategy: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT MAX(ts) FROM signals WHERE portfolio = ? AND strategy = ?",
            (portfolio, strategy),
        ).fetchone()
        return None if row[0] is None else _dt(row[0])


_CHECK_COLUMNS = (
    "ticker", "series", "market_date", "measure", "station", "rules_source", "strike_type",
    "floor_strike", "cap_strike", "observed_value", "source", "source_ref", "derived_result",
    "kalshi_result", "kalshi_value", "kalshi_settlement_value", "status", "note", "checked_at",
)  # fmt: skip


def _check_row(c: "SettlementCheck") -> tuple[Any, ...]:
    return (
        c.ticker, c.series, c.market_date.isoformat() if c.market_date else None, c.measure,
        c.station, c.rules_source, c.strike_type, c.floor_strike, c.cap_strike,
        c.observed_value, c.source, c.source_ref, c.derived_result, c.kalshi_result,
        c.kalshi_value, c.kalshi_settlement_value, c.status, c.note, _ts(c.checked_at),
    )  # fmt: skip


def _check(r: sqlite3.Row) -> "SettlementCheck":
    from nem.settle.model import SettlementCheck

    return SettlementCheck(
        ticker=r["ticker"], series=r["series"],
        market_date=date.fromisoformat(r["market_date"]) if r["market_date"] else None,
        measure=r["measure"], station=r["station"], rules_source=r["rules_source"],
        strike_type=r["strike_type"], floor_strike=r["floor_strike"],
        cap_strike=r["cap_strike"], observed_value=r["observed_value"], source=r["source"],
        source_ref=r["source_ref"], derived_result=r["derived_result"],
        kalshi_result=r["kalshi_result"], kalshi_value=r["kalshi_value"],
        kalshi_settlement_value=r["kalshi_settlement_value"], status=r["status"],
        note=r["note"], checked_at=_dt(r["checked_at"]),
    )  # fmt: skip


_BACKFILL_COLUMNS = (
    "source", "station", "target_date", "field", "value", "run_time", "valid_from",
    "known_at", "run_id", "provenance", "fetched_at",
)  # fmt: skip


def _backfill_row(b: "BackfillRow") -> tuple[Any, ...]:
    return (
        b.source, b.station, b.target_date.isoformat(), b.field, b.value, _ts(b.run_time),
        _ts(b.valid_from), _ts(b.known_at), b.run_id, b.provenance, _ts(b.fetched_at),
    )  # fmt: skip


def _backfill(r: sqlite3.Row) -> "BackfillRow":
    from nem.backfill.model import BackfillRow

    return BackfillRow(
        source=r["source"], station=r["station"],
        target_date=date.fromisoformat(r["target_date"]), field=r["field"], value=r["value"],
        run_time=_dt(r["run_time"]), valid_from=_dt(r["valid_from"]),
        known_at=_dt(r["known_at"]), run_id=r["run_id"], fetched_at=_dt(r["fetched_at"]),
        provenance=r["provenance"],
    )  # fmt: skip


def _snapshot(r: sqlite3.Row) -> MarketSnapshot:
    return MarketSnapshot(
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
        strike_type=r["strike_type"],
        floor_strike=r["floor_strike"],
        cap_strike=r["cap_strike"],
    )


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
        excluded_at=None if r["excluded_at"] is None else _dt(r["excluded_at"]),
        exclude_reason=r["exclude_reason"],
    )
