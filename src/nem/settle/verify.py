"""`nem settle verify`: Kalshi's reported result vs the independently observed value.

Which answer wins depends on the question, and nothing here picks one silently:
- money (what was actually made or lost): the realized-PnL export, always;
- strategy replay (what a strategy *should* have scored): `derived_result`;
- every market where they differ, or either is missing, goes to the disagreements CSV.
"""

import csv
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from nem.core.types import StrikeType
from nem.gates.feeds import market_date
from nem.market.kalshi import STRIKE_TYPES, KalshiError
from nem.settle.model import SettlementCheck
from nem.settle.observed import Iem, Reading, SourceError
from nem.settle.rules import (
    STATUSES,
    derive_result,
    rules_measure,
    rules_source,
    rules_stations,
    status_of,
)
from nem.settle.stations import lookup, series_of

WEATHER_PREFIXES = ("KXHIGH", "KXLOW")
INFO = "info: "  # a note that alone doesn't put a market in the review report
FetchMarket = Callable[[str], dict[str, Any]]

REPORT_COLUMNS = (
    "ticker", "series", "market_date", "measure", "station", "rules_source", "strike_type",
    "floor_strike", "cap_strike", "observed_value", "source", "source_ref", "derived_result",
    "kalshi_result", "kalshi_value", "kalshi_settlement_value", "status", "note",
    "realized_pnl_usd", "replay_result",
)  # fmt: skip


def is_weather(ticker: str) -> bool:
    return ticker.startswith(WEATHER_PREFIXES)


def _number(v: object) -> float | None:
    if isinstance(v, bool) or v is None or v == "":
        return None
    if isinstance(v, int | float | str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def check_market(
    ticker: str,
    market: dict[str, Any] | None,
    reading: Callable[[], Reading | None],
    now: datetime,
    kalshi_problem: str = "",
) -> SettlementCheck:
    """One market. `market` is Kalshi's raw market (None if it couldn't be fetched);
    `reading` fetches the independent observation."""
    notes: list[str] = []
    series = series_of(ticker)
    info = lookup(ticker)
    day = market_date(ticker)
    m: dict[str, Any] = market or {}
    if kalshi_problem:
        notes.append(f"kalshi: {kalshi_problem}")
    rules = str(m.get("rules_primary") or "")
    source_named = rules_source(rules) if rules else "unverified"
    raw_type = m.get("strike_type")
    strike_type: StrikeType | None = raw_type if raw_type in STRIKE_TYPES else None
    floor, cap = _number(m.get("floor_strike")), _number(m.get("cap_strike"))
    measure = info.measure if info else ("low" if ticker.startswith("KXLOW") else "high")

    if source_named == "unverified":
        notes.append("rules: settlement source not recognised (unverified)")
    elif source_named != "nws_cli":
        notes.append(f"{INFO}rules settle on {source_named}, not the NWS CLI (cross-check only)")
    if rules and rules_measure(rules) not in (None, measure):
        notes.append(f"rules measure {rules_measure(rules)} != series {measure}")
    named = rules_stations(rules, str(m.get("rules_secondary") or ""))
    if info and named and named != {info.station.cli}:
        notes.append(f"rules name {','.join(sorted(named))}, mapping says {info.station.cli}")

    got: Reading | None = None
    if info is None:
        notes.append("series not in nem.settle.stations")
    elif day is None:
        notes.append("no date in ticker")
    else:
        try:
            got = reading()
        except SourceError as e:
            notes.append(f"source: {e}")
    derived = None
    if got is not None:
        derived = derive_result(got.value, strike_type, floor, cap)
        if derived is None:
            notes.append("no usable strike from Kalshi")
        if got.approximate:
            notes.append("approximate source (METAR reports, not the CLI)")
            edges = [s for s in (floor, cap) if s is not None]
            if edges and min(abs(got.value - s) for s in edges) <= 1:
                notes.append("within 1F of a strike: not conclusive")
    elif info is not None and day is not None:
        notes.append("no independent reading for that day")

    kalshi_result = (str(m.get("result")) or None) if market is not None else None
    kalshi_value = _number(m.get("expiration_value"))
    settle_value = _number(m.get("settlement_value_dollars"))
    if kalshi_result not in (None, "yes", "no"):
        notes.append(f"kalshi result {kalshi_result!r}, paid ${settle_value} per YES")
    if got is not None and kalshi_value is not None and kalshi_value != got.value:
        notes.append(f"value mismatch: kalshi {kalshi_value:g}, observed {got.value}")
    return SettlementCheck(
        ticker=ticker,
        series=series,
        market_date=day,
        measure=measure,
        station=info.station.cli if info else "",
        rules_source=source_named,
        strike_type=strike_type,
        floor_strike=floor,
        cap_strike=cap,
        observed_value=float(got.value) if got else None,
        source=got.source if got else None,
        source_ref=got.ref if got else None,
        derived_result=derived,
        kalshi_result=kalshi_result,
        kalshi_value=kalshi_value,
        kalshi_settlement_value=settle_value,
        # without Kalshi's market there is no strike either: nothing to derive against
        status="kalshi_missing" if market is None else status_of(derived, kalshi_result),
        note="; ".join(notes),
        checked_at=now,
    )


def verify(
    tickers: Iterable[str], fetch_market: FetchMarket, iem: Iem, now: datetime
) -> list[SettlementCheck]:
    out: list[SettlementCheck] = []
    for ticker in sorted(set(tickers)):
        market: dict[str, Any] | None = None
        problem = ""
        try:
            market = fetch_market(ticker)
        except KalshiError as e:
            problem = str(e)
        info, day = lookup(ticker), market_date(ticker)

        def reading(info: Any = info, day: Any = day) -> Reading | None:
            return iem.reading(info.station, day, info.measure)

        out.append(check_market(ticker, market, reading, now, problem))
    return out


def needs_review(c: SettlementCheck) -> bool:
    """Shown in the disagreements report: anything but a clean agreement."""
    notes = [n for n in c.note.split("; ") if n]
    return c.status != "agree" or any(not n.startswith(INFO) for n in notes)


def tickers_from_file(path: Path) -> list[str]:
    """One ticker per line; blank lines and `#` comments ignored."""
    lines = (line.split("#", 1)[0].strip() for line in path.read_text().splitlines())
    return [line for line in lines if line]


def pnl_by_ticker(paths: Sequence[Path]) -> dict[str, float]:
    """Kalshi realized-PnL exports -> realized P&L after fees per ticker (money truth).
    Read-only; rows without a ticker or a number are skipped."""
    out: dict[str, float] = {}
    for path in paths:
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                ticker = (row.get("market_ticker") or "").strip()
                pnl = _number((row.get("realized_pnl_with_fees_dollars") or "").strip())
                if ticker and pnl is not None:
                    out[ticker] = out.get(ticker, 0.0) + pnl
    return out


def _cell(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def write_report(
    path: Path, checks: Sequence[SettlementCheck], pnl: dict[str, float] | None = None
) -> int:
    """Write every market that needs review; returns how many rows were written."""
    rows = [c for c in checks if needs_review(c)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(REPORT_COLUMNS)
        for c in rows:
            money = pnl.get(c.ticker) if pnl is not None else None
            values = [
                c.ticker, c.series, c.market_date, c.measure, c.station, c.rules_source,
                c.strike_type, c.floor_strike, c.cap_strike, c.observed_value, c.source,
                c.source_ref, c.derived_result, c.kalshi_result, c.kalshi_value,
                c.kalshi_settlement_value, c.status, c.note,
                None if money is None else round(money, 4), c.derived_result or "exclude",
            ]  # fmt: skip
            w.writerow([_cell(v) for v in values])
    return len(rows)


def render_counts(checks: Sequence[SettlementCheck]) -> str:
    counts = Counter(c.status for c in checks)
    sources = Counter(c.source or "none" for c in checks)
    named = Counter(c.rules_source for c in checks)
    lines = [f"{len(checks)} markets checked"]
    lines += [f"  {s:<15} {counts.get(s, 0)}" for s in STATUSES]
    lines.append("observed from: " + ", ".join(f"{k} {v}" for k, v in sorted(sources.items())))
    lines.append("rules name: " + ", ".join(f"{k} {v}" for k, v in sorted(named.items())))
    flagged = sum(1 for c in checks if c.status == "agree" and needs_review(c))
    if flagged:
        lines.append(f"{flagged} agreeing markets carry a caveat (see the report's note)")
    return "\n".join(lines)
