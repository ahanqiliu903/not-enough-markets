# NEM Desk tickets

Work one ticket at a time; edit only `apps/desk/**`. The data contract lives in
[`contract/`](../../contract/README.md) (read it; never edit it). Build against
`contract/fixtures/` only. Never recompute statistics, verdicts or fills: display the
engine's. Colors only in `src/renderer/theme.ts`; original pixel art only (no Hypixel or
Mojang names, fonts, textures or sprites). Ask before adding a dependency not listed in the
README's stack.

Decisions: macOS first, then Windows. Releases are **unsigned** (no paid Apple account): the
first-open warning is accepted, and auto-update is replaced by an "update available" notice.

| ID | Title | Status |
|---|---|---|
| DESK-001 | Scaffold | ✅ |
| DESK-002 | Theme tokens and fonts | ✅ |
| DESK-005 | App shell | ✅ |
| DESK-003 | Data layer | ✅ |
| DESK-004 | Source manager and file watching | ✅ |
| DESK-006 | Portfolios screen | ✅ |
| DESK-007 | Strategy detail | ✅ |
| DESK-008 | Trades blotter | ✅ |
| DESK-009 | Compare | ✅ |
| DESK-010 | SQLite reader | ✅ |
| DESK-011 | Market and order book screen | ✅ |
| DESK-012 | Google Sheets source | ✅ |
| DESK-013 | VPS sync | ✅ |
| DESK-014 | Feeds and Status screen | ✅ |
| DESK-015 | Backtest runs list and detail | ✅ |
| DESK-016 | Sweep heatmap | ✅ |
| DESK-017 | Backtest vs paper vs live | ✅ |
| DESK-018 | Packaging (unsigned dmg + NSIS on GitHub Releases) | ✅ |
| DESK-019 | Update-available notice | ✅ |
| DESK-020 | Logs and diagnostics | ✅ |
| DESK-021 | Halt and resume over SSH (deferred) | ⬜ |
| DESK-022 | Pixel accents pass | ⬜ |
| DESK-023 | Docs and screenshots | ⬜ |
| DESK-024 | Explore Kalshi markets | ✅ |

## Phase 1: local files

- **DESK-003 Data layer.** Main-process readers for a folder source laid out like the
  engine's `out/` (`<portfolio>/{summary,strategies,trades}.csv`, `backtests/*.json`) plus
  optional `summary.json`/`status.json`. Validate against `contract/schema/*.json`; the
  CSV coercion rules are the ones in `tests/contract/test_contract.py` (empty = null,
  integer/number by schema type, unparseable stays text so validation reports it). Accept
  `schema_version` major 1 only. Errors are structured values (file, row, field, message),
  never thrown to the renderer. Trades are paged in main. Typed IPC; unit tests on every
  fixture (`basic`, `empty`, each `broken/<case>`).
- **DESK-004 Sources and watching.** Add/remove a folder source (persisted in main, e.g.
  `app.getPath("userData")`), file watching (UI updates < 2 s after a CSV changes), a deleted
  file shows an error not a crash, stale banner from `summary.as_of` (15 min default).
- **DESK-006 Portfolios.** Spec screen 1: a card per portfolio (equity, P&L vs cash, open
  positions) with strategy rows and verdict badges; works on `basic` and `empty`.
- **DESK-007 Strategy detail.** Spec screen 2: tier badge + lore card, win-rate interval vs
  break-even chart, edge interval, SPRT progress (`trades` vs `trades_to_detect`), P&L curve
  and drawdown (cumulative sums of `trades.csv` are allowed), calibration and signal reasons
  from `summary.json` when present.
- **DESK-008 Trades.** Virtualized blotter (TanStack Table + Virtual): sort, filter, CSV
  export; 100k rows smooth (generate the large set in the test, don't commit it).
- **DESK-009 Compare.** 2 to 4 strategies, retired included; P&L curves from a common start.

## Phase 2+

- **DESK-010** SQLite read-only; a snapshot file (`data/snapshot/nem.db` from
  `deploy/snapshot.sh`) or `contract/fixtures/basic/nem.db`. Built on Node's `node:sqlite`
  (works in Electron 45 and Node 24, no native module) instead of `better-sqlite3`.
- **DESK-011** Market screen: depth (canvas), top-of-book series (uPlot), feed overlay with
  `known_at` markers.
- **DESK-013** VPS sync: host/user/key path in settings; system `ssh` + `rsync` of
  `data/snapshot/nem.db` and `out/` into a local cache; host key verified, fails closed.
- **DESK-012** Google Sheets: no credentials. The sheet is shared "Anyone with the link:
  Viewer"; main fetches each `<portfolio> summary|strategies|trades` tab as CSV (gviz
  endpoint, `docs.google.com` only, GET, no cookies, redirects not followed) into a userData
  cache read like any folder (same coercion and validation). ID kept in userData only,
  never logged; poll every 5 min by default (1 min minimum), backoff on 429/5xx. Replaces
  the spec's service-account + `safeStorage` design (no keys in the app).
- **DESK-014** Status screen from `status.json` / `nem status --json` schema.
- **DESK-015/016** Backtests from `backtest_run.schema.json`: run list, grid heatmap of train
  edge with `n` per cell, chosen cell marked, train vs test (test exists for the chosen
  variant only, by design), `fill_estimate` is null in contract 1.0.
- **DESK-020** Logs: `<userData>/logs/main.log`, rotated at 1 MB (3 old files); Settings →
  Diagnostics (versions, contract, sources and last loads, DB, sync state, recent lines)
  and "Copy diagnostics"; everything redacted (sheet IDs, hosts, users, home/key paths).

## DESK-024: Explore Kalshi markets (screen 9)

Read-only research on Kalshi's **public** market data: no key, no account, no orders.

- **Client (main process only).** GET-only, host allowlist `api.elections.kalshi.com`
  (`/trade-api/v2`): `/series`, `/series/{ticker}`, `/markets?series_ticker=&status=open`,
  `/markets/{ticker}`, `/markets/{ticker}/orderbook`. Mirror `src/nem/market/kalshi.py`:
  dollar-string prices to numbers, orderbook bids sorted best-first, retries with backoff,
  at most 5 requests/s, `User-Agent: NEM Desk/<version>`. Errors (offline, 429, 5xx) come back
  as values and render as states. Tests parse recorded JSON (`tests/fixtures/kalshi/*.json`);
  no network in tests. Renderer CSP stays `connect-src 'none'`.
- **Layout, in the spirit of a game marketplace (original art, our own names):**
  - **Category tabs** on the left (Crypto 15m, Temperature, ...; seeded from
    `docs/kalshi-tickers.txt`), each with an original 16x16 pixel icon.
  - **Series grid**: slot-like tiles (icon, ticker, open-market count, next close countdown).
  - **Market list** for a series with a live **countdown to close**, sortable "closing soon".
  - **Market page**: two columns, YES and NO, each with best price and top 5 levels
    (quantity per level), like an instant-buy / instant-sell panel; top-of-book chart.
  - **Rules card on hover** (reuse `LoreCard`): settlement source and measurement, what YES
    means (`strike_type`, floor/cap), close time in ET, and a predictor note when a common
    feed differs from the settlement source (NWS vs The Weather Company, Coinbase vs CF
    Benchmarks).
  - **"Start a strategy here"** copies `uv run nem strategy add PORTFOLIO NAME --series S`.
- **Rules.** Tier colors stay reserved for verdicts; market tiles use amber/neutral. Pixel
  styling only on frames, icons, headers and cards; prices and tables stay monospace.
  "Pixel accents: off" leaves a plain terminal layout. Labels are "Explore", "Series",
  "Market", "Order book".
- **Keys.** Cmd/Ctrl+9; command bar "explore SERIES".
