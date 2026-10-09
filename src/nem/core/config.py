"""Portfolio config (YAML, safe to commit) and env-only secrets.

A portfolio is one YAML file: a pool of capital (paper or live) holding strategies. A
strategy is one market series plus a signal, gates, a sizer and its own risk limits.

Unknown keys are rejected everywhere except inside plugin specs, whose extra keys are the
plugin's params and are validated by the plugin itself at build time (see registry).
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)

from nem.core.types import Mode

NAME_PATTERN = r"^[a-z][a-z0-9_]*$"


class ConfigError(ValueError):
    pass


class _Yaml12Loader(yaml.SafeLoader):
    """YAML 1.1 reads bare yes/no/on/off as booleans, which breaks `yes:`/`no:` keys in a
    prediction-market config. Like YAML 1.2, only true/false are booleans here."""


_Yaml12Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Yaml12Loader.add_implicit_resolver(  # pyright: ignore[reportUnknownMemberType]
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def load_yaml(text: str) -> object:
    return yaml.load(text, Loader=_Yaml12Loader)


_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(s|m|h)$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}


def parse_duration(v: object) -> timedelta:
    """`5s`, `2m`, `1.5h`, or a number of seconds. Must be positive."""
    if isinstance(v, timedelta):
        seconds = v.total_seconds()
    elif isinstance(v, int | float) and not isinstance(v, bool):
        seconds = float(v)
    elif isinstance(v, str) and (m := _DURATION_RE.match(v.strip())):
        seconds = float(m.group(1)) * _UNIT_SECONDS[m.group(2)]
    else:
        raise ValueError(f"expected a duration like 5s, 2m or 1h, got {v!r}")
    if seconds <= 0:
        raise ValueError("duration must be positive")
    return timedelta(seconds=seconds)


Duration = Annotated[timedelta, BeforeValidator(parse_duration)]


def _positive_or_null(v: float | None) -> float | None:
    # 0 once meant "halt immediately" when the author meant "off". Make that impossible.
    if v is not None and v <= 0:
        raise ValueError("must be > 0; use null to disable")
    return v


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PluginSpec(BaseModel):
    """`{type: <registered name>, **params}`."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1)

    @property
    def params(self) -> dict[str, object]:
        return dict(self.model_extra or {})


class RiskConfig(_Strict):
    """Hard limits, checked before every order. They always fail closed. null = disabled."""

    max_allocation: float | None = None  # dollars tied up in open positions
    daily_loss_cap: float | None = None  # realized loss today (exchange time), dollars
    max_drawdown: float | None = None  # peak-to-trough of realized P&L, dollars
    max_open_positions: PositiveInt | None = None
    # Kill rule: stop entering once N trades have settled and net P&L is negative. Holding
    # binaries to settlement, that is the same as "win rate below break-even".
    stop_if_losing_after: PositiveInt | None = None

    _positive = field_validator("max_allocation", "daily_loss_cap", "max_drawdown")(
        _positive_or_null
    )


class FeedSpec(PluginSpec):
    """External data (spot price, forecast, ...). `{name, type, every, max_age, **params}`."""

    name: str = Field(pattern=NAME_PATTERN)
    every: Duration = timedelta(minutes=1)  # how often to fetch when live
    max_age: Duration  # older values count as unavailable


class StrategyConfig(_Strict):
    name: str = Field(pattern=NAME_PATTERN)
    status: Literal["active", "paused", "retired"] = "active"  # retired keeps its history
    series: str = Field(pattern=r"^[A-Z0-9]+$")
    check_every: Duration | None = None  # null = the portfolio's default
    # Kalshi taker fee = round_up_to_cent(rate * contracts * price * (1 - price)).
    # 0.07 for most series; some differ (see the series' fee_multiplier). Verify; it drifts.
    taker_fee_rate: float = Field(default=0.07, ge=0, lt=1)
    signal: PluginSpec
    gates: list[PluginSpec] = Field(default_factory=list[PluginSpec])
    sizing: PluginSpec
    risk: RiskConfig = Field(default_factory=lambda: RiskConfig(max_open_positions=1))


class PortfolioConfig(_Strict):
    name: str = Field(pattern=NAME_PATTERN)
    mode: Mode = "paper"
    # Paper portfolios start with virtual cash; live ones get a budget carved out of the
    # real Kalshi balance (several live portfolios can share one account).
    starting_balance: PositiveFloat | None = None
    budget: PositiveFloat | None = None
    # Exchange for live orders. Real money needs env=prod here AND `nem run --live`.
    env: Literal["demo", "prod"] = "demo"
    # Kalshi pays APY on cash and open positions, accrued daily. The rate changes and has
    # eligibility rules, so set it yourself; null = no interest. Paper portfolios only.
    interest_apy: float | None = Field(default=None, ge=0, lt=1)
    check_every: Duration = timedelta(seconds=5)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    feeds: list[FeedSpec] = Field(default_factory=list[FeedSpec])
    strategies: list[StrategyConfig] = Field(default_factory=list[StrategyConfig])
    reporting: list[PluginSpec] = Field(default_factory=list[PluginSpec])

    @model_validator(mode="after")
    def _cross_checks(self) -> Self:
        if self.mode == "paper" and (self.starting_balance is None or self.budget is not None):
            raise ValueError("paper portfolios need starting_balance (and no budget)")
        if self.mode == "live" and (self.budget is None or self.starting_balance is not None):
            raise ValueError("live portfolios need budget (and no starting_balance)")
        for what, names in (
            ("strategy", [s.name for s in self.strategies]),
            ("feed", [f.name for f in self.feeds]),
        ):
            dupes = sorted({n for n in names if names.count(n) > 1})
            if dupes:
                raise ValueError(f"duplicate {what} names: {dupes}")
        return self

    def strategy(self, name: str) -> StrategyConfig:
        for s in self.strategies:
            if s.name == name:
                return s
        raise KeyError(name)

    def check_every_for(self, strategy: StrategyConfig) -> timedelta:
        return strategy.check_every if strategy.check_every is not None else self.check_every

    def feed(self, name: str) -> FeedSpec:
        for f in self.feeds:
            if f.name == name:
                return f
        raise KeyError(name)


def load_portfolio(path: str | Path) -> PortfolioConfig:
    """Load one portfolio file. Its `name` must match the file name, so the two never drift."""
    path = Path(path)
    try:
        raw = load_yaml(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"{path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    try:
        portfolio = PortfolioConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"{path}: {e}") from e
    if portfolio.name != path.stem:
        raise ConfigError(f"{path}: name {portfolio.name!r} must match the file name")
    return portfolio


def load_portfolios(directory: str | Path) -> list[PortfolioConfig]:
    """Every `*.yaml` in `directory`, sorted by name."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ConfigError(f"{directory}: not a directory")
    return [load_portfolio(p) for p in sorted(directory.glob("*.yaml"))]


@dataclass(frozen=True)
class Secrets:
    """Credentials, read only from environment variables. Never logged in full."""

    kalshi_api_key_id: str | None = None
    kalshi_private_key_path: str | None = None
    sheets_credentials: str | None = None
    sheet_id: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "Secrets":
        return cls(**{f.name: environ.get(f.name.upper()) or None for f in fields(cls)})

    def masked(self) -> dict[str, str]:
        return {f.name: "set" if getattr(self, f.name) else "unset" for f in fields(self)}

    def __repr__(self) -> str:
        return f"Secrets({self.masked()})"
