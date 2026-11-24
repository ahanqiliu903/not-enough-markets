"""Fee rates from the series' fee schedule (GET /series/{ticker}). Offline, synthetic."""

from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from nem.cli import main
from nem.core.config import load_portfolio
from nem.engine.runtime import BuildError, build_strategies
from nem.execution.fees import FeeScheduleUnknown, series_maker_rate, series_taker_rate
from nem.market.fees import FeeLookupError, resolve_fees
from nem.market.kalshi import KalshiClient, KalshiError, SeriesFees, parse_series_fees

from engine_helpers import portfolio, strategy
from factories import portfolio_dict
from kalshi_mock import Recording, default_routes, mock_client

TP: dict[str, Any] = {"take_profit": {"price": 0.97, "fill_model": "realistic"}}


def series_routes(fee_type: str, multiplier: object = 1) -> Recording:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/trade-api/v2")
        if path.startswith("/series/"):
            body = {"ticker": path.removeprefix("/series/"), "fee_type": fee_type,
                    "fee_multiplier": multiplier}  # fmt: skip
            return httpx.Response(200, json={"series": body})
        return default_routes(request)

    return Recording(handler)


def lookup_of(fee_type: str, multiplier: float = 1.0) -> tuple[list[str], Any]:
    asked: list[str] = []

    def lookup(series: str) -> SeriesFees:
        asked.append(series)
        return SeriesFees(series, fee_type, multiplier)

    return asked, lookup


@pytest.mark.parametrize(
    ("fee_type", "mult", "taker", "maker"),
    [
        ("quadratic", 1, 0.07, 0.0),
        ("quadratic", 0.5, 0.035, 0.0),
        ("quadratic_with_maker_fees", 1, 0.07, 0.0175),
        ("quadratic_with_maker_fees", 2, 0.14, 0.035),
    ],
)
def test_series_rates(fee_type: str, mult: float, taker: float, maker: float) -> None:
    assert series_taker_rate(fee_type, mult) == pytest.approx(taker)
    assert series_maker_rate(fee_type, mult) == pytest.approx(maker)


@pytest.mark.parametrize("fee_type", ["flat", "something_new"])
def test_unmodelled_fee_types_are_refused(fee_type: str) -> None:
    with pytest.raises(FeeScheduleUnknown):
        series_taker_rate(fee_type, 1)
    with pytest.raises(FeeScheduleUnknown):
        series_maker_rate(fee_type, 1)


def test_client_reads_series_fees() -> None:
    rec = series_routes("quadratic_with_maker_fees", "0.5")
    fees = mock_client(rec).series_fees("KXEXAMPLE")
    assert fees == SeriesFees("KXEXAMPLE", "quadratic_with_maker_fees", 0.5)
    assert [r.url.path for r in rec.requests] == ["/trade-api/v2/series/KXEXAMPLE"]


@pytest.mark.parametrize("body", [{}, {"fee_type": "quadratic"}, {"fee_multiplier": 1}])
def test_missing_fee_fields_raise(body: dict[str, Any]) -> None:
    with pytest.raises(KalshiError, match="fee_type"):
        parse_series_fees(body)


def test_null_rates_default() -> None:
    s = portfolio(strategies=[strategy(exit=TP)]).strategies[0]
    assert s.taker_fee_rate is None and s.taker_rate == 0.07
    assert s.exit is not None and s.exit.take_profit is not None
    assert s.exit.take_profit.maker_fee_rate is None


@pytest.mark.parametrize(
    ("fee_type", "mult", "taker", "maker"),
    [("quadratic", 1.0, 0.07, 0.0), ("quadratic_with_maker_fees", 0.5, 0.035, 0.00875)],
)
def test_resolve_fills_nulls(fee_type: str, mult: float, taker: float, maker: float) -> None:
    asked, lookup = lookup_of(fee_type, mult)
    p = portfolio(strategies=[strategy(exit=TP), strategy(name="other", exit=TP)])
    (resolved,) = resolve_fees([p], lookup)
    assert asked == ["KXBTC15M"]  # one lookup per series
    for s in resolved.strategies:
        assert s.taker_fee_rate == pytest.approx(taker)
        assert s.exit is not None and s.exit.take_profit is not None
        assert s.exit.take_profit.maker_rate == pytest.approx(maker)
        assert s.exit.take_profit.price == 0.97  # the rest is untouched
    assert p.strategies[0].taker_fee_rate is None  # the input isn't mutated
    assert len(build_strategies([resolved])) == 2


def test_explicit_rates_win_and_are_not_looked_up() -> None:
    asked, lookup = lookup_of("quadratic_with_maker_fees", 2.0)
    exit = {"take_profit": {**TP["take_profit"], "maker_fee_rate": 0.01}}
    p = portfolio(strategies=[strategy(exit=exit, taker_fee_rate=0.05)])
    (resolved,) = resolve_fees([p], lookup)
    assert asked == []
    s = resolved.strategies[0]
    assert s.taker_fee_rate == 0.05
    assert s.exit is not None and s.exit.take_profit is not None
    assert s.exit.take_profit.maker_fee_rate == 0.01


def test_taker_false_only_looks_up_maker_rates() -> None:
    asked, lookup = lookup_of("quadratic")
    held = portfolio()
    assert resolve_fees([held], lookup, taker=False) == [held]
    assert asked == []
    (resolved,) = resolve_fees([portfolio(strategies=[strategy(exit=TP)])], lookup, taker=False)
    assert asked == ["KXBTC15M"]
    assert resolved.strategies[0].taker_fee_rate is None


def test_retired_strategies_are_not_looked_up() -> None:
    asked, lookup = lookup_of("quadratic")
    resolve_fees([portfolio(strategies=[strategy(status="retired", exit=TP)])], lookup)
    assert asked == []


@pytest.mark.parametrize("fee_type", ["flat", "unknown"])
def test_resolve_fails_closed_on_unmodelled_schedules(fee_type: str) -> None:
    _, lookup = lookup_of(fee_type)
    with pytest.raises(FeeLookupError, match=r"research/fav90 \(KXBTC15M\).*maker_fee_rate"):
        resolve_fees([portfolio(strategies=[strategy(exit=TP)])], lookup)


def test_resolve_fails_closed_when_the_series_cant_be_read() -> None:
    def missing(series: str) -> SeriesFees:
        raise KalshiError(f"GET /series/{series}: HTTP 404")

    with pytest.raises(FeeLookupError, match="taker_fee_rate"):
        resolve_fees([portfolio()], missing)


def test_unresolved_maker_rate_refuses_to_build() -> None:
    """Replay and suggest never look rates up: a null maker rate stops them."""
    with pytest.raises(BuildError, match="maker_fee_rate is unknown"):
        build_strategies([portfolio(strategies=[strategy(exit=TP)])])


def _write(tmp_path: Path, exit: object) -> Path:
    d = portfolio_dict()
    d["strategies"][0]["exit"] = exit
    directory = tmp_path / "portfolios"
    directory.mkdir()
    (directory / "research.yaml").write_text(yaml.safe_dump(d))
    return directory


def _offline(monkeypatch: pytest.MonkeyPatch, rec: Recording) -> None:
    import nem.market.kalshi

    def client(_env: str) -> KalshiClient:
        return mock_client(rec)

    monkeypatch.setattr(nem.market.kalshi, "KalshiClient", client)


def test_validate_reads_the_maker_rate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = series_routes("quadratic")
    _offline(monkeypatch, rec)
    directory = _write(tmp_path, TP)
    assert main(["validate", "--dir", str(directory)]) == 0
    assert [r.url.path for r in rec.requests] == ["/trade-api/v2/series/KXBTC15M"]
    assert load_portfolio(directory / "research.yaml").strategies[0].exit is not None


def test_validate_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _offline(monkeypatch, series_routes("flat"))
    assert main(["validate", "--dir", str(_write(tmp_path, TP))]) == 1
    err = capsys.readouterr().err
    assert "fee_type 'flat' is not modelled" in err
    assert "exit.take_profit.maker_fee_rate explicitly" in err


def test_validate_without_take_profit_stays_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = series_routes("quadratic")
    _offline(monkeypatch, rec)
    assert main(["validate", "--dir", str(_write(tmp_path, None))]) == 0
    assert rec.requests == []


def test_resolved_taker_rate_reaches_the_broker() -> None:
    _, lookup = lookup_of("quadratic", 0.5)
    (rt,) = build_strategies(resolve_fees([portfolio()], lookup))
    assert rt.broker.taker_fee_rate == pytest.approx(0.035)


def test_run_refuses_an_unmodelled_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = series_routes("flat")
    _offline(monkeypatch, rec)
    directory = _write(tmp_path, None)
    args = ["run", "--once", "--dir", str(directory), "--db", str(tmp_path / "nem.db")]
    assert main(args) == 1  # taker rate null and the schedule isn't modelled: no start
    assert "taker_fee_rate explicitly" in capsys.readouterr().err
