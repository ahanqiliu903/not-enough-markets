"""Value types shared by every module.

Prices are dollars per contract in [0, 1]. All datetimes must be timezone-aware.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Side = Literal["yes", "no"]
Mode = Literal["paper", "live"]
DecisionKind = Literal["take", "skip", "no_fill"]
StrategyKey = tuple[str, str]  # (portfolio, strategy)

Level = tuple[float, float]  # (price, contracts); Kalshi sizes can be fractional
# Resting bids per side, best (highest) first.
# Buying YES at p means lifting a NO bid at 1 - p (and vice versa).
Depth = Mapping[Side, Sequence[Level]]


def _require_aware(name: str, dt: datetime) -> None:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got {dt!r}")


def _require_price(name: str, price: float | None) -> None:
    if price is not None and not 0.0 <= price <= 1.0:
        raise ValueError(f"{name} must be in [0, 1], got {price}")


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    ts: datetime
    series: str  # "KXNATGAS15M"
    window_id: str  # event ticker, e.g. "KXNATGAS15M-26OCT071530"
    ticker: str  # market ticker
    open_time: datetime
    close_time: datetime
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    depth: Depth = field(default_factory=dict[Side, Sequence[Level]])

    def __post_init__(self) -> None:
        for name in ("ts", "open_time", "close_time"):
            _require_aware(name, getattr(self, name))
        if self.close_time <= self.open_time:
            raise ValueError("close_time must be after open_time")
        for name in ("yes_bid", "yes_ask", "no_bid", "no_ask"):
            _require_price(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class Signal:
    portfolio: str
    strategy: str
    window_id: str
    ticker: str
    side: Side
    limit_price: float  # max price willing to pay
    p_model: float  # strategy's probability that `side` wins
    meta: Mapping[str, object] = field(default_factory=dict[str, object])  # free-form, logged

    def __post_init__(self) -> None:
        if not 0.0 < self.limit_price < 1.0:
            raise ValueError(f"limit_price must be in (0, 1), got {self.limit_price}")
        _require_price("p_model", self.p_model)


@dataclass(frozen=True, slots=True)
class Decision:
    take: bool
    reason: str  # "ok" or the name of whatever blocked it

    @classmethod
    def ok(cls) -> "Decision":
        return cls(True, "ok")

    @classmethod
    def skip(cls, reason: str) -> "Decision":
        return cls(False, reason)


_COID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://github.com/ahanqiliu903/not-enough-markets"
)


def client_order_id(
    portfolio: str, strategy: str, window_id: str, side: Side, attempt: int = 0
) -> str:
    """Deterministic order ID, so resubmitting the same attempt can never double-fill.

    Bump `attempt` only after the previous attempt is confirmed dead (IOC no-fill or reject).
    """
    return str(uuid.uuid5(_COID_NAMESPACE, f"{portfolio}|{strategy}|{window_id}|{side}|{attempt}"))


@dataclass(frozen=True, slots=True)
class Order:
    client_order_id: str
    portfolio: str
    strategy: str
    window_id: str
    ticker: str
    side: Side
    limit_price: float
    qty: int

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"qty must be positive, got {self.qty}")

    @classmethod
    def from_signal(cls, sig: Signal, qty: int, attempt: int = 0) -> "Order":
        return cls(
            client_order_id=client_order_id(
                sig.portfolio, sig.strategy, sig.window_id, sig.side, attempt
            ),
            portfolio=sig.portfolio,
            strategy=sig.strategy,
            window_id=sig.window_id,
            ticker=sig.ticker,
            side=sig.side,
            limit_price=sig.limit_price,
            qty=qty,
        )


@dataclass(frozen=True, slots=True)
class Fill:
    client_order_id: str
    ts: datetime
    price: float  # average fill price
    qty: int
    fee: float  # total fee in dollars

    def __post_init__(self) -> None:
        _require_aware("ts", self.ts)
        _require_price("price", self.price)
        if self.qty <= 0:
            raise ValueError(f"qty must be positive, got {self.qty}")


@dataclass(frozen=True, slots=True)
class Trade:
    """A position held to settlement. One per (portfolio, strategy, window_id)."""

    portfolio: str
    strategy: str
    window_id: str
    ticker: str
    side: Side
    avg_price: float
    qty: int
    fee: float
    mode: Mode
    opened_at: datetime
    won: bool | None = None  # None until settled
    realized_pnl: float | None = None
    settled_at: datetime | None = None

    @property
    def settled(self) -> bool:
        return self.settled_at is not None


@dataclass(frozen=True, slots=True)
class Settlement:
    """A market's final outcome."""

    ticker: str
    series: str
    window_id: str
    result: Side
    settled_at: datetime

    def __post_init__(self) -> None:
        _require_aware("settled_at", self.settled_at)
