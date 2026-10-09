# AGENTS.md: guide for coding agents working in NotEnoughMarkets

Read this before doing anything in this repo. It applies to any coding agent (Claude Code,
Codex, Cursor, ...).

## What this project is

NotEnoughMarkets (NEM) is a framework for testing Kalshi trading ideas. A user states a
hypothesis ("BTC 15-minute favorites above 90c win more often than their price implies");
NEM turns it into a **portfolio** of **strategies**, paper-trades them on live Kalshi
prices, records everything, and reports whether the strategy is actually beating
break-even after fees.

Core model (details in [README.md](README.md)):

- **Portfolio**: one YAML file in `portfolios/`, one pool of paper (or later live) capital.
- **Strategy**: one Kalshi series plus pluggable parts: a **signal** (when to enter, which
  side, at what price), **gates** (vetoes), **sizing**, and **risk** limits.
- **Feeds**: external data (spot prices, forecasts) that signals and gates read by name.
- One engine for replay, paper and live; only the data source and the broker change.

## Your job when a user asks you to test an idea

You translate the user's hypothesis into a working, honest paper-trading setup. You do
**not** decide whether the idea is good, and you never make results look better than they
are.

1. **Restate the hypothesis** in one or two sentences, including what would prove it wrong.
2. **Find the markets.** Start from [docs/kalshi-tickers.txt](docs/kalshi-tickers.txt), then
   confirm the series exists and has open markets via the public API (no key needed):
   `https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=SERIES&status=open`.
3. **Read the settlement rules for every series the user asks about** (see the next
   section) and summarize them back to the user before writing any config.
4. **Configure, don't code, when you can.** Express the idea with existing plugins in a
   portfolio file (`uv run nem portfolio new`, `uv run nem strategy add`, then edit the
   YAML). Write a new plugin only if no combination of existing ones can express it.
5. **Validate**: `uv run nem validate`. Fix every error; never weaken validation.
6. **Show the economics before trading**: the entry price range, the fee, and the
   break-even win rate (about price + fee, e.g. 91% at a 90c entry), and roughly how many
   trades it will take to detect a 2c edge (often 1,000+ near 90c).
7. **Backtest only to reject** (`uv run nem replay --data data/nem.db`) when recorded data
   exists. A backtest that looks good is not evidence; one that loses clearly is.
8. **Paper trade**: `uv run nem run` (or the systemd service on a server, see
   [deploy/RUNBOOK.md](deploy/RUNBOOK.md)). Read results with `uv run nem summary`.
9. **Report honestly**: quote the verdict and sample size from `nem summary`. "Undecided
   after 40 trades" is the expected answer for weeks; say so.

## Read the settlement rules (mandatory)

Kalshi decides every outcome, and the details vary by series. Titles can be wrong:
`KXHIGHTSDF` is titled "SATX" but settles on Louisville. For each series you configure:

```bash
curl -s "https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXHIGHNY&status=open&limit=1" \
  | python3 -c "import sys,json; m=json.load(sys.stdin)['markets'][0]; print(m['rules_primary']); print(m['rules_secondary']); print(m['strike_type'], m.get('floor_strike'), m.get('cap_strike'))"
curl -s "https://api.elections.kalshi.com/trade-api/v2/series/KXHIGHNY" \
  | python3 -c "import sys,json; s=json.load(sys.stdin)['series']; print(s['settlement_sources'], s['contract_terms_url'])"
```

Report to the user, per series:

- **Settlement source and exact measurement**: e.g. "minimum temperature at Central Park
  (CLINYC) per The Weather Company", or "60-second average of the CF Benchmarks BRTI
  before close vs before open".
- **Timing**: when markets open and close, the time zone, and how long results take.
- **What YES means**: the strike type (`greater`, `greater_or_equal`, `less`, `between`)
  and strike values.
- **Mismatches with any feed you use**: a feed is a predictor, not the settlement source
  (NWS forecasts vs Weather Company settlement; Coinbase spot vs CF Benchmarks index).
- **Anything unusual**: early close, revisions, "last fair price" fallbacks, custom strikes
  (the `feed_agrees` gate can't evaluate custom strikes).

If the rules can't be fetched or are ambiguous, stop and ask; don't guess.

## Hard rules

- **Paper only.** Never set `mode: live`, `env: prod`, or pass `--live` unless the user
  explicitly asks for live trading in this conversation. Live trading isn't implemented
  yet anyway.
- **No secrets in files.** API keys, sheet IDs, service-account paths and server details
  come from environment variables only (`deploy/nem.env.example`). Never commit them;
  gitleaks runs on every commit and in CI.
- **No look-ahead.** Signals and gates get data only through `Context` (`ctx`), which shows
  only results settled and feed values known *before* now. Never read the same window's
  outcome, a later forecast, or the network directly from a signal or gate.
- **Don't build a gate from the outcomes of the trades it gates.** Once it blocks, no new
  trades arrive to unblock it, and it freezes forever. Use ungated data instead.
- **Priors are the hypothesis.** `extreme_favorite` priors are the user's starting belief;
  keep them modest (as if from ~100 trades) and never tune them to make results look good.
  `max_entry_price` must stay independent of the model.
- **Risk limits:** `null` means disabled; `0` is rejected on purpose. Risk checks fail
  closed. Choose each gate's `fail_open` deliberately and tell the user what you chose.
- **Kelly sizing** always needs `max_contracts`.
- **Don't overfit.** If you suggest settings from recorded data, use few parameters, check
  them on a later period they weren't tuned on, prefer the middle of a stable region over
  the single best point, and state the sample size.
- **Respect data sources.** Keep `check_every` and feed `every` reasonable (seconds for
  15-minute crypto, minutes for daily weather; NWS `every: 30m`). Put a real contact in
  `nws_forecast`'s `user_agent`.
- **Never edit tests to make them pass**, never weaken type checking, and only commit when
  the user asks.

## Commands

```bash
make setup                    # install deps + pre-commit hooks
make check                    # ruff, pyright (strict), pytest: must pass before you finish
make demo                     # replay bundled sample data

uv run nem init               # first portfolio (interactive)
uv run nem portfolio new NAME --balance 1000
uv run nem strategy add PORTFOLIO NAME --series KXBTC15M   # appends a template to edit
uv run nem validate           # every portfolio file and plugin
uv run nem run --once         # one live paper tick (smoke test)
uv run nem run                # paper-trade continuously (records data too)
uv run nem summary            # statistics per strategy
uv run nem status             # process health, halts
uv run nem halt SCOPE --reason "..." / uv run nem resume SCOPE
uv run nem help [COMMAND]     # every command and option, generated from the CLI itself
```

## Where things are

| Path | What |
|---|---|
| `src/nem/core/` | types, config models, plugin registry, `Context` (the only door to data) |
| `src/nem/signals/`, `gates/`, `sizing/`, `feeds/`, `reporting/` | plugins; `base.py` in each defines the interface |
| `src/nem/engine/` | runner, one decision step, risk limits, settlement + interest |
| `src/nem/execution/` | Kalshi fees, paper broker (fills walk the recorded orderbook) |
| `src/nem/market/` | Kalshi REST client, live/replay sources, recorder |
| `src/nem/stats/` | win rate vs break-even, sequential test, calibration, reports |
| `src/nem/builtins.py` | list of built-in plugin modules (add new ones here) |
| `examples/` | demo portfolio + sample data, feed examples |
| `docs/kalshi-tickers.txt` | common series tickers, settlement sources, station coordinates |
| `deploy/` | systemd units, env template, runbook |

## Writing a plugin (only when config can't express the idea)

One file, one class, one decorator. Parameters are a pydantic model, so YAML is validated.

```python
from pydantic import Field

from nem.core.context import Context
from nem.core.registry import register
from nem.core.types import Decision, MarketSnapshot, Signal
from nem.gates.base import Gate, GateParams


@register("gate", "min_seconds_open")
class MinSecondsOpen(Gate):
    """Skip markets that opened less than `seconds` ago."""

    class Params(GateParams):
        seconds: float = Field(gt=0)

    def __init__(self, params: Params) -> None:
        super().__init__(params)
        self.seconds = params.seconds

    def check(self, signal: Signal, snap: MarketSnapshot, ctx: Context) -> Decision:
        if (ctx.now() - snap.open_time).total_seconds() < self.seconds:
            return Decision.skip(self.name)
        return Decision.ok()
```

Then add the module to `BUILTIN_MODULES` in `src/nem/builtins.py` and write tests in
`tests/unit/` (offline: mock HTTP like `tests/kalshi_mock.py` and `test_feeds.py` do).

- **Signals** return a `Signal` via `ctx.make_signal(...)` or `None`. Read past results
  with `ctx.stat(...)` / `ctx.settled_trades()`, and feeds with `ctx.feed(name)`.
- **Gates** that need data raise `DataUnavailable` when it's missing or stale; the gate's
  `fail_open` decides. Declare feeds in `feeds_used()` so startup can check them.
- **Sizers** return `Sizing(qty, reason)`. The reason is logged with every trade.
- **Feeds** return `(key, value, known_at)` where `known_at` is when the value became
  knowable (publish or issue time), never later than `now`.

Code style: Python 3.12, ruff (100 columns), pyright strict, comments only where they
explain why.
