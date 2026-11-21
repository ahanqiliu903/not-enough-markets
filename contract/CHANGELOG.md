# Contract changelog

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
