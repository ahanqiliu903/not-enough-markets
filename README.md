# NotEnoughMarkets (NEM)

A template for Kalshi algorithmic trading.

<sub>Name inspired by Moulberry's [NotEnoughUpdates](https://github.com/Moulberry/NotEnoughUpdates) for Hypixel Skyblock.</sub>

> [!WARNING]
> **This is NOT a trading strategy, and it will NOT guarantee you any profits.**
> It is meant to be an all-in-one home for automated prediction market trading.

## What is this for?

Say you have a hypothesis:

> *Is Kalshi's 15-min BTC Up/Down market overpricing contracts (>90c)?*

How do you test it? Given the constraints of the strategy, a code editor/agent *(planned)* takes in inputs about the strategy you want to test (which market, price range, how often, etc.) and turns them into a testable paper trading system, with automated market and portfolio data ingestion and analysis.

- **No new infrastructure every time.** Reuse the same engine for every idea.
- **Common parameters** (stricter entry, timing, sizing) so you can tweak your strategy as you go.
- **Easy comparison** of your current strategies against previous ones.

## Planned workflow

1. Write the strategy hypothesis in plain English.
2. Fit its constraints into a YAML config.
3. Set up the required data pipelines.
   - If data needs to be collected before backtesting, set up logging and schedules to do so.
4. Run a backtest (**not** a validation).
5. Run paper trading.
6. *(Optional)* Deploy live.

### Hot take: I hate backtests

Especially nowadays, it's very easy for them to give optimistic results. The workflow includes a backtest only to reject strategies that show clearly negative results. Paper trading is a better indicator.

## Requirements

| Requirement | Needed for |
|---|---|
| Virtual Private Server (VPS) | 24/7 data collection and paper/live trading. I personally use DigitalOcean. |
| Google Cloud service account | *Optional but recommended.* Reporting CSV data to Google Sheets. |
| Kalshi account | Live trading only. |
| Patience | Everything. |
