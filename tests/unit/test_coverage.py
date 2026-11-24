"""`nem series uncovered`: Kalshi series with open markets that no strategy trades yet."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from nem.market.coverage import uncovered

from kalshi_mock import Recording, mock_client

# synthetic series list; KXHIGHTZZZ has no station mapping, KXHIGHTOLD has no open markets
SERIES: list[dict[str, Any]] = [
    {"ticker": "KXHIGHNY", "title": "Highest temperature in NYC",
     "settlement_sources": [{"name": "The Weather Company"}]},
    {"ticker": "KXHIGHTEWR", "title": "Highest temperature in Newark",
     "settlement_sources": [{"name": "The Weather Company"}]},
    {"ticker": "KXHIGHTZZZ", "title": "Highest temperature in Nowhere", "settlement_sources": []},
    {"ticker": "KXHIGHTOLD", "title": "Retired", "settlement_sources": []},
    {"ticker": "KXBTC15M", "title": "Bitcoin 15 minutes", "settlement_sources": []},
]  # fmt: skip
OPEN = {"KXHIGHNY": 12, "KXHIGHTEWR": 12, "KXHIGHTZZZ": 6, "KXHIGHTOLD": 0, "KXBTC15M": 4}


def _market(series: str, i: int) -> dict[str, Any]:
    t = f"{series}-26NOV22-T{60 + i}"
    return {
        "ticker": t, "event_ticker": f"{series}-26NOV22", "status": "active",
        "open_time": "2026-11-21T15:00:00Z", "close_time": "2026-11-23T05:00:00Z",
        "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.42",
        "no_bid_dollars": "0.58", "no_ask_dollars": "0.60", "strike_type": "greater",
        "floor_strike": 60 + i,
    }  # fmt: skip


def routes(request: httpx.Request) -> httpx.Response:
    path = request.url.path.removeprefix("/trade-api/v2")
    if path == "/series":
        return httpx.Response(200, json={"series": SERIES})
    if path == "/markets":
        s = request.url.params["series_ticker"]
        return httpx.Response(200, json={"markets": [_market(s, i) for i in range(OPEN[s])]})
    return httpx.Response(404, json={"error": "not found"})


def test_lists_open_unused_series_matching_a_prefix() -> None:
    rec = Recording(routes)
    got = uncovered(mock_client(rec), ["KXHIGH"], used={"KXHIGHNY"})
    assert [u.series for u in got] == ["KXHIGHTEWR", "KXHIGHTZZZ"]
    ewr, zzz = got
    assert (ewr.open_markets, ewr.settlement, ewr.mapped) == (12, "The Weather Company", True)
    assert (zzz.mapped, zzz.settlement) == (False, "")
    # open markets are looked up only for candidates, never for series already traded
    asked = {
        r.url.params.get("series_ticker") for r in rec.requests if r.url.path.endswith("/markets")
    }
    assert asked == {"KXHIGHTEWR", "KXHIGHTZZZ", "KXHIGHTOLD"}


def test_nothing_uncovered() -> None:
    used = {"KXHIGHNY", "KXHIGHTEWR", "KXHIGHTZZZ"}
    assert uncovered(mock_client(routes), ["KXHIGH"], used=used) == []


def test_cli_exit_code_flags_new_series(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from nem import cli

    (tmp_path / "p.yaml").write_text(
        "name: p\nmode: paper\nstarting_balance: 100\nstrategies:\n"
        "  - name: ny\n    series: KXHIGHNY\n    signal: {type: extreme_favorite, threshold: 0.9}\n"
        "    sizing: {type: fixed, contracts: 1}\n"
    )
    monkeypatch.setattr(cli, "_public_client", lambda: mock_client(routes))
    assert cli.main(["series", "uncovered", "--dir", str(tmp_path), "--prefix", "KXHIGH"]) == 1
    out = capsys.readouterr().out
    assert "KXHIGHTEWR" in out and "KXHIGHTZZZ" in out and "KXHIGHNY " not in out
    assert "no station mapping" in out
    assert cli.main(["series", "uncovered", "--dir", str(tmp_path), "--prefix", "KXBTC"]) == 1
    assert cli.main(["series", "uncovered", "--dir", str(tmp_path), "--prefix", "KXETH"]) == 0
