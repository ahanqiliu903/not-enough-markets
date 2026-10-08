from datetime import UTC, datetime, timedelta
from typing import Any

from nem.core.types import MarketSnapshot, Side, Signal, Trade

T0 = datetime(2026, 10, 7, 19, 15, tzinfo=UTC)  # 15:15 ET
WINDOW = "KXBTC15M-26OCT071530"


def make_snapshot(ts: datetime = T0, series: str = "KXBTC15M", **kw: Any) -> MarketSnapshot:
    fields: dict[str, Any] = {
        "ts": ts,
        "series": series,
        "window_id": f"{series}-26OCT071530",
        "ticker": f"{series}-26OCT071530-T1",
        "open_time": T0,
        "close_time": T0 + timedelta(minutes=15),
        "yes_bid": 0.88,
        "yes_ask": 0.90,
        "no_bid": 0.10,
        "no_ask": 0.12,
    }
    fields.update(kw)
    return MarketSnapshot(**fields)


def make_signal(side: Side = "yes", **kw: Any) -> Signal:
    fields: dict[str, Any] = {
        "portfolio": "research",
        "strategy": "fav90",
        "window_id": WINDOW,
        "ticker": f"{WINDOW}-T1",
        "side": side,
        "limit_price": 0.91,
        "p_model": 0.94,
    }
    fields.update(kw)
    return Signal(**fields)


def make_trade(window_id: str = WINDOW, **kw: Any) -> Trade:
    fields: dict[str, Any] = {
        "portfolio": "research",
        "strategy": "fav90",
        "window_id": window_id,
        "ticker": f"{window_id}-T1",
        "side": "yes",
        "avg_price": 0.90,
        "qty": 5,
        "fee": 0.02,
        "mode": "paper",
        "opened_at": T0,
    }
    fields.update(kw)
    return Trade(**fields)


def portfolio_dict(**kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "name": "research",
        "mode": "paper",
        "starting_balance": 1000,
        "strategies": [
            {
                "name": "fav90",
                "series": "KXBTC15M",
                "signal": {"type": "extreme_favorite", "threshold": 0.9},
                "gates": [{"type": "max_entry_price", "value": 0.95}],
                "sizing": {"type": "fixed", "contracts": 5},
            }
        ],
    }
    d.update(kw)
    return d
