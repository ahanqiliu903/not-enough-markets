import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from nem.core.types import Fill, Order
from nem.store import Store, StoreError
from nem.store.sqlite import SCHEMA_VERSION

from factories import T0, WINDOW, make_signal, make_snapshot, make_trade

KEY = ("research", "fav90")


@pytest.fixture
def store() -> Store:
    return Store()


def test_schema_created_once(tmp_path: Path) -> None:
    db = tmp_path / "nem.db"
    with Store(db) as s:
        s.bump_stat(*KEY, "yes", True)
    with Store(db) as s:  # reopening must not recreate tables
        assert s.stat(*KEY, "yes") == (1, 1)
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_newer_schema_refused(tmp_path: Path) -> None:
    db = tmp_path / "nem.db"
    conn = sqlite3.connect(db)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(StoreError, match="newer"):
        Store(db)


def test_snapshot_round_trip(store: Store) -> None:
    snap = make_snapshot(depth={"yes": [(0.88, 10), (0.87, 4)], "no": []}, no_bid=None)
    store.insert_snapshot(snap)
    assert list(store.iter_snapshots()) == [snap]


def test_iter_snapshots_filters_and_orders(store: Store) -> None:
    times = [T0 + timedelta(seconds=s) for s in (10, 0, 5)]
    for t in times:
        store.insert_snapshot(make_snapshot(ts=t))
    store.insert_snapshot(make_snapshot(ts=T0, series="KXETH15M"))
    btc = list(store.iter_snapshots(series=["KXBTC15M"]))
    assert [s.ts for s in btc] == sorted(times)
    window = store.iter_snapshots(start=T0 + timedelta(seconds=5), end=T0 + timedelta(seconds=10))
    assert [s.ts for s in window] == [T0 + timedelta(seconds=5)]


def test_signal_reasons(store: Store) -> None:
    sig = make_signal(meta={"edge_src": "prior"})
    store.log_signal(sig, T0, "take", "ok", edge=0.02)
    store.log_signal(sig, T0, "skip", "canary")
    store.log_signal(sig, T0, "skip", "canary")
    store.log_signal(make_signal(strategy="other"), T0, "skip", "canary")
    assert store.signal_reasons(*KEY) == {"ok": 1, "canary": 2}


def test_orders_are_idempotent_by_client_order_id(store: Store) -> None:
    order = Order.from_signal(make_signal(), qty=5)
    store.record_order(order, T0, "paper", "submitted")
    with pytest.raises(StoreError, match="duplicate"):
        store.record_order(order, T0, "paper", "submitted")
    store.set_order_status(order.client_order_id, "filled")
    assert store.order_status(order.client_order_id) == "filled"
    assert store.order_status("nope") is None
    with pytest.raises(StoreError):
        store.set_order_status("nope", "filled")


def test_fill_requires_known_order(store: Store) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        store.record_fill(Fill("unknown", T0, 0.9, 5, 0.02))
    order = Order.from_signal(make_signal(), qty=5)
    store.record_order(order, T0, "paper", "submitted")
    store.record_fill(Fill(order.client_order_id, T0, 0.9, 5, 0.02))


def test_trade_lifecycle(store: Store) -> None:
    store.open_trade(make_trade())
    with pytest.raises(StoreError, match="already open"):
        store.open_trade(make_trade())
    assert [t.window_id for t in store.open_trades()] == [WINDOW]
    assert store.settled_trades() == []

    settled_at = T0 + timedelta(minutes=15)
    store.settle_trade(KEY, WINDOW, True, 0.48, settled_at)
    with pytest.raises(StoreError, match="no open trade"):
        store.settle_trade(KEY, WINDOW, True, 0.48, settled_at)

    assert store.open_trades() == []
    [t] = store.settled_trades()
    assert (t.won, t.realized_pnl, t.settled_at) == (True, 0.48, settled_at)


def test_settled_trades_filters(store: Store) -> None:
    for i, strategy in enumerate(["fav90", "fav90", "fav85"]):
        w = f"KXBTC15M-26OCT07153{i}"
        store.open_trade(make_trade(w, strategy=strategy))
        store.settle_trade(("research", strategy), w, True, 0.5, T0 + timedelta(minutes=i))

    assert len(store.settled_trades()) == 3
    assert len(store.settled_trades(strategies=[KEY])) == 2
    assert store.settled_trades(strategies=[]) == []
    # `before` is strict
    assert len(store.settled_trades(before=T0 + timedelta(minutes=1))) == 1
    # `limit` keeps the most recent, returned oldest first
    recent = store.settled_trades(limit=2)
    assert [t.settled_at for t in recent] == [T0 + timedelta(minutes=1), T0 + timedelta(minutes=2)]


def test_open_trade_rejects_settled(store: Store) -> None:
    with pytest.raises(StoreError):
        store.open_trade(make_trade(settled_at=T0, won=True, realized_pnl=0.5))


def test_stats(store: Store) -> None:
    assert store.stat(*KEY, "yes") == (0, 0)
    for won in (True, True, False):
        store.bump_stat(*KEY, "yes", won)
    store.bump_stat("research", "fav85", "yes", False)
    assert store.stat(*KEY, "yes") == (2, 3)


def test_heartbeats(store: Store) -> None:
    assert store.last_heartbeat("runner") is None
    store.heartbeat(T0, "runner", "ok")
    store.heartbeat(T0 + timedelta(seconds=5), "runner", "ok", *KEY)
    assert store.last_heartbeat("runner") == T0 + timedelta(seconds=5)
