"""Fill in fee rates a portfolio leaves null from the series' own fee schedule.

`taker_fee_rate: null` becomes 0.07 x the series' `fee_multiplier`; a take-profit's
`maker_fee_rate: null` becomes 0 on "quadratic" series (no maker fee) and 0.0175 x
`fee_multiplier` on "quadratic_with_maker_fees" ones (nem.execution.fees). A rate set in
the YAML always wins and is never looked up. Only `nem run` and `nem validate` look rates
up (from GET /series/{ticker}); replay and suggest don't touch the network, so there a
null maker rate stops the run (build_strategies) and a null taker rate means 0.07.
Series metadata isn't recorded in the store, so nothing is cached across processes.
"""

from collections.abc import Callable, Sequence

from nem.core.config import ConfigError, PortfolioConfig, StrategyConfig
from nem.execution.fees import FeeScheduleUnknown, series_maker_rate, series_taker_rate
from nem.market.kalshi import KalshiError, SeriesFees

Lookup = Callable[[str], SeriesFees]


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
