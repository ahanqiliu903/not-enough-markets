# NEM data contract

The only interface between the engine (`src/nem/`) and anything that reads its output, NEM
Desk (`apps/desk/`) first. The engine lane owns this folder; readers never edit it.

Current version: **1.0** emitted (`schema_version` on every row and JSON document); 1.1
additions are unreleased and already written (see [CHANGELOG.md](CHANGELOG.md)).

## Sources

| Source | Written by | Contents | Schema |
|---|---|---|---|
| `out/<portfolio>/summary.csv` | `nem report` (`csv` reporter) | one row: the portfolio | `schema/summary_row.schema.json` |
| `out/<portfolio>/strategies.csv` | `nem report` | one row per strategy, including paused and retired | `schema/strategy_row.schema.json` |
| `out/<portfolio>/trades.csv` | `nem report` | every trade, settled and open | `schema/trade_row.schema.json` |
| `out/<portfolio>/equity.csv` (1.1) | `nem report` | portfolio value over time: one row per hour from the first activity, plus the report time | `schema/equity_row.schema.json` |
| Google Sheets tabs `<portfolio> summary/strategies/trades/equity` | `nem report` (`sheets` reporter) | same rows as the CSVs (`equity` from 1.1) | same |
| `nem summary --json` | CLI | summary + strategies, plus per-strategy `signal_reasons` and `calibration` | `schema/summary_report.schema.json` |
| `nem status --json` | CLI (exit code 2 if unhealthy) | process heartbeats, halts, per-strategy activity | `schema/status.schema.json` |
| `out/backtests/<run_id>.json` | `nem suggest` (`--save`) | one parameter sweep: grid, windows, every variant's train metrics, the chosen variant's test metrics, and (1.1) the manifest `nem rerun` reproduces it from | `schema/backtest_run.schema.json` |
| `data/nem.db` | `nem run` / `nem record` | SQLite, raw data (below) | `src/nem/store/schema.sql` |

## Rules

- **Versioning.** `schema_version` is `MAJOR.MINOR`. Added nullable columns bump the
  minor; renamed or removed columns bump the major. Readers accept their own major and
  ignore unknown columns; any other major is a blocking error naming expected vs found.
- **Columns added in a minor** carry `"x-since": "1.1"` (etc.) in their schema property.
  They are always written and listed in `required`, but a reader must accept an older file
  of the same major without them and read them as null (NEM Desk does).
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
| `mark_value_usd`, `mark_bid_cents`, `mark_contracts` (1.1, trades) | an open trade sold now as a taker: the recorded bids of its side in the latest snapshot, walked best first, after the taker exit fee; contracts the bids don't cover count at $0 (`mark_contracts` is the covered count, so `mark_contracts` < `contracts` means no bids for the rest; an empty bid side gives value 0 and an empty `mark_bid_cents`) |
| `unrealized_pnl_usd` (1.1, trades) | `mark_value_usd` - entry cost incl. entry fee; set whenever the trade is marked |
| `mark_mid_cents` (1.1) | (best bid + best ask) / 2 on the trade's side: a reference, never used for P&L |
| `marked_at` (1.1) | time of the snapshot priced. All mark columns are empty for settled trades and when the latest snapshot is older than 15 minutes, has no depth for the trade's side, or the market has closed |
| `excluded`, `exclude_reason` (1.1, trades) | excluded by `nem trade exclude` (treated as never taken) (CSV text `true`/`false`; Sheets may show `TRUE`/`FALSE`) and why. Read absent (older file) as `false` |
| `excluded_trades` (1.1, summary and strategies) | excluded trades, settled and open, treated as never taken. A strategy row's statistics (`trades`, `wins`, win rate, edge, `verdict`, `trades_to_detect`, `brier`, `pnl_usd`, `pnl_per_trade_usd`, `roi`, `max_drawdown_usd`) and the money columns (`realized_pnl_usd`, `equity_usd`, `return_rate`, `open_positions`, `open_cost_usd`, open mark totals) all leave them out. `trades.csv` still lists them with their own `pnl_usd` and marks, so a P&L curve from it must skip `excluded` rows to match the summary |
| `open_value_usd`, `unrealized_pnl_usd`, `open_unmarked`, `open_no_bid_contracts` (1.1, summary and strategies) | sums over marked open trades (uncovered contracts at $0); `open_unmarked` counts open trades with no fresh book (left out); `open_no_bid_contracts` sums `contracts` - `mark_contracts` over the marked ones (a pending loss). 0 with no open trades, empty when open trades exist but none is marked |
| `provisional_result`, `provisional_basis`, `provisional_pnl_usd`, `observed_extreme_f` (1.1, trades) | an open trade's provisional result (`nem.settle.provisional`; empty when settled or not computed, e.g. `--no-provisional`): `locked_win` / `locked_loss` (the climate day's running high/low already decides the market, even 1°F back toward the strike), `likely_win` / `likely_loss` (observed only at the exact extreme, or a side bid / closed market's last price at 98c+; price is never locked), `conflict` (observed and price disagree), `unknown`. Basis `observed` / `price` / `both`. `provisional_pnl_usd` is the P&L if it holds, as settlement would book it. **Reporting only**: never in any P&L, equity, statistic, risk limit or what signals see; Kalshi settles. Show an unrecognised value as text |
| `pending_locked_pnl_usd`, `pending_likely_pnl_usd`, `pending_locked`, `pending_likely`, `pending_conflict` (1.1, summary and strategies) | sums / counts of the above over counted (not excluded) open trades; conflicts counted, not summed. 0 with no open trades, empty when open trades exist but nothing was computed |
| `equity.csv` (1.1, whole table) | `ts`: every hour on the hour from the hour of the portfolio's first activity, then the report's `as_of` (the last row). `realized_pnl_usd`: trades settled at or before `ts`; `interest_usd`: ledger before `ts`; `book_value_usd` = start + both (open positions at cost; the last row equals the summary's `equity_usd`). `unrealized_pnl_usd`: trades open at `ts`, marked as the trade mark columns are but at `ts` (latest snapshot at or before `ts`, 15-minute staleness, closed market, uncovered contracts at $0); `open_unmarked` of `open_positions` have no fresh book and count at cost (0). `value_usd` = book + unrealized. Excluded trades are left out at every point. Written only by `nem report` (not `nem summary`); a reader must treat a missing file as "not available" (older 1.x engine), not an error. The schema carries `"x-since": "1.1"` at the top level |
| backtest `variants[].test` | held-out metrics, present **only for the chosen variant**: showing every variant's test result would invite choosing on test data |
| backtest `fill_estimate` | always `null` in 1.0: fills walk the recorded book, no range is modelled yet |
| backtest `config`, `fees`, `execution`, `dataset`, `inputs`, `engine`, `hypothesis`, `notes` (1.1) | the run's manifest: the resolved portfolio (reporting and feed `user_agent` left out), each fee rate with its `source` (`config`, `recorded_series`, `default`, `unresolved`), fill assumptions, a SHA-256 of the replayed rows, holdout, engine version / git revision. Optional in the schema (1.0 runs lack them); always written by 1.1 engines. `run_id` gains a hash of the inputs and, if the file exists, `-2`, `-3`, ...: a run never overwrites another |

Verdict tiers in NEM Desk derive only from `verdict` and `edge_cents` (see the spec).

## SQLite (`data/nem.db`)

Schema version 10 (`PRAGMA user_version`; readers accept 3 to 10: v4 only added the
`manual_*` tables, v5 only `settlement_checks`, v6 only `forecast_backfill`, v7 only
`exit_learning`, v8 only the nullable `trades` columns `excluded_at`, `exclude_reason`,
v9 only the order lifecycle columns `orders.kind`, `filled_qty`, `cancelled_qty`,
`close_time` and `fills.fill_id`, v10 only `series_fees`);
DDL and comments in
[`src/nem/store/schema.sql`](../src/nem/store/schema.sql). Open it read-only, ideally a
consistent snapshot (`sqlite3 data/nem.db ".backup snap.db"`), never a raw copy of a live
file. Timestamps here are `isoformat` with `+00:00` and microseconds (they sort as text).

| Table | Used for |
|---|---|
| `snapshots` | top of book (`yes_bid/yes_ask/no_bid/no_ask`, dollars) and `depth_json` (`{"yes": [[price, size], ...], "no": [...]}`, best first) per market per poll |
| `settlements` | each market's `result` (`yes`/`no`) and `settled_at` |
| `signals` | every signal, taken or not: `decision` (`take`/`skip`/`no_fill`) and `reason` (e.g. `gate:max_entry_price`, `risk:strategy:stop_if_losing_after`) |
| `trades`, `orders`, `fills` | positions and how they were filled; `trades.excluded_at` / `exclude_reason` (v8) set = excluded (`nem trade exclude`). An entry order is `submitted`, then one terminal `status` (`filled`, `partial`, `no_fill`, `rejected_over_risk`, `cancelled`) with `filled_qty` + `cancelled_qty` = `qty` (v9); exit orders (`kind = 'exit'`) are `exit_take_profit` / `exit_stop` |
| `feed_values` | external data: `feed`, `key`, `value`, `known_at` (when it became knowable) |
| `heartbeats`, `halts` | process health and the kill switch |
| `manual_lots`, `manual_fills` | imported manual trading (`nem import kalshi-pnl`), `provenance = manual_import`; never traded by the engine ([docs/data-provenance.md](../docs/data-provenance.md)) |
| `settlement_checks` | `nem settle verify`: per market, Kalshi's result vs an independent station reading (`observed_value`, `source`, `derived_result`, `kalshi_result`, `status`); separate from `settlements`, which replay still reads ([docs/data-provenance.md](../docs/data-provenance.md)) |

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
- `empty/`: the same files with no strategies or trades (`equity.csv` has a header only).
- `broken/<case>/`: one failure each that readers must report, not crash on:
  `missing_column`, `wrong_major_version`, `bad_timestamp`, `duplicate_rows`.

`tests/contract/` checks that the schemas match the engine's columns, the good fixtures
validate, each broken fixture fails as expected, and the committed fixtures are current.

Changes: [CHANGELOG.md](CHANGELOG.md).
