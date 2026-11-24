"""Entries never act on a snapshot from the future or older than `max_snapshot_age`.
Synthetic data only."""

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from nem.core.clock import ManualClock
from nem.core.config import PortfolioConfig
from nem.core.types import MarketSnapshot, Trade
from nem.engine.runner import Runner
from nem.engine.step import evaluate
from nem.market.source import FakeSource, Tick
from nem.store import Store

from engine_helpers import portfolio, window
from factories import portfolio_dict

OPEN = datetime(2026, 10, 7, 14, 0, tzinfo=UTC)
KEY = ("research", "fav90")
SNAP = window(OPEN)[0]


def enter_at(now: datetime, snap: MarketSnapshot = SNAP, **pkw: Any) -> tuple[Trade | None, Store]:
    store = Store()
    runner = Runner([portfolio(**pkw)], FakeSource([]), store, ManualClock(now), fetch_feeds=False)
    rt = runner.strategies[0]
    return evaluate(rt, snap, runner.context(rt), store), store


def test_default_max_age_is_two_minutes() -> None:
    assert portfolio().max_snapshot_age == timedelta(minutes=2)
    p = PortfolioConfig.model_validate(portfolio_dict(max_snapshot_age="30s"))
    assert p.max_snapshot_age == timedelta(seconds=30)


@pytest.mark.parametrize("age", [0, -5, "0s"])
def test_max_age_must_be_positive(age: object) -> None:
    with pytest.raises(ValidationError, match="positive"):
        PortfolioConfig.model_validate(portfolio_dict(max_snapshot_age=age))


def test_fresh_snapshot_trades() -> None:
    trade, _ = enter_at(SNAP.ts)
    assert trade is not None


def test_future_snapshot_never_trades(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        trade, store = enter_at(SNAP.ts - timedelta(seconds=1))
    assert trade is None
    assert store.order_attempts(KEY, SNAP.window_id, "yes") == 0
    assert "snapshot:future" in caplog.text


@pytest.mark.parametrize(
    ("age", "trades"),
    [
        (timedelta(minutes=2), True),  # exactly max age is still fresh
        (timedelta(minutes=2, microseconds=1), False),
        (timedelta(minutes=5), False),
    ],
)
def test_stale_boundary(age: timedelta, trades: bool, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        trade, store = enter_at(SNAP.ts + age)
    assert (trade is not None) is trades
    if not trades:
        assert store.order_attempts(KEY, SNAP.window_id, "yes") == 0
        assert "snapshot:stale" in caplog.text


def test_max_age_is_configurable() -> None:
    trade, _ = enter_at(SNAP.ts + timedelta(seconds=31), max_snapshot_age="30s")
    assert trade is None
    trade, _ = enter_at(SNAP.ts + timedelta(seconds=30), max_snapshot_age="30s")
    assert trade is not None


def test_stale_warning_logged_once_per_window(caplog: pytest.LogCaptureFixture) -> None:
    store = Store()
    clock = ManualClock(SNAP.ts + timedelta(minutes=3))
    runner = Runner([portfolio()], FakeSource([]), store, clock, fetch_feeds=False)
    rt = runner.strategies[0]
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert evaluate(rt, SNAP, runner.context(rt), store) is None
    assert caplog.text.count("snapshot:stale") == 1


def test_runner_skips_old_snapshots_in_a_tick() -> None:
    # The clock is 5 minutes past the snapshots it is handed: no entry
    now = OPEN + timedelta(minutes=5)
    store, clock = Store(), ManualClock(now)
    runner = Runner([portfolio()], FakeSource([]), store, clock, fetch_feeds=False)
    assert runner.on_tick(Tick(now, [SNAP])) == []
    # The same quote restamped at the tick time (a replay's carried-forward quote) trades
    later = now + timedelta(seconds=5)
    clock.set(later)
    assert len(runner.on_tick(Tick(later, [replace(SNAP, ts=later)]))) == 1
