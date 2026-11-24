"""Kalshi trading fees.

Taker fee per fill = round_up_to_cent(rate * contracts * price * (1 - price)), with rate
0.07 for most series. It peaks at 50c (1.75c per contract) and is small near the extremes,
but rounding up to a whole cent makes small orders relatively expensive. Verify against
Kalshi's current fee schedule; it changes.
"""

import math

DEFAULT_TAKER_RATE = 0.07


def taker_fee(price: float, contracts: float, rate: float = DEFAULT_TAKER_RATE) -> float:
    raw = rate * contracts * price * (1 - price)
    # round() first so float noise (0.0300000001) doesn't add a cent
    return math.ceil(round(raw * 100, 6)) / 100


def fee_per_contract(price: float, rate: float = DEFAULT_TAKER_RATE) -> float:
    """Fee per contract before rounding, for edge estimates. Real orders round up."""
    return rate * price * (1 - price)


# Maker fees apply only on series Kalshi lists as charging them; check the fee schedule.
DEFAULT_MAKER_RATE = 0.0175


def maker_fee(price: float, contracts: float, rate: float = DEFAULT_MAKER_RATE) -> float:
    """Fee for a resting order that is filled: same formula and rounding as the taker fee,
    at the (lower) maker rate."""
    return taker_fee(price, contracts, rate)


# How Kalshi says a series is charged: GET /series/{ticker} -> `fee_type`, `fee_multiplier`.
# "quadratic" charges takers only; "quadratic_with_maker_fees" charges resting orders too;
# "flat" (and anything new) isn't modelled here, so a rate must be set explicitly.
QUADRATIC_FEE_TYPES = ("quadratic", "quadratic_with_maker_fees")


class FeeScheduleUnknown(ValueError):
    """The series' fee schedule can't be turned into a rate; set it explicitly."""


def series_taker_rate(fee_type: str, multiplier: float) -> float:
    if fee_type not in QUADRATIC_FEE_TYPES:
        raise FeeScheduleUnknown(f"fee_type {fee_type!r} is not modelled")
    return DEFAULT_TAKER_RATE * multiplier


def series_maker_rate(fee_type: str, multiplier: float) -> float:
    if fee_type == "quadratic":
        return 0.0
    if fee_type == "quadratic_with_maker_fees":
        return DEFAULT_MAKER_RATE * multiplier
    raise FeeScheduleUnknown(f"fee_type {fee_type!r} is not modelled")
