from dataclasses import dataclass
from datetime import datetime

from nem.core.config import StrategyConfig
from nem.stats.metrics import StrategyStats

type ParamValue = float | int | str | bool | None

# Dotted paths into a strategy's config -> values to try, in order. Paths index lists by
# position: {"signal.threshold": [0.88, 0.90, 0.92], "gates.0.value": [0.94, 0.96]}.
# Order matters: neighbours along each axis are what "stable region" means.
type Grid = dict[str, list[ParamValue]]


@dataclass(frozen=True, slots=True)
class Period:
    """Half-open time range, `start <= ts < end` (UTC)."""

    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class Variant:
    name: str  # unique within a sweep, a valid strategy name, e.g. "v07"
    overrides: dict[str, ParamValue]  # one value per grid path
    index: tuple[int, ...]  # position of each value in its grid axis, in grid order
    config: StrategyConfig  # the base strategy with overrides applied, renamed to `name`


@dataclass(frozen=True, slots=True)
class VariantResult:
    variant: Variant
    period: Period
    stats: StrategyStats
