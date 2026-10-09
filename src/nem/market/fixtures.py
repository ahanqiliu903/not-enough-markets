"""Portable recorded data: snapshots and settlements as gzipped JSON lines.

Used to ship sample data with the repo (`make demo`, tests) without committing a binary
database.
"""

import gzip
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from nem.core.types import MarketSnapshot, Settlement
from nem.store import Store


def _snapshot_row(s: MarketSnapshot) -> dict[str, Any]:
    return {
        "type": "snapshot",
        "ts": s.ts.isoformat(),
        "series": s.series,
        "window_id": s.window_id,
        "ticker": s.ticker,
        "open_time": s.open_time.isoformat(),
        "close_time": s.close_time.isoformat(),
        "yes_bid": s.yes_bid,
        "yes_ask": s.yes_ask,
        "no_bid": s.no_bid,
        "no_ask": s.no_ask,
        "depth": {side: [list(lv) for lv in levels] for side, levels in s.depth.items()},
    }


def export_fixture(store: Store, path: Path) -> int:
    """Write every snapshot and known settlement. Returns the number of rows."""
    rows = [_snapshot_row(s) for s in store.iter_snapshots()]
    for ticker in sorted({r["ticker"] for r in rows}):
        settled = store.settlement(ticker)
        if settled is not None:
            rows.append(
                {
                    "type": "settlement",
                    "ticker": settled.ticker,
                    "series": settled.series,
                    "window_id": settled.window_id,
                    "result": settled.result,
                    "settled_at": settled.settled_at.isoformat(),
                }
            )
    with gzip.open(path, "wt") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    return len(rows)


def import_fixture(path: Path, store: Store) -> int:
    snaps: list[MarketSnapshot] = []
    n = 0
    with gzip.open(path, "rt") as f:
        for line in f:
            row: dict[str, Any] = json.loads(line)
            n += 1
            if row["type"] == "settlement":
                store.record_settlement(
                    Settlement(
                        row["ticker"],
                        row["series"],
                        row["window_id"],
                        row["result"],
                        datetime.fromisoformat(row["settled_at"]),
                    )
                )
                continue
            snaps.append(
                MarketSnapshot(
                    ts=datetime.fromisoformat(row["ts"]),
                    series=row["series"],
                    window_id=row["window_id"],
                    ticker=row["ticker"],
                    open_time=datetime.fromisoformat(row["open_time"]),
                    close_time=datetime.fromisoformat(row["close_time"]),
                    yes_bid=row["yes_bid"],
                    yes_ask=row["yes_ask"],
                    no_bid=row["no_bid"],
                    no_ask=row["no_ask"],
                    depth={
                        side: [(float(p), float(q)) for p, q in levels]
                        for side, levels in row["depth"].items()
                    },
                )
            )
    store.insert_snapshots(snaps)
    return n
