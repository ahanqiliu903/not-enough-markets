from datetime import timedelta
from typing import Any

import pytest

from nem.core.config import PluginSpec
from nem.core.registry import REGISTRY, PluginError
from nem.gates.base import Gate
from nem.signals.base import SignalPlugin
from nem.sizing.base import Sizer
from nem.store import Store

from engine_helpers import context, portfolio, strategy
from factories import T0, make_signal, make_snapshot

CLOSE = T0 + timedelta(minutes=15)


def build(kind: Any, base: Any, **spec: object) -> Any:
    return REGISTRY.build(kind, PluginSpec.model_validate(spec), base)


def signal(**params: object) -> SignalPlugin:
    params.setdefault("threshold", 0.9)
    return build("signal", SignalPlugin, type="extreme_favorite", **params)


PRIORS = {"yes": {"wins": 95, "n": 100}, "no": {"wins": 95, "n": 100}}


# --- extreme_favorite ---------------------------------------------------------


def test_favorite_with_edge_signals() -> None:
    ctx = context(portfolio(), Store(), T0)
    sig = signal(priors=PRIORS, min_edge=0.01).on_market(make_snapshot(yes_ask=0.90), ctx)
    assert sig is not None
    assert (sig.side, sig.limit_price, sig.p_model) == ("yes", 0.90, 0.95)
    assert sig.meta["edge"] == pytest.approx(0.95 - 0.90 - 0.0063, abs=1e-5)
    assert (sig.portfolio, sig.strategy) == ("research", "fav90")


def test_no_side_favorite() -> None:
    ctx = context(portfolio(), Store(), T0)
    sig = signal(priors=PRIORS).on_market(make_snapshot(yes_ask=0.08, no_ask=0.92), ctx)
    assert sig is not None
    assert (sig.side, sig.limit_price) == ("no", 0.92)


@pytest.mark.parametrize(
    ("snap_kw", "params"),
    [
        ({"yes_ask": 0.85, "no_ask": 0.16}, {"priors": PRIORS}),  # below threshold
        ({"yes_ask": 0.90}, {}),  # no prior and no history: no estimate
        ({"yes_ask": 0.94}, {"priors": PRIORS, "min_edge": 0.01}),  # 0.95-0.94-fee < 0.01
        ({"yes_ask": None, "no_ask": None}, {"priors": PRIORS}),  # no quotes
    ],
)
def test_no_signal(snap_kw: dict[str, Any], params: dict[str, Any]) -> None:
    ctx = context(portfolio(), Store(), T0)
    assert signal(**params).on_market(make_snapshot(**snap_kw), ctx) is None


def test_estimate_updates_with_history() -> None:
    store = Store()
    ctx = context(portfolio(), store, T0)
    for won in [False] * 10:
        store.bump_stat("research", "fav90", "yes", won)
    # prior 95/100 plus 0/10 observed = 95/110 = 0.864: no longer worth 0.90
    params = {"priors": PRIORS, "min_edge": 0.0}
    assert signal(**params).on_market(make_snapshot(yes_ask=0.90), ctx) is None


def test_signal_params_validated() -> None:
    with pytest.raises(PluginError):
        signal(threshold=0.4)
    with pytest.raises(PluginError):
        signal(priors={"maybe": {"wins": 1, "n": 1}})


# --- gates --------------------------------------------------------------------


def gate(type_: str, **params: object) -> Gate:
    return build("gate", Gate, type=type_, **params)


def test_max_entry_price() -> None:
    ctx = context(portfolio(), Store(), T0)
    g = gate("max_entry_price", value=0.91)
    assert g.check(make_signal(limit_price=0.91), make_snapshot(), ctx).take
    d = g.check(make_signal(limit_price=0.92), make_snapshot(), ctx)
    assert (d.take, d.reason) == (False, "max_entry_price")
    assert g.fail_open is False


@pytest.mark.parametrize(
    ("seconds_left", "reason"),
    [(600, "time_in_window:too_early"), (300, "ok"), (31, "ok"), (29, "time_in_window:too_late")],
)
def test_time_in_window(seconds_left: int, reason: str) -> None:
    ctx = context(portfolio(), Store(), CLOSE - timedelta(seconds=seconds_left))
    g = gate("time_in_window", last_minutes=5, min_seconds_left=30)
    assert g.check(make_signal(), make_snapshot(), ctx).reason == reason


def test_time_in_window_rejects_inconsistent_params() -> None:
    with pytest.raises(PluginError, match="min_seconds_left"):
        gate("time_in_window", last_minutes=1, min_seconds_left=60)


def test_gate_fail_open_is_configurable() -> None:
    assert gate("max_entry_price", value=0.9, fail_open=True).fail_open is True


# --- sizers -------------------------------------------------------------------


def sizer(type_: str, **params: object) -> Sizer:
    return build("sizer", Sizer, type=type_, **params)


def test_fixed() -> None:
    ctx = context(portfolio(), Store(), T0)
    s = sizer("fixed", contracts=7).size(make_signal(), ctx)
    assert (s.qty, s.reason) == (7, "fixed 7")


def test_percent() -> None:
    ctx = context(portfolio(starting_balance=1000), Store(), T0)
    s = sizer("percent", percent=10).size(make_signal(limit_price=0.90), ctx)
    # $100 / (0.90 + 0.0063 fee) = 110.3 -> 110
    assert s.qty == 110
    assert "10% of $1000.00" in s.reason


def test_kelly_fraction_and_cap() -> None:
    ctx = context(portfolio(starting_balance=1000), Store(), T0)
    sig = make_signal(limit_price=0.90, p_model=0.95)
    c = 0.90 + 0.0063
    f = (0.95 - c) / (1 - c)
    s = sizer("kelly", fraction=0.25, max_contracts=1000).size(sig, ctx)
    assert s.qty == int(0.25 * f * 1000 / c)
    capped = sizer("kelly", fraction=1, max_contracts=3).size(sig, ctx)
    assert capped.qty == 3
    assert "capped at 3" in capped.reason


def test_kelly_without_edge_sizes_zero() -> None:
    ctx = context(portfolio(), Store(), T0)
    s = sizer("kelly", fraction=0.5, max_contracts=10).size(make_signal(p_model=0.9), ctx)
    assert s.qty == 0
    assert "no kelly edge" in s.reason


def test_kelly_requires_cap() -> None:
    with pytest.raises(PluginError, match="max_contracts"):
        sizer("kelly", fraction=0.5)


def test_strategy_helper_is_valid() -> None:
    assert portfolio(strategies=[strategy()]).strategy("fav90").signal.type == "extreme_favorite"
