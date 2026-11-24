"""Hold-to-settlement counterfactual for imported manual lots (nem.stats.held), on made-up
lots and settlement checks only."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from nem.core.types import ManualLot, Side
from nem.settle.model import SettlementCheck
from nem.settle.rules import Status
from nem.stats.held import HeldLot, HeldMarket, held_markets, held_result, held_to_settlement
from nem.stats.metrics import strategy_stats, wilson
from nem.stats.report import (
    HELD_UNCHECKED,
    HELD_VERDICT,
    MANUAL_VERDICT,
    build_manual_report,
    render_text,
    report_data,
    verdict,
)
from nem.store import Store

NOW = datetime(2026, 11, 1, tzinfo=UTC)
OPEN = datetime(2026, 1, 10, 12, tzinfo=UTC)


def lot(
    ticker: str,
    side: Side = "no",
    qty: float = 10,
    entry: float = 0.90,
    exit_price: float = 0.99,
    open_fees: float = 0.10,
    close_fees: float = 0.01,
    n: int = 0,
) -> ManualLot:
    moved = qty * (exit_price - entry)
    return ManualLot(
        portfolio="manual_baseline", strategy="manual", lot_key=f"{ticker}#{n}", ticker=ticker,
        side=side, qty=qty, entry_price=entry, exit_price=exit_price, open_fees=open_fees,
        close_fees=close_fees, pnl_without_fees=moved,
        realized_pnl=moved - open_fees - close_fees, opened_at=OPEN + timedelta(minutes=n),
        closed_at=OPEN + timedelta(hours=6, minutes=n), source_file="x.csv", source_line=n + 2,
    )  # fmt: skip


def check(
    ticker: str,
    status: Status,
    derived: Side | None,
    kalshi: str | None,
    day: date | None = date(2026, 1, 10),
) -> SettlementCheck:
    return SettlementCheck(
        ticker=ticker, series=ticker.split("-")[0], market_date=day, measure="high",
        station="CLINYC", rules_source="nws_cli", strike_type="greater", floor_strike=40,
        cap_strike=None, observed_value=None, source="iem_cli", source_ref=None,
        derived_result=derived, kalshi_result=kalshi, kalshi_value=None,
        kalshi_settlement_value=None, status=status, note="", checked_at=NOW,
    )  # fmt: skip


def test_result_rule_derived_wins_and_unusable_is_none() -> None:
    assert held_result(check("A", "agree", "no", "no")) == "no"
    assert held_result(check("A", "disagree", "yes", "no")) == "yes"  # derived wins
    assert held_result(check("A", "source_missing", None, "no")) == "no"  # Kalshi's
    assert held_result(check("A", "source_missing", None, "yes")) == "yes"
    assert held_result(check("A", "source_missing", None, None)) is None
    # Kalshi settled at a price ("scalar"): the derived result, as `nem settle verify`
    # replays it; without a derived value there is no $1/$0 payout to hold to
    assert held_result(check("A", "kalshi_missing", "no", "scalar")) == "no"
    assert held_result(check("A", "kalshi_missing", None, "scalar")) is None
    assert held_result(check("A", "kalshi_missing", None, None)) is None


def test_payout_fees_and_exclusions() -> None:
    lots = [
        lot("KXHIGHNY-26JAN10-T40", "no"),  # agree no: wins, sold at 99c
        lot("KXHIGHNY-26JAN11-T40", "no", exit_price=0.40, n=1),  # stop that would have won
        lot("KXHIGHNY-26JAN12-T40", "no", exit_price=0.40, n=2),  # disagree: derived yes, lost
        lot("KXHIGHNY-26JAN13-T40", "no", n=3),  # source_missing: Kalshi's no, won
        lot("KXHIGHNY-26JAN14-T40", "no", n=4),  # kalshi_missing / scalar: derived no, won
        lot("KXHIGHNY-26JAN15-T40", "no", n=5),  # no check: excluded
    ]
    checks = {
        c.ticker: c
        for c in [
            check("KXHIGHNY-26JAN10-T40", "agree", "no", "no", date(2026, 1, 10)),
            check("KXHIGHNY-26JAN11-T40", "agree", "no", "no", date(2026, 1, 11)),
            check("KXHIGHNY-26JAN12-T40", "disagree", "yes", "no", date(2026, 1, 12)),
            check("KXHIGHNY-26JAN13-T40", "source_missing", None, "no", None),
            check("KXHIGHNY-26JAN14-T40", "kalshi_missing", "no", "scalar"),
        ]
    }
    h = held_to_settlement(lots, checks)
    assert h is not None
    assert (h.lots, h.unchecked, h.no_result, h.excluded) == (5, 1, 0, 1)
    st = h.stats
    assert (st.n, st.wins) == (5, 4)
    # cost = entry + actual open fee per contract, no close fee: 0.90 + 0.10 / 10
    assert st.breakeven == pytest.approx(0.91)
    assert st.edge == pytest.approx((4 * 0.09 - 0.91) / 5)
    # held: 10 contracts pay $10 on a win, minus $9.10 paid
    assert h.held_pnl == pytest.approx(4 * 0.90 - 9.10)
    sold = h.by_exit["sold"]  # the stops
    assert (sold.lots, sold.held_wins) == (2, 1)
    assert sold.actual == pytest.approx(2 * (10 * (0.40 - 0.90) - 0.11))
    assert sold.held == pytest.approx(0.90 - 9.10)
    at99 = h.by_exit["sold_at_99c"]  # each gives up 1c per contract plus the close fee
    assert at99.held - at99.actual == pytest.approx(3 * (10 * 0.01 + 0.01))
    # the existing machinery, verdict wording included
    assert verdict(st, 0.02).startswith("undecided after 5 trades")
    assert (h.markets, h.markets_with_loss, h.markets_won) == (5, 1, 4)


def test_fractional_lot_counts_once() -> None:
    a, b = lot("KXHIGHNY-26JAN10-T40", qty=0.5), lot("KXHIGHNY-26JAN11-T40", qty=100, n=1)
    checks = {
        a.ticker: check(a.ticker, "agree", "no", "no"),
        b.ticker: check(b.ticker, "agree", "yes", "yes"),
    }
    h = held_to_settlement([a, b], checks)
    assert h is not None
    # per contract: 0.90 + 0.10/0.5 = 1.10 and 0.90 + 0.10/100 = 0.901, one weight each
    assert h.stats.breakeven == pytest.approx((1.10 + 0.901) / 2)
    assert h.stats.win_rate == 0.5
    assert h.held_pnl == pytest.approx((0.5 - 0.55) + (0 - 90.10))


def test_settlement_order_and_same_stats_as_engine() -> None:
    late = lot("KXHIGHNY-26JAN12-T40", n=0)  # opened first, settles last
    early = lot("KXHIGHNY-26JAN11-T40", n=1)
    checks = {
        late.ticker: check(late.ticker, "agree", "no", "no", date(2026, 1, 12)),
        early.ticker: check(early.ticker, "agree", "yes", "yes", date(2026, 1, 11)),
    }
    h = held_to_settlement([late, early], checks)
    assert h is not None
    ordered = [HeldLot(early, "yes", date(2026, 1, 11)), HeldLot(late, "no", date(2026, 1, 12))]
    assert h.stats == strategy_stats(ordered)


def test_no_checks_for_these_markets_is_none() -> None:
    other = {"KXHIGHNY-26FEB01-T40": check("KXHIGHNY-26FEB01-T40", "agree", "no", "no")}
    assert held_to_settlement([lot("KXHIGHNY-26JAN10-T40")], other) is None


def schema_violations(instance: object) -> list[str]:
    root = Path(__file__).parents[2] / "contract" / "schema"
    schemas = {p.name: json.loads(p.read_text()) for p in root.glob("*.json")}
    resources: list[tuple[str, Any]] = [(n, Resource.from_contents(s)) for n, s in schemas.items()]
    empty: Any = Registry[Any]()
    v: Any = Draft202012Validator(
        schemas["summary_report.schema.json"], registry=empty.with_resources(resources)
    )
    return [str(e.message) for e in v.iter_errors(instance)]


def strategy_rows(data: dict[str, object]) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", data["strategies"])


def test_report_text_and_json() -> None:
    store = Store()
    won, stop = lot("KXHIGHNY-26JAN10-T40"), lot("KXHIGHNY-26JAN11-T40", exit_price=0.4, n=1)
    store.add_manual_lots([won, stop], NOW)
    report = build_manual_report(store, "manual_baseline", NOW)
    assert report is not None
    text = render_text(report)
    assert f"verdict     {MANUAL_VERDICT}" in text
    assert text.rstrip().endswith(f"    {HELD_UNCHECKED}")  # nothing else
    assert "held to settlement (counterfactual" not in text
    (row,) = strategy_rows(report_data(report))
    assert "held_to_settlement" not in row

    store.save_settlement_checks([check(won.ticker, "agree", "no", "no")])
    report = build_manual_report(store, "manual_baseline", NOW)
    assert report is not None
    text = render_text(report)
    assert HELD_UNCHECKED not in text
    assert "lots        1 used, 1 excluded (1 not checked, 0 without a yes/no result)" in text
    assert "no close fee at settlement" in text
    assert f"verdict     {HELD_VERDICT}\n      per market  undecided after 1 markets" in text
    assert "      per lot     undecided after 1 lots" in text
    data = report_data(report)
    (row,) = strategy_rows(data)
    assert row["verdict"] == "undecided"  # the actual exits' row is unchanged
    held: dict[str, Any] = row["held_to_settlement"]
    assert held == {
        "lots": 1, "excluded": 1, "win_rate": 1.0, "win_rate_lo": held["win_rate_lo"],
        "win_rate_hi": 1.0, "break_even_rate": 0.91, "edge_cents": 9.0,
        "edge_lo_cents": held["edge_lo_cents"], "edge_hi_cents": 9.0, "verdict": "undecided",
        "markets": 1, "markets_with_loss": 0, "market_win_rate": 1.0,
        "market_win_rate_lo": held["market_win_rate_lo"], "market_win_rate_hi": 1.0,
        "market_break_even_rate": 0.91, "market_edge_cents": 9.0,
        "market_edge_lo_cents": held["market_edge_lo_cents"], "market_edge_hi_cents": 9.0,
        "market_z": held["market_z"], "market_p": held["market_p"],
        "market_verdict": "undecided", "markets_to_detect": held["markets_to_detect"],
    }  # fmt: skip
    assert schema_violations({"schema_version": "1.0", "portfolios": [data]}) == []


def test_lots_on_one_market_count_as_one_market() -> None:
    """Three lots on one market share its outcome: one market, lost if any lot lost."""
    same = [lot("KXHIGHNY-26JAN10-T40", "no", n=i) for i in range(3)]
    hedge = lot("KXHIGHNY-26JAN10-T40", "yes", n=3)  # the other side of the same market
    other = lot("KXHIGHNY-26JAN11-T40", "no", n=4)
    checks = {
        c.ticker: c
        for c in [
            check("KXHIGHNY-26JAN10-T40", "agree", "no", "no"),
            check("KXHIGHNY-26JAN11-T40", "agree", "no", "no", date(2026, 1, 11)),
        ]
    }
    h = held_to_settlement([*same, hedge, other], checks)
    assert h is not None
    assert (h.stats.n, h.stats.wins) == (5, 4)
    assert (h.markets, h.markets_with_loss, h.markets_won) == (2, 1, 1)
    assert h.market_win_rate == 0.5
    assert h.market_win_ci == wilson(1, 2)


def test_held_text_shows_markets_and_clustering() -> None:
    store = Store()
    a, b = lot("KXHIGHNY-26JAN10-T40"), lot("KXHIGHNY-26JAN10-T40", n=1)
    store.add_manual_lots([a, b], NOW)
    store.save_settlement_checks([check(a.ticker, "agree", "no", "no")])
    report = build_manual_report(store, "manual_baseline", NOW)
    assert report is not None
    text = render_text(report)
    assert "markets     1 distinct, 0 with a loss; won 100.0%" in text
    assert "(1/1 markets with no losing lot)" in text
    assert "not independent" in text and "overstate the evidence" in text


def test_per_market_break_even_and_verdict_reuse_the_engine_statistics() -> None:
    """Each market is one bet at its lots' mean cost per contract, won when no lot lost,
    in market-date order; the statistics are strategy_stats on those bets."""
    jan10, jan11 = "KXHIGHNY-26JAN10-T40", "KXHIGHNY-26JAN11-T40"
    lots = [
        lot(jan11, entry=0.80, open_fees=0.0, n=0),  # later market, opened first
        lot(jan10, entry=0.90, qty=10, open_fees=0.10, n=1),  # 0.91 per contract
        lot(jan10, entry=0.94, qty=4, open_fees=0.04, n=2),  # 0.95 per contract
    ]
    checks = {
        jan10: check(jan10, "agree", "no", "no"),
        jan11: check(jan11, "agree", "yes", "yes", date(2026, 1, 11)),  # "no" lot loses
    }
    h = held_to_settlement(lots, checks)
    assert h is not None
    expected_bets = [
        HeldMarket(jan10, date(2026, 1, 10), 2, 0.93, True),
        HeldMarket(jan11, date(2026, 1, 11), 1, 0.80, False),
    ]
    held = [HeldLot(x, checks[x.ticker].derived_result or "no", date(2026, 1, 10)) for x in lots]
    held[0] = HeldLot(lots[0], "yes", date(2026, 1, 11))
    got = held_markets(held)
    assert [(m.ticker, m.lots, m.won) for m in got] == [(jan10, 2, True), (jan11, 1, False)]
    assert [m.cost for m in got] == pytest.approx([0.93, 0.80])
    expected = strategy_stats(expected_bets)
    ms = h.market_stats
    assert (ms.n, ms.wins) == (2, 1)
    assert h.market_breakeven == pytest.approx((0.93 + 0.80) / 2)
    assert ms.breakeven == pytest.approx(expected.breakeven)
    assert ms.sprt == expected.sprt
    assert ms.z_vs_breakeven == pytest.approx(expected.z_vs_breakeven)
    assert h.market_win_ci == wilson(1, 2)
    assert (h.stats.n, h.stats.wins) == (3, 2)  # per lot, unchanged


def test_per_market_verdict_can_differ_from_per_lot() -> None:
    """Many winning lots piled on few markets: the per-lot test sees far more evidence."""
    days = [date(2026, 1, 10 + d) for d in range(3)]
    tickers = [f"KXHIGHNY-26JAN{day.day}-T40" for day in days]
    lots = [lot(t, entry=0.6, n=40 * d + i) for d, t in enumerate(tickers) for i in range(40)]
    checks = {t: check(t, "agree", "no", "no", day) for t, day in zip(tickers, days, strict=True)}
    h = held_to_settlement(lots, checks)
    assert h is not None
    assert h.stats.n == 120 and h.markets == 3
    assert h.stats.sprt.decision == "edge"
    assert h.market_stats.sprt.decision == "undecided"
    store = Store()
    store.add_manual_lots(lots, NOW)
    store.save_settlement_checks(list(checks.values()))
    report = build_manual_report(store, "manual_baseline", NOW)
    assert report is not None
    text = render_text(report)
    assert "      per market  undecided after 3 markets" in text
    per_lot = "      per lot     evidence of a 2c+ edge per contract (sequential test, 120 lots)"
    assert per_lot in text
    assert "(overstates: lots cluster)" in text
    assert "break-even 61.0%" in text.split("markets     ")[1]
    (row,) = strategy_rows(report_data(report))
    held: dict[str, Any] = row["held_to_settlement"]
    assert (held["verdict"], held["market_verdict"]) == ("edge", "undecided")
    assert held["market_break_even_rate"] == 0.61
