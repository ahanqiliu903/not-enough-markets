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
> **Work in progress.** You can record live Kalshi market data today, but nothing trades yet, not even on paper. See [Roadmap](#roadmap).

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

- **Portfolio**: one YAML file and one pool of capital. It's either **paper** (virtual starting balance) or **live** (a budget carved out of your real Kalshi balance), never both, so its P&L always means something. Portfolio-level risk limits apply across all its strategies.
- **Strategy**: one market series plus four pluggable parts:
  - **signal**: when to enter and at what price (e.g. `extreme_favorite`)
  - **gates**: extra filters that can veto a trade (e.g. `max_entry_price`)
  - **sizing**: how many contracts (e.g. `fixed`)
  - **risk**: per-strategy limits

  Strategies can be `active`, `paused` or `retired`. Retired ones keep their history so you can compare old ideas against new ones.

Example portfolio file *(the config format works today; the plugins named here arrive in M3)*:

```yaml
name: btc_research
mode: paper
starting_balance: 1000
poll_seconds: 5                  # default for every strategy below
risk: {daily_loss_cap: null, max_open_positions: 3}   # null = disabled

strategies:
  - name: fav90
    series: KXBTC15M
    signal: {type: extreme_favorite, threshold: 0.90, min_edge: 0.015}
    gates:
      - {type: max_entry_price, value: 0.95}
    sizing: {type: fixed, contracts: 5}

  - name: fav85
    series: KXBTC15M
    poll_seconds: 2                # per-strategy override
    signal: {type: extreme_favorite, threshold: 0.85, min_edge: 0.015}
    sizing: {type: fixed, contracts: 5}
```

## Planned workflow

1. Write the strategy hypothesis in plain English.
2. Fit its constraints into a portfolio YAML config.
3. Set up the required data pipelines.
   - If data needs to be collected before backtesting, set up logging and schedules to do so.
4. Run a backtest (**not** a validation).
5. Run paper trading.
6. *(Optional)* Deploy live.

### Hot take: I hate backtests

Especially nowadays, it's very easy for them to give optimistic results. The workflow includes a backtest only to reject strategies that show clearly negative results. Paper trading is a better indicator.

## Design principles

- **Same code for backtest, paper and live.** Only the market data source and the broker change, so a strategy can't behave differently in replay than it does live.
- **No look-ahead.** Signals and gates only see results that settled *before* the current time. This is enforced in one place and tested.
- **Every signal is logged with a reason**, including the ones that were skipped, so you can measure what each gate actually did.
- **Idempotent orders.** Order IDs are deterministic, so a retry can never double-fill.
- **Fail safe.** Risk checks fail closed. Each gate declares whether it fails open or closed. Risk limits use `null` for "disabled", never `0`.
- **Secrets only from environment variables.** Nothing secret is ever in a config file or a commit; [gitleaks](https://github.com/gitleaks/gitleaks) runs on every commit and in CI.

## Roadmap

| | Milestone | What it adds |
|---|---|---|
| ✅ | **M0 Skeleton** | Packaging (uv), lint (ruff), strict typing (pyright), tests (pytest), secret scanning, CI |
| ✅ | **M1 Core** | Portfolio/strategy config with validation, plugin registry, SQLite store, look-ahead-safe strategy context, Kalshi window-code math |
| ✅ | **M2 Market data** | Kalshi read client and auth, `nem record` to collect market snapshots and settlements, replay from recorded data |
| ⬜ | **M3 Paper trading** | The trading loop, paper broker with fees and realistic fills, `extreme_favorite` signal, `nem init` / `nem portfolio` / `nem strategy` commands, `make demo` |
| ⬜ | **M4 Stats & reporting** | Win rate with confidence intervals vs break-even, P&L, drawdown, "enough trades to decide yet?" test, CSV + Google Sheets reports |
| ⬜ | **M5 24/7 operation** | systemd service, heartbeats, `nem status` / `nem halt` / `nem resume` |
| ⬜ | **M6 Backtest** | Replay recorded data through the same loop, to filter out losers |
| ⬜ | **M7 Live trading** | Live broker on Kalshi (demo first), risk manager, budgets across live portfolios |
| ⬜ | **M8 Docs** | Lessons from running live, "write a signal" guide, a worked case study |

Later: the agent workflow (hypothesis in plain English → portfolio config), and a website view of portfolios and strategies.

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
