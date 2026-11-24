"""Kalshi history import, its replay source, the trade_through fill model and the history
backtest. Synthetic markets and a mocked API only; nothing here is real trading data."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from nem.backfill.sources import ArchiveClient
from nem.backtest.suggest import suggest
from nem.core.clock import ManualClock
from nem.core.config import PortfolioConfig
from nem.core.types import MarketSnapshot, Trade
from nem.engine.runner import Runner
from nem.engine.runtime import BuildError
from nem.execution.exits import take_profit_fill
from nem.feeds import http
from nem.history import fetch
from nem.history.backtest import (
    DatePeriod,
    date_bets,
    date_stats,
    expand_portfolio,
    labels,
    period_report,
    render_period,
    replay,
    sweep,
)
from nem.history.db import HistoryDb, HistoryMarket, Print, is_history_db
from nem.history.fetch import HistoryClient, candle_snapshots, import_history, parse_print
from nem.history.source import HistorySource
from nem.market.source import FakeSource
from nem.store import Store

from engine_helpers import portfolio
from factories import portfolio_dict

D1, D2, OUT = "26JAN10", "26JAN11", "26MAR01"


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)  # pyright: ignore[reportArgumentType]


def raw_market(
    day: str, open_: datetime, result: str = "no", strike: str = "B40.5"
) -> dict[str, Any]:
    close = open_ + timedelta(hours=4)
    return {
        "ticker": f"KXHIGHNY-{day}-{strike}",
        "event_ticker": f"KXHIGHNY-{day}",
        "open_time": open_.isoformat().replace("+00:00", "Z"),
        "close_time": close.isoformat().replace("+00:00", "Z"),
        "settlement_ts": (close + timedelta(hours=8)).isoformat().replace("+00:00", "Z"),
        "result": result,
        "strike_type": "between",
        "floor_strike": 40,
        "cap_strike": 41,
        "rules_primary": "synthetic",
    }


OPEN1 = utc(2026, 1, 10, 12)
OPEN2 = utc(2026, 1, 11, 12)
MARKETS = [
    raw_market(D1, OPEN1),
    raw_market(OUT, utc(2026, 3, 1, 12)),
    raw_market(D2, OPEN2, result=""),
]


def candle(ts: datetime, bid: str, ask: str) -> dict[str, Any]:
    return {
        "end_period_ts": int(ts.timestamp()),
        "yes_bid": {"open": bid, "low": bid, "high": bid, "close": bid},
        "yes_ask": {"open": ask, "low": ask, "high": ask, "close": ask},
        "price": {"close": None},
        "volume": "0.00",
        "open_interest": "0.00",
    }


def candles_for(open_: datetime, lo: int, hi: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    t = open_ + timedelta(minutes=1)
    while t <= open_ + timedelta(hours=4, minutes=1):
        if lo <= t.timestamp() <= hi:
            minute = int((t - open_).total_seconds() // 60)
            # quote changes at minute 1 and 60 only; YES 0.08/0.10 -> NO ask 0.92
            out.append(candle(t, "0.0800" if minute < 60 else "0.0500", "0.1000"))
        t += timedelta(minutes=1)
    return out


def trade(i: int, ts: datetime, no_price: str, count: str) -> dict[str, Any]:
    return {
        "trade_id": f"t{i}",
        "ticker": f"KXHIGHNY-{D1}-B40.5",
        "created_time": ts.isoformat().replace("+00:00", "Z"),
        "yes_price_dollars": f"{1 - float(no_price):.4f}",
        "no_price_dollars": no_price,
        "count_fp": count,
        "taker_outcome_side": "no",
    }


TRADES = [
    trade(1, OPEN1 + timedelta(minutes=30), "0.9900", "4.00"),
    trade(2, OPEN1 + timedelta(minutes=90), "0.9900", "4.00"),
    trade(3, OPEN1 + timedelta(minutes=95), "0.9800", "50.00"),  # below 99c: never counts
    trade(4, OPEN1 + timedelta(minutes=120), "0.9900", "6.00"),
]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path, q = request.url.path, request.url.params
        if path.endswith("/historical/markets"):
            assert q["series_ticker"] == "KXHIGHNY"
            if q.get("cursor", "") == "":
                return httpx.Response(200, json={"markets": MARKETS[:2], "cursor": "p2"})
            return httpx.Response(200, json={"markets": MARKETS[2:], "cursor": ""})
        if path.endswith("/candlesticks"):
            open_ = OPEN1 if D1 in path else OPEN2
            body = candles_for(open_, int(q["start_ts"]), int(q["end_ts"]))
            return httpx.Response(200, json={"candlesticks": body})
        if path.endswith("/historical/trades"):
            if D1 not in q["ticker"]:
                return httpx.Response(200, json={"trades": [], "cursor": ""})
            if q.get("cursor", "") == "":
                return httpx.Response(200, json={"trades": TRADES[2:][::-1], "cursor": "n"})
            return httpx.Response(200, json={"trades": TRADES[:2][::-1], "cursor": ""})
        return httpx.Response(404, json={"error": "not found"})

    monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(handler))
    yield seen


def client(tmp_path: Path) -> HistoryClient:
    return HistoryClient(ArchiveClient(tmp_path / "cache", sleep=lambda _: None))


def imported(tmp_path: Path) -> HistoryDb:
    db = HistoryDb(tmp_path / "history.db")
    c = client(tmp_path)
    import_history(c, db, ["KXHIGHNY"], date(2026, 1, 1), date(2026, 2, 28), utc(2026, 11, 1))
    c.close()
    return db


# --- import ---------------------------------------------------------------------------------


def test_import_writes_changed_quotes_settlements_and_prints(
    api: list[httpx.Request], tmp_path: Path
) -> None:
    db = HistoryDb(tmp_path / "history.db")
    c = client(tmp_path)
    res = import_history(c, db, ["KXHIGHNY"], date(2026, 1, 1), date(2026, 2, 28), utc(2026, 11, 1))
    assert (res.markets_listed, res.markets_in_window, res.imported) == (3, 2, 2)
    assert res.unsettled == 1 and res.problems == []
    # 241 candles each (2 at or after close dropped), 2 distinct quotes stored
    assert res.candles == 2 * 241 and res.snapshots == 4
    assert res.prints == 4
    counts = db.counts()
    assert counts["markets"] == 2 and counts["settled"] == 1 and counts["prints"] == 4
    s = db.store.settlement(f"KXHIGHNY-{D1}-B40.5")
    assert s is not None and s.result == "no"
    assert db.store.settlement(f"KXHIGHNY-{D2}-B40.5") is None  # no yes/no archived
    snaps = list(db.store.iter_snapshots(["KXHIGHNY"]))
    assert all(s.depth == {} for s in snaps)
    first = snaps[0]
    assert (first.yes_bid, first.yes_ask, first.no_bid, first.no_ask) == (0.08, 0.1, 0.9, 0.92)
    assert is_history_db(tmp_path / "history.db")
    # resume: a second run asks only for the listing and imports nothing
    n = len(api)
    again = import_history(
        c, db, ["KXHIGHNY"], date(2026, 1, 1), date(2026, 2, 28), utc(2026, 11, 1)
    )
    assert again.imported == 0 and again.skipped_existing == 2
    assert len(api) == n  # listing pages come from the raw cache
    c.close()
    db.close()


def test_candles_are_split_into_requests_of_at_most_max_candles(
    api: list[httpx.Request], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch, "MAX_CANDLES", 100)
    c = client(tmp_path)
    got = c.candles(f"KXHIGHNY-{D1}-B40.5", OPEN1, OPEN1 + timedelta(hours=4, minutes=1))
    c.close()
    asks = [r for r in api if r.url.path.endswith("/candlesticks")]
    assert len(asks) == 3
    for r in asks:
        assert (int(r.url.params["end_ts"]) - int(r.url.params["start_ts"])) // 60 < 100
    assert len(got) == 241 and len({x["end_period_ts"] for x in got}) == 241


def test_api_error_is_reported_and_market_left_for_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/historical/markets"):
            return httpx.Response(200, json={"markets": MARKETS[:1], "cursor": ""})
        return httpx.Response(404, json={"error": "gone"})

    monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(handler))
    db = HistoryDb(tmp_path / "h.db")
    c = client(tmp_path)
    res = import_history(c, db, ["KXHIGHNY"], date(2026, 1, 1), date(2026, 1, 31), utc(2026, 11, 1))
    assert res.imported == 0 and len(res.problems) == 1
    assert not db.has_market(f"KXHIGHNY-{D1}-B40.5")
    c.close()
    db.close()


def test_snapshots_drop_candles_outside_the_market_and_mirror_no_quotes() -> None:
    m = HistoryMarket(
        "KXHIGHNY-26JAN10-B40.5", "KXHIGHNY", "KXHIGHNY-26JAN10", date(2026, 1, 10),
        OPEN1, OPEN1 + timedelta(hours=1), "between", 40, 41, "no", None, "",
    )  # fmt: skip
    cs = [
        candle(OPEN1 - timedelta(minutes=1), "0.10", "0.20"),
        candle(OPEN1 + timedelta(minutes=1), "0.00", "1.00"),  # no bid, no ask
        candle(OPEN1 + timedelta(minutes=2), "0.00", "1.00"),  # unchanged: not stored
        candle(OPEN1 + timedelta(minutes=3), "0.03", "0.05"),
        candle(OPEN1 + timedelta(hours=1), "0.50", "0.60"),  # at close: dropped
    ]
    snaps = candle_snapshots(m, cs)
    assert [s.ts for s in snaps] == [OPEN1 + timedelta(minutes=1), OPEN1 + timedelta(minutes=3)]
    assert (snaps[0].yes_bid, snaps[0].yes_ask, snaps[0].no_bid, snaps[0].no_ask) == (
        None, None, None, None,
    )  # fmt: skip
    assert (snaps[1].no_bid, snaps[1].no_ask) == (0.95, 0.97)


def test_parse_print_skips_unusable_rows() -> None:
    assert parse_print({**TRADES[0], "count_fp": "0.00"}) is None
    p = parse_print(TRADES[0])
    assert p is not None and p.price("no") == 0.99 and p.taker_side == "no"


# --- source ---------------------------------------------------------------------------------


def test_history_source_fills_minutes_and_filters_by_market_date(
    api: list[httpx.Request], tmp_path: Path
) -> None:
    db = imported(tmp_path)
    src = HistorySource(db, date(2026, 1, 10), date(2026, 1, 10))
    ticks = list(src.ticks(["KXHIGHNY"]))
    # every minute of the market's life that has a quote, D2 left out by date
    assert len(ticks) == 239
    assert all(len(t.snapshots) == 1 and t.snapshots[0].ts == t.ts for t in ticks)
    assert {t.snapshots[0].ticker for t in ticks} == {f"KXHIGHNY-{D1}-B40.5"}
    assert ticks[30].snapshots[0].yes_bid == 0.08 and ticks[100].snapshots[0].yes_bid == 0.05
    db.close()


def test_traded_volume_counts_prints_at_or_above_price_after_placement(
    api: list[httpx.Request], tmp_path: Path
) -> None:
    db = imported(tmp_path)
    src = HistorySource(db)
    t = f"KXHIGHNY-{D1}-B40.5"
    assert src.traded_volume(t, "no", 0.99, OPEN1, OPEN1 + timedelta(hours=4)) == 14
    # the print at placement time and earlier don't count; (after, until]
    assert (
        src.traded_volume(
            t, "no", 0.99, OPEN1 + timedelta(minutes=30), OPEN1 + timedelta(minutes=90)
        )
        == 4
    )
    assert src.traded_volume(t, "no", 0.98, OPEN1, OPEN1 + timedelta(minutes=100)) == 58
    assert src.traded_volume(t, "yes", 0.05, OPEN1, OPEN1 + timedelta(hours=4)) == 0
    db.close()


# --- trade_through fill model ------------------------------------------------------------------


def _trade(opened: datetime) -> Trade:
    return Trade("p", "s", "W", "KXHIGHNY-26JAN10-B40.5", "no", 0.92, 10, 0.06, "paper", opened,
                 opened + timedelta(hours=4))  # fmt: skip


def _snap(ts: datetime) -> MarketSnapshot:
    return MarketSnapshot(ts, "KXHIGHNY", "W", "KXHIGHNY-26JAN10-B40.5", OPEN1,
                          OPEN1 + timedelta(hours=4), 0.01, 0.02, 0.98, 0.99)  # fmt: skip


def test_trade_through_fills_once_prints_cover_the_position() -> None:
    from nem.core.config import TakeProfitConfig

    tp = TakeProfitConfig(price=0.99, fill_model="trade_through", maker_fee_rate=0.0)
    calls: list[tuple[Any, ...]] = []

    def traded(*a: Any) -> float:
        calls.append(a)
        return 9.0 if a[4] < OPEN1 + timedelta(hours=2) else 10.0

    t = _trade(OPEN1)
    assert take_profit_fill(t, tp, _snap(OPEN1 + timedelta(hours=1)), traded) is None
    fill = take_profit_fill(t, tp, _snap(OPEN1 + timedelta(hours=2)), traded)
    assert fill is not None and fill.price == 0.99 and fill.qty == 10 and fill.fee == 0
    assert calls[0][:4] == (t.ticker, "no", 0.99, OPEN1)
    assert take_profit_fill(t, tp, _snap(OPEN1), traded) is None  # entry book never fills
    with pytest.raises(ValueError, match="trade prints"):
        take_profit_fill(t, tp, _snap(OPEN1 + timedelta(hours=2)))


def _hist_strategy(name: str, series: str = "KXHIGHNY", **kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "name": name,
        "series": series,
        "taker_fee_rate": 0.07,
        "signal": {
            "type": "extreme_favorite",
            "threshold": 0.85,
            "priors": {"no": {"wins": 95, "n": 100}},
        },
        "gates": [{"type": "max_entry_price", "value": 0.94}],
        "sizing": {"type": "fixed", "contracts": 10},
    }
    d.update(kw)
    return d


TP99 = {"take_profit": {"price": 0.99, "fill_model": "trade_through", "maker_fee_rate": 0.0}}


def test_runner_refuses_trade_through_without_prints() -> None:
    p = portfolio(strategies=[_hist_strategy("ny", exit=TP99)])
    with pytest.raises(BuildError, match="trade prints"):
        Runner([p], FakeSource([]), Store(), ManualClock(OPEN1))


# --- backtest ----------------------------------------------------------------------------------


def test_replay_enters_at_minute_ask_and_trade_through_exits(
    api: list[httpx.Request], tmp_path: Path
) -> None:
    db = imported(tmp_path)
    p = PortfolioConfig.model_validate(
        portfolio_dict(
            name="h", check_every="5m",
            strategies=[_hist_strategy("hold"), _hist_strategy("tp", exit=TP99)],
        )
    )  # fmt: skip
    period = DatePeriod(date(2026, 1, 10), date(2026, 1, 10))
    with Store() as results:
        replay(db, [p], period, results)
        trades = {t.strategy: t for t in results.settled_trades()}
        hold, tp = trades["hold"], trades["tp"]
        # NO ask 1 - 0.08 = 0.92 at the first minute; fee ceil(0.07*10*0.92*0.08)
        assert (hold.avg_price, hold.qty, hold.fee) == (0.92, 10, 0.06)
        assert hold.opened_at == OPEN1 + timedelta(minutes=1)
        assert hold.won and hold.realized_pnl == pytest.approx(10 - 9.26)
        # 4 + 4 + 6 contracts at 99c after entry: the 10th contract prints at minute 120
        assert tp.settled_at == OPEN1 + timedelta(minutes=120)
        assert tp.realized_pnl == pytest.approx(9.9 - 9.26)
        r = period_report(results, p, period, "train")
    assert r.held.n == 2 and r.held.wins == 2
    assert r.verdict in ("undecided", "no_edge")
    text = render_period(r)
    assert "[historical candles, no depth]" in text and "take_profit" in text
    db.close()


def test_labels_name_both_provenances() -> None:
    gate = {"type": "source_agreement", "backfill_sources": ["iem_mos_gfs"], "min_sources": 1}
    p = portfolio(strategies=[_hist_strategy("ny", gates=[gate])])
    assert labels([p]) == "[backfilled forecasts] [historical candles, no depth]"
    assert (
        labels([portfolio(strategies=[_hist_strategy("ny")])]) == "[historical candles, no depth]"
    )


def test_expand_portfolio_applies_each_path_to_every_strategy() -> None:
    p = portfolio(strategies=[_hist_strategy("ny"), _hist_strategy("phil", series="KXHIGHPHIL")])
    out = expand_portfolio(p, {"gates.0.value": [0.9, 0.94]})
    assert [v.name for v, _ in out] == ["v00", "v01"]
    for (v, q), value in zip(out, (0.9, 0.94), strict=True):
        assert [s.name for s in q.strategies] == ["ny", "phil"]
        assert all(s.gates[0].params["value"] == value for s in q.strategies)
        assert q.name == f"research_{v.name}"


def test_sweep_and_suggest_on_history(api: list[httpx.Request], tmp_path: Path) -> None:
    db = imported(tmp_path)
    p = PortfolioConfig.model_validate(
        portfolio_dict(name="h", check_every="5m", strategies=[_hist_strategy("ny")])
    )
    grid: dict[str, Any] = {"gates.0.value": [0.90, 0.94]}
    train = DatePeriod(date(2026, 1, 10), date(2026, 1, 10))
    test = DatePeriod(date(2026, 1, 11), date(2026, 1, 11))
    res = sweep(db, p, grid, [train, test])
    assert [r.stats.n for r in res[train]] == [0, 1]  # 0.92 is above a 0.90 cap
    assert [r.stats.n for r in res[test]] == [0, 0]  # unsettled market: no result archived
    s = suggest(grid, res[train], res[test], min_trades=1)
    # v01's only neighbour (v00) has no trades, so "stable" can't be judged
    assert s.status == "insufficient" and s.variant is None
    db.close()


def test_date_bets_lose_a_date_when_any_trade_lost() -> None:
    def t(window: str, strategy: str, won: bool) -> Trade:
        return Trade("p", strategy, window, window + "-B1", "no", 0.9, 10, 0.06, "paper",
                     OPEN1, OPEN1, won, 1.0 if won else -9.0, OPEN1)  # fmt: skip

    trades = [t("KXHIGHNY-26JAN10", "ny", True), t("KXHIGHPHIL-26JAN10", "phil", False),
              t("KXHIGHNY-26JAN11", "ny", True)]  # fmt: skip
    bets = date_bets(trades, {})
    assert [(b.day, b.n_trades, b.won) for b in bets] == [
        (date(2026, 1, 10), 2, False), (date(2026, 1, 11), 1, True),
    ]  # fmt: skip
    assert bets[0].realized_pnl == pytest.approx(-8.0) and bets[0].edge == pytest.approx(-0.4)
    st = date_stats(bets)
    assert (st.n, st.lost) == (2, 1) and st.edge == pytest.approx((-0.4 + 0.1) / 2)
    assert st.ci is not None and st.edge is not None and st.ci.low < st.edge < st.ci.high
    assert st.decision == "undecided"
    assert date_stats([]).decision == "undecided"


def test_print_price_by_side() -> None:
    p = Print("T", "x", OPEN1, 0.03, 0.97, 1.0, None)
    assert (p.price("yes"), p.price("no")) == (0.03, 0.97)


def test_cli_keeps_history_and_recorded_data_apart(
    api: list[httpx.Request], tmp_path: Path
) -> None:
    from nem.cli import main
    from nem.store import Store

    imported(tmp_path).close()
    hist = str(tmp_path / "history.db")
    (tmp_path / "pf").mkdir()
    with pytest.raises(SystemExit, match="history file"):
        main(["replay", "--data", hist, "--dir", str(tmp_path / "pf")])
    recorded = tmp_path / "nem.db"
    with Store(recorded) as s:
        s.insert_snapshot(_snap(OPEN1))
    with pytest.raises(SystemExit, match="recorded data"):
        main([
            "import", "kalshi-history", "--series", "KXHIGHNY", "--db", str(recorded),
            "--from", "2026-01-01", "--to", "2026-01-31",
        ])  # fmt: skip
    with pytest.raises(SystemExit, match="not a history file"):
        main(["history", "replay", "--data", str(recorded), "--from", "2026-01-10",
              "--to", "2026-01-10", "--dir", str(tmp_path / "pf")])  # fmt: skip


def test_import_can_keep_only_some_strike_types(api: list[httpx.Request], tmp_path: Path) -> None:
    db = HistoryDb(tmp_path / "history.db")
    c = client(tmp_path)
    window = (date(2026, 1, 1), date(2026, 2, 28), utc(2026, 11, 1))
    none = import_history(c, db, ["KXHIGHNY"], *window, strike_types=["less"])
    assert (none.markets_listed, none.markets_in_window, none.imported) == (3, 0, 0)
    assert not any(r.url.path.endswith("/candlesticks") for r in api)  # nothing fetched
    some = import_history(c, db, ["KXHIGHNY"], *window, strike_types=["between", "less"])
    assert (some.markets_in_window, some.imported) == (2, 2)
    with pytest.raises(ValueError, match="unknown strike types"):
        import_history(c, db, ["KXHIGHNY"], *window, strike_types=["lowest"])
    c.close()
    db.close()
