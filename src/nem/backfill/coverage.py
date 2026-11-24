"""Forecast coverage at your imported manual entry times (`nem backfill coverage`).

For each manual entry (ticker, open time) in `manual_lots`: which backfilled sources had
a forecast for that market's day *known before the entry* (`known_before`), their values,
the spread, and whether at least `MIN_SOURCES` existed (fewer can't test the spec's
`source_agreement` gate). Everything here is labeled "backfilled forecasts".
"""

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import median

from nem.backfill.model import SOURCES, BackfillRow, known_before
from nem.gates.feeds import market_date
from nem.settle.model import SettlementCheck
from nem.settle.rules import derive_result
from nem.settle.stations import lookup
from nem.stats.provenance import BACKFILL_LABEL

MIN_SOURCES = 3


@dataclass(frozen=True, slots=True)
class Entry:
    ticker: str
    side: str
    opened_at: datetime


@dataclass(frozen=True, slots=True)
class Coverage:
    entry: Entry
    station: str
    day: date | None
    measure: str
    highs: dict[str, BackfillRow]  # source -> the forecast of the market's measure
    sky: dict[str, dict[str, float]]  # source -> {cloud, pop, qpf_in, qpf_cat}
    check: SettlementCheck | None

    @property
    def n_sources(self) -> int:
        return len(self.highs)

    @property
    def values(self) -> list[float]:
        return [r.value for r in self.highs.values()]

    @property
    def spread(self) -> float | None:
        return max(self.values) - min(self.values) if self.values else None

    @property
    def median(self) -> float | None:
        return median(self.values) if self.values else None

    def implied_result(self) -> str | None:
        """YES/NO the median forecast implies, from the market's own strike (informative,
        not a gate)."""
        c, m = self.check, self.median
        if c is None or m is None or c.strike_type is None:
            return None
        return derive_result(m, c.strike_type, c.floor_strike, c.cap_strike)


def entries_from_lots(lots: Sequence[tuple[str, str, datetime]]) -> list[Entry]:
    """(ticker, side, opened_at) per lot -> distinct entries (same ticker and minute)."""
    seen: dict[tuple[str, str, str], Entry] = {}
    for ticker, side, opened in lots:
        k = (ticker, side, opened.strftime("%Y-%m-%dT%H:%M"))
        if k not in seen or opened < seen[k].opened_at:
            seen[k] = Entry(ticker, side, opened)
    return sorted(seen.values(), key=lambda e: (e.ticker, e.opened_at))


def coverage(
    entries: Sequence[Entry],
    rows_for: "dict[tuple[str, date], list[BackfillRow]]",
    checks: dict[str, SettlementCheck],
) -> list[Coverage]:
    out: list[Coverage] = []
    for e in entries:
        info, day = lookup(e.ticker), market_date(e.ticker)
        station = info.station.cli if info else ""
        measure = info.measure if info else "high"
        visible = known_before(rows_for.get((station, day), []), e.opened_at) if day else []
        highs = {r.source: r for r in visible if r.field == measure}
        sky: dict[str, dict[str, float]] = {}
        for r in visible:
            if r.field not in ("high", "low"):
                sky.setdefault(r.source, {})[r.field] = r.value
        out.append(Coverage(e, station, day, measure, highs, sky, checks.get(e.ticker)))
    return out


def _yes_if(c: SettlementCheck | None) -> str:
    if c is None or c.strike_type is None:
        return ""
    if c.strike_type == "between":
        return f"{c.floor_strike:g}-{c.cap_strike:g}"
    if c.strike_type == "less":
        return f"<{c.cap_strike:g}"
    if c.strike_type == "greater":
        return f">{c.floor_strike:g}"
    return c.strike_type


def _fmt(v: float | None) -> str:
    return "" if v is None else f"{v:g}"


def write_csv(path: Path, rows: Sequence[Coverage], sources: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "provenance", "ticker", "entry_utc", "side", "target_date", "station", "measure",
            "yes_if", "n_sources", "lt3_sources", "sources", "min", "max", "spread", "median",
            "median_implies", "observed", "derived_result",
            *[f"{s}_{k}" for s in sources for k in ("value", "run", "known_at")],
            "cloud_max", "pop_max", "qpf_in_max", "qpf_cat_max",
        ])  # fmt: skip
        for c in rows:
            vals = c.values
            sky_cols: list[str] = []
            for field in ("cloud", "pop", "qpf_in", "qpf_cat"):
                got = [s[field] for s in c.sky.values() if field in s]
                sky_cols.append(_fmt(max(got)) if got else "")
            per_source: list[str] = []
            for s in sources:
                r = c.highs.get(s)
                per_source += (
                    [_fmt(r.value), r.run_id, r.known_at.isoformat()] if r else ["", "", ""]
                )
            chk = c.check
            w.writerow([
                BACKFILL_LABEL, c.entry.ticker, c.entry.opened_at.isoformat(), c.entry.side,
                c.day.isoformat() if c.day else "", c.station, c.measure, _yes_if(chk),
                c.n_sources, "yes" if c.n_sources < MIN_SOURCES else "no",
                "+".join(sorted(c.highs)), _fmt(min(vals) if vals else None),
                _fmt(max(vals) if vals else None), _fmt(c.spread), _fmt(c.median),
                c.implied_result() or "", _fmt(chk.observed_value if chk else None),
                (chk.derived_result if chk else None) or "", *per_source, *sky_cols,
            ])  # fmt: skip


def render(rows: Sequence[Coverage], sources: Sequence[str]) -> str:
    """Console summary, labeled as backfilled."""
    markets = sorted({c.entry.ticker for c in rows})
    first = {c.entry.ticker: c for c in reversed(rows)}  # rows sorted by time per ticker
    thin = sorted(t for t, c in first.items() if c.n_sources < MIN_SOURCES)
    lines = [
        f"Forecast coverage ({BACKFILL_LABEL}: approximate, not what was on screen)",
        f"  entries: {len(rows)}   markets: {len(markets)}",
        f"  entries with >= {MIN_SOURCES} sources before entry:"
        f" {sum(c.n_sources >= MIN_SOURCES for c in rows)}",
        f"  markets with < {MIN_SOURCES} sources at first entry: {len(thin)}",
    ]
    for s in sources:
        n = sum(s in c.highs for c in rows)
        lines.append(f"    {s:<22} {n:>4} / {len(rows)} entries  ({SOURCES[s].fidelity})")
    if thin:
        lines.append(f"  cannot test source_agreement: {', '.join(thin)}")
    return "\n".join(lines)
