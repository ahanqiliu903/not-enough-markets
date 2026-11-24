"""Reports cap the verdict of strategies that read backfilled forecasts. Synthetic trades."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from nem.core.types import Trade
from nem.stats.metrics import strategy_stats
from nem.stats.provenance import BACKFILL_LABEL
from nem.stats.report import (
    BACKFILL_CAPPED,
    PortfolioReport,
    StrategyReport,
    render_text,
    report_data,
    tables,
    verdict,
)

from engine_helpers import portfolio, strategy
from factories import make_trade

NOW = datetime(2031, 3, 1, tzinfo=UTC)
BACKFILL_GATE = {"type": "bracket_distance", "backfill_sources": ["iem_mos_gfs"]}


def trades(n: int, won: bool, price: float = 0.60) -> list[Trade]:
    t0 = datetime(2031, 1, 1, tzinfo=UTC)
    pnl = 5 * (1 - price) if won else -5 * price
    return [
        make_trade(
            f"W{i}",
            avg_price=price,
            fee=0.0,
            won=won,
            realized_pnl=pnl,
            opened_at=t0 + timedelta(hours=i),
            settled_at=t0 + timedelta(hours=i, minutes=20),
        )
        for i in range(n)
    ]


def report(ts: list[Trade], backfill: bool) -> PortfolioReport:
    gates = [BACKFILL_GATE] if backfill else []
    p = portfolio(strategies=[strategy(gates=gates)])
    sr = StrategyReport(p.strategies[0], strategy_stats(ts), ts, [], {})
    return PortfolioReport(
        portfolio=p, as_of=NOW, start=1000.0, realized=sum(t.realized_pnl or 0 for t in ts),
        interest=0.0, open_cost=0.0, open_positions=0, started_at=None, cash_benchmark=None,
        strategies=[sr], target_edge=0.02,
    )  # fmt: skip


def row(r: PortfolioReport) -> dict[str, Any]:
    (only,) = cast("list[dict[str, Any]]", report_data(r)["strategies"])
    return only


WINS = trades(100, True)  # 100 straight wins at 60c: the sequential test says edge


def test_edge_on_backfilled_forecasts_is_capped() -> None:
    plain, capped = report(WINS, backfill=False), report(WINS, backfill=True)
    assert plain.strategies[0].stats.sprt.decision == "edge"
    assert row(plain)["verdict"] == "edge" and "backfilled_forecasts" not in row(plain)
    assert row(capped)["verdict"] == "undecided"
    assert row(capped)["backfilled_forecasts"] is True
    _, strategies, _ = tables(capped)
    assert strategies.rows[0][strategies.columns.index("verdict")] == "undecided"  # CSV too
    text = render_text(capped)
    assert f"verdict     [{BACKFILL_LABEL}] {BACKFILL_CAPPED}" in text
    assert f"  [{BACKFILL_LABEL}] fav90  KXBTC15M  active  100 trades (100 won), 0 open" in text
    assert "evidence of a" not in text
    assert "  fav90  KXBTC15M  active  100 trades (100 won), 0 open" in render_text(plain)


def test_no_edge_and_undecided_pass_through_labelled() -> None:
    losing = report(trades(60, False), backfill=True)
    assert row(losing)["verdict"] == "no_edge"  # a clear loss still rejects the idea
    st = losing.strategies[0].stats
    assert f"verdict     [{BACKFILL_LABEL}] {verdict(st, 0.02)}" in render_text(losing)
    few = report(trades(3, True), backfill=True)
    assert row(few)["verdict"] == "undecided"
    assert row(few)["backfilled_forecasts"] is True
