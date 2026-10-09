"""Operations: is everything running, and what's halted?

Health comes from heartbeats in the store, so it works across processes and machines
that share the database. A process that is up but has nothing to trade (no open markets)
still heartbeats every tick: idle is not dead. Stopped-on-purpose is visible too, as a
strategy's `status` in its portfolio file or a halt from `nem halt`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from nem.core.config import PortfolioConfig
from nem.store import Store


@dataclass(frozen=True, slots=True)
class ProcessHealth:
    name: str
    last: datetime | None
    detail: str | None  # latest heartbeat status, e.g. "ok" or "error: ..."
    state: str  # "ok" | "failing" (alive, last attempt errored) | "stale" | "never"

    @property
    def healthy(self) -> bool:
        return self.state in ("ok", "failing")


def process_health(
    store: Store, names: Sequence[str], now: datetime, max_age: timedelta
) -> list[ProcessHealth]:
    out: list[ProcessHealth] = []
    for name in names:
        latest = store.heartbeat_status(name)
        if latest is None:
            out.append(ProcessHealth(name, None, None, "never"))
            continue
        ts, detail = latest
        if now - ts > max_age:
            state = "stale"
        elif detail.startswith("error"):
            state = "failing"
        else:
            state = "ok"
        out.append(ProcessHealth(name, ts, detail, state))
    return out


def _ago(now: datetime, ts: datetime | None) -> str:
    if ts is None:
        return "never"
    s = max(0, int((now - ts).total_seconds()))
    if s < 120:
        return f"{s}s ago"
    if s < 7200:
        return f"{s // 60}m ago"
    if s < 172800:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def render_status(
    store: Store,
    portfolios: Sequence[PortfolioConfig],
    now: datetime,
    expect: Sequence[str],
    max_age: timedelta,
) -> tuple[str, bool]:
    """Text for `nem status`, and whether every expected process is healthy."""
    names = list(dict.fromkeys([*expect, *sorted(store.processes())]))
    health = process_health(store, names, now, max_age)
    lines = ["Processes"]
    for h in health:
        expected = "" if h.name in expect else "  (not expected)"
        detail = f"  {h.detail}" if h.detail and h.state == "failing" else ""
        lines.append(
            f"  {h.name:<10}{h.state:<9}last heartbeat {_ago(now, h.last)}{detail}{expected}"
        )

    lines.append("Halts")
    halts = store.halts()
    if not halts:
        lines.append("  none")
    for scope, reason, ts in halts:
        what = "EVERYTHING" if scope == "*" else scope
        lines.append(f"  {what}  since {_ago(now, ts)}: {reason}")

    for p in portfolios:
        open_all = store.open_trades(portfolio=p.name)
        lines.append(f"Portfolio {p.name} ({p.mode})  open positions {len(open_all)}")
        for s in p.strategies:
            state = s.status
            if s.status == "active" and (scope := store.halted(p.name, s.name)) is not None:
                state = f"halted ({'all' if scope == '*' else scope})"
            n_open = len(store.open_trades(strategy=(p.name, s.name)))
            n_done = len(store.settled_trades(strategies=[(p.name, s.name)]))
            last = _ago(now, store.last_signal(p.name, s.name))
            lines.append(
                f"  {s.name:<16}{state:<28}{s.series:<12}open {n_open}  settled {n_done}"
                f"  last signal {last}"
            )
    healthy = all(h.healthy for h in health if h.name in expect)
    return "\n".join(lines), healthy
