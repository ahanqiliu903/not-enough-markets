import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nem.core.clock import ManualClock
from nem.engine.runner import Runner
from nem.engine.runtime import BuildError
from nem.market.source import FakeSource
from nem.ops import process_health, render_status
from nem.store import Store
from nem.store.sqlite import SCHEMA_VERSION

from engine_helpers import portfolio, settle, strategy, ticks, window
from factories import make_snapshot, make_trade

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
OPEN = datetime(2026, 10, 7, 19, 0, tzinfo=UTC)


# --- store: migration, halts, heartbeats ------------------------------------------------


def test_v1_database_is_migrated(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    with Store(db) as s:
        s.heartbeat(NOW, "runner", "ok")
    conn = sqlite3.connect(db)  # turn it back into a v1 database
    conn.executescript(
        "DROP TABLE halts; DROP INDEX heartbeats_process_ts;"  # v2
        " ALTER TABLE snapshots DROP COLUMN strike_type;"  # v3
        " ALTER TABLE snapshots DROP COLUMN floor_strike;"
        " ALTER TABLE snapshots DROP COLUMN cap_strike;"
        " PRAGMA user_version = 1;"
    )
    conn.close()
    with Store(db) as s:
        assert s.last_heartbeat("runner") == NOW  # data kept
        s.halt("*", "after migration", NOW)
        assert s.halted("p", "s") == "*"
        s.insert_snapshot(make_snapshot(floor_strike=81780.54, strike_type="greater_or_equal"))
        assert next(s.iter_snapshots()).floor_strike == 81780.54
    assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_halt_scopes() -> None:
    s = Store()
    assert s.halted("research", "fav90") is None
    s.halt("research/fav90", "one strategy", NOW)
    assert s.halted("research", "fav90") == "research/fav90"
    assert s.halted("research", "other") is None
    s.halt("research", "portfolio", NOW)
    assert s.halted("research", "other") == "research"
    assert s.halted("research", "fav90") == "research"  # most general scope reported
    s.halt("*", "everything", NOW)
    assert s.halted("elsewhere", "x") == "*"
    s.halt("*", "updated reason", NOW + timedelta(minutes=1))
    assert [(scope, reason) for scope, reason, _ in s.halts()] == [
        ("*", "updated reason"),
        ("research", "portfolio"),
        ("research/fav90", "one strategy"),
    ]
    assert s.resume("*") and s.resume("research")
    assert not s.resume("research")
    assert s.halted("research", "other") is None


def test_heartbeats_are_pruned_and_reported() -> None:
    s = Store()
    s.heartbeat(NOW - timedelta(days=3), "runner", "ok")
    s.heartbeat(NOW, "runner", "error: down")
    assert s.heartbeat_status("runner") == (NOW, "error: down")
    count = s._conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0]  # pyright: ignore[reportPrivateUsage]
    assert count == 1  # the 3-day-old row is gone
    assert s.processes() == ["runner"]
    assert s.heartbeat_status("nope") is None


# --- runner: halts and reload -----------------------------------------------------------


def test_halted_strategy_skips_entries_but_settles() -> None:
    w1, w2 = window(OPEN), window(OPEN + timedelta(minutes=15))
    store, clock = Store(), ManualClock(OPEN)
    store.open_trade(make_trade(w1[0].window_id, ticker=w1[0].ticker, close_time=w1[0].close_time))
    store.halt("research/fav90", "test", OPEN)
    runner = Runner([portfolio()], FakeSource([], [settle(w1, "yes")]), store, clock)
    runner.run(ticks(w1, w2))
    assert len(store.settled_trades()) == 1
    assert store.open_trades() == []  # nothing new while halted
    store.resume("research/fav90")
    w3 = window(OPEN + timedelta(minutes=30))
    runner.run(ticks(w3))
    assert len(store.open_trades()) == 1


def test_reload_keeps_state_and_rejects_bad_configs() -> None:
    w = window(OPEN)
    store, clock = Store(), ManualClock(OPEN)
    runner = Runner([portfolio()], FakeSource([]), store, clock)
    runner.run(ticks(w[:2]))
    before = runner.strategies[0].last_check
    assert before is not None

    runner.reload([portfolio(strategies=[strategy(), strategy(name="new", series="KXETH15M")])])
    assert [rt.config.name for rt in runner.strategies] == ["fav90", "new"]
    assert runner.strategies[0].last_check == before  # carried over
    assert runner.series == ["KXBTC15M", "KXETH15M"]

    bad = strategy(sizing={"type": "fixed", "contracts": 0})
    with pytest.raises(BuildError):
        runner.reload([portfolio(strategies=[bad])])
    assert [rt.config.name for rt in runner.strategies] == ["fav90", "new"]  # unchanged

    runner.reload([portfolio(strategies=[strategy(status="paused")])])
    assert runner.series == []


# --- status --------------------------------------------------------------------------------


def test_process_health_states() -> None:
    s = Store()
    s.heartbeat(NOW - timedelta(seconds=10), "runner", "ok")
    s.heartbeat(NOW - timedelta(seconds=10), "recorder", "error: HTTP 503")
    s.heartbeat(NOW - timedelta(minutes=10), "reporter", "ok")
    health = {h.name: h for h in process_health(
        s, ["runner", "recorder", "reporter", "ghost"], NOW, timedelta(minutes=2)
    )}  # fmt: skip
    assert {k: h.state for k, h in health.items()} == {
        "runner": "ok",
        "recorder": "failing",
        "reporter": "stale",
        "ghost": "never",
    }
    assert health["recorder"].healthy and not health["reporter"].healthy


def test_render_status() -> None:
    s = Store()
    s.heartbeat(NOW - timedelta(seconds=5), "runner", "ok")
    s.heartbeat(NOW - timedelta(hours=3), "recorder", "ok")
    s.halt("research/fav90", "checking fills", NOW - timedelta(minutes=30))
    s.open_trade(make_trade())
    p = portfolio(strategies=[strategy(), strategy(name="old", status="retired")])
    text, healthy = render_status(s, [p], NOW, ["runner"], timedelta(minutes=2))
    assert healthy  # the stale recorder isn't expected
    assert "runner    ok       last heartbeat 5s ago" in text
    assert "recorder  stale    last heartbeat 3h ago  (not expected)" in text
    assert "research/fav90  since 30m ago: checking fills" in text
    assert "fav90           halted (research/fav90)" in text
    assert "old             retired" in text
    assert "open positions 1" in text
    _, healthy = render_status(s, [p], NOW, ["runner", "recorder"], timedelta(minutes=2))
    assert not healthy
