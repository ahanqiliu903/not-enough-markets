import csv
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nem.core.config import PluginSpec
from nem.core.registry import REGISTRY
from nem.core.types import Signal
from nem.reporting.base import Reporter
from nem.reporting.csv_reporter import CsvReporter
from nem.reporting.sheets import SheetsError, SheetsReporter
from nem.stats.report import PortfolioReport, build_report, render_all, tables, verdict
from nem.store import Store

from engine_helpers import portfolio, strategy
from factories import make_trade

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def seeded_store() -> Store:
    store = Store()
    for i, won in enumerate([True, True, False]):
        w = f"KXBTC15M-26OCT0819{i}0"
        settled = NOW - timedelta(hours=3 - i)
        store.open_trade(make_trade(w, opened_at=settled - timedelta(minutes=10)))
        store.settle_trade(("research", "fav90"), w, won, 0.48 if won else -4.52, settled)
        sig = Signal("research", "fav90", w, f"{w}-T1", "yes", 0.9, 0.94)
        store.log_signal(sig, settled - timedelta(minutes=10), "take", "ok: fixed 5")
    store.log_signal(Signal("research", "fav90", "W9", "T", "yes", 0.9, 0.94), NOW, "skip", "x")
    store.open_trade(make_trade("KXBTC15M-26OCT081930", opened_at=NOW))
    return store


def report(**pkw: Any) -> PortfolioReport:
    pkw.setdefault("strategies", [strategy(), strategy(name="idle")])
    return build_report(seeded_store(), portfolio(**pkw), NOW)


def test_build_report() -> None:
    r = report(interest_apy=0.0365)
    fav = r.strategies[0]
    assert (fav.stats.n, fav.stats.wins, fav.open_positions) == (3, 2, 1)
    assert fav.reasons == {"ok: fixed 5": 3, "x": 1}
    assert r.realized == pytest.approx(0.48 + 0.48 - 4.52)
    assert r.open_positions == 1
    assert r.strategies[1].stats.n == 0
    # started 3h10m before NOW: $1000 at 3.65% for that long
    assert r.cash_benchmark == pytest.approx(1000 * 0.0365 * (190 / 60 / 24) / 365)


def test_render_text() -> None:
    text = render_all([report(interest_apy=0.0365)])
    assert "Portfolio research (paper)" in text
    assert "fav90  KXBTC15M  active  3 trades (2 won), 1 open" in text
    assert "break-even 90.4%" in text
    assert "undecided after 3 trades" in text
    assert "skipped     1 x" in text
    assert "vs holding cash at 3.65% APY" in text
    assert "idle  KXBTC15M  active  0 trades" in text
    assert "no settled trades yet" in text


def test_verdicts() -> None:
    from dataclasses import replace

    st = report().strategies[0].stats
    assert "evidence of a 2c+ edge" in verdict(
        replace(st, sprt=replace(st.sprt, decision="edge")), 0.02
    )
    assert "consider pausing" in verdict(
        replace(st, sprt=replace(st.sprt, decision="no_edge")), 0.02
    )


def test_tables_shape() -> None:
    summary, strategies, trades = tables(report())
    assert [t.name for t in (summary, strategies, trades)] == ["summary", "strategies", "trades"]
    for t in (summary, strategies, trades):
        assert all(len(row) == len(t.columns) for row in t.rows)
    assert len(strategies.rows) == 2
    assert len(trades.rows) == 3  # settled only
    row = dict(zip(strategies.columns, strategies.rows[0], strict=True))
    assert (row["strategy"], row["trades"], row["wins"], row["sprt"]) == (
        "fav90",
        3,
        2,
        "undecided",
    )


def test_csv_reporter(tmp_path: Path) -> None:
    rep = REGISTRY.build("reporter", PluginSpec(type="csv", path=str(tmp_path)), Reporter)  # type: ignore[call-arg]
    assert isinstance(rep, CsvReporter)
    where = rep.publish(report())
    files = sorted(p.name for p in (tmp_path / "research").iterdir())
    assert files == ["strategies.csv", "summary.csv", "trades.csv"]
    with (tmp_path / "research" / "trades.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    assert rows[0]["strategy"] == "fav90"
    assert where.endswith("research")


class FakeWorksheet:
    def __init__(self) -> None:
        self.values: Sequence[Sequence[Any]] = []
        self.cleared = 0

    def clear(self) -> None:
        self.cleared += 1

    def update(self, values: Sequence[Sequence[Any]], range_name: str | None = None) -> None:
        assert range_name == "A1"
        self.values = values


class FakeSheet:
    def __init__(self) -> None:
        self.tabs: dict[str, FakeWorksheet] = {"research summary": FakeWorksheet()}

    def worksheet(self, title: str) -> FakeWorksheet:
        return self.tabs[title]  # KeyError plays gspread.WorksheetNotFound

    def add_worksheet(self, title: str, rows: int, cols: int) -> FakeWorksheet:
        self.tabs[title] = FakeWorksheet()
        return self.tabs[title]


def test_sheets_reporter_writes_portfolio_tabs(monkeypatch: pytest.MonkeyPatch) -> None:
    sheet = FakeSheet()
    opened: list[tuple[str, str]] = []

    def opener(creds: str, sheet_id: str) -> FakeSheet:
        opened.append((creds, sheet_id))
        return sheet

    monkeypatch.setenv("SHEETS_CREDENTIALS", "/secret/sa.json")
    monkeypatch.setenv("SHEET_ID_RESEARCH", "sheet-123")
    rep = SheetsReporter(SheetsReporter.Params(sheet_id_env="SHEET_ID_RESEARCH"), opener)
    rep.publish(report())
    assert opened == [("/secret/sa.json", "sheet-123")]
    assert sorted(sheet.tabs) == ["research strategies", "research summary", "research trades"]
    assert sheet.tabs["research summary"].cleared == 1  # existing tab reused and cleared
    trades = sheet.tabs["research trades"].values
    assert trades[0][0] == "portfolio"
    assert len(trades) == 4  # header + 3 settled trades
    assert all(v is not None for row in trades for v in row)  # None -> ""


def test_sheets_reporter_needs_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHEETS_CREDENTIALS", raising=False)
    rep = SheetsReporter(SheetsReporter.Params(), lambda _c, _s: FakeSheet())
    with pytest.raises(SheetsError, match="SHEETS_CREDENTIALS and SHEET_ID"):
        rep.publish(report())
