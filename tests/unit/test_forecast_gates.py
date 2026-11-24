"""Forecast gates (source_agreement, bracket_distance, sky_conditions) and ctx.forecasts.

Synthetic data only: a fictional series (KXHIGHFAKE) on a future date."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from nem.backfill.model import BackfillRow
from nem.core.clock import ManualClock
from nem.core.config import PluginSpec, StrategyConfig
from nem.core.context import Context, DataUnavailable
from nem.core.registry import REGISTRY, PluginError
from nem.core.types import FeedValue, MarketSnapshot, Side
from nem.engine.runtime import BuildError, build_strategies
from nem.engine.step import check_gate
from nem.gates.base import Gate
from nem.gates.forecast import distance_to_loss
from nem.stats.provenance import uses_backfill
from nem.store import Store

from engine_helpers import portfolio, strategy
from factories import make_signal, make_snapshot

DAY = date(2031, 1, 15)
EVENT = "KXHIGHFAKE-31JAN15"
NOW = datetime(2031, 1, 15, 3, 0, tzinfo=UTC)  # 22:00 EST the evening before
FEEDS = [
    {"name": f"f{i}", "type": "nws_forecast", "lat": 40.0, "lon": -74.0, "max_age": "12h"}
    for i in (1, 2, 3)
]
FAKE = {"field": "high", "station": "CLINYC"}  # KXHIGHFAKE has no station mapping


def snap(strike_type: str, floor: float | None = None, cap: float | None = None) -> MarketSnapshot:
    return make_snapshot(
        series="KXHIGHFAKE", window_id=EVENT, ticker=f"{EVENT}-X",
        strike_type=strike_type, floor_strike=floor, cap_strike=cap,
    )  # fmt: skip


BETWEEN = snap("between", 40, 41)  # B40.5: YES at 40 or 41


def ctx_with(
    feeds: dict[str, dict[str, float]] | None = None,
    backfill: list[BackfillRow] | None = None,
    now: datetime = NOW,
    known_at: datetime | None = None,
) -> Context:
    store = Store()
    for name, kv in (feeds or {}).items():
        at = known_at or now - timedelta(hours=1)
        store.record_feed_values([FeedValue(name, k, v, at) for k, v in kv.items()], now)
    store.save_backfill(backfill or [])
    p = portfolio(feeds=FEEDS)
    return Context(p, p.strategy("fav90"), ManualClock(now), store)


def highs(*values: float, key: str = "high") -> dict[str, dict[str, float]]:
    return {f"f{i + 1}": {f"{DAY.isoformat()}/{key}": v} for i, v in enumerate(values)}


def brow(
    source: str,
    value: float,
    run: datetime,
    field: Any = "high",
    lag: timedelta = timedelta(hours=5),
) -> BackfillRow:
    return BackfillRow(
        source=source, station="CLINYC", target_date=DAY, field=field, value=value,
        run_time=run, valid_from=datetime(2031, 1, 15, 12, tzinfo=UTC), known_at=run + lag,
        run_id=f"{source} {run:%Y-%m-%dT%HZ}", fetched_at=datetime(2031, 2, 1, tzinfo=UTC),
    )  # fmt: skip


def gate(**spec: object) -> Gate:
    return REGISTRY.build("gate", PluginSpec.model_validate(spec), Gate)


def agreement(**kw: object) -> Gate:
    return gate(type="source_agreement", feeds=["f1", "f2", "f3"], **FAKE, **kw)


# --- source_agreement ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("market", "side", "values", "take"),
    [
        (BETWEEN, "no", (45, 46, 44), True),
        (BETWEEN, "no", (45, 41.4, 44), False),  # 41.4 rounds to 41: inside the bracket
        (BETWEEN, "yes", (40, 41, 40.6), True),
        (snap("greater", 40), "no", (38, 39, 40.4), True),  # greater 40: YES at 41+
        (snap("greater", 40), "no", (38, 39, 40.5), False),  # 40.5 rounds up to 41
        (snap("less", cap=40), "no", (40, 42, 45), True),  # less 40: YES at 39 or below
        (snap("less", cap=40), "no", (39.4, 42, 45), False),
        (snap("less", cap=40), "yes", (35, 36, 37), True),
        (snap("greater_or_equal", 40), "yes", (40, 41, 42), True),
        (snap("less_or_equal", cap=40), "yes", (40, 41, 42), False),
    ],
)
def test_source_agreement_per_strike_type(
    market: MarketSnapshot, side: Side, values: tuple[float, ...], take: bool
) -> None:
    d = agreement().check(make_signal(side=side), market, ctx_with(highs(*values)))
    assert d.take is take
    if not take:
        assert d.reason == "source_agreement:disagree"


def test_source_agreement_fraction_and_spread() -> None:
    ctx = ctx_with(highs(45, 41, 50))
    assert not agreement().check(make_signal(side="no"), BETWEEN, ctx).take
    assert agreement(min_agree_fraction=0.6).check(make_signal(side="no"), BETWEEN, ctx).take
    wide = agreement(max_spread=4).check(
        make_signal(side="no"), BETWEEN, ctx_with(highs(45, 50, 44))
    )
    assert wide.reason == "source_agreement:spread"


def test_too_few_sources_is_unavailable_and_fail_open_decides() -> None:
    ctx = ctx_with(highs(45, 46))  # 2 of the default 3
    with pytest.raises(DataUnavailable, match="2 of 3"):
        agreement().check(make_signal(side="no"), BETWEEN, ctx)
    closed = check_gate(agreement(), make_signal(side="no"), BETWEEN, ctx)
    assert closed.reason == "source_agreement:fail_closed"
    assert check_gate(agreement(fail_open=True), make_signal(side="no"), BETWEEN, ctx).take


def test_no_usable_strike_is_unavailable() -> None:
    with pytest.raises(DataUnavailable, match="no usable strike"):
        agreement().check(make_signal(side="no"), snap("between", 40), ctx_with(highs(1, 2, 3)))


def test_stale_and_future_feed_values_do_not_count() -> None:
    stale = ctx_with(highs(45, 46, 47), known_at=NOW - timedelta(hours=13))  # max_age 12h
    with pytest.raises(DataUnavailable):
        agreement().check(make_signal(side="no"), BETWEEN, stale)
    future = ctx_with(highs(45, 46, 47), known_at=NOW + timedelta(minutes=1))
    with pytest.raises(DataUnavailable):
        agreement().check(make_signal(side="no"), BETWEEN, future)


def test_unmapped_series_is_blocked() -> None:
    g = gate(type="source_agreement", feeds=["f1", "f2", "f3"])  # no field: can't tell
    assert g.check(make_signal(side="no"), BETWEEN, ctx_with(highs(1, 2, 3))).reason == (
        "source_agreement:unknown_series"
    )
    g = gate(type="source_agreement", backfill_sources=["iem_mos_gfs"], field="high")
    assert g.check(make_signal(side="no"), BETWEEN, ctx_with()).reason == (
        "source_agreement:unknown_series"
    )


# --- backfilled forecasts through ctx ------------------------------------------------------


RUN = datetime(2031, 1, 14, 12, tzinfo=UTC)


def test_backfill_excludes_runs_known_after_now() -> None:
    rows = [
        brow("iem_mos_gfs", 45, RUN),
        brow("iem_mos_gfs", 41, RUN + timedelta(hours=12)),  # 00Z run, known 05Z: after NOW
        brow("iem_mos_nbs", 46, RUN, lag=timedelta(hours=2)),
        brow("openmeteo_ecmwf_ifs", 44, RUN - timedelta(hours=12), lag=timedelta(hours=8)),
    ]
    ctx = ctx_with(backfill=rows)
    got = ctx.forecasts(
        DAY, "high", station="CLINYC",
        backfill_sources=["iem_mos_gfs", "iem_mos_nbs", "openmeteo_ecmwf_ifs"],
    )  # fmt: skip
    assert sorted((f.source, f.value) for f in got) == [
        ("iem_mos_gfs", 45), ("iem_mos_nbs", 46), ("openmeteo_ecmwf_ifs", 44),
    ]  # fmt: skip
    assert {f.provenance for f in got} == {"backfill"}
    assert all(f.known_at <= NOW and f.run_id for f in got)
    g = gate(
        type="source_agreement", **FAKE,
        backfill_sources=["iem_mos_gfs", "iem_mos_nbs", "openmeteo_ecmwf_ifs"],
    )  # fmt: skip
    assert g.check(make_signal(side="no"), BETWEEN, ctx).take
    # once the later GFS run is public it replaces the earlier one, and 41 is in the bracket
    later = ctx_with(backfill=rows, now=RUN + timedelta(hours=18))
    assert g.check(make_signal(side="no"), BETWEEN, later).reason == "source_agreement:disagree"


def test_backfill_max_age_and_mixing_with_feeds() -> None:
    old = brow("iem_mos_gfs", 45, NOW - timedelta(hours=60))
    ctx = ctx_with(feeds=highs(46, 47), backfill=[old])
    g = agreement(backfill_sources=["iem_mos_gfs"])
    with pytest.raises(DataUnavailable):  # f3 empty, GFS run too old (48h default)
        g.check(make_signal(side="no"), BETWEEN, ctx)
    fresh = ctx_with(feeds=highs(46, 47), backfill=[brow("iem_mos_gfs", 45, RUN)])
    got = fresh.forecasts(
        DAY, "high", feeds=["f1", "f2", "f3"], station="CLINYC", backfill_sources=["iem_mos_gfs"]
    )
    assert sorted((f.provenance, f.source) for f in got) == [
        ("backfill", "iem_mos_gfs"), ("feed", "f1"), ("feed", "f2"),
    ]  # fmt: skip
    assert g.check(make_signal(side="no"), BETWEEN, fresh).take


# --- bracket_distance -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("market", "side", "value", "distance"),
    [
        (BETWEEN, "no", 44, 2.5),  # YES region is [39.5, 41.5)
        (BETWEEN, "no", 38, 1.5),
        (BETWEEN, "no", 40.7, 0.0),  # inside the bracket
        (BETWEEN, "yes", 40.5, 1.0),
        (snap("greater", 40), "no", 37, 3.5),  # YES from 40.5 up
        (snap("less", cap=40), "no", 43, 3.5),  # YES below 39.5
        (snap("less", cap=40), "yes", 36, 3.5),
    ],
)
def test_distance_to_loss(
    market: MarketSnapshot, side: Side, value: float, distance: float
) -> None:
    assert distance_to_loss(market, make_signal(side=side), value, 10) == pytest.approx(distance)


@pytest.mark.parametrize(
    ("values", "use", "take"),
    [
        ((43.5, 44, 50), "median", True),  # median 44: 2.5 from 41.5
        ((43.4, 43.4, 50), "median", False),  # 1.9
        ((43.5, 44, 50), "each", True),
        ((43.4, 44, 50), "each", False),
        ((40, 41, 50), "median", False),  # median in the bracket: never sell it
    ],
)
def test_bracket_distance(values: tuple[float, ...], use: str, take: bool) -> None:
    g = gate(type="bracket_distance", feeds=["f1", "f2", "f3"], use=use, **FAKE)
    d = g.check(make_signal(side="no"), BETWEEN, ctx_with(highs(*values)))
    assert d.take is take
    if not take:
        assert d.reason == "bracket_distance:too_close"


def test_bracket_distance_zero_still_needs_the_winning_side() -> None:
    g = gate(type="bracket_distance", feeds=["f1"], min_distance=0, **FAKE)
    assert not g.check(make_signal(side="no"), BETWEEN, ctx_with(highs(41))).take
    assert g.check(make_signal(side="no"), BETWEEN, ctx_with(highs(41.5))).take


def test_bracket_distance_without_forecasts_is_unavailable() -> None:
    g = gate(type="bracket_distance", feeds=["f1"], **FAKE)
    with pytest.raises(DataUnavailable):
        g.check(make_signal(side="no"), BETWEEN, ctx_with())


# --- sky_conditions -------------------------------------------------------------------


def test_sky_pop_reads_nws_precip_and_vetoes_above_max() -> None:
    g = gate(type="sky_conditions", feeds=["f1", "f2"], station="CLINYC")  # max_pop 50
    assert g.check(make_signal(side="no"), BETWEEN, ctx_with(highs(20, 50, key="precip"))).take
    d = g.check(make_signal(side="no"), BETWEEN, ctx_with(highs(20, 60, key="precip")))
    assert d.reason == "sky_conditions:pop"
    median_g = gate(type="sky_conditions", feeds=["f1", "f2", "f3"], aggregate="median")
    assert median_g.check(make_signal(), BETWEEN, ctx_with(highs(20, 30, 90, key="pop"))).take


def test_sky_cloud_from_mos_categories() -> None:
    rows = [brow("iem_mos_gfs", 75.0, RUN, field="cloud")]  # BK
    g = gate(
        type="sky_conditions", backfill_sources=["iem_mos_gfs"], max_pop=None, max_cloud=75,
        station="CLINYC",
    )  # fmt: skip
    assert g.check(make_signal(), BETWEEN, ctx_with(backfill=rows)).take
    overcast = [brow("iem_mos_gfs", 100.0, RUN, field="cloud")]
    assert g.check(make_signal(), BETWEEN, ctx_with(backfill=overcast)).reason == (
        "sky_conditions:cloud"
    )


def test_sky_missing_field_is_unavailable() -> None:
    g = gate(type="sky_conditions", feeds=["f1"], max_cloud=75)  # nws has no cloud key
    ctx = ctx_with(highs(10, key="precip"))
    with pytest.raises(DataUnavailable, match="cloud"):
        g.check(make_signal(), BETWEEN, ctx)
    assert check_gate(g, make_signal(), BETWEEN, ctx).reason == "sky_conditions:fail_closed"


# --- validation ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        ({"type": "source_agreement"}, "set feeds and/or backfill_sources"),
        ({"type": "source_agreement", "backfill_sources": ["nope"]}, "unknown backfill_sources"),
        ({"type": "bracket_distance", "feeds": ["a"], "station": "KNYC"}, "unknown station"),
        ({"type": "bracket_distance", "feeds": ["a", "a"]}, "duplicate feeds"),
        ({"type": "bracket_distance", "feeds": ["a"], "min_distance": -1}, "min_distance"),
        ({"type": "source_agreement", "feeds": ["a"], "min_agree_fraction": 0}, "agree"),
        ({"type": "sky_conditions", "feeds": ["a"], "max_pop": None}, "set at least one"),
        ({"type": "sky_conditions", "feeds": ["a"], "max_pop": 150}, "max_pop"),
    ],
)
def test_validation(spec: dict[str, object], match: str) -> None:
    with pytest.raises(PluginError, match=match):
        gate(**spec)


def test_undeclared_feed_fails_build_and_backfill_is_flagged() -> None:
    gates = [{"type": "source_agreement", "feeds": ["missing"], **FAKE}]
    with pytest.raises(BuildError, match="undeclared feeds"):
        build_strategies([portfolio(feeds=FEEDS, strategies=[strategy(gates=gates)])])
    bf = StrategyConfig.model_validate(
        strategy(gates=[{"type": "bracket_distance", "backfill_sources": ["iem_mos_gfs"]}])
    )
    assert uses_backfill(bf)
    assert not uses_backfill(StrategyConfig.model_validate(strategy()))


def test_example_portfolio_builds() -> None:
    from pathlib import Path

    from nem.core.config import load_portfolio

    root = Path(__file__).resolve().parents[2]
    p = load_portfolio(root / "examples/weather/forecast_gates.yaml")
    (rt,) = build_strategies([p])
    assert [g.name for g in rt.gates][1:] == [
        "source_agreement", "bracket_distance", "sky_conditions",
    ]  # fmt: skip
    assert all(g.fail_open is False for g in rt.gates)
    # forward example: live feeds only, so its verdict isn't capped as backfill-driven
    assert not uses_backfill(p.strategies[0])
