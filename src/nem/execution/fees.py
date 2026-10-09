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
