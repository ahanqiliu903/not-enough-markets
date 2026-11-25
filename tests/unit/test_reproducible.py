"""Reproducible backtests (audit R9): explicit fee provenance, dataset identity,
collision-safe run ids, offline reruns. Synthetic data only, no network."""

import json
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from nem.backtest.export import (
    RerunError,
    engine_info,
    load_run,
    manifest,
    rerun,
    save_run,
    sweep_and_suggest,
    sweep_run_data,
)
from nem.backtest.types import Grid
from nem.cli import main
from nem.core.config import PortfolioConfig
from nem.market.fees import resolve_fees_offline
from nem.market.kalshi import SeriesFees
from nem.store import Store
from nem.store.sqlite import SCHEMA_VERSION

from engine_helpers import portfolio, settle, strategy, window

START = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
GRID: Grid = {"signal.threshold": [0.85, 0.9, 0.95]}
TP = {"take_profit": {"price": 0.95, "fill_model": "realistic"}}


def record(store: Store, n: int = 8) -> None:
    for i in range(n):
        w = window(START + timedelta(minutes=15 * i), every=timedelta(minutes=1))
        store.insert_snapshots(w)
        store.record_settlement(settle(w, "yes"))


def data_db(tmp_path: Path) -> Path:
    db = tmp_path / "data.db"
    with Store(db) as store:
        record(store)
    return db


# --- fees --------------------------------------------------------------------------------


def test_offline_fees_say_where_each_rate_came_from() -> None:
    p = portfolio(strategies=[
        strategy(name="set", taker_fee_rate=0.05),
        strategy(name="unset"),
        strategy(name="other", series="KXETH15M", exit=TP),
    ])  # fmt: skip
    store = Store()
    [resolved], fees = resolve_fees_offline([p], store.series_fees)
    by = {f.strategy: f for f in fees}
    assert (by["set"].taker.rate, by["set"].taker.source) == (0.05, "config")
    assert (by["unset"].taker.rate, by["unset"].taker.source) == (0.07, "default")
    assert "no fee schedule recorded for KXBTC15M" in by["unset"].taker.detail
    assert resolved.strategy("unset").taker_fee_rate == 0.07  # filled in, not implicit
    assert by["other"].maker is not None and by["other"].maker.source == "unresolved"
    assert by["unset"].maker is None  # no take-profit, no maker fee


def test_recorded_series_schedule_resolves_replay_fees() -> None:
    """The audit's probe: a quadratic series with multiplier 2 is 0.14 in replay too."""
    store = Store()
    at = START
    assert store.record_series_fees(SeriesFees("KXBTC15M", "quadratic_with_maker_fees", 2.0), at)
    assert not store.record_series_fees(
        SeriesFees("KXBTC15M", "quadratic_with_maker_fees", 2.0), at + timedelta(hours=1)
    )  # unchanged: nothing added
    p = portfolio(strategies=[strategy(exit=TP)])
    [resolved], (f,) = resolve_fees_offline([p], store.series_fees)
    assert (f.taker.rate, f.taker.source) == (pytest.approx(0.14), "recorded_series")
    assert f.maker is not None and f.maker.rate == pytest.approx(0.035)
    tp = resolved.strategy("fav90").exit
    assert tp is not None and tp.take_profit is not None
    assert tp.take_profit.maker_fee_rate == pytest.approx(0.035)
    assert "x2" in f.describe() and "recorded_series" in f.describe()


def test_unmodelled_recorded_schedule_falls_back_to_labelled_default() -> None:
    store = Store()
    store.record_series_fees(SeriesFees("KXBTC15M", "flat", 1.0), START)
    _, (f,) = resolve_fees_offline([portfolio()], store.series_fees)
    assert (f.taker.rate, f.taker.source) == (0.07, "default")
    assert "'flat' isn't modelled" in f.taker.detail


def test_v9_database_gets_the_series_fees_table(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    with Store(db):
        pass
    conn = sqlite3.connect(db)
    conn.executescript("DROP TABLE series_fees; PRAGMA user_version = 9;")
    conn.close()
    with Store(db) as store:
        store.record_series_fees(SeriesFees("KXBTC15M", "quadratic", 1.0), START)
        assert store.series_fees("KXBTC15M") == (SeriesFees("KXBTC15M", "quadratic", 1.0), START)
    assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 10


# --- dataset identity ---------------------------------------------------------------------


def test_dataset_identity_is_content_not_file() -> None:
    a, b = Store(), Store()
    record(a)
    b.insert_snapshot(window(START + timedelta(days=9), series="KXETH15M")[0])  # other ids
    record(b)
    ia = a.dataset_identity(["KXBTC15M"], [])
    ib = b.dataset_identity(["KXBTC15M"], [])
    assert ia == ib
    assert ia["rows"]["snapshots"] == 8 * 15 and ia["rows"]["settlements"] == 8
    assert ia["first_snapshot"] == START
    b.insert_snapshot(window(START + timedelta(days=1))[0])
    assert b.dataset_identity(["KXBTC15M"], [])["sha256"] != ia["sha256"]
    # a fee schedule recorded later is not data a past run read (it saved its rates)
    a.record_series_fees(SeriesFees("KXBTC15M", "quadratic", 3.0), START)
    assert a.dataset_identity(["KXBTC15M"], [])["sha256"] == ia["sha256"]


# --- run ids and saving -----------------------------------------------------------------


def saved_run(data: Path, base: PortfolioConfig | None = None, **kw: Any) -> dict[str, object]:
    with Store(data) as store:
        run = sweep_and_suggest(
            store, base or portfolio(), "fav90", GRID, holdout=0.3, min_trades=2
        )
    fields = manifest(run, data_label=str(data), **kw)
    return sweep_run_data(run, START.replace(microsecond=0), fields)


def test_same_second_runs_never_overwrite(tmp_path: Path) -> None:
    data = data_db(tmp_path)
    doc = saved_run(data)
    other = saved_run(data, portfolio(strategies=[strategy(taker_fee_rate=0.05)]))
    assert doc["run_id"] != other["run_id"]  # different inputs, same second
    out = tmp_path / "runs"
    first = save_run(dict(doc), out)
    second = save_run(dict(doc), out)  # identical run, same second
    assert first != second and second.name == f"{doc['run_id']}-2.json"
    assert json.loads(second.read_text())["run_id"] == f"{doc['run_id']}-2"
    assert json.loads(first.read_text())["run_id"] == doc["run_id"]
    assert sorted(p.name for p in out.iterdir()) == sorted([first.name, second.name])


def test_manifest_contents(tmp_path: Path) -> None:
    data = data_db(tmp_path)
    p = portfolio(reporting=[{"type": "csv", "path": "somewhere"}])
    doc = saved_run(data, p, hypothesis="favorites win", notes="first look")
    config = doc["config"]
    assert isinstance(config, dict)
    assert config["reporting"] == []  # output settings never saved
    assert config["strategies"][0]["taker_fee_rate"] == 0.07  # resolved, explicitly
    assert doc["fees"] == [{
        "portfolio": "research", "strategy": "fav90", "series": "KXBTC15M",
        "taker_fee_rate": {
            "rate": 0.07, "source": "default",
            "detail": "no fee schedule recorded for KXBTC15M; set taker_fee_rate",
        },
        "maker_fee_rate": None,
    }]  # fmt: skip
    assert doc["execution"] == {
        "entry_fills": "walk the recorded order book as a taker (IOC)",
        "missing_depth": "none",
        "max_snapshot_age_seconds": 120.0,
    }
    ds = cast(dict[str, Any], doc["dataset"])
    assert ds["path"] == str(data) and len(ds["sha256"]) == 64
    assert (doc["hypothesis"], doc["notes"]) == ("favorites win", "first look")
    assert doc["inputs"] == {"holdout": 0.3}


def test_engine_info_without_git() -> None:
    with patch("nem.backtest.export.subprocess.run", side_effect=OSError("no git")):
        info = engine_info()
    assert info["git_revision"] is None and info["git_dirty"] is None
    assert info["name"] == "nem"
    failed = subprocess.CompletedProcess[str]([], 128, "", "not a git repository")
    with patch("nem.backtest.export.subprocess.run", return_value=failed):
        assert engine_info()["git_revision"] is None


# --- rerun ------------------------------------------------------------------------------


def test_rerun_reproduces_exactly_and_detects_changed_data(tmp_path: Path) -> None:
    data = data_db(tmp_path)
    with Store(data) as store:
        store.record_series_fees(SeriesFees("KXBTC15M", "quadratic", 2.0), START)
    doc = saved_run(data)
    path = save_run(doc, tmp_path / "runs")
    saved = load_run(path)
    assert saved["fees"][0]["taker_fee_rate"]["rate"] == pytest.approx(0.14)
    with Store(data) as store:
        # today's schedule changed upstream: the saved run keeps its own rate
        store.record_series_fees(SeriesFees("KXBTC15M", "quadratic", 3.0), START + timedelta(1))
        new, differs = rerun(saved, store)
    assert differs == []
    assert new["run_id"] == saved["run_id"]
    assert {k: new[k] for k in new if k != "engine"} == {
        k: saved[k] for k in saved if k != "engine"
    }
    with Store(data) as store:
        store.insert_snapshot(window(START + timedelta(days=2))[0])
        with pytest.raises(RerunError, match="the data differs from the run's: sha256"):
            rerun(saved, store)


def test_old_runs_have_no_manifest(tmp_path: Path) -> None:
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"run_id": "x", "status": "suggested"}))
    with pytest.raises(RerunError, match=r"before contract 1\.1"):
        load_run(old)


def test_cli_suggest_then_rerun(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d = tmp_path / "portfolios"
    main(["portfolio", "new", "research", "--dir", str(d)])
    main(["strategy", "add", "research", "btc", "--series", "KXBTC15M", "--dir", str(d)])
    data = data_db(tmp_path)
    runs = tmp_path / "runs"
    capsys.readouterr()
    assert main([
        "suggest", "research", "btc", "--data", str(data), "--dir", str(d), "--save", str(runs),
        "--grid", "signal.threshold=0.85,0.9,0.95", "--min-trades", "2",
        "--hypothesis", "favorites win",
    ]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "fees research/btc: taker 0.0700 (default: no fee schedule recorded" in out
    (path,) = runs.glob("*.json")
    assert main(["rerun", str(path)]) == 0
    assert "reproduced exactly" in capsys.readouterr().out
    doc = json.loads(path.read_text())
    doc["score_cents"] = 999.0  # a tampered result is not reproduced
    path.write_text(json.dumps(doc))
    assert main(["rerun", str(path), "--data", str(data)]) == 1
    assert "NOT reproduced; these results differ: score_cents" in capsys.readouterr().out
    with Store(data) as store:
        store.insert_snapshot(window(START + timedelta(days=3))[0])
    with pytest.raises(SystemExit, match="can't rerun: the data differs"):
        main(["rerun", str(path)])
    with pytest.raises(SystemExit, match="no such database"):
        main(["rerun", str(path), "--data", str(tmp_path / "missing.db")])


def test_replay_prints_fee_sources(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d = tmp_path / "portfolios"
    main(["portfolio", "new", "research", "--dir", str(d)])
    main(["strategy", "add", "research", "btc", "--series", "KXBTC15M", "--dir", str(d)])
    data = data_db(tmp_path)
    with Store(data) as store:
        store.record_series_fees(SeriesFees("KXBTC15M", "quadratic", 2.0), START)
    capsys.readouterr()
    assert main(["replay", "--data", str(data), "--dir", str(d)]) == 0
    assert "fees research/btc: taker 0.1400 (recorded_series: KXBTC15M quadratic x2" in (
        capsys.readouterr().out
    )
