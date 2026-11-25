-- One database per deployment. Rows are keyed by (portfolio, strategy) so reports can roll
-- up to a portfolio or drill into one strategy.
-- Timestamps are UTC ISO-8601 TEXT with fixed precision, so they sort lexicographically.

CREATE TABLE snapshots (               -- powers replay and backtests
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    series      TEXT NOT NULL,
    window_id   TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    open_time   TEXT NOT NULL,
    close_time  TEXT NOT NULL,
    yes_bid     REAL,
    yes_ask     REAL,
    no_bid      REAL,
    no_ask      REAL,
    depth_json  TEXT NOT NULL DEFAULT '{}',
    strike_type  TEXT,                 -- what YES means, e.g. greater_or_equal / between
    floor_strike REAL,
    cap_strike   REAL
);
CREATE INDEX snapshots_series_ts ON snapshots (series, ts);
CREATE INDEX snapshots_ticker ON snapshots (ticker);

CREATE TABLE settlements (             -- market outcomes, so replay can settle trades
    ticker      TEXT PRIMARY KEY,
    series      TEXT NOT NULL,
    window_id   TEXT NOT NULL,
    result      TEXT NOT NULL CHECK (result IN ('yes', 'no')),
    settled_at  TEXT NOT NULL
);

CREATE TABLE signals (                 -- every signal, taken or not; `reason` says why
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    portfolio   TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    window_id   TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    side        TEXT NOT NULL CHECK (side IN ('yes', 'no')),
    price       REAL NOT NULL,
    p_model     REAL NOT NULL,
    edge        REAL,
    decision    TEXT NOT NULL CHECK (decision IN ('take', 'skip', 'no_fill')),
    reason      TEXT NOT NULL,
    meta_json   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX signals_strategy_ts ON signals (portfolio, strategy, ts);

CREATE TABLE orders (
    id              INTEGER PRIMARY KEY,
    ts              TEXT NOT NULL,
    portfolio       TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    client_order_id TEXT NOT NULL UNIQUE,
    window_id       TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    side            TEXT NOT NULL CHECK (side IN ('yes', 'no')),
    limit_price     REAL NOT NULL,
    qty             INTEGER NOT NULL CHECK (qty > 0),
    mode            TEXT NOT NULL CHECK (mode IN ('paper', 'live')),
    status          TEXT NOT NULL,     -- entry: submitted, then exactly one terminal status
                                       -- (filled, partial, no_fill, rejected_over_risk,
                                       -- cancelled); exit: exit_take_profit / exit_stop
    kind            TEXT NOT NULL DEFAULT 'entry' CHECK (kind IN ('entry', 'exit')),  -- v9
    filled_qty      INTEGER NOT NULL DEFAULT 0,  -- v9: set when terminal; filled +
    cancelled_qty   INTEGER NOT NULL DEFAULT 0,  -- cancelled = qty (IOC remainder cancelled)
    close_time      TEXT                         -- v9: the market's close, to rebuild a trade
);
CREATE INDEX orders_attempts ON orders (portfolio, strategy, window_id, side);

CREATE TABLE fills (
    id              INTEGER PRIMARY KEY,
    client_order_id TEXT NOT NULL REFERENCES orders (client_order_id),
    ts              TEXT NOT NULL,
    price           REAL NOT NULL,
    qty             INTEGER NOT NULL CHECK (qty > 0),
    fee             REAL NOT NULL,
    fill_id         TEXT               -- v9: execution identity; a duplicate is refused
);
CREATE UNIQUE INDEX fills_fill_id ON fills (fill_id);

CREATE TABLE trades (
    portfolio    TEXT NOT NULL,
    strategy     TEXT NOT NULL,
    window_id    TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    side         TEXT NOT NULL CHECK (side IN ('yes', 'no')),
    avg_price    REAL NOT NULL,
    qty          INTEGER NOT NULL CHECK (qty > 0),
    fee          REAL NOT NULL,
    mode         TEXT NOT NULL CHECK (mode IN ('paper', 'live')),
    opened_at    TEXT NOT NULL,
    close_time   TEXT NOT NULL,
    won          INTEGER,                -- NULL until settled
    realized_pnl REAL,
    settled_at   TEXT,
    excluded_at    TEXT,               -- `nem trade exclude`: out of every statistic, still
    exclude_reason TEXT,               -- in money (P&L, equity, risk limits); NULL = counted
    PRIMARY KEY (portfolio, strategy, window_id)
);
CREATE INDEX trades_settled_at ON trades (settled_at);

CREATE TABLE ledger (                  -- cash movements that aren't trades, e.g. interest
    portfolio  TEXT NOT NULL,
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,
    amount     REAL NOT NULL,
    PRIMARY KEY (portfolio, kind, ts)
);

CREATE TABLE feed_values (             -- external data, replayed by `known_at`
    feed        TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       REAL NOT NULL,
    known_at    TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (feed, key, known_at)
);

CREATE TABLE stats (                   -- learned per-strategy counts, e.g. win rate by side
    portfolio TEXT NOT NULL,
    strategy  TEXT NOT NULL,
    key       TEXT NOT NULL,
    wins      INTEGER NOT NULL DEFAULT 0 CHECK (wins >= 0),
    n         INTEGER NOT NULL DEFAULT 0 CHECK (n >= wins),
    PRIMARY KEY (portfolio, strategy, key)
);

CREATE TABLE heartbeats (
    ts        TEXT NOT NULL,
    process   TEXT NOT NULL,
    portfolio TEXT,
    strategy  TEXT,
    status    TEXT NOT NULL
);
CREATE INDEX heartbeats_process_ts ON heartbeats (process, ts);

CREATE TABLE halts (                   -- kill switch; see `nem halt` / `nem resume`
    scope   TEXT PRIMARY KEY,          -- "*", "<portfolio>" or "<portfolio>/<strategy>"
    reason  TEXT NOT NULL,
    ts      TEXT NOT NULL
);

CREATE TABLE manual_lots (             -- your own manual trades (nem import kalshi-pnl); history
    portfolio        TEXT NOT NULL,    -- only, never traded by the engine
    strategy         TEXT NOT NULL,
    lot_key          TEXT NOT NULL,    -- stable id of the source row; re-imports add nothing
    provenance       TEXT NOT NULL DEFAULT 'manual_import' CHECK (provenance = 'manual_import'),
    ticker           TEXT NOT NULL,
    side             TEXT NOT NULL CHECK (side IN ('yes', 'no')),
    qty              REAL NOT NULL CHECK (qty > 0),   -- can be fractional
    entry_price      REAL NOT NULL,
    exit_price       REAL NOT NULL,    -- 0.99 sell, other sell, or settlement at 1.00 / 0.00
    open_fees        REAL NOT NULL,
    close_fees       REAL NOT NULL,
    pnl_without_fees REAL NOT NULL,
    realized_pnl     REAL NOT NULL,    -- Kalshi's realized P&L after fees: truth for money
    opened_at        TEXT NOT NULL,
    closed_at        TEXT NOT NULL,
    source_file      TEXT NOT NULL,
    source_line      INTEGER NOT NULL,
    imported_at      TEXT NOT NULL,
    PRIMARY KEY (portfolio, lot_key)
);

CREATE TABLE manual_fills (            -- fills from Kalshi's activity export, for fill times
    portfolio   TEXT NOT NULL,
    fill_key    TEXT NOT NULL,
    provenance  TEXT NOT NULL DEFAULT 'manual_import' CHECK (provenance = 'manual_import'),
    ticker      TEXT NOT NULL,
    side        TEXT NOT NULL CHECK (side IN ('yes', 'no')),
    qty         REAL NOT NULL CHECK (qty > 0),
    price       REAL NOT NULL,         -- paid per contract of `side` (No rows inverted)
    fee         REAL NOT NULL,
    liquidity   TEXT NOT NULL CHECK (liquidity IN ('maker', 'taker')),
    filled_at   TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_line INTEGER NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (portfolio, fill_key)
);

CREATE TABLE settlement_checks (       -- `nem settle verify`: Kalshi's result vs an independent
    ticker                  TEXT PRIMARY KEY,  -- station reading; one row per market
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

CREATE TABLE forecast_backfill (       -- `nem backfill forecasts`: archived forecasts fetched
    source      TEXT NOT NULL,         -- after the fact (approximate; never forward-recorded)
    station     TEXT NOT NULL,         -- NWS CLI id, e.g. CLINYC
    target_date TEXT NOT NULL,         -- YYYY-MM-DD CLI climate day (local standard time)
    field       TEXT NOT NULL,         -- high / low / cloud / pop / qpf_in / qpf_cat
    value       REAL NOT NULL,
    run_time    TEXT NOT NULL,         -- model run (initialization) time, UTC
    valid_from  TEXT NOT NULL,         -- start of the window the value summarizes; >= run_time
    known_at    TEXT NOT NULL,         -- run_time + the source's publication lag (conservative)
    run_id      TEXT NOT NULL,         -- e.g. "GFS MOS 2026-01-23T12Z"
    provenance  TEXT NOT NULL DEFAULT 'backfill' CHECK (provenance = 'backfill'),
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (source, station, target_date, field, run_time)
);
CREATE INDEX forecast_backfill_target ON forecast_backfill (station, target_date);

CREATE TABLE exit_learning (           -- trades closed by an exit (nem.engine.exits), learned
    portfolio  TEXT NOT NULL,          -- from once their market settles: `stats` and the
    strategy   TEXT NOT NULL,          -- signal's on_settle get the settlement outcome
    window_id  TEXT NOT NULL,
    won        INTEGER,                -- our side won at settlement; NULL until learned
    learned_at TEXT,                   -- the market's settlement time; NULL = pending
    PRIMARY KEY (portfolio, strategy, window_id),
    FOREIGN KEY (portfolio, strategy, window_id) REFERENCES trades (portfolio, strategy, window_id)
);
CREATE INDEX exit_learning_pending ON exit_learning (learned_at);
