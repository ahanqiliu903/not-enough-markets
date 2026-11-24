"""Import your own manual Kalshi trading as a read-only baseline portfolio
(`nem import kalshi-pnl`; docs/data-provenance.md).

Sources, both Kalshi account exports:

- realized-PnL CSVs, one row per closed lot: the truth for money. P&L is stored as Kalshi
  reports it, never recomputed from prices.
- the activity CSV (optional), one row per fill: exact fill times and maker/taker. Its
  `Price_In_Cents` is the YES price even on No rows (paid = 100 - price), and its
  `Amount_In_Dollars` is a contract count.

Every row is validated first; any malformed row fails the whole import with every error
listed by file and line, and nothing is written.

Idempotency: each lot's key is a hash of its parsed fields plus its occurrence index among
identical rows, in file order. Identical-looking rows are real separate lots (same market,
size, prices and times), so they are kept apart, and importing the same files again adds
nothing. The source file name is not part of the key, so a renamed export still matches.
"""

import csv
import hashlib
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from nem.core.types import Liquidity, ManualFill, ManualLot, Side
from nem.store import Store

WEATHER_PREFIX = "KXHIGH"  # daily high-temperature series: KXHIGHNY, KXHIGHTDC, ...
MANUAL_STRATEGY = "manual"
DEFAULT_PORTFOLIO = "manual_baseline"

PNL_COLUMNS = (
    "type", "quantity_fp", "market_ticker", "side", "entry_price_dollars",
    "exit_price_dollars", "open_fees_dollars", "close_fees_dollars",
    "realized_pnl_without_fees_dollars", "realized_pnl_with_fees_dollars", "close_timestamp",
    "open_timestamp",
)  # fmt: skip
ACTIVITY_COLUMNS = (
    "type", "Original_Date", "Market_Ticker", "Direction", "Order_Type", "Price_In_Cents",
    "Amount_In_Dollars", "Fee_In_Dollars",
)  # fmt: skip

TICKER = r"^[A-Z0-9][A-Z0-9._-]*$"
# Kalshi rounds to the cent; anything further off means a shifted or edited column
TOLERANCE = 0.011


class ManualImportError(ValueError):
    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__(f"{len(self.errors)} malformed rows:\n" + "\n".join(self.errors))


class PnlRow(BaseModel):
    """One realized-PnL row. Unknown columns (e.g. `subtrader_id`) are ignored, never stored."""

    model_config = ConfigDict(extra="ignore", frozen=True, allow_inf_nan=False)

    type: Literal["trade", "credit"]
    quantity_fp: float = Field(ge=0)
    market_ticker: str
    side: Literal["yes", "no", ""]
    entry_price_dollars: float = Field(ge=0, le=1)
    exit_price_dollars: float = Field(ge=0, le=1)
    open_fees_dollars: float = Field(ge=0)
    close_fees_dollars: float = Field(ge=0)
    realized_pnl_without_fees_dollars: float
    realized_pnl_with_fees_dollars: float
    close_timestamp: AwareDatetime
    open_timestamp: AwareDatetime

    @model_validator(mode="after")
    def _trade_fields(self) -> Self:
        if self.type == "credit":  # referral credits etc.: not trading, excluded later
            return self
        problems: list[str] = []
        if not self.market_ticker or not _is_ticker(self.market_ticker):
            problems.append(f"market_ticker {self.market_ticker!r} is not a market ticker")
        if self.side == "":
            problems.append("side must be yes or no on a trade")
        if self.quantity_fp <= 0:
            problems.append("quantity_fp must be positive on a trade")
        if self.close_timestamp < self.open_timestamp:
            problems.append("close_timestamp is before open_timestamp")
        fees = self.open_fees_dollars + self.close_fees_dollars
        after = self.realized_pnl_without_fees_dollars - fees
        if abs(after - self.realized_pnl_with_fees_dollars) > TOLERANCE:
            problems.append("realized_pnl_with_fees != realized_pnl_without_fees - fees")
        moved = self.quantity_fp * (self.exit_price_dollars - self.entry_price_dollars)
        if abs(moved - self.realized_pnl_without_fees_dollars) > TOLERANCE:
            problems.append("realized_pnl_without_fees != quantity * (exit - entry)")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def key_fields(self) -> str:
        return "|".join(
            str(x)
            for x in (
                self.type, self.market_ticker, self.side, self.quantity_fp,
                self.entry_price_dollars, self.exit_price_dollars, self.open_fees_dollars,
                self.close_fees_dollars, self.realized_pnl_without_fees_dollars,
                self.realized_pnl_with_fees_dollars, self.open_timestamp.isoformat(),
                self.close_timestamp.isoformat(),
            )
        )  # fmt: skip


class ActivityFill(BaseModel):
    """A `type=Trade` row of the activity export (other row types are not fills)."""

    model_config = ConfigDict(extra="ignore", frozen=True, allow_inf_nan=False)

    Market_Ticker: str = Field(pattern=TICKER)
    Direction: Literal["Yes", "No"]
    Order_Type: Literal["Maker", "Taker"]
    Price_In_Cents: float = Field(gt=0, lt=100)  # the YES price, even on No rows
    Amount_In_Dollars: float = Field(gt=0)  # a contract count, despite the name
    Fee_In_Dollars: float = Field(ge=0)
    Original_Date: AwareDatetime

    @property
    def side(self) -> Side:
        return "yes" if self.Direction == "Yes" else "no"

    @property
    def paid(self) -> float:
        """Dollars paid per contract of `side`."""
        yes = self.Price_In_Cents / 100
        return round(yes if self.side == "yes" else 1 - yes, 6)

    def key_fields(self) -> str:
        return "|".join(
            str(x)
            for x in (
                self.Market_Ticker, self.Direction, self.Order_Type, self.Price_In_Cents,
                self.Amount_In_Dollars, self.Fee_In_Dollars, self.Original_Date.isoformat(),
            )
        )  # fmt: skip


def _is_ticker(s: str) -> bool:
    return re.match(TICKER, s) is not None


def _key(fields: str, occurrence: int) -> str:
    return hashlib.sha256(f"{fields}#{occurrence}".encode()).hexdigest()[:32]


def _rows(
    path: Path, required: Sequence[str], errors: list[str]
) -> Iterator[tuple[int, dict[str, str]]]:
    """(line number, row) for each data row; header problems go to `errors`."""
    try:
        f = path.open(newline="", encoding="utf-8-sig")  # the activity export has a BOM
    except OSError as e:
        errors.append(f"{path}: {e}")
        return
    with f:
        reader = csv.DictReader(f)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            errors.append(f"{path.name}: missing columns {missing}")
            return
        for row in reader:
            if None in row:  # more cells than header columns
                errors.append(f"{path.name} line {reader.line_num}: more cells than columns")
                continue
            yield reader.line_num, {k: (v or "").strip() for k, v in row.items()}


def _messages(e: ValidationError) -> list[str]:
    out: list[str] = []
    for err in e.errors():
        where = ".".join(str(x) for x in err["loc"])
        msg = err["msg"].removeprefix("Value error, ")
        out.append(f"{where}: {msg}" if where else msg)
    return out


@dataclass(frozen=True)
class ImportResult:
    rows: int  # realized-PnL rows read
    credits: int  # excluded: type=credit (referral credits etc.)
    non_weather: int  # excluded: not a KXHIGH market (unless --include-non-weather)
    outside_window: int  # excluded: closed outside --from/--to
    lots: list[ManualLot]  # kept, in file order
    new_lots: int  # of `lots`, not imported before
    fills: int  # activity fills read
    fills_kept: int  # fills on a market with a kept lot
    new_fills: int
    activity_other: int  # activity rows that aren't fills (orders, deposits, credits)


def parse_pnl(
    files: Sequence[Path],
    portfolio: str,
    start: date | None = None,
    end: date | None = None,
    include_non_weather: bool = False,
    strategy: str = MANUAL_STRATEGY,
) -> tuple[list[ManualLot], Counter[str], list[str]]:
    """Kept lots, counts of rows read/excluded by reason, and every validation error."""
    errors: list[str] = []
    counts: Counter[str] = Counter()
    seen: Counter[str] = Counter()
    lots: list[ManualLot] = []
    for path in files:
        for line, raw in _rows(path, PNL_COLUMNS, errors):
            counts["rows"] += 1
            try:
                row = PnlRow.model_validate(raw)
            except ValidationError as e:
                errors += [f"{path.name} line {line}: {m}" for m in _messages(e)]
                continue
            # occurrence index over every row, before filtering, so the window never
            # changes a lot's key
            fields = row.key_fields()
            occurrence = seen[fields]
            seen[fields] += 1
            if row.type == "credit":
                counts["credits"] += 1
                continue
            if not include_non_weather and not row.market_ticker.startswith(WEATHER_PREFIX):
                counts["non_weather"] += 1
                continue
            # inclusive, by the close date in the timestamp's own UTC offset (Kalshi writes
            # US Eastern), so a lot closing 23:30 ET on the last day is in
            closed = row.close_timestamp.date()
            if (start is not None and closed < start) or (end is not None and closed > end):
                counts["outside_window"] += 1
                continue
            side: Side = "yes" if row.side == "yes" else "no"
            lots.append(
                ManualLot(
                    portfolio=portfolio,
                    strategy=strategy,
                    lot_key=_key(fields, occurrence),
                    ticker=row.market_ticker,
                    side=side,
                    qty=row.quantity_fp,
                    entry_price=row.entry_price_dollars,
                    exit_price=row.exit_price_dollars,
                    open_fees=row.open_fees_dollars,
                    close_fees=row.close_fees_dollars,
                    pnl_without_fees=row.realized_pnl_without_fees_dollars,
                    realized_pnl=row.realized_pnl_with_fees_dollars,
                    opened_at=row.open_timestamp,
                    closed_at=row.close_timestamp,
                    source_file=path.name,
                    source_line=line,
                )
            )
    return lots, counts, errors


def parse_activity(path: Path, portfolio: str) -> tuple[list[ManualFill], int, list[str]]:
    """Fills, the number of non-fill rows, and every validation error."""
    errors: list[str] = []
    seen: Counter[str] = Counter()
    fills: list[ManualFill] = []
    other = 0
    for line, raw in _rows(path, ACTIVITY_COLUMNS, errors):
        if raw["type"] != "Trade":
            other += 1
            continue
        try:
            row = ActivityFill.model_validate(raw)
        except ValidationError as e:
            errors += [f"{path.name} line {line}: {m}" for m in _messages(e)]
            continue
        fields = row.key_fields()
        liquidity: Liquidity = "maker" if row.Order_Type == "Maker" else "taker"
        fills.append(
            ManualFill(
                portfolio=portfolio,
                fill_key=_key(fields, seen[fields]),
                ticker=row.Market_Ticker,
                side=row.side,
                qty=row.Amount_In_Dollars,
                price=row.paid,
                fee=row.Fee_In_Dollars,
                liquidity=liquidity,
                filled_at=row.Original_Date,
                source_file=path.name,
                source_line=line,
            )
        )
        seen[fields] += 1
    return fills, other, errors


def import_kalshi_pnl(
    store: Store,
    files: Sequence[Path],
    now: datetime,
    activity: Path | None = None,
    start: date | None = None,
    end: date | None = None,
    include_non_weather: bool = False,
    portfolio: str = DEFAULT_PORTFOLIO,
) -> ImportResult:
    """Validate everything, then store the kept lots (and fills on their markets). Raises
    ManualImportError, writing nothing, if any row is malformed."""
    lots, counts, errors = parse_pnl(files, portfolio, start, end, include_non_weather)
    fills: list[ManualFill] = []
    other = 0
    if activity is not None:
        fills, other, activity_errors = parse_activity(activity, portfolio)
        errors += activity_errors
    if errors:
        raise ManualImportError(errors)
    tickers = {lot.ticker for lot in lots}
    kept = [f for f in fills if f.ticker in tickers]
    new_lots = store.add_manual_lots(lots, now)
    new_fills = store.add_manual_fills(kept, now)
    return ImportResult(
        rows=counts["rows"],
        credits=counts["credits"],
        non_weather=counts["non_weather"],
        outside_window=counts["outside_window"],
        lots=lots,
        new_lots=new_lots,
        fills=len(fills),
        fills_kept=len(kept),
        new_fills=new_fills,
        activity_other=other,
    )


def render_import(result: ImportResult, start: date | None, end: date | None) -> str:
    window = f"{start or 'the first'} to {end or 'the last'} close date"
    lines = [
        f"read {result.rows} realized-PnL rows: excluded {result.credits} credits,"
        f" {result.non_weather} non-weather, {result.outside_window} closed outside {window}",
        f"lots  {len(result.lots)} in window: {result.new_lots} new,"
        f" {len(result.lots) - result.new_lots} already imported",
    ]
    if result.fills or result.activity_other:
        lines.append(
            f"fills {result.fills} in the activity file ({result.activity_other} other rows"
            f" skipped), {result.fills_kept} on imported markets: {result.new_fills} new"
        )
    return "\n".join(lines)
