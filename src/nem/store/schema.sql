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
    depth_json  TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX snapshots_series_ts ON snapshots (series, ts);

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
    status          TEXT NOT NULL
);

CREATE TABLE fills (
    id              INTEGER PRIMARY KEY,
    client_order_id TEXT NOT NULL REFERENCES orders (client_order_id),
    ts              TEXT NOT NULL,
    price           REAL NOT NULL,
    qty             INTEGER NOT NULL CHECK (qty > 0),
    fee             REAL NOT NULL
);

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
    won          INTEGER,                -- NULL until settled
    realized_pnl REAL,
    settled_at   TEXT,
    PRIMARY KEY (portfolio, strategy, window_id)
);
CREATE INDEX trades_settled_at ON trades (settled_at);

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
