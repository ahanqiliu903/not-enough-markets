# NotEnoughMarkets (NEM)

[![CI](https://github.com/ahanqiliu903/not-enough-markets/actions/workflows/ci.yml/badge.svg)](https://github.com/ahanqiliu903/not-enough-markets/actions/workflows/ci.yml)
![Status: WIP](https://img.shields.io/badge/status-WIP-orange)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A template for Kalshi algorithmic trading.

<sub>Name inspired by Moulberry's [NotEnoughUpdates](https://github.com/Moulberry/NotEnoughUpdates) for Hypixel Skyblock.</sub>

> [!WARNING]
> **This is NOT a trading strategy, and it will NOT guarantee you any profits.**
> It is meant to be an all-in-one home for automated prediction market trading.

> [!NOTE]
> **Work in progress.** You can record live Kalshi data, paper-trade on it, and get statistics and CSV/Google Sheets reports today. Live trading and external feeds are still to come. See [Roadmap](#roadmap).

## What is this for?

Say you have a hypothesis:

> *Is Kalshi's 15-min BTC Up/Down market overpricing contracts in the 90c+ price range?*

How do you test it? Given the constraints of the strategy, a code editor/agent *(planned)* takes in inputs about the strategy you want to test (which market, price range, how often, etc.) and turns them into a testable paper trading system, with automated market and portfolio data ingestion and analysis.

- **No new infrastructure every time.** Reuse the same engine for every idea.
- **Common parameters** (stricter entry, timing, sizing) so you can tweak your strategy as you go.
- **Easy comparison** of your current strategies against previous ones.

## How it's organized

```
portfolios/
├── btc_research.yaml     # paper portfolio, $1,000 virtual balance
│   ├── fav90             #   strategy: BTC 15m, buy favorite at 90c+
│   └── fav85             #   strategy: same idea, 85c+ threshold
└── live_small.yaml       # live portfolio, $50 budget
    └── fav90_live
```

- **Portfolio**: one YAML file and one pool of capital. It's either **paper** (virtual starting balance) or **live** (a budget carved out of your real Kalshi balance), never both, so its P&L always means something. Portfolio-level risk limits apply across all its strategies, and its **feeds** are shared by all of them.
- **Strategy**: one market series, checked on its own schedule (`check_every`, from seconds to minutes), plus four pluggable parts:

| Part | Decides | Examples |
|---|---|---|
| **signal** | When to enter, which side, at what price. Can read feeds. | `extreme_favorite` (buy the favorite at 90c+) |
| **gates** | Whether to veto a trade the signal wants. Can read feeds. | `max_entry_price`, `time_in_window` (only the last N minutes), "skip if forecast precipitation > 60%" |
| **sizing** | How many contracts. | `fixed` (N contracts), `percent` (N% of the portfolio), `kelly` (fractional, always with a contract cap) |
| **risk** | Hard limits. Always fail closed. | `max_allocation`, `daily_loss_cap`, `max_drawdown`, `max_open_positions`, kill rule ("pause if win rate < break-even after N trades") |

Strategies can be `active`, `paused` or `retired`. Retired ones keep their history so you can compare old ideas against new ones.

- **Feeds** (external data): anything a market depends on besides its own prices, e.g. a crypto spot price, a commodity price, or a temperature forecast. Every market targets something different, so feeds are plugins too: write one small class per data source and any signal or gate can use it by name.
  - Feed values are **recorded with timestamps**, like market data, so replay sees only what was known at the time. Forecasts also record when they were **issued**, so a backtest can't use a forecast published after the trade.
  - Each feed has a `max_age`. Older data counts as unavailable, and each gate declares whether it then lets trades through or blocks them.

Example portfolio file *(`coinbase_spot` and `spot_agrees` are examples of feed plugins planned for M6; everything else works today)*:

```yaml
name: btc_research
mode: paper
starting_balance: 1000
interest_apy: 0.0325             # Kalshi's current rate; it changes, so check it. null = off
check_every: 5s                  # default for every strategy below
risk: {daily_loss_cap: null, max_open_positions: 3}   # null = disabled

feeds:
  - {name: btc_spot, type: coinbase_spot, product: BTC-USD, every: 5s, max_age: 30s}

strategies:
  - name: fav90
    series: KXBTC15M
    signal: {type: extreme_favorite, threshold: 0.90, min_edge: 0.015}
    gates:
      - {type: max_entry_price, value: 0.95}
      - {type: time_in_window, last_minutes: 5}
    sizing: {type: fixed, contracts: 5}
    risk: {max_allocation: 200, max_open_positions: 1}

  - name: fav85_spot
    series: KXBTC15M
    check_every: 2m                # per-strategy override
    signal: {type: extreme_favorite, threshold: 0.85, min_edge: 0.015}
    gates:
      - {type: spot_agrees, feed: btc_spot}   # veto if spot has moved against the favorite
    sizing: {type: kelly, fraction: 0.25, max_contracts: 10}
```

Positions are held to settlement for now. Exits before settlement (stop-loss, take-profit) are planned as a later plugin kind.

## Planned workflow

1. Write the strategy hypothesis in plain English.
2. Fit its constraints into a portfolio YAML config.
3. Set up the required data pipelines.
   - If data needs to be collected before backtesting, set up logging and schedules to do so.
4. Run a backtest (**not** a validation).
5. For strategies that weren't rejected, get suggested starting settings (e.g. entry threshold, time in window, sizing).
   - A small parameter sweep over recorded data, tuned on one period and checked on a later one it never saw.
   - Suggests the middle of a stable region of good settings rather than the single best point, and shows sample sizes, so you don't start paper trading on a lucky fit.
6. Run paper trading with those settings.
7. *(Optional)* Deploy live.

### Hot take: I hate backtests

Especially nowadays, it's very easy for them to give optimistic results. The workflow includes a backtest only to reject strategies that show clearly negative results, and the suggested settings are just a sensible starting point. Paper trading is a better indicator.

## Design principles

- **Same code for backtest, paper and live.** Only the data sources (market and feeds) and the broker change, so a strategy can't behave differently in replay than it does live.
- **No look-ahead.** Signals and gates only see results that settled, and feed values that were published, *before* the current time. This is enforced in one place and tested.
- **Every signal is logged with a reason**, including the ones that were skipped, so you can measure what each gate actually did.
- **Kalshi's fees and interest are part of every number.** Paper fills charge Kalshi's taker fee (rounded up to the cent, per order) and walk the real orderbook, so entry prices include slippage. Edge and break-even win rate are computed after fees. Paper portfolios can accrue Kalshi's interest (APY on cash and open positions, accrued daily), so a strategy has to beat simply holding cash to look good.
- **Idempotent orders.** Order IDs are deterministic, so a retry can never double-fill.
- **Fail safe.** Risk checks fail closed. Each gate declares whether it fails open or closed. Risk limits use `null` for "disabled", never `0`.
- **Secrets only from environment variables.** Nothing secret is ever in a config file or a commit; [gitleaks](https://github.com/gitleaks/gitleaks) runs on every commit and in CI.

## Roadmap

| | Milestone | What it adds |
|---|---|---|
| ✅ | **M0 Skeleton** | Packaging (uv), lint (ruff), strict typing (pyright), tests (pytest), secret scanning, CI |
| ✅ | **M1 Core** | Portfolio/strategy config with validation, plugin registry, SQLite store, look-ahead-safe strategy context, Kalshi window-code math |
| ✅ | **M2 Market data** | Kalshi read client and auth, `nem record` to collect market snapshots and settlements, replay from recorded data |
| ✅ | **M3 Paper trading** | The trading loop; plugin interfaces for signals, gates, sizing, risk and feeds; paper broker with Kalshi fees, realistic fills and interest accrual; `extreme_favorite`, `max_entry_price`, `time_in_window`, `fixed` / `percent` / `kelly` sizing; `nem init` / `nem portfolio` / `nem strategy` commands; `make demo` |
| ✅ | **M4 Stats & reporting** | Win rate with confidence intervals vs break-even, P&L, drawdown, "enough trades to decide yet?" test, CSV + Google Sheets reports |
| ⬜ | **M5 24/7 operation** | systemd service, heartbeats, `nem status` / `nem halt` / `nem resume` |
| ⬜ | **M6 External feeds** | First real feeds (crypto spot price, weather forecast) with recording, and example feed-based gates |
| ⬜ | **M7 Backtest & suggested settings** | Replay recorded market and feed data through the same loop to filter out losers; parameter sweeps with held-out validation to suggest starting settings for paper trading |
| ⬜ | **M8 Live trading** | Live broker on Kalshi (demo first), risk manager, budgets across live portfolios |
| ⬜ | **M9 Docs** | Lessons from running live, "write a signal / feed" guides, a worked case study |

Later: exits before settlement, the agent workflow (hypothesis in plain English → portfolio config), and a website view of portfolios and strategies.

## Quickstart

No Kalshi account needed for any of this.

```bash
make setup                 # install dependencies
make demo                  # replay ~2h of bundled BTC/ETH data through examples/portfolios/
```

Paper-trade your own idea:

```bash
uv run nem init                                          # first portfolio (asks name + balance)
uv run nem strategy add paper fav90 --series KXBTC15M    # appends a template; edit it in portfolios/paper.yaml
uv run nem validate                                      # checks every file and plugin
uv run nem run                                           # paper-trade on live Kalshi prices (Ctrl-C stops)
uv run nem summary                                       # statistics per strategy (see below)
```

More: `nem portfolio new|list|show`, `nem replay --data data/nem.db` to run portfolios over recorded data, `nem export` to save recordings as a portable file.

## Reading the statistics

`nem summary` (and `make demo`) print this for every strategy:

```
  fav90_any  KXBTC15M  active  5 trades (4 won), 0 open
    win rate    80.0% [37.6%, 96.4%]   break-even 91.2%   z -0.88 (p 0.81)
    edge        -11.2c per contract [-53.6c, +5.2c]
    P&L         -2.79  (-0.56/trade, ROI -12.3%)   max drawdown 4.58
    verdict     undecided after 5 trades; ~1,152 trades to detect a 2.0c edge
    calibration Brier 0.177  92%-94%: said 93.1%, won 80.0% (n=5)
```

- **win rate**: with a 95% confidence interval (Wilson), so a lucky streak doesn't look like skill.
- **break-even**: every trade pays price + fees to win $1, so it breaks even at a win rate equal to its cost per contract. The question is always *win rate vs break-even*.
- **z / p**: how surprising the wins are if the strategy has no edge (each trade winning exactly as often as its price implies).
- **edge**: average profit per contract, with its interval.
- **verdict**: a sequential test (SPRT) checked after every trade. It says *edge*, *no edge* or *undecided*, plus roughly how many trades it takes to detect an edge of that size. Near 90c, telling a 2c edge from none takes over a thousand trades, which is why paper trading takes patience.
- **calibration**: does the signal's estimated win probability match reality? Brier score: lower is better.
- Portfolios with `interest_apy` also show **P&L vs simply holding cash** at Kalshi's rate.

Tune the test with `--edge 0.03`, `--alpha`, `--power`.

## Reports (CSV, Google Sheets)

Reporting runs as its own process, so reports keep flowing even when no strategy is running:

```bash
uv run nem report                 # print and publish once
uv run nem report --every 5m      # keep publishing
```

Each portfolio chooses where its reports go:

```yaml
reporting:
  - {type: csv, path: out}                       # out/<portfolio>/{summary,strategies,trades}.csv
  - {type: sheets, sheet_id_env: SHEET_ID}       # tabs "<portfolio> summary|strategies|trades"
```

Google Sheets setup: `uv sync --extra sheets`, create a Google Cloud service account with a JSON key, share the spreadsheet with the service account's email, then set `SHEETS_CREDENTIALS=/path/to/key.json` and `SHEET_ID=<spreadsheet id>` in the environment (never in the YAML). Several portfolios can share one spreadsheet; each writes only its own tabs.

## Recording market data

Recording takes calendar time, so start it early (ideally 24/7 on a VPS). No Kalshi account needed: public market data doesn't require an API key.

```bash
uv run nem record --series KXBTC15M --series KXETH15M        # poll every 5s, forever
uv run nem record --series KXBTC15M --interval 10 --depth 20  # slower, deeper orderbook
uv run nem record --series KXBTC15M --once                    # single poll, then exit
```

Each poll stores every open market's top of book and orderbook depth to `data/nem.db` (SQLite). Once a market closes, the recorder fetches its result, so recorded data can later be replayed and settled. Network errors are logged and retried on the next poll.

## Development

Requires [uv](https://docs.astral.sh/uv/) and [gitleaks](https://github.com/gitleaks/gitleaks).

```bash
make setup    # install dependencies and pre-commit hooks
make check    # lint, type-check, test
make fmt      # auto-fix lint and formatting
```

## Requirements

| Requirement | Needed for |
|---|---|
| Virtual Private Server (VPS) | 24/7 data collection and paper/live trading. I personally use DigitalOcean. |
| Google Cloud service account | *Optional but recommended.* Reporting CSV data to Google Sheets. |
| Kalshi account | Live trading only. |
| Patience | Everything. |

## License

[MIT](LICENSE)
