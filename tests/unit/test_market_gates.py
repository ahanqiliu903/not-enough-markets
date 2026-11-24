"""strike_filter and market_day_window gates, on synthetic snapshots only."""

from datetime import UTC, datetime
from typing import Any

import pytest

from nem.core.config import PluginSpec
from nem.core.registry import REGISTRY, PluginError
from nem.gates.base import Gate
from nem.store import Store

from engine_helpers import context, portfolio
from factories import make_signal, make_snapshot


def gate(type_: str, **params: object) -> Gate:
    return REGISTRY.build("gate", PluginSpec.model_validate({"type": type_, **params}), Gate)


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)  # pyright: ignore[reportArgumentType]


OPEN = utc(2026, 1, 23, 15)
CLOSE = utc(2026, 1, 25, 4, 59)


def weather(series: str = "KXHIGHNY", day: str = "26JAN24", **kw: Any) -> Any:
    fields: dict[str, Any] = {
        "series": series,
        "window_id": f"{series}-{day}",
        "ticker": f"{series}-{day}-T20",
        "open_time": OPEN,
        "close_time": CLOSE,
        "strike_type": "less",
        "cap_strike": 20.0,
    }
    fields.update(kw)
    return make_snapshot(**fields)


def decide(g: Gate, now: datetime, snap: Any) -> str:
    return g.check(make_signal(side="no"), snap, context(portfolio(), Store(), now)).reason


# --- strike_filter ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("strike", "reason"),
    [("less", "ok"), ("between", "strike_filter:between"), ("greater", "strike_filter:greater"),
     (None, "strike_filter:unknown")],
)  # fmt: skip
def test_strike_filter_keeps_only_listed_strike_types(strike: str | None, reason: str) -> None:
    g = gate("strike_filter", strike_types=["less"])
    assert decide(g, OPEN, weather(strike_type=strike)) == reason


def test_strike_filter_unknown_strike_blocks_even_when_failing_open() -> None:
    g = gate("strike_filter", strike_types=["less"], fail_open=True)
    assert decide(g, OPEN, weather(strike_type=None)) == "strike_filter:unknown"


def test_strike_filter_params_validated() -> None:
    with pytest.raises(PluginError):
        gate("strike_filter", strike_types=[])
    with pytest.raises(PluginError):
        gate("strike_filter", strike_types=["lowest"])


# --- market_day_window --------------------------------------------------------------------

# NYC (America/New_York, EST = UTC-5 in January). Market date Jan 24.
DAY_BEFORE = {"until_day": 0, "until_time": "00:00"}
THROUGH_10AM = {"until_day": 0, "until_time": "10:00"}


@pytest.mark.parametrize(
    ("params", "now", "reason"),
    [
        (DAY_BEFORE, utc(2026, 1, 23, 15), "ok"),  # 10:00 EST the day before
        (DAY_BEFORE, utc(2026, 1, 24, 4, 59), "ok"),  # 23:59 EST the day before
        (DAY_BEFORE, utc(2026, 1, 24, 5), "market_day_window:too_late"),  # midnight
        (THROUGH_10AM, utc(2026, 1, 24, 14, 59), "ok"),  # 09:59 EST on the day
        (THROUGH_10AM, utc(2026, 1, 24, 15), "market_day_window:too_late"),
        ({"from_day": -1, "from_time": "18:00", **DAY_BEFORE}, utc(2026, 1, 23, 22, 59),
         "market_day_window:too_early"),
        ({"from_day": -1, "from_time": "18:00", **DAY_BEFORE}, utc(2026, 1, 23, 23), "ok"),
    ],
)  # fmt: skip
def test_window_is_relative_to_the_market_date_in_station_time(
    params: dict[str, object], now: datetime, reason: str
) -> None:
    assert decide(gate("market_day_window", **params), now, weather()) == reason


def test_window_uses_each_series_own_zone_and_daylight_time() -> None:
    g = gate("market_day_window", **THROUGH_10AM)
    # Seattle (America/Los_Angeles), PDT = UTC-7 on May 10: 10:00 local = 17:00Z
    sea = weather("KXHIGHTSEA", "26MAY10")
    assert decide(g, utc(2026, 5, 10, 16, 59), sea) == "ok"
    assert decide(g, utc(2026, 5, 10, 17), sea) == "market_day_window:too_late"
    # the same instant is already past 10:00 in New York (EDT, 13:00 local)
    assert decide(g, utc(2026, 5, 10, 16, 59), weather(day="26MAY10")) == (
        "market_day_window:too_late"
    )


def test_explicit_zone_overrides_the_station() -> None:
    g = gate("market_day_window", tz="America/Los_Angeles", **DAY_BEFORE)
    # 01:00 EST Jan 24 = 22:00 PST Jan 23: still the day before in the given zone
    assert decide(g, utc(2026, 1, 24, 6), weather()) == "ok"


def test_window_blocks_markets_it_cannot_place() -> None:
    g = gate("market_day_window", fail_open=True, **DAY_BEFORE)
    assert decide(g, OPEN, weather("KXNOTMAPPED")) == "market_day_window:unknown_zone"
    undated = make_snapshot(window_id="KXHIGHNY-SOMETHING", ticker="KXHIGHNY-SOMETHING-T20")
    assert decide(g, OPEN, undated) == "market_day_window:no_date"


@pytest.mark.parametrize(
    "params",
    [
        {"until_time": "10"},
        {"until_time": 600},  # what YAML 1.1 makes of an unquoted 10:00
        {"until_time": "24:00"},
        {"tz": "Mars/Olympus"},
        {"from_day": 0, "from_time": "10:00", "until_day": 0, "until_time": "10:00"},
        {"from_day": 0, "until_day": -1},
    ],
)
def test_window_params_validated(params: dict[str, object]) -> None:
    with pytest.raises(PluginError):
        gate("market_day_window", **params)


def test_market_gates_example_loads_and_builds() -> None:
    from pathlib import Path

    from nem.core.config import load_portfolio
    from nem.engine.runtime import build_strategies

    root = Path(__file__).resolve().parents[2]
    p = load_portfolio(root / "examples/weather/market_gates.yaml")
    (rt,) = build_strategies([p])
    assert [g.name for g in rt.gates][1:] == ["strike_filter", "market_day_window"]
