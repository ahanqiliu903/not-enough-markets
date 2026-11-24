"""Liquidity is only what the recorded book shows (audit R2). Synthetic markets only."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from nem.core.clock import ManualClock
from nem.core.config import PortfolioConfig
from nem.core.types import MarketSnapshot
from nem.engine.runner import Runner
from nem.history.backtest import quote_fills
from nem.market.source import FakeSource
from nem.stats.provenance import QUOTE_FILLS_LABEL
from nem.stats.report import build_report, render_text
from nem.store import Store

from engine_helpers import portfolio, settle, strategy, ticks, window
from factories import T0, portfolio_dict

MINUTE = timedelta(minutes=1)


def runner(
    portfolios: list[PortfolioConfig], *windows: list[MarketSnapshot]
) -> tuple[Runner, Store]:
    store = Store()
    source = FakeSource(ticks(*windows), [settle(w, "yes") for w in windows])
    return Runner(portfolios, source, store, ManualClock(windows[0][0].ts)), store


# --- R2: no recorded depth, no liquidity -------------------------------------------------


def top_of_book_only(w: list[MarketSnapshot]) -> list[MarketSnapshot]:
    return [replace(s, depth={}) for s in w]


def quote(**kw: Any) -> PortfolioConfig:
    return portfolio(fills={"missing_depth": "quote"}, **kw)


def test_top_of_book_only_never_enters_by_default() -> None:
    w = top_of_book_only(window(T0, every=MINUTE))
    r, store = runner([portfolio()], w)
    r.run(ticks(w))
    assert store.open_trades() == []
    assert store.signal_reasons("research", "fav90") == {"no_depth_recorded": 1}


def test_quote_mode_enters_and_is_labelled() -> None:
    w = top_of_book_only(window(T0, every=MINUTE))
    p = quote()
    r, store = runner([p], w)
    r.run(ticks(w)[:1])
    (t,) = store.open_trades()
    assert (t.qty, t.avg_price) == (5, 0.90)
    text = render_text(build_report(store, p, w[1].ts))
    assert f"[{QUOTE_FILLS_LABEL}]" in text
    assert QUOTE_FILLS_LABEL not in render_text(build_report(store, portfolio(), w[1].ts))


@pytest.mark.parametrize(
    "exit",
    [
        {"stop": {"bid_at_or_below": 0.60}},
        {"take_profit": {"price": 0.95, "fill_model": "realistic", "maker_fee_rate": 0}},
    ],
)
def test_exits_need_recorded_bids(exit: dict[str, Any]) -> None:
    w = window(T0, every=MINUTE)
    bid = 0.60 if "stop" in exit else 0.95
    for i in range(1, len(w)):  # after the entry: a bid at the trigger, no depth recorded
        w[i] = replace(w[i], yes_bid=bid, depth={})
    for p, exits in ((portfolio(strategies=[strategy(exit=exit)]), False),
                     (quote(strategies=[strategy(exit=exit)]), True)):  # fmt: skip
        r, store = runner([p], w)
        r.run(ticks(w)[:2])
        assert (store.open_trades() == []) is exits


def test_stop_does_not_fill_from_a_verified_empty_bid_side() -> None:
    w = window(T0, every=MINUTE)
    w[1] = replace(w[1], yes_bid=0.60, depth={"yes": [], "no": [(0.39, 10.0)]})
    r, store = runner([quote(strategies=[strategy(exit={"stop": {"bid_at_or_below": 0.6}})])], w)
    r.run(ticks(w)[:2])
    assert len(store.open_trades()) == 1


def test_fills_config_validates() -> None:
    assert portfolio().fills.missing_depth == "none"
    assert quote().fills.missing_depth == "quote"
    for bad in ({"missing_depth": "top_of_book"}, {"missing_depth": "quote", "extra": 1}):
        with pytest.raises(ValidationError):
            PortfolioConfig.model_validate(portfolio_dict(fills=bad))


def test_history_replay_sets_quote_fills_explicitly() -> None:
    p = portfolio()
    q = quote_fills(p)
    assert (p.fills.missing_depth, q.fills.missing_depth) == ("none", "quote")
    assert quote_fills(q) is q
