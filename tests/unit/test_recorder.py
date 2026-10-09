from datetime import UTC, datetime, timedelta

import httpx

from nem.core.clock import ManualClock
from nem.market.recorder import Recorder
from nem.market.source import KalshiSource
from nem.store import Store

from kalshi_mock import Handler, Recording, default_routes, mock_client

# The open-market fixture KXBTC15M-26OCT081430-30 closes at 18:30Z.
DURING = datetime(2026, 10, 8, 18, 20, tzinfo=UTC)
CLOSE = datetime(2026, 10, 8, 18, 30, tzinfo=UTC)


def make(handler: Handler = default_routes) -> tuple[Recorder, Store, ManualClock]:
    clock, store = ManualClock(DURING), Store()
    client = mock_client(handler)
    return Recorder(KalshiSource(client, clock), store, ["KXBTC15M"], clock), store, clock


def test_step_records_snapshots_and_heartbeat() -> None:
    recorder, store, _ = make()
    r = recorder.step()
    assert (r.snapshots, r.settled) == (1, 0)  # open market not closed yet: nothing to settle
    assert len(list(store.iter_snapshots())) == 1
    assert store.last_heartbeat("recorder") == DURING


def test_settles_closed_markets_once() -> None:
    rec = Recording()
    recorder, store, clock = make(rec)
    recorder.step()
    clock.set(CLOSE + timedelta(seconds=5))
    assert recorder.step().settled == 1
    settled = store.settlement("KXBTC15M-26OCT081430-30")
    assert settled is not None
    assert (settled.result, settled.window_id) == ("no", "KXBTC15M-26OCT081430")
    asked = [r.url.path for r in rec.requests if r.url.path.endswith("-30")]
    assert asked == ["/trade-api/v2/markets/KXBTC15M-26OCT081430-30"]
    assert store.unsettled_tickers(clock.now()) == []
    assert recorder.step().settled == 0  # no repeat lookups


def test_unsettled_result_is_retried_next_step() -> None:
    pending: dict[str, dict[str, str]] = {"market": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("-30"):
            body = default_routes(request).json()
            body["market"].update({"result": "", **pending["market"]})
            return httpx.Response(200, json=body)
        return default_routes(request)

    recorder, store, clock = make(handler)
    recorder.step()
    clock.set(CLOSE + timedelta(seconds=1))
    assert recorder.step().settled == 0
    assert store.unsettled_tickers(clock.now()) == ["KXBTC15M-26OCT081430-30"]
    pending["market"] = {"result": "yes"}
    assert recorder.step().settled == 1


def test_run_survives_network_errors() -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 4:  # first poll exhausts its retries
            raise httpx.ConnectError("down", request=request)
        return default_routes(request)

    recorder, store, _ = make(flaky)
    sleeps: list[float] = []
    recorder.run(interval=5, iterations=2, sleep=sleeps.append)
    assert len(list(store.iter_snapshots())) == 1  # second poll worked
    assert len(sleeps) == 1  # no sleep after the last iteration


def test_embedded_recorder_skips_heartbeat_and_returns_tick() -> None:
    clock, store = ManualClock(DURING), Store()
    rec = Recorder(KalshiSource(mock_client(), clock), store, ["KXBTC15M"], clock, process=None)
    result = rec.step()
    assert result.tick.ts == DURING
    assert result.snapshots == 1
    assert store.last_heartbeat("recorder") is None
