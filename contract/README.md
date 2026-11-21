# NEM data contract

The only interface between the engine (`src/nem/`) and anything that reads its output, NEM
Desk (`apps/desk/`) first. The engine lane owns this folder; readers never edit it.

Current version: **1.0** (`schema_version` on every row and JSON document).

## Sources

| Source | Written by | Contents | Schema |
|---|---|---|---|
| `out/<portfolio>/summary.csv` | `nem report` (`csv` reporter) | one row: the portfolio | `schema/summary_row.schema.json` |
| `out/<portfolio>/strategies.csv` | `nem report` | one row per strategy, including paused and retired | `schema/strategy_row.schema.json` |
| `out/<portfolio>/trades.csv` | `nem report` | every trade, settled and open | `schema/trade_row.schema.json` |
| Google Sheets tabs `<portfolio> summary/strategies/trades` | `nem report` (`sheets` reporter) | same rows as the CSVs | same |
| `nem summary --json` | CLI | summary + strategies, plus per-strategy `signal_reasons` and `calibration` | `schema/summary_report.schema.json` |
| `nem status --json` | CLI (exit code 2 if unhealthy) | process heartbeats, halts, per-strategy activity | `schema/status.schema.json` |
| `out/backtests/<run_id>.json` | `nem suggest` (`--save`) | one parameter sweep: grid, windows, every variant's train metrics, the chosen variant's test metrics | `schema/backtest_run.schema.json` |
| `data/nem.db` | `nem run` / `nem record` | SQLite, raw data (below) | `src/nem/store/schema.sql` |

## Rules

- **Versioning.** `schema_version` is `MAJOR.MINOR`. Added nullable columns bump the
  minor; renamed or removed columns bump the major. Readers accept their own major and
  ignore unknown columns; any other major is a blocking error naming expected vs found.
- **Units are in column names:** `_usd` dollars, `_cents` cents (to 0.01c),
  `_rate` a fraction 0..1. Unsuffixed counts are integers.
- **Timestamps** in CSV/JSON are UTC ISO 8601 ending in `Z`, e.g. `2026-10-01T00:15:03Z`.
- **Empty means null.** A missing value is an empty CSV cell / JSON `null`, never `0`.
  Examples: `win_rate` before any trade, `pnl_usd` of an open trade, `interest_apy` when
  no interest is modelled.
- **Statistics are the engine's.** Readers display them; they never recompute win rates,
  intervals, edges or verdicts. Cumulative sums for charts (e.g. a P&L curve from
  `trades.csv`) are fine.
- Files are replaced atomically (written to a temp file, then renamed), so a reader never
  sees half a file.

## Fields

Every column, with its unit and meaning, is in the schema files' `description`s. The ones
that need care:

| Field | Meaning |
|---|---|
| `break_even_rate` | mean (price + fee) per contract: the win rate needed to break even |
| `edge_cents`, `edge_lo_cents`, `edge_hi_cents` | mean (payout - cost) per contract and its 95% interval, derived from the Wilson interval on the win rate |
| `verdict` | sequential test (SPRT): `edge`, `no_edge` or `undecided` |
| `trades_to_detect` | settled trades needed to detect `trades_to_detect_edge_cents` (the observed edge if positive, else the 2c target) at 5% / 80% power |
| `result` | `won`, `lost`, or `open` (not settled yet) |
| `pnl_vs_cash_usd` | gain minus what holding cash at `interest_apy` would have earned |
| backtest `variants[].test` | held-out metrics, present **only for the chosen variant**: showing every variant's test result would invite choosing on test data |
| backtest `fill_estimate` | always `null` in 1.0: fills walk the recorded book, no range is modelled yet |

Verdict tiers in NEM Desk derive only from `verdict` and `edge_cents` (see the spec).

## SQLite (`data/nem.db`)

Schema version 3 (`PRAGMA user_version`); DDL and comments in
[`src/nem/store/schema.sql`](../src/nem/store/schema.sql). Open it read-only, ideally a
consistent snapshot (`sqlite3 data/nem.db ".backup snap.db"`), never a raw copy of a live
file. Timestamps here are `isoformat` with `+00:00` and microseconds (they sort as text).

| Table | Used for |
|---|---|
| `snapshots` | top of book (`yes_bid/yes_ask/no_bid/no_ask`, dollars) and `depth_json` (`{"yes": [[price, size], ...], "no": [...]}`, best first) per market per poll |
| `settlements` | each market's `result` (`yes`/`no`) and `settled_at` |
| `signals` | every signal, taken or not: `decision` (`take`/`skip`/`no_fill`) and `reason` (e.g. `gate:max_entry_price`, `risk:strategy:stop_if_losing_after`) |
| `trades`, `orders`, `fills` | positions and how they were filled |
| `feed_values` | external data: `feed`, `key`, `value`, `known_at` (when it became knowable) |
| `heartbeats`, `halts` | process health and the kill switch |

## Fixtures

`fixtures/` is generated, never edited by hand:

```bash
uv run python contract/generate_fixtures.py
```

- `basic/`: real engine output from seeded synthetic markets. Portfolio `fixture` with three
  strategies ending on the three verdicts (`btc_edge` edge, `eth_no_edge` no_edge and
  halted, `sol_undecided` undecided), about 400 trades, 42 hours of 1-minute snapshots on
  three series, a `btc_spot` feed, heartbeats, `summary.json`, `status.json`, and one
  `suggested` backtest run.
- `empty/`: the same files with no strategies or trades.
- `broken/<case>/`: one failure each that readers must report, not crash on:
  `missing_column`, `wrong_major_version`, `bad_timestamp`, `duplicate_rows`.

`tests/contract/` checks that the schemas match the engine's columns, the good fixtures
validate, each broken fixture fails as expected, and the committed fixtures are current.

Changes: [CHANGELOG.md](CHANGELOG.md).
