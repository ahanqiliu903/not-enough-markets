"""Records live snapshots and settlements to the store, for replay and backtests.

Recording takes calendar time, so start it early (ideally 24/7 on a VPS) and build on top
while data accumulates. Network errors are logged and retried next poll; they never stop
the recorder.
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx

from nem.core.clock import Clock, SystemClock
from nem.market.kalshi import KalshiError
from nem.market.source import KalshiSource
from nem.store import Store

log = logging.getLogger(__name__)

PROCESS = "recorder"


@dataclass(frozen=True, slots=True)
class StepResult:
    snapshots: int
    settled: int


class Recorder:
    def __init__(
        self,
        source: KalshiSource,
        store: Store,
        series: Sequence[str],
        clock: Clock | None = None,
        *,
        max_settlement_checks: int = 20,
    ) -> None:
        self._source = source
        self._store = store
        self._series = list(series)
        self._clock = clock or SystemClock()
        self._max_settlement_checks = max_settlement_checks

    def step(self) -> StepResult:
        tick = self._source.poll(self._series)
        self._store.insert_snapshots(tick.snapshots)
        settled = 0
        now = self._clock.now()
        for ticker in self._store.unsettled_tickers(now)[: self._max_settlement_checks]:
            settlement = self._source.result(ticker, now)
            if settlement is not None:
                self._store.record_settlement(settlement)
                settled += 1
        self._store.heartbeat(now, PROCESS, "ok")
        return StepResult(len(tick.snapshots), settled)

    def run(
        self,
        interval: float,
        iterations: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Poll every `interval` seconds, forever unless `iterations` is given."""
        n = 0
        while iterations is None or n < iterations:
            started = time.monotonic()
            try:
                r = self.step()
                log.info("recorded %d snapshots, %d settlements", r.snapshots, r.settled)
            except (KalshiError, httpx.HTTPError) as e:
                log.warning("poll failed: %s", e)
                self._store.heartbeat(self._clock.now(), PROCESS, f"error: {e}"[:200])
            n += 1
            if iterations is None or n < iterations:
                sleep(max(0.0, interval - (time.monotonic() - started)))
