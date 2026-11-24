import pytest

from nem.core.types import Order
from nem.execution.fees import fee_per_contract, taker_fee
from nem.execution.paper_broker import PaperBroker

from factories import T0, make_snapshot


@pytest.mark.parametrize(
    ("price", "qty", "fee"),
    [
        (0.60, 20, 0.34),  # 0.336 rounds up
        (0.90, 1, 0.01),  # 0.0063 rounds up to a full cent
        (0.90, 100, 0.63),
        (0.50, 100, 1.75),  # fee peaks at 50c
        (0.99, 10, 0.01),
        (0.50, 0, 0.0),
    ],
)
def test_taker_fee(price: float, qty: int, fee: float) -> None:
    assert taker_fee(price, qty) == fee


def test_taker_fee_ignores_float_noise() -> None:
    # 0.07 * 300 * 0.5 * 0.5 = 5.25 exactly; float math must not round it to 5.26
    assert taker_fee(0.5, 300) == 5.25


def test_fee_per_contract_is_unrounded() -> None:
    assert fee_per_contract(0.9) == pytest.approx(0.0063)


def order(qty: int = 10, limit: float = 0.92, side: str = "yes") -> Order:
    return Order("coid", "p", "s", "W", "T", side, limit, qty)  # type: ignore[arg-type]


def snap_with_no_bids(*levels: tuple[float, float]):
    return make_snapshot(depth={"yes": [(0.08, 500.0)], "no": list(levels)})


def test_walks_book_up_to_limit() -> None:
    # NO bids at 0.10/0.09/0.07 are YES asks at 0.90/0.91/0.93
    snap = snap_with_no_bids((0.10, 3), (0.09, 5), (0.07, 100))
    fill = PaperBroker(0.07).submit(order(qty=10, limit=0.92), snap, T0)
    assert fill is not None
    assert fill.qty == 8  # 0.93 is above the limit; the rest is cancelled (IOC)
    assert fill.price == pytest.approx((0.90 * 3 + 0.91 * 5) / 8)
    assert fill.fee == taker_fee(0.90, 3) + taker_fee(0.91, 5)
    assert fill.price <= 0.92


def test_full_fill_at_best_level() -> None:
    fill = PaperBroker(0.07).submit(order(qty=2), snap_with_no_bids((0.10, 50)), T0)
    assert fill is not None
    assert (fill.qty, fill.price) == (2, 0.90)


def test_no_side_buys_against_yes_bids() -> None:
    snap = make_snapshot(depth={"yes": [(0.88, 4)], "no": []})
    fill = PaperBroker(0.07).submit(order(qty=5, limit=0.15, side="no"), snap, T0)
    assert fill is not None
    assert (fill.qty, fill.price) == (4, 0.12)


def test_nothing_at_limit_is_no_fill() -> None:
    assert PaperBroker(0.07).submit(order(limit=0.85), snap_with_no_bids((0.10, 50)), T0) is None


def test_fractional_sizes_round_down_to_whole_contracts() -> None:
    fill = PaperBroker(0.07).submit(order(qty=5), snap_with_no_bids((0.10, 2.7)), T0)
    assert fill is not None
    assert fill.qty == 2


# Audit R2 (intentional change): a quote is a price, not a size. Without recorded depth
# nothing fills unless the portfolio opts in to `fills.missing_depth: quote`.


@pytest.mark.parametrize(
    ("depth", "reason"),
    [
        ({}, "no_depth_recorded"),  # top of book only
        ({"yes": [(0.88, 500.0)]}, "no_depth_recorded"),  # opposite (NO) side not recorded
        ({"yes": [(0.88, 500.0)], "no": []}, "book_empty"),  # NO side recorded empty
    ],
)
def test_without_depth_nothing_fills_by_default(depth: dict[str, object], reason: str) -> None:
    snap = make_snapshot(yes_ask=0.90, depth=depth)
    broker = PaperBroker(0.07)
    o = order(qty=1_000_000)
    assert broker.submit(o, snap, T0) is None
    assert broker.no_fill_reason(o, snap) == reason


def test_quote_mode_fills_at_top_of_book_without_depth() -> None:
    snap = make_snapshot(yes_ask=0.90)
    fill = PaperBroker(0.07, "quote").submit(order(qty=7), snap, T0)
    assert fill is not None
    assert (fill.qty, fill.price) == (7, 0.90)
    assert PaperBroker(0.07, "quote").submit(order(qty=7), make_snapshot(yes_ask=None), T0) is None
    assert PaperBroker(0.07, "quote").no_fill_reason(order(), make_snapshot(yes_ask=None)) == (
        "no_quote"
    )
    above = order(qty=7, limit=0.89)
    assert PaperBroker(0.07, "quote").submit(above, snap, T0) is None


def test_quote_mode_never_fills_a_verified_empty_book() -> None:
    snap = make_snapshot(yes_ask=0.90, depth={"yes": [(0.88, 5.0)], "no": []})
    assert PaperBroker(0.07, "quote").submit(order(qty=7), snap, T0) is None


def test_depth_wins_over_quote_in_quote_mode() -> None:
    # recorded depth bounds the fill even when quotes are allowed
    snap = make_snapshot(yes_ask=0.90, depth={"yes": [], "no": [(0.10, 3.0)]})
    fill = PaperBroker(0.07, "quote").submit(order(qty=7), snap, T0)
    assert fill is not None and (fill.qty, fill.price) == (3, 0.90)


def test_limit_is_enforced_on_unsorted_depth() -> None:
    snap = snap_with_no_bids((0.07, 100), (0.10, 2))  # worst level listed first
    fill = PaperBroker(0.07).submit(order(qty=5, limit=0.92), snap, T0)
    assert fill is not None and (fill.qty, fill.price) == (2, 0.90)
