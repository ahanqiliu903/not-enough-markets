"""Generate contract/fixtures/ deterministically: `uv run python contract/generate_fixtures.py`.

The `basic` fixture is real engine output: seeded synthetic markets are recorded into a
database, paper-traded by the same Runner as `nem run`, then reported by the same code as
`nem report`, `nem summary --json`, `nem status --json` and `nem suggest`. Re-running
writes identical files (tests/contract checks that the committed copy is current).

    basic/     nem.db, out/fixture/{summary,strategies,trades}.csv, summary.json,
               status.json, out/backtests/<run_id>.json
    empty/     the same files with no rows
    broken/    one folder per failure the app must report: missing_column,
               wrong_major_version, bad_timestamp, duplicate_rows
"""

import csv
import json
import logging
import random
import shutil
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nem.backtest.export import run_data, save_run
from nem.backtest.suggest import suggest
from nem.backtest.sweep import run_sweep, split
from nem.backtest.types import Grid
from nem.builtins import load_builtins
from nem.core.clock import ManualClock
from nem.core.config import PortfolioConfig
from nem.core.types import FeedValue, MarketSnapshot, Settlement
from nem.engine.runner import Runner
from nem.market.source import ReplaySource
from nem.ops import status_data
from nem.reporting.csv_reporter import CsvReporter
from nem.stats.report import SCHEMA_VERSION, build_report, report_data
from nem.store import Store

ROOT = Path(__file__).parent / "fixtures"
SEED = 20261120
START = datetime(2026, 10, 1, tzinfo=UTC)
WINDOWS = 170  # per series: about 500 trades across the three strategies
EVERY = timedelta(minutes=1)
# Series -> chance the favorite wins. Chosen so the three strategies end on the three
# verdicts: clearly above break-even, clearly below, and too close to call.
SERIES = {"KXBTC15M": 0.995, "KXETH15M": 0.78, "KXSOL15M": 0.925}
PORTFOLIO = {
    "name": "fixture",
    "starting_balance": 1000,
    "interest_apy": 0.04,
    "strategies": [
        {
            "name": name,
            "series": series,
            "signal": {
                "type": "extreme_favorite",
                "threshold": 0.88,
                "min_edge": 0.0,
                "priors": {"yes": {"wins": 93, "n": 100}, "no": {"wins": 93, "n": 100}},
            },
            "gates": [{"type": "max_entry_price", "value": 0.97}],
            "sizing": {"type": "fixed", "contracts": 5},
        }
        for name, series in [
            ("btc_edge", "KXBTC15M"),
            ("eth_no_edge", "KXETH15M"),
            ("sol_undecided", "KXSOL15M"),
        ]
    ],
}


def _window(
    rng: random.Random, series: str, open_time: datetime
) -> tuple[list[MarketSnapshot], str]:
    close = open_time + timedelta(minutes=15)
    window_id = f"{series}-{close:%y}{close.strftime('%b').upper()}{close:%d%H%M}"
    favorite = rng.choice(["yes", "no"])
    out: list[MarketSnapshot] = []
    t = open_time
    while t < close:
        # the favorite firms up through the window, like a 15-minute crypto market
        progress = (t - open_time) / (close - open_time)
        fav_ask = round(min(0.97, 0.80 + 0.17 * progress + rng.uniform(-0.02, 0.02)), 2)
        yes_ask = fav_ask if favorite == "yes" else round(1.01 - fav_ask, 2)
        no_ask = round(1.01 - yes_ask, 2)
        out.append(
            MarketSnapshot(
                ts=t,
                series=series,
                window_id=window_id,
                ticker=f"{window_id}-00",
                open_time=open_time,
                close_time=close,
                yes_bid=round(yes_ask - 0.01, 2),
                yes_ask=yes_ask,
                no_bid=round(no_ask - 0.01, 2),
                no_ask=no_ask,
                depth={
                    "yes": [(round(yes_ask - 0.01 * i, 2), 40.0 + 10 * i) for i in range(1, 4)],
                    "no": [(round(no_ask - 0.01 * i, 2), 40.0 + 10 * i) for i in range(1, 4)],
                },
            )
        )
        t += EVERY
    won = rng.random() < SERIES[series]
    result = favorite if won else ("no" if favorite == "yes" else "yes")
    return out, result


def _record(store: Store, rng: random.Random) -> datetime:
    for series in SERIES:
        for i in range(WINDOWS):
            snaps, result = _window(rng, series, START + timedelta(minutes=15 * i))
            store.insert_snapshots(snaps)
            s = snaps[0]
            store.record_settlement(
                Settlement(
                    s.ticker,
                    s.series,
                    s.window_id,
                    result,  # type: ignore[arg-type]
                    s.close_time + timedelta(seconds=3),
                )
            )
    end = START + timedelta(minutes=15 * WINDOWS)
    price, t = 65000.0, START
    values: list[FeedValue] = []
    while t < end:
        price = round(price * (1 + rng.gauss(0, 0.0008)), 2)
        values.append(FeedValue("btc_spot", "BTC-USD", price, t))
        t += EVERY
    store.record_feed_values(values, end)
    return end


def _clean_db(path: Path) -> None:
    """Checkpoint WAL into one plain file so the fixture is a single, portable .db."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("VACUUM")
    conn.close()


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def basic(root: Path) -> None:
    rng = random.Random(SEED)
    portfolio = PortfolioConfig.model_validate(PORTFOLIO)
    db = root / "nem.db"
    with Store(db) as store:
        end = _record(store, rng)
        clock = ManualClock(START)
        runner = Runner([portfolio], ReplaySource(store), store, clock, fetch_feeds=False)
        runner.run(ReplaySource(store).ticks(runner.series))
        runner.settle_remaining(end + timedelta(hours=1))
        as_of = end + timedelta(minutes=5)
        for minute in range(0, 5):
            store.heartbeat(end + timedelta(minutes=minute), "runner", "ok")
            store.heartbeat(end + timedelta(minutes=minute), "reporter", "ok")
        store.halt("fixture/eth_no_edge", "fixture: verdict no_edge", end)
        report = build_report(store, portfolio, as_of)
        CsvReporter(CsvReporter.Params(path=str(root / "out"))).publish(report)
        _write_json(
            root / "summary.json",
            {"schema_version": SCHEMA_VERSION, "portfolios": [report_data(report)]},
        )
        _write_json(
            root / "status.json",
            status_data(store, [portfolio], as_of, ["runner", "reporter"], timedelta(minutes=2)),
        )
        grid: Grid = {"signal.threshold": [0.86, 0.88, 0.90, 0.92], "gates.0.value": [0.95, 0.97]}
        train, test = split(store, ["KXSOL15M"], 0.3)
        results = run_sweep(store, portfolio, "sol_undecided", grid, [train, test])
        s = suggest(grid, results[train], results[test], min_trades=20)
        run = run_data(
            s, grid, portfolio="fixture", strategy="sol_undecided", series="KXSOL15M",
            train=train, test=test, train_results=results[train], min_trades=20,
            created_at=as_of,
        )  # fmt: skip
        save_run(run, root / "out" / "backtests")
    _clean_db(db)


def empty(root: Path) -> None:
    portfolio = PortfolioConfig.model_validate({**PORTFOLIO, "strategies": []})
    as_of = START
    db = root / "nem.db"
    with Store(db) as store:
        report = build_report(store, portfolio, as_of)
        CsvReporter(CsvReporter.Params(path=str(root / "out"))).publish(report)
        _write_json(
            root / "summary.json",
            {"schema_version": SCHEMA_VERSION, "portfolios": [report_data(report)]},
        )
        _write_json(root / "status.json", status_data(store, [portfolio], as_of, [], EVERY))
    _clean_db(db)


def _read(path: Path) -> list[list[str]]:
    with path.open(newline="") as f:
        return list(csv.reader(f))


def _write(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        csv.writer(f).writerows(rows)


def broken(root: Path, basic_root: Path) -> None:
    src = basic_root / "out" / "fixture"

    def case(name: str) -> Path:
        out = root / name / "out" / "fixture"
        shutil.copytree(src, out)
        return out

    out = case("missing_column")
    rows = _read(out / "strategies.csv")
    drop = rows[0].index("edge_cents")
    _write(out / "strategies.csv", [r[:drop] + r[drop + 1 :] for r in rows])

    out = case("wrong_major_version")
    rows = _read(out / "strategies.csv")
    _write(out / "strategies.csv", [rows[0], *[["2.0", *r[1:]] for r in rows[1:]]])

    out = case("bad_timestamp")
    rows = _read(out / "trades.csv")
    col = rows[0].index("opened_at")
    rows[1][col] = "2026-13-45 25:61"
    _write(out / "trades.csv", rows)

    out = case("duplicate_rows")
    rows = _read(out / "trades.csv")
    _write(out / "trades.csv", [*rows, rows[1], rows[2]])


def generate(root: Path = ROOT) -> None:
    logging.disable(logging.INFO)
    load_builtins()
    if root.exists():
        shutil.rmtree(root)
    for name in ("basic", "empty", "broken"):
        (root / name).mkdir(parents=True)
    basic(root / "basic")
    empty(root / "empty")
    broken(root / "broken", root / "basic")


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT)
    print(f"fixtures written to {ROOT if len(sys.argv) < 2 else sys.argv[1]}")
