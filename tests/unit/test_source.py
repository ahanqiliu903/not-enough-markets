from datetime import UTC, datetime, timedelta
from pathlib import Path

from nem.core.clock import ManualClock
from nem.core.types import Settlement
from nem.market.kalshi import parse_market, parse_orderbook
from nem.market.source import FakeSource, KalshiSource, ReplaySource, Tick, to_snapshot
from nem.store import Store

from factories import T0, make_snapshot
from kalshi_mock import Recording, fixture, mock_client

NOW = datetime(2026, 10, 8, 18, 20, tzinfo=UTC)


def test_to_snapshot_prefers_orderbook() -> None:
    m = parse_market(fixture("markets_open")["markets"][0])
    book = parse_orderbook(fixture("orderbooks")["orderbooks"][0]["orderbook_fp"])
    snap = to_snapshot("KXBTC15M", m, NOW, book, depth=2)
    assert (snap.yes_bid, snap.no_bid) == (0.82, 0.17)
    assert (snap.yes_ask, snap.no_ask) == (0.83, 0.18)  # 1 - opposite best bid, rounded
    assert [len(v) for v in snap.depth.values()] == [2, 2]
    assert snap.window_id == "KXBTC15M-26OCT081430"


def test_to_snapshot_without_book_uses_listing() -> None:
    m = parse_market(fixture("markets_open")["markets"][0])
    snap = to_snapshot("KXBTC15M", m, NOW, None, depth=10)
    assert (snap.yes_bid, snap.yes_ask) == (0.73, 0.74)
    assert snap.depth == {}


def test_kalshi_source_poll_stamps_one_time() -> None:
    source = KalshiSource(mock_client(), ManualClock(NOW))
    tick = source.poll(["KXBTC15M"])
    assert tick.ts == NOW
    assert [s.ts for s in tick.snapshots] == [NOW]
    assert tick.snapshots[0].depth["yes"][0] == (0.82, 1784.35)


def test_kalshi_source_depth_zero_skips_orderbook() -> None:
    rec = Recording()
    KalshiSource(mock_client(rec), ManualClock(NOW), depth=0).poll(["KXBTC15M"])
    assert all("orderbooks" not in r.url.path for r in rec.requests)


def test_kalshi_source_ticks_sleeps_between_polls() -> None:
    sleeps: list[float] = []
    source = KalshiSource(mock_client(), ManualClock(NOW), interval=5, sleep=sleeps.append)
    ticks = source.ticks(["KXBTC15M"])
    next(ticks)
    next(ticks)
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 5


def test_kalshi_source_result() -> None:
    source = KalshiSource(mock_client(), ManualClock(NOW))
    s = source.result("KXBTC15M-26OCT081415-15", NOW)
    assert s is not None
    assert (s.result, s.series, s.window_id) == ("no", "KXBTC15M", "KXBTC15M-26OCT081415")


def test_replay_groups_by_poll_time_and_is_causal() -> None:
    store = Store()
    t1 = T0 + timedelta(seconds=5)
    store.insert_snapshots([make_snapshot(T0), make_snapshot(T0, series="KXETH15M")])
    store.insert_snapshots([make_snapshot(t1)])
    ticks = list(ReplaySource(store).ticks(["KXBTC15M", "KXETH15M"]))
    assert [(t.ts, len(t.snapshots)) for t in ticks] == [(T0, 2), (t1, 1)]

    snap = make_snapshot()
    store.record_settlement(
        Settlement(snap.ticker, snap.series, snap.window_id, "yes", snap.close_time)
    )
    replay = ReplaySource(store)
    assert replay.result(snap.ticker, snap.close_time - timedelta(seconds=1)) is None
    assert replay.result(snap.ticker, snap.close_time) is not None


def test_replay_respects_time_range() -> None:
    store = Store()
    for s in range(3):
        store.insert_snapshot(make_snapshot(T0 + timedelta(seconds=s)))
    replay = ReplaySource(store, start=T0 + timedelta(seconds=1), end=T0 + timedelta(seconds=2))
    assert [t.ts for t in replay.ticks(["KXBTC15M"])] == [T0 + timedelta(seconds=1)]


def test_fake_source_filters_series_and_hides_future_results() -> None:
    btc, eth = make_snapshot(), make_snapshot(series="KXETH15M")
    settled = Settlement(btc.ticker, btc.series, btc.window_id, "no", btc.close_time)
    source = FakeSource([Tick(T0, [btc, eth])], [settled])
    [tick] = list(source.ticks(["KXETH15M"]))
    assert tick.snapshots == [eth]
    assert list(source.ticks(["KXSOL15M"])) == []
    assert source.result(btc.ticker, T0) is None
    assert source.result(btc.ticker, btc.close_time) == settled


def test_fixture_round_trip(tmp_path: Path) -> None:
    from nem.market.fixtures import export_fixture, import_fixture

    src = Store()
    snap = make_snapshot(depth={"yes": [(0.88, 817.01)], "no": [(0.1, 3.0)]})
    src.insert_snapshots([snap, make_snapshot(T0 + timedelta(seconds=5), yes_bid=None)])
    src.record_settlement(Settlement(snap.ticker, snap.series, snap.window_id, "yes", T0))
    path = tmp_path / "f.jsonl.gz"
    assert export_fixture(src, path) == 3

    dst = Store()
    assert import_fixture(path, dst) == 3
    assert list(dst.iter_snapshots()) == list(src.iter_snapshots())
    assert dst.settlement(snap.ticker) == src.settlement(snap.ticker)


def test_example_portfolios_validate() -> None:
    from nem.builtins import load_builtins
    from nem.core.config import load_portfolios
    from nem.engine.runtime import build_strategies

    load_builtins()
    examples = Path(__file__).parents[2] / "examples" / "portfolios"
    assert len(build_strategies(load_portfolios(examples))) == 3
