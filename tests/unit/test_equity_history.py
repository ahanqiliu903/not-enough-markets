"""Portfolio value over time (nem.stats.equity) and its `equity` report table."""

import csv
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nem.core.types import Trade
from nem.execution.fees import taker_fee
from nem.execution.mark import mark_position
from nem.reporting.csv_reporter import CsvReporter
from nem.reporting.sheets import SheetsReporter
from nem.stats.equity import EquityPoint, equity_history, time_grid
from nem.stats.report import (
    EQUITY_COLUMNS,
    build_report,
    equity_table,
    report_tables,
)
from nem.store import Store

from engine_helpers import portfolio
from factories import make_snapshot, make_trade

H = timedelta(hours=1)
T10 = datetime(2026, 10, 1, 10, tzinfo=UTC)
NOW = T10 + 3 * H + timedelta(minutes=30)  # 13:30
RATE = 0.07
DEPTH = {"yes": [(0.93, 50.0)], "no": [(0.06, 50.0)]}


def trade(name: str, opened: datetime, close: datetime, **kw: Any) -> Trade:
    return make_trade(name, ticker=f"{name}-T1", opened_at=opened, close_time=close, **kw)


def snap(t: Trade, ts: datetime, depth: Any = None) -> Any:
    return make_snapshot(
        ts=ts, window_id=t.window_id, ticker=t.ticker, open_time=t.opened_at - H,
        close_time=t.close_time, depth=DEPTH if depth is None else depth,
    )  # fmt: skip


def mark_at(t: Trade, s: Any, now: datetime) -> float:
    m = mark_position(replace(t, settled_at=None, won=None, realized_pnl=None), s, RATE, now)
    assert m is not None
    return m.unrealized_pnl


def scenario() -> tuple[Store, dict[str, Trade], dict[str, Any]]:
    """10:00 .. 13:30. A opens 10:20, settles 12:30 (+0.48); B opens 11:50, still open;
    C (excluded) opens 10:30, settles 11:10 (-4.52); interest 0.01 at 11:00 sharp."""
    store = Store()
    a = trade("A", T10 + timedelta(minutes=20), T10 + 2 * H + timedelta(minutes=15))
    b = trade("B", T10 + H + timedelta(minutes=50), NOW + H)
    c = trade("C", T10 + timedelta(minutes=30), T10 + H + timedelta(minutes=5))
    for t in (a, b, c):
        store.open_trade(t)
    key = ("research", "fav90")
    store.settle_trade(key, "A", True, 0.48, T10 + 2 * H + timedelta(minutes=30))
    store.settle_trade(key, "C", False, -4.52, T10 + H + timedelta(minutes=10))
    store.set_excluded(c, "test", NOW)
    store.add_ledger("research", T10 + H, "interest", 0.01)
    snaps = {
        "a11": snap(a, T10 + timedelta(minutes=58)),  # fresh at 11:00, stale at 12:00
        "c11": snap(c, T10 + timedelta(minutes=59)),
        "b12": snap(b, T10 + H + timedelta(minutes=55)),  # fresh at 12:00, stale at 13:00
        "b1325": snap(b, NOW - timedelta(minutes=5), {"yes": [(0.91, 2.0)], "no": []}),
    }
    store.insert_snapshots(list(snaps.values()))
    return store, {"A": a, "B": b, "C": c}, snaps


def test_time_grid_hourly_plus_final_point() -> None:
    first = T10 + timedelta(minutes=20)
    assert time_grid(first, NOW) == [T10, T10 + H, T10 + 2 * H, T10 + 3 * H, NOW]
    assert time_grid(T10, T10 + 2 * H) == [T10, T10 + H, T10 + 2 * H]  # no duplicate end
    assert time_grid(NOW, NOW) == [T10 + 3 * H, NOW]
    assert time_grid(T10, T10) == [T10]


def test_value_over_time() -> None:
    store, t, s = scenario()
    h = equity_history(store, portfolio(), NOW)
    assert [p.ts for p in h] == [T10, T10 + H, T10 + 2 * H, T10 + 3 * H, NOW]

    # 10:00: nothing open yet (A opens 10:20; excluded C never counts)
    assert h[0] == EquityPoint(T10, 0.0, 0.0, 1000.0, 0.0, 0, 0)
    assert h[0].value == 1000.0
    # 11:00: A marked; C (excluded) left out though it was open and marked; ledger at
    # exactly 11:00 is not yet counted (before t)
    a11 = mark_at(t["A"], s["a11"], T10 + H)
    assert a11 == pytest.approx(5 * 0.93 - taker_fee(0.93, 5, RATE) - (5 * 0.90 + 0.02))
    assert (h[1].open_positions, h[1].open_unmarked, h[1].interest) == (1, 0, 0.0)
    assert h[1].unrealized == pytest.approx(a11)
    assert h[1].value == pytest.approx(1000 + a11)
    # 12:00: A's book is an hour old (unmarked, at cost), B marked; interest counted
    b12 = mark_at(t["B"], s["b12"], T10 + 2 * H)
    assert (h[2].open_positions, h[2].open_unmarked) == (2, 1)
    assert h[2].unrealized == pytest.approx(b12)
    assert (h[2].realized, h[2].interest) == (0.0, 0.01)
    assert h[2].book_value == pytest.approx(1000.01)
    # 13:00: A settled +0.48 (realized), B's book stale -> unmarked at cost
    assert (h[3].open_positions, h[3].open_unmarked, h[3].unrealized) == (1, 1, 0.0)
    assert h[3].realized == pytest.approx(0.48)
    assert h[3].value == h[3].book_value == pytest.approx(1000.49)
    # 13:30 (report time): B marked on a thin book, 3 contracts at $0
    b_now = mark_at(t["B"], s["b1325"], NOW)
    assert h[4].unrealized == pytest.approx(b_now)
    assert h[4].value == pytest.approx(1000.49 + b_now)


def test_final_point_matches_the_report() -> None:
    store, _, _ = scenario()
    r = build_report(store, portfolio(), NOW, history=True)
    assert r.history is not None
    last = r.history[-1]
    assert last.ts == r.as_of
    assert last.book_value == pytest.approx(r.equity)
    assert last.realized == pytest.approx(r.realized)
    assert last.unrealized == pytest.approx(r.open.unrealized_pnl or 0.0)
    assert (last.open_positions, last.open_unmarked) == (r.open_positions, r.open.unmarked)


def test_trade_opened_and_settled_between_points_never_shows_open() -> None:
    store = Store()
    t = trade("Q", T10 + timedelta(minutes=10), T10 + timedelta(minutes=25))
    store.open_trade(t)
    store.insert_snapshots([snap(t, T10 + timedelta(minutes=15))])
    store.settle_trade(("research", "fav90"), "Q", False, -4.52, T10 + timedelta(minutes=26))
    h = equity_history(store, portfolio(), T10 + H)
    assert [(p.open_positions, p.realized) for p in h] == [(0, 0.0), (0, -4.52)]


def test_open_at_settlement_instant_counts_as_settled() -> None:
    store = Store()
    t = trade("S", T10 - timedelta(minutes=10), T10 + timedelta(minutes=5))
    store.open_trade(t)
    store.settle_trade(("research", "fav90"), "S", True, 0.48, T10)
    h = equity_history(store, portfolio(), T10 + timedelta(minutes=1))
    assert h[1].realized == 0.48 and h[1].open_positions == 0  # settled at or before t


def test_no_activity_is_empty() -> None:
    store = Store()
    assert equity_history(store, portfolio(), NOW) == []
    r = build_report(store, portfolio(), NOW, history=True)
    table = equity_table(r)
    assert table is not None and table.rows == []


def test_history_only_on_request() -> None:
    store, _, _ = scenario()
    r = build_report(store, portfolio(), NOW)
    assert r.history is None and equity_table(r) is None
    assert [t.name for t in report_tables(r)] == ["summary", "strategies", "trades"]


def test_latest_snapshots_matches_latest_snapshot() -> None:
    store = Store()
    t = trade("L", T10, T10 + 3 * H)
    stamps = [T10 + timedelta(minutes=m) for m in (5, 30, 30, 90)]  # a tie at :30
    store.insert_snapshots(
        [snap(t, ts, {"yes": [(0.5 + i / 100, 1.0)]}) for i, ts in enumerate(stamps)]
    )
    times = [T10, T10 + timedelta(minutes=30), T10 + H, T10 + 2 * H]
    assert store.latest_snapshots(t.ticker, times) == [
        store.latest_snapshot(t.ticker, x) for x in times
    ]
    assert store.latest_snapshots(t.ticker, []) == []
    assert store.latest_snapshots("nope", times) == [None] * 4


def rows_of(r: Any) -> list[dict[str, Any]]:
    table = equity_table(r)
    assert table is not None
    return [dict(zip(table.columns, row, strict=True)) for row in table.rows]


def test_equity_table_shape() -> None:
    store, _, _ = scenario()
    r = build_report(store, portfolio(), NOW, history=True)
    assert [t.name for t in report_tables(r)] == ["summary", "strategies", "trades", "equity"]
    rows = rows_of(r)
    assert len(rows) == 5
    assert list(rows[0]) == EQUITY_COLUMNS
    assert rows[0] == {
        "schema_version": "1.0", "portfolio": "research", "ts": "2026-10-01T10:00:00Z",
        "book_value_usd": 1000.0, "realized_pnl_usd": 0.0, "interest_usd": 0.0,
        "unrealized_pnl_usd": 0.0, "value_usd": 1000.0, "open_positions": 0,
        "open_unmarked": 0,
    }  # fmt: skip
    assert rows[-1]["ts"] == "2026-10-01T13:30:00Z"
    assert rows[3]["value_usd"] == 1000.49


def test_csv_reporter_writes_equity(tmp_path: Path) -> None:
    store, _, _ = scenario()
    r = build_report(store, portfolio(), NOW, history=True)
    CsvReporter(CsvReporter.Params(path=str(tmp_path))).publish(r)
    folder = tmp_path / "research"
    assert sorted(p.name for p in folder.iterdir()) == [
        "equity.csv", "strategies.csv", "summary.csv", "trades.csv",
    ]  # fmt: skip
    with (folder / "equity.csv").open(newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == EQUITY_COLUMNS and len(rows) == 6
    assert rows[1][2] == "2026-10-01T10:00:00Z"


class Tab:
    def __init__(self) -> None:
        self.updates: list[Sequence[Sequence[Any]]] = []

    def clear(self) -> None:
        pass

    def update(self, values: Sequence[Sequence[Any]], range_name: str | None = None) -> None:
        self.updates.append(values)


class Sheet:
    def __init__(self) -> None:
        self.tabs: dict[str, Tab] = {}

    def worksheet(self, title: str) -> Tab:
        return self.tabs[title]

    def add_worksheet(self, title: str, rows: int, cols: int) -> Tab:
        self.tabs[title] = Tab()
        return self.tabs[title]


def test_sheets_reporter_writes_equity_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    store, _, _ = scenario()
    sheet = Sheet()
    monkeypatch.setenv("SHEETS_CREDENTIALS", "/x.json")
    monkeypatch.setenv("SHEET_ID", "id")
    rep = SheetsReporter(SheetsReporter.Params(), lambda _c, _s: sheet)
    rep.publish(build_report(store, portfolio(), NOW, history=True))
    tab = sheet.tabs["research equity"]
    assert len(tab.updates) == 1  # one batch
    values = tab.updates[0]
    assert list(values[0]) == EQUITY_COLUMNS and len(values) == 6
    assert all(v is not None for row in values for v in row)
