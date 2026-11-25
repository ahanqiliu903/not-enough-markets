"""Fill in fee rates a portfolio leaves null from the series' own fee schedule.

`taker_fee_rate: null` becomes 0.07 x the series' `fee_multiplier`; a take-profit's
`maker_fee_rate: null` becomes 0 on "quadratic" series (no maker fee) and 0.0175 x
`fee_multiplier` on "quadratic_with_maker_fees" ones (nem.execution.fees). A rate set in
the YAML always wins and is never looked up. Only `nem run` and `nem validate` look rates
up (from GET /series/{ticker}), and `nem run` records each schedule it reads in the store
(`series_fees`, schema v10). Replay and suggest don't touch the network: they resolve null
rates from the schedule recorded in the data they replay (`resolve_fees_offline`); with
none recorded, a null maker rate stops the run (build_strategies) and a null taker rate is
the default 0.07, filled in and labelled as such. Every rate carries where it came from
(`FeeRate.source`), printed by replay/suggest and saved with each backtest run.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from nem.core.config import ConfigError, PortfolioConfig, StrategyConfig
from nem.execution.fees import (
    DEFAULT_TAKER_RATE,
    FeeScheduleUnknown,
    series_maker_rate,
    series_taker_rate,
)
from nem.market.kalshi import KalshiError, SeriesFees

Lookup = Callable[[str], SeriesFees]
# Recorded schedule of a series and when it was read, or None (Store.series_fees).
Recorded = Callable[[str], tuple[SeriesFees, datetime] | None]
FeeSource = Literal["config", "recorded_series", "default", "unresolved"]


@dataclass(frozen=True, slots=True)
class FeeRate:
    rate: float | None  # None only for an unresolved maker rate (the run refuses to start)
    source: FeeSource
    detail: str  # e.g. which recorded schedule, or why the default applies

    def describe(self) -> str:
        rate = "unknown" if self.rate is None else f"{self.rate:.4f}"
        return f"{rate} ({self.source}: {self.detail})"

    def as_dict(self) -> dict[str, object]:
        return {"rate": self.rate, "source": self.source, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class StrategyFees:
    portfolio: str
    strategy: str
    series: str
    taker: FeeRate
    maker: FeeRate | None  # None: no take-profit, no maker fee

    def describe(self) -> str:
        out = f"{self.portfolio}/{self.strategy}: taker {self.taker.describe()}"
        if self.maker is not None:
            out += f", maker {self.maker.describe()}"
        return out

    def as_dict(self) -> dict[str, object]:
        return {
            "portfolio": self.portfolio,
            "strategy": self.strategy,
            "series": self.series,
            "taker_fee_rate": self.taker.as_dict(),
            "maker_fee_rate": None if self.maker is None else self.maker.as_dict(),
        }


class FeeLookupError(ConfigError):
    pass


def _needs(s: StrategyConfig, *, taker: bool) -> tuple[bool, bool]:
    tp = s.exit.take_profit if s.exit is not None else None
    return taker and s.taker_fee_rate is None, tp is not None and tp.maker_fee_rate is None


def resolve_fees(
    portfolios: Sequence[PortfolioConfig], lookup: Lookup, *, taker: bool = True
) -> list[PortfolioConfig]:
    """Copies of `portfolios` with every null rate of a non-retired strategy filled in.
    `taker=False` leaves null taker rates alone (0.07), so only maker rates are looked up.
    Raises FeeLookupError, naming the strategy, when a needed schedule can't be read or
    isn't modelled: fail closed rather than trade on a guessed fee."""
    cache: dict[str, SeriesFees] = {}
    out: list[PortfolioConfig] = []
    for p in portfolios:
        strategies: list[StrategyConfig] = []
        for s in p.strategies:
            need_taker, need_maker = _needs(s, taker=taker)
            if s.status == "retired" or not (need_taker or need_maker):
                strategies.append(s)
                continue
            where = f"{p.name}/{s.name} ({s.series})"
            try:
                if s.series not in cache:
                    cache[s.series] = lookup(s.series)
                fees = cache[s.series]
                update: dict[str, object] = {}
                if need_taker:
                    update["taker_fee_rate"] = series_taker_rate(fees.fee_type, fees.fee_multiplier)
                if need_maker and s.exit is not None and s.exit.take_profit is not None:
                    rate = series_maker_rate(fees.fee_type, fees.fee_multiplier)
                    tp = s.exit.take_profit.model_copy(update={"maker_fee_rate": rate})
                    update["exit"] = s.exit.model_copy(update={"take_profit": tp})
            except (KalshiError, FeeScheduleUnknown) as e:
                which = " and ".join(
                    n for n, need in (("taker_fee_rate", need_taker),
                                      ("exit.take_profit.maker_fee_rate", need_maker)) if need
                )  # fmt: skip
                raise FeeLookupError(
                    f"{where}: can't read the series' fee schedule ({e}); set {which}"
                    " explicitly in the portfolio file"
                ) from e
            strategies.append(s.model_copy(update=update))
        out.append(p.model_copy(update={"strategies": strategies}))
    return out


def resolve_fees_offline(
    portfolios: Sequence[PortfolioConfig], recorded: Recorded
) -> tuple[list[PortfolioConfig], list[StrategyFees]]:
    """Replay/suggest: copies of `portfolios` with every null rate of a non-retired
    strategy filled in, from the series' schedule recorded in the replayed data or, for a
    taker rate, the labelled default 0.07, plus where each rate came from. A null maker
    rate with nothing recorded stays null (build_strategies refuses it). Never touches
    the network, so the result depends only on the config and the data."""
    out: list[PortfolioConfig] = []
    sources: list[StrategyFees] = []
    for p in portfolios:
        strategies: list[StrategyConfig] = []
        for s in p.strategies:
            if s.status == "retired":
                strategies.append(s)
                continue
            found = recorded(s.series)
            schedule = (
                None if found is None
                else f"{s.series} {found[0].fee_type} x{found[0].fee_multiplier:g},"
                f" read {found[1].isoformat()}"
            )  # fmt: skip
            update: dict[str, object] = {}
            if s.taker_fee_rate is not None:
                taker = FeeRate(s.taker_fee_rate, "config", "taker_fee_rate in the portfolio")
            else:
                taker = _from_schedule(found, schedule, series_taker_rate)
                if taker is None:
                    why = (
                        f"no fee schedule recorded for {s.series}"
                        if found is None
                        else f"{found[0].fee_type!r} isn't modelled"
                    )
                    taker = FeeRate(DEFAULT_TAKER_RATE, "default", f"{why}; set taker_fee_rate")
                update["taker_fee_rate"] = taker.rate
            maker: FeeRate | None = None
            tp = s.exit.take_profit if s.exit is not None else None
            if s.exit is not None and tp is not None:
                if tp.maker_fee_rate is not None:
                    maker = FeeRate(tp.maker_fee_rate, "config", "maker_fee_rate in the portfolio")
                else:
                    maker = _from_schedule(found, schedule, series_maker_rate) or FeeRate(
                        None, "unresolved", f"no usable fee schedule recorded for {s.series}"
                    )
                    if maker.rate is not None:
                        tp = tp.model_copy(update={"maker_fee_rate": maker.rate})
                        update["exit"] = s.exit.model_copy(update={"take_profit": tp})
            strategies.append(s.model_copy(update=update))
            sources.append(StrategyFees(p.name, s.name, s.series, taker, maker))
        out.append(p.model_copy(update={"strategies": strategies}))
    return out, sources


def _from_schedule(
    found: tuple[SeriesFees, datetime] | None,
    schedule: str | None,
    rate: Callable[[str, float], float],
) -> FeeRate | None:
    if found is None or schedule is None:
        return None
    try:
        return FeeRate(
            rate(found[0].fee_type, found[0].fee_multiplier), "recorded_series", schedule
        )
    except FeeScheduleUnknown:
        return None
