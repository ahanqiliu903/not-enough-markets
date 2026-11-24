"""`nem backfill forecasts` / `nem backfill coverage`: glue between store and archives."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from nem.backfill.coverage import Coverage, Entry, coverage, entries_from_lots
from nem.backfill.model import BackfillRow, validate
from nem.backfill.sources import (
    MOS_MODELS,
    OPEN_METEO_MODELS,
    ArchiveClient,
    Target,
    fetch_mos,
    fetch_open_meteo,
)
from nem.gates.feeds import market_date
from nem.settle.stations import STATIONS, lookup
from nem.store import Store


def zones() -> dict[str, ZoneInfo]:
    return {cli: ZoneInfo(s.tz) for cli, s in STATIONS.items()}


def manual_entries(store: Store) -> list[Entry]:
    lots = [
        (lot.ticker, lot.side, lot.opened_at)
        for portfolio, strategy in store.manual_strategies()
        for lot in store.manual_lots(portfolio, strategy)
    ]
    return entries_from_lots(lots)


def targets_for(tickers: Sequence[str], entries: Sequence[Entry] = ()) -> list[Target]:
    """One target per mapped (station, day); `until` = the last entry in that market,
    or None (any run before the day) when there are no entries."""
    last: dict[str, datetime] = {}
    for e in entries:
        last[e.ticker] = max(last.get(e.ticker, e.opened_at), e.opened_at)
    by_key: dict[tuple[str, date], Target] = {}
    for t in sorted(set(tickers)):
        info, day = lookup(t), market_date(t)
        if info is None or day is None:
            continue
        k = (info.station.cli, day)
        until = last.get(t)
        prev = by_key.get(k)
        if prev is not None and (prev.until is None or until is None):
            until = None
        elif prev is not None and prev.until is not None and until is not None:
            until = max(prev.until, until)
        by_key[k] = Target(info.station, day, until)
    return [by_key[k] for k in sorted(by_key)]


@dataclass
class BackfillResult:
    rows: list[BackfillRow] = field(default_factory=list[BackfillRow])
    problems: list[str] = field(default_factory=list[str])
    invalid: list[str] = field(default_factory=list[str])


def backfill(
    client: ArchiveClient,
    targets: Sequence[Target],
    sources: Sequence[str],
    fetched_at: datetime,
    lookback: int = 2,
) -> BackfillResult:
    """Fetch every source; rows breaking a horizon rule are dropped and listed."""
    res = BackfillResult()
    fetched: list[BackfillRow] = []
    for source in sources:
        if source in MOS_MODELS:
            fetched += fetch_mos(client, source, targets, fetched_at, lookback)
        elif source in OPEN_METEO_MODELS:
            rows, problems = fetch_open_meteo(client, source, targets, fetched_at, lookback)
            fetched += rows
            res.problems += problems
        else:
            raise ValueError(f"unknown backfill source {source!r}")
    tz = zones()
    for r in fetched:
        errors = validate([r], tz)
        if errors:
            res.invalid += errors
        else:
            res.rows.append(r)
    return res


def coverage_for(store: Store) -> list[Coverage]:
    entries = manual_entries(store)
    rows: dict[tuple[str, date], list[BackfillRow]] = defaultdict(list)
    for r in store.backfill_rows():
        rows[(r.station, r.target_date)].append(r)
    checks = {c.ticker: c for c in store.settlement_checks()}
    return coverage(entries, rows, checks)
