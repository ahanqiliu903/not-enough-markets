"""Plain-text results per portfolio and strategy. Proper statistics arrive in M4."""

from collections.abc import Sequence
from datetime import datetime

from nem.core.config import PortfolioConfig
from nem.store import Store


def summarize(store: Store, portfolios: Sequence[PortfolioConfig], now: datetime) -> str:
    lines: list[str] = []
    for p in portfolios:
        start = p.starting_balance or p.budget or 0.0
        settled_all = store.settled_trades(portfolio=p.name)
        realized = sum(t.realized_pnl or 0.0 for t in settled_all)
        interest = store.ledger_total(p.name)
        open_all = store.open_trades(portfolio=p.name)
        lines.append(f"Portfolio {p.name} ({p.mode})")
        lines.append(
            f"  start ${start:,.2f}  realized {realized:+,.2f}  interest {interest:+,.2f}"
            f"  equity ${start + realized + interest:,.2f}  open positions {len(open_all)}"
        )
        header = f"  {'strategy':<16}{'trades':>7}{'won':>5}{'win%':>8}{'b/e%':>8}{'net P&L':>10}"
        lines.append(header)
        for s in p.strategies:
            trades = store.settled_trades(strategies=[(p.name, s.name)])
            n = len(trades)
            wins = sum(1 for t in trades if t.won)
            qty = sum(t.qty for t in trades)
            # break-even win rate = cost per contract (price + fees), since a win pays $1
            breakeven = sum(t.cost for t in trades) / qty if qty else 0.0
            pnl = sum(t.realized_pnl or 0.0 for t in trades)
            wr = f"{100 * wins / n:.1f}" if n else "-"
            be = f"{100 * breakeven:.1f}" if n else "-"
            lines.append(f"  {s.name:<16}{n:>7}{wins:>5}{wr:>8}{be:>8}{pnl:>+10.2f}")
            reasons = store.signal_reasons(p.name, s.name)
            if reasons:
                lines.append("    signals:")
                for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
                    lines.append(f"      {count:>5}  {reason}")
        lines.append("")
    lines.append(f"as of {now:%Y-%m-%d %H:%M} UTC")
    return "\n".join(lines)
