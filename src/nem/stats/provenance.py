"""Provenance labels and the verdict cap for statistics driven by backfilled data.

Backfilled forecasts are approximations fetched after the fact, so a replay
that uses them can reject an idea but not confirm one. Wherever such a replay's verdict
is reported, pass it through `cap_verdict`: "edge" becomes "undecided" unless a verdict
from forward-recorded data (feeds recorded live by `nem run`) also says "edge".
"no_edge" stays: a backtest that loses clearly is evidence (AGENTS.md: backtest only to
reject). Label the output with `label`.
"""

from nem.core.config import PortfolioConfig, StrategyConfig

BACKFILL_LABEL = "backfilled forecasts"
# `fills.missing_depth: quote`: a book side without recorded depth was assumed to cover the
# whole order at its quote. Size is invented, so such results are never depth-backed.
QUOTE_FILLS_LABEL = "quote fills: no depth, full size assumed at top of book"
VERDICT_ORDER = ("no_edge", "undecided", "edge")  # SPRT decisions, worst to best


def cap_verdict(verdict: str, *, uses_backfill: bool, forward_verdict: str | None = None) -> str:
    """The verdict to report. Without backfill it is returned unchanged."""
    if verdict not in VERDICT_ORDER:
        raise ValueError(f"unknown verdict {verdict!r}")
    if not uses_backfill or verdict != "edge":
        return verdict
    return "edge" if forward_verdict == "edge" else "undecided"


def label(text: str, *, uses_backfill: bool) -> str:
    """Prefix a report line or title when it rests on backfilled data."""
    return f"[{BACKFILL_LABEL}] {text}" if uses_backfill else text


def uses_backfill(strategy: StrategyConfig) -> bool:
    """Whether any of the strategy's gates reads backfilled forecasts (the forecast gates'
    `backfill_sources`, nem.gates.forecast). Its verdicts then go through `cap_verdict`."""
    return any(g.params.get("backfill_sources") for g in strategy.gates)


def uses_quote_fills(portfolio: PortfolioConfig) -> bool:
    """Whether the portfolio fills orders at the quote when depth wasn't recorded
    (`fills.missing_depth: quote`). Its results carry `QUOTE_FILLS_LABEL`."""
    return portfolio.fills.missing_depth == "quote"
