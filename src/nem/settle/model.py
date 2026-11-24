"""The stored result of checking one market's settlement (table `settlement_checks`)."""

from dataclasses import dataclass
from datetime import date, datetime

from nem.core.types import Side, StrikeType
from nem.settle.rules import RulesSource, Status
from nem.settle.stations import Measure


@dataclass(frozen=True, slots=True)
class SettlementCheck:
    ticker: str
    series: str
    market_date: date | None  # the CLI climate day (local standard time), from the ticker
    measure: Measure
    station: str  # NWS CLI id, e.g. CLINYC
    rules_source: RulesSource  # what the market's rules name as settlement source
    strike_type: StrikeType | None
    floor_strike: float | None
    cap_strike: float | None
    observed_value: float | None  # whole degrees F, None when no source had it
    source: str | None  # iem_cli / iem_asos_obs
    source_ref: str | None  # e.g. the CLI product id
    derived_result: Side | None  # from observed_value and the strike: the replay truth
    kalshi_result: str | None  # as Kalshi reports it ("yes", "no", "scalar", ...)
    kalshi_value: float | None  # Kalshi's expiration_value (its reading)
    kalshi_settlement_value: float | None  # dollars paid per YES contract
    status: Status
    note: str  # every caveat, ";"-separated (approximate source, value mismatch, ...)
    checked_at: datetime
