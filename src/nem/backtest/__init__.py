"""Backtests and suggested settings (M7).

- `sweep`: replay recorded data through every combination of a small parameter grid, in
  one pass, and return each variant's statistics for a time period.
- `suggest`: pick starting settings from the earlier (train) period's results, check them
  on the later (test) period they weren't tuned on, and render the result.

Backtests here exist to reject ideas and to pick sane starting settings for paper trading,
never to prove an edge.
"""

from nem.backtest.types import Grid, ParamValue, Period, Variant, VariantResult

__all__ = ["Grid", "ParamValue", "Period", "Variant", "VariantResult"]
