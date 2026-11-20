"""A `nem suggest` run as JSON (contract/schema/backtest_run.schema.json), for NEM Desk.

Test-period metrics are included for the chosen variant only: showing every variant's
test result would invite picking the best one on test, which is exactly the overfitting
the held-out period exists to catch.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from nem.backtest.suggest import Suggestion
from nem.backtest.types import Grid, Period, VariantResult
from nem.stats.report import SCHEMA_VERSION, iso, stats_fields


def _period(p: Period) -> dict[str, str | None]:
    return {"start": iso(p.start), "end": iso(p.end)}


def run_id(created_at: datetime, portfolio: str, strategy: str) -> str:
    return f"{created_at:%Y%m%dT%H%M%SZ}-{portfolio}-{strategy}"


def run_data(
    s: Suggestion,
    grid: Grid,
    *,
    portfolio: str,
    strategy: str,
    series: str,
    train: Period,
    test: Period,
    train_results: Sequence[VariantResult],
    min_trades: int,
    created_at: datetime,
) -> dict[str, object]:
    chosen = s.variant.name if s.variant is not None else None
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id(created_at, portfolio, strategy),
        "created_at": iso(created_at),
        "portfolio": portfolio,
        "strategy": strategy,
        "series": series,
        "status": s.status,
        "min_trades": min_trades,
        "grid": [{"path": path, "values": values} for path, values in grid.items()],
        "train_window": _period(train),
        "test_window": _period(test),
        "chosen": chosen,
        "score_cents": None if s.score is None else round(100 * s.score, 2),
        "reasons": s.reasons,
        "variants": [
            {
                "name": r.variant.name,
                "overrides": r.variant.overrides,
                "index": list(r.variant.index),
                "train": stats_fields(r.stats),
                "test": stats_fields(s.test.stats)
                if s.test is not None and r.variant.name == chosen
                else None,
            }
            for r in train_results
        ],
        # Fill-quality range isn't modelled yet: fills always walk the recorded book.
        "fill_estimate": None,
    }


def save_run(data: dict[str, object], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{data['run_id']}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)
    return path
