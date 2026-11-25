# Contract changelog

## 1.1 (unreleased, backward-compatible)

- `nem summary --json`: an imported manual strategy (`nem import kalshi-pnl`) may carry an
  optional `held_to_settlement` object, the hold-to-settlement counterfactual on checked
  settlement results (`nem settle verify`): `lots`, `excluded` (integers), `win_rate`,
  `win_rate_lo`, `win_rate_hi`, `break_even_rate`, `edge_cents`, `edge_lo_cents`,
  `edge_hi_cents`, `verdict` (same units and meanings as the strategy row's columns). It is
  absent until the lots' markets are checked. The row's own columns stay the actual exits'
  (`verdict` `undecided`). No schema file changed: strategy rows already allow additional
  properties, so 1.0 readers ignore it. CSV and Sheets tables are unchanged.
- `held_to_settlement` also carries `markets` and `markets_with_loss` (integers) and
  `market_win_rate`, `market_win_rate_lo`, `market_win_rate_hi` (markets with no losing
  lot / markets, Wilson 95%): lots on one market share its outcome, so the per-lot numbers
  overstate the evidence. A lot on a market Kalshi settled `scalar` now counts with the
  settlement check's `derived_result` (as `nem settle verify` replays it) instead of being
  excluded.
- `held_to_settlement` also carries the per-market statistics (one bet per market, won
  when none of its lots lost, at the mean of its lots' cost per contract incl. open fee, in
  market-date order): `market_break_even_rate`, `market_edge_cents`,
  `market_edge_lo_cents`, `market_edge_hi_cents`, `market_z`, `market_p`,
  `market_verdict` (`edge` / `no_edge` / `undecided`) and `markets_to_detect`. When lots
  cluster on markets, `market_verdict` is the one to read; `verdict` stays per lot.
- `nem summary --json`, CSV and Sheets: a strategy whose gates read backfilled forecasts
  (`backfill_sources`) has its `verdict` capped: `edge` is reported as `undecided` (a
  backtest on backfilled data can reject an idea, not confirm it); `no_edge` stays. Its
  JSON row carries `backfilled_forecasts: true` (optional, absent otherwise). The text
  report labels the strategy line and verdict `[backfilled forecasts]`.
- `nem summary --json`: an engine strategy that exits early (or has exited) may carry an
  optional `exits` object keyed by `settlement`, `take_profit`, `stop`, each with `trades`,
  `wins` (P&L > 0), `contracts`, `pnl_usd`, `won_at_settlement` (the side won when the
  market settled) and `learning_pending` (exits whose market hasn't settled). Additive, as
  above; absent for strategies that hold to settlement.
- SQLite schema v7 adds `exit_learning` (exited trades, learned from at settlement); NEM
  Desk accepts v3 to v7.
- Mark-to-market of open positions (docs/exits-and-fills.md, "Mark-to-market"). New
  nullable columns, marked `"x-since": "1.1"` in the schemas (readers fill them with null
  when an older 1.x file lacks them):
  - `trades`: `mark_bid_cents`, `mark_mid_cents`, `mark_contracts` (integer),
    `mark_value_usd`, `unrealized_pnl_usd`, `marked_at` (UTC Z). Empty for settled trades,
    for a latest snapshot older than 15 minutes, once the market has closed, and when the
    snapshot recorded no depth for the trade's side.
  - Contracts the recorded bids don't cover are valued at $0 (nobody would buy them now):
    `mark_value_usd` = proceeds of the `mark_contracts` covered after the exit fee + $0
    for the rest, and `unrealized_pnl_usd` = `mark_value_usd` - cost is always set for a
    marked trade. An empty bid side gives `mark_value_usd` 0, `mark_bid_cents` empty,
    unrealized = -cost. `mark_contracts` < `contracts` flags it. (Semantics change within
    unreleased 1.1: earlier drafts left `unrealized_pnl_usd` empty unless every contract
    was covered and excluded such trades from the totals, which overstated unrealized P&L.)
  - `summary` and `strategies`: `open_value_usd`, `unrealized_pnl_usd` (sums over marked
    open trades, uncovered contracts at $0), `open_unmarked` (integer, open trades with no
    fresh book: stale, missing or no-depth snapshot, closed market; left out of the sums),
    `open_no_bid_contracts` (integer, sum of `contracts` - `mark_contracts` over marked
    open trades; 0 with no open trades, empty when none is marked).
  - `nem summary` text: an `unrealized` line per portfolio and strategy with open
    positions, labelled "liquidation value at best bids, after exit fee", ending in
    "N contracts with no bids valued at $0 (pending loss)" when any exist.
  - Fixtures regenerated with the new columns (every `basic` trade is settled, so the trade
    mark columns are empty and the totals 0).
- Excluding trades (`nem trade exclude` / `include` / `list --excluded`).
  SQLite schema v8 adds nullable `trades.excluded_at` and `trades.exclude_reason` (no new
  table); NEM Desk accepts v3 to v8. New columns, `"x-since": "1.1"`:
  - `trades`: `excluded` (boolean; CSV text `true`/`false`, read absent as false) and
    `exclude_reason` (string, null when counted).
  - `summary` and `strategies`: `excluded_trades` (integer, settled and open).
- SQLite schema v9: order lifecycle for atomic execution. `orders` gains `kind`
  (`entry`/`exit`), `filled_qty`, `cancelled_qty` (terminal entry orders: filled +
  cancelled = qty, the IOC remainder cancelled) and nullable `close_time`; `fills` gains
  `fill_id` (unique). Entry `status` gains `cancelled` (an unknown outcome resolved on
  startup). Existing databases are upgraded in place; no CSV/JSON column changed. NEM Desk
  accepts v3 to v9.
  - Semantics: excluded trades are left out of every statistic (strategy row `trades`,
    `wins`, win rate, edge and intervals, `verdict`, `trades_to_detect`, `brier`, calibration,
    `exits`, and the row's `pnl_usd`, `pnl_per_trade_usd`, `roi`, `max_drawdown_usd`), of
    `stop_if_losing_after`, and of what signals and gates see (`ctx.settled_trades`,
    `ctx.portfolio_settled_trades`, `ctx.stat`; `on_settle` isn't called for a trade
    excluded before it settles), and of money as if never taken: summary
    `realized_pnl_usd`, `equity_usd`, `return_rate`, `pnl_vs_cash_usd`, `open_positions`,
    `open_cost_usd` and the open mark totals, cash, and every risk limit (`daily_loss_cap`,
    `max_drawdown`, `max_allocation`, `max_open_positions`, `max_city_allocation`).
    `trades.csv` still lists them, with their own `pnl_usd` and marks.
  - `nem summary` text: "N excluded (M settled, P&L x not counted; left out of P&L, equity
    and risk limits too; ...)" per portfolio and strategy when N > 0.
  - Fixtures regenerated (no trade is excluded in them; every row has `excluded` false).
- Provisional ("pending / likely") results of open trades (`nem.settle.provisional`,
  computed by `nem summary` / `nem report` unless `--no-provisional`). New columns,
  `"x-since": "1.1"`:
  - `trades`: `provisional_result` (string: `locked_win`, `locked_loss`, `likely_win`,
    `likely_loss`, `conflict`, `unknown`; no enum in the schema, so readers show a new
    value as text), `provisional_basis` (`observed`, `price`, `both`),
    `provisional_pnl_usd`, `observed_extreme_f` (integer). Empty for settled trades and
    when not computed.
  - `summary` and `strategies`: `pending_locked_pnl_usd`, `pending_likely_pnl_usd`,
    `pending_locked`, `pending_likely`, `pending_conflict` (integers), over counted open
    trades. 0 with no open trades, empty when not computed.
  - Reporting only: nothing here enters realized P&L, equity, statistics, risk or
    `Context`. `nem summary` text: a `pending` line per portfolio and strategy ("locked
    +x (n won, m lost), likely +y (...), k conflict; if they settle that way; not counted,
    Kalshi settles").
  - Fixtures regenerated (no open trades in them: the pending totals are 0 and the trade
    columns empty).
- New table `equity` (portfolio value over time, `nem.stats.equity`):
  `out/<portfolio>/equity.csv` and the Sheets tab `<portfolio> equity`, written by
  `nem report` only (`build_report(..., history=True)`; `nem summary` and
  `nem report --no-publish` skip it; not in `nem summary --json`). Schema
  `equity_row.schema.json`, `"x-since": "1.1"` at the top level: the whole table is new,
  so a reader treats a missing file as "not available", never an error. Columns:
  `schema_version`, `portfolio`, `ts` (UTC Z), `book_value_usd`, `realized_pnl_usd`,
  `interest_usd`, `unrealized_pnl_usd`, `value_usd`, `open_positions`, `open_unmarked`
  (integers). One row per hour on the hour from the hour of the portfolio's first activity,
  plus a last row at the report's `as_of`; none before any activity. Excluded trades left
  out; marks as the trade mark columns, at each `ts`; unmarked open trades at cost.
  Reporting only. Fixtures regenerated (`basic`: 44 rows; every hourly point falls on a
  15-minute close, so its open trades are unmarked and value equals book value; `empty`:
  header only).
- Emitted `schema_version` is still `1.0`; bumping it also means regenerating
  `contract/fixtures/`.

## 1.0 (2026-11-20)

First frozen version (CONTRACT-001 to 004).

- Report tables renamed to carry units (`_usd`, `_cents`, `_rate`) and a `schema_version`
  column. Old names: `start` → `starting_balance_usd`, `realized` → `realized_pnl_usd`,
  `edge_per_contract` (dollars) → `edge_cents`, `breakeven` → `break_even_rate`,
  `sprt` → `verdict`, `trades_needed` → `trades_to_detect`, `qty` → `contracts`,
  `avg_price` (dollars) → `entry_price_cents`, `won` (0/1) → `result`, and so on.
- Added: `started_at`, `interest_apy`, `pnl_vs_cash_usd`, edge interval, `pnl_per_trade_usd`,
  `trades_to_detect_edge_cents`, trade `close_time`; open trades now appear in `trades`.
- Timestamps end in `Z`.
- New: `nem summary --json`, `nem status --json`, backtest run files from `nem suggest`.
