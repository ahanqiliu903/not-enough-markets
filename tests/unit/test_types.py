from datetime import datetime, timedelta

import pytest

from nem.core.types import Decision, Fill, Order, client_order_id

from factories import T0, make_signal, make_snapshot


def test_snapshot_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        make_snapshot(ts=datetime(2026, 10, 7, 15, 0))


def test_snapshot_rejects_out_of_range_price() -> None:
    with pytest.raises(ValueError, match="yes_ask"):
        make_snapshot(yes_ask=1.2)


def test_snapshot_rejects_close_before_open() -> None:
    with pytest.raises(ValueError, match="close_time"):
        make_snapshot(close_time=T0 - timedelta(minutes=1))


def test_snapshot_allows_missing_quotes() -> None:
    assert make_snapshot(yes_bid=None, no_ask=None).yes_bid is None


@pytest.mark.parametrize("price", [0.0, 1.0, -0.1])
def test_signal_limit_price_must_be_tradeable(price: float) -> None:
    with pytest.raises(ValueError, match="limit_price"):
        make_signal(limit_price=price)


def test_client_order_id_is_deterministic() -> None:
    a = client_order_id("research", "fav90", "W1", "yes")
    assert a == client_order_id("research", "fav90", "W1", "yes")
    others = {
        client_order_id("research", "fav90", "W1", "no"),
        client_order_id("research", "fav90", "W1", "yes", attempt=1),
        client_order_id("research", "fav85", "W1", "yes"),
        client_order_id("other", "fav90", "W1", "yes"),
        client_order_id("research", "fav90", "W2", "yes"),
    }
    assert a not in others
    assert len(others) == 5


def test_order_from_signal() -> None:
    sig = make_signal()
    order = Order.from_signal(sig, qty=5)
    assert order.client_order_id == client_order_id("research", "fav90", sig.window_id, "yes")
    assert (order.portfolio, order.strategy, order.qty) == ("research", "fav90", 5)
    assert order.limit_price == sig.limit_price
    assert Order.from_signal(sig, 5).client_order_id == order.client_order_id  # retry-safe


def test_order_rejects_nonpositive_qty() -> None:
    with pytest.raises(ValueError, match="qty"):
        Order.from_signal(make_signal(), qty=0)


def test_fill_validation() -> None:
    with pytest.raises(ValueError, match="qty"):
        Fill("c", T0, 0.9, 0, 0.0)
    with pytest.raises(ValueError, match="price"):
        Fill("c", T0, 1.5, 1, 0.0)


def test_decision_helpers() -> None:
    assert Decision.ok() == Decision(True, "ok")
    assert Decision.skip("canary") == Decision(False, "canary")
