"""`nem import kalshi-pnl` on a tiny made-up fixture (tests/fixtures/manual/).

The fixture: pnl_a.csv has a credit, a non-weather lot, a fractional lot sold at 0.99, two
identical-looking lots, one lot closing 23:30 ET the day before the window (inside it in
UTC) and one closing 23:30 ET on the window's last day (outside it in UTC). pnl_b.csv has a
settled loss and a stop-out. activity.csv has three fills (one on a non-weather market),
an order and a deposit.
"""

import json
import shutil
import sqlite3
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path

import pytest

from nem.cli import main
from nem.manual import ManualImportError
from nem.manual import import_kalshi_pnl as import_all
from nem.stats.report import build_manual_report, render_text, report_data
from nem.store import Store
from nem.store.sqlite import SCHEMA_VERSION

FIXTURES = Path(__file__).parents[1] / "fixtures" / "manual"
PNL = [FIXTURES / "pnl_a.csv", FIXTURES / "pnl_b.csv"]
# the fixture's window: its edge lots sit just inside and just outside it
START, END = date(2025, 12, 13), date(2026, 5, 31)
WINDOW = ["--from", START.isoformat(), "--to", END.isoformat()]
import_kalshi_pnl = partial(import_all, start=START, end=END)
ACTIVITY = FIXTURES / "activity.csv"
NOW = datetime(2026, 11, 1, tzinfo=UTC)
HEADER = (FIXTURES / "pnl_a.csv").read_text().splitlines()[0]


@pytest.fixture
def store() -> Store:
    return Store()


def test_import_filters_and_keeps_every_lot(store: Store) -> None:
    r = import_kalshi_pnl(store, PNL, NOW, ACTIVITY)
    assert (r.rows, r.credits, r.non_weather, r.outside_window) == (9, 1, 1, 1)
    assert r.new_lots == len(r.lots) == 6
    lots = store.manual_lots("manual_baseline")
    assert len(lots) == 6
    assert {lot.strategy for lot in lots} == {"manual"}
    tickers = [lot.ticker for lot in lots]
    assert "KXFAKECOIN-26JAN10-B1" not in tickers  # non-weather
    assert "KXHIGHTEST-25DEC12-T30" not in tickers  # 23:30 ET Dec 12 is out (UTC Dec 13)
    assert "KXHIGHTEST-26MAY31-T80" in tickers  # 23:30 ET May 31 is in (UTC Jun 1)
    # identical-looking rows are two real lots, with different keys
    twins = [lot for lot in lots if lot.ticker == "KXHIGHTEST-26JAN12-B35.5"]
    assert len(twins) == 2 and twins[0].lot_key != twins[1].lot_key
    frac = next(lot for lot in lots if lot.ticker == "KXHIGHTEST-26JAN11-T40")
    assert frac.qty == 12.5 and frac.exit == "sold_at_99c" and frac.won
    assert sorted(lot.exit for lot in lots) == [
        "settled_lost", "settled_won", "sold", "sold_at_99c", "sold_at_99c", "sold_at_99c",
    ]  # fmt: skip
    # money is Kalshi's, as written
    assert sum(lot.realized_pnl for lot in lots) == pytest.approx(-21.555)
    assert sum(lot.notional for lot in lots) == pytest.approx(50.29)


def test_include_non_weather_and_window(store: Store) -> None:
    r = import_kalshi_pnl(store, PNL, NOW, include_non_weather=True)
    assert (r.non_weather, len(r.lots)) == (0, 7)
    other = Store()
    r = import_kalshi_pnl(other, PNL, NOW, start=date(2025, 12, 12), end=date(2026, 1, 31))
    assert (r.outside_window, len(r.lots)) == (3, 4)


def test_reimport_is_idempotent(store: Store, tmp_path: Path) -> None:
    import_kalshi_pnl(store, PNL[:1], NOW)
    assert len(store.manual_lots("manual_baseline")) == 4
    r = import_kalshi_pnl(store, PNL, NOW, ACTIVITY)
    assert (len(r.lots), r.new_lots) == (6, 2)
    renamed = tmp_path / "export_copy.csv"  # the key doesn't depend on the file name
    shutil.copy(PNL[1], renamed)
    r = import_kalshi_pnl(store, [PNL[0], renamed], NOW, ACTIVITY)
    assert (r.new_lots, r.new_fills) == (0, 0)
    assert len(store.manual_lots("manual_baseline")) == 6
    assert len(store.manual_fills("manual_baseline")) == 2


def test_window_does_not_change_keys(store: Store) -> None:
    wide = import_kalshi_pnl(Store(), PNL, NOW, start=date(2025, 1, 1)).lots
    narrow = import_kalshi_pnl(store, PNL, NOW).lots
    assert {lot.lot_key for lot in narrow} < {lot.lot_key for lot in wide}


def test_activity_no_price_is_inverted(store: Store) -> None:
    r = import_kalshi_pnl(store, PNL, NOW, ACTIVITY)
    assert (r.fills, r.fills_kept, r.activity_other) == (3, 2, 2)
    fills = {f.ticker: f for f in store.manual_fills("manual_baseline")}
    no = fills["KXHIGHTEST-26FEB02-T50"]
    assert (no.side, no.price, no.qty, no.liquidity) == ("no", pytest.approx(0.91), 20, "taker")
    yes = fills["KXHIGHTEST-26MAY31-T80"]
    assert (yes.side, yes.price, yes.liquidity) == ("yes", pytest.approx(0.88), "maker")
    assert yes.filled_at == datetime(2026, 5, 31, 14, tzinfo=UTC)


def write(path: Path, *rows: str, header: str = HEADER) -> Path:
    path.write_text("\n".join([header, *rows]) + "\n")
    return path


GOOD = PNL[1].read_text().splitlines()[1]
Z = "00000000-0000-0000-0000-000000000000"
TS = "2026-02-03T15:00:00-05:00,2026-02-03T07:00:00-05:00"


def test_malformed_rows_fail_the_whole_import(store: Store, tmp_path: Path) -> None:
    bad = write(
        tmp_path / "bad.csv",
        GOOD,
        f"{Z},trade,10.00,KXHIGHTEST-26FEB03-T55,maybe,0.90,0.40,0.07,0.17,-5.00,-5.24,{TS}",
        f"{Z},trade,abc,KXHIGHTEST-26FEB03-T55,yes,0.90,0.40,0.07,0.17,-5.00,-5.24,{TS}",
        f"{Z},trade,10.00,KXHIGHTEST-26FEB03-T55,yes,0.90,0.40,0.07,0.17,-5.00,-4.00,{TS}",
        f"{Z},trade,10.00,,yes,0.90,0.40,0.07,0.17,-5.00,-5.24,{TS}",
        f"{Z},trade,10.00,KXHIGHTEST-26FEB03-T55,yes,1.90,0.40,0.07,0.17,-5.00,-5.24,{TS}",
        f"{Z},trade,10.00,KXHIGHTEST-26FEB03-T55,yes,0.90,0.40,0.07,0.17,-5.00,-5.24,"
        "2026-02-03T15:00:00,2026-02-03T07:00:00-05:00",
    )
    activity = tmp_path / "act.csv"
    text = ACTIVITY.read_text(encoding="utf-8-sig").replace('"No","Taker"', '"Up","Taker"')
    activity.write_text(text, encoding="utf-8-sig")
    with pytest.raises(ManualImportError) as exc:
        import_kalshi_pnl(store, [PNL[0], bad], NOW, activity)
    errors = exc.value.errors
    for line, field in [
        (3, "side"), (4, "quantity_fp"), (5, "realized_pnl_with_fees"), (6, "market_ticker"),
        (7, "entry_price_dollars"), (8, "close_timestamp"),
    ]:  # fmt: skip
        assert any(e.startswith(f"bad.csv line {line}: ") and field in e for e in errors), errors
    assert any(e.startswith("act.csv line 3: Direction") for e in errors), errors
    assert len(errors) == 7
    assert store.manual_lots("manual_baseline") == []  # nothing written


def test_missing_columns_are_reported(store: Store, tmp_path: Path) -> None:
    bad = write(tmp_path / "short.csv", "x,trade", header="subtrader_id,type")
    with pytest.raises(ManualImportError, match=r"short.csv: missing columns \['quantity_fp'"):
        import_kalshi_pnl(store, [bad], NOW)


def test_report_reuses_stats_and_is_read_only(store: Store) -> None:
    import_kalshi_pnl(store, PNL, NOW)
    report = build_manual_report(store, "manual_baseline", NOW)
    assert report is not None and report.manual is not None
    m = report.manual
    assert (m.lots, m.exits["sold_at_99c"][0]) == (6, 3)
    assert m.notional == pytest.approx(50.29)
    assert m.pnl == pytest.approx(-21.555)
    assert m.exits["sold_at_99c"][1] == pytest.approx(1.675)
    (sr,) = report.strategies
    assert sr.config.status == "retired" and report.portfolio.mode == "live"
    st = sr.stats
    assert (st.n, st.wins) == (6, 4)  # win = P&L after fees > 0
    assert st.pnl == pytest.approx(-21.555)
    assert st.win_rate_ci is not None and st.breakeven is not None
    text = render_text(report)
    assert "lots 6   entry notional $50.29   P&L after fees -21.55" in text
    assert "exits at 99c 3 lots +1.68; other exits 3 lots -23.23" in text
    assert "win = lot P&L after fees > 0" in text
    # per lot, unweighted by size: each lot's P&L per contract, averaged
    assert (m.wins, m.contracts) == (4, 55.5)
    assert m.mean_entry == pytest.approx(0.905)
    assert m.edge == pytest.approx(-1.1192 / 6)
    assert m.win_ci is not None and m.edge_ci is not None and m.edge_ci.low < m.edge
    assert [(mo.month, mo.lots, mo.contracts) for mo in m.months] == [
        ("2026-01", 3, 22.5), ("2026-02", 2, 30), ("2026-05", 1, 3),  # May 31 23:30 ET: May
    ]  # fmt: skip
    assert m.months[1].pnl == pytest.approx(-23.56)
    assert m.months[1].edge == pytest.approx((-0.916 - 0.524) / 2)
    assert "edge        -18.7c per contract after fees" in text
    assert "2026-02                      2       30.00        27.20     -23.56     -72.0c" in text
    assert "manual  KXHIGHTEST  retired  6 trades (4 won)" in text
    data = report_data(report)
    assert data["schema_version"] == "1.0"
    assert build_manual_report(store, "nope", NOW) is None


def test_cli_import_and_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db, d = str(tmp_path / "nem.db"), str(tmp_path / "portfolios")
    args = ["import", "kalshi-pnl", *map(str, PNL), "--activity", str(ACTIVITY), *WINDOW]
    assert main([*args, "--db", db, "--dir", d]) == 0
    out = capsys.readouterr().out
    assert "lots  6 in window: 6 new, 0 already imported" in out
    assert "fills 3 in the activity file (2 other rows skipped), 2 on imported markets" in out
    assert main([*args, "--db", db, "--dir", d]) == 0
    assert "6 in window: 0 new, 6 already imported" in capsys.readouterr().out
    # no portfolio files: summary still shows the imported baseline
    assert main(["summary", "--json", "--db", db, "--dir", d]) == 0
    (p,) = json.loads(capsys.readouterr().out)["portfolios"]
    assert p["summary"]["portfolio"] == "manual_baseline"
    assert p["summary"]["realized_pnl_usd"] == pytest.approx(-21.56, abs=0.01)
    (s,) = p["strategies"]
    assert (s["strategy"], s["status"], s["trades"], s["wins"]) == ("manual", "retired", 6, 4)
    # the $1/$0 sequential test doesn't apply to early exits: no verdict either way
    assert (s["verdict"], s["trades_to_detect"]) == ("undecided", None)
    assert main(["summary", "--db", db, "--dir", d]) == 0
    assert "verdict     not applicable: these lots exited before settlement" in (
        capsys.readouterr().out
    )
    # a portfolio file of the same name is refused
    Path(d).mkdir()
    (Path(d) / "manual_baseline.yaml").write_text("name: manual_baseline\n")
    with pytest.raises(SystemExit, match="needs its own name"):
        main([*args, "--db", db, "--dir", d])
    bad = write(tmp_path / "bad.csv", GOOD.replace(",no,", ",sideways,"))
    capsys.readouterr()
    assert main(["import", "kalshi-pnl", str(bad), "--db", db]) == 1
    assert "bad.csv line 2: side" in capsys.readouterr().err


def test_no_window_keeps_every_close_date() -> None:
    r = import_all(Store(), PNL, NOW)
    assert (r.outside_window, len(r.lots)) == (0, 7)


def test_v3_database_is_migrated(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    with Store(db):
        pass
    conn = sqlite3.connect(db)
    conn.executescript(
        "DROP TABLE manual_lots; DROP TABLE manual_fills;"
        " DROP TABLE settlement_checks;"  # v5 (settlement truth)
        " DROP TABLE forecast_backfill;"  # v6 (forecast backfill)
        " DROP TABLE exit_learning;"  # v7 (exits feed learning)
        " PRAGMA user_version = 3;"
    )
    conn.close()
    with Store(db) as s:
        import_kalshi_pnl(s, PNL, NOW)
        assert len(s.manual_lots("manual_baseline")) == 6
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 9
    conn.close()
