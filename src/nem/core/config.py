"""Portfolio config (YAML, safe to commit) and env-only secrets.

A portfolio is one YAML file: a pool of capital (paper or live) holding strategies. A
strategy is one market series plus a signal, gates, a sizer and its own risk limits.

Unknown keys are rejected everywhere except inside plugin specs, whose extra keys are the
plugin's params and are validated by the plugin itself at build time (see registry).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import (
    BaseModel,
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
    daily_loss_cap: float | None = None  # dollars; null = disabled
    max_open_positions: PositiveInt | None = None  # null = unlimited

    @field_validator("daily_loss_cap")
    @classmethod
    def _positive_or_null(cls, v: float | None) -> float | None:
        # 0 once meant "halt immediately" when the author meant "off". Make that impossible.
        if v is not None and v <= 0:
            raise ValueError("must be > 0; use null to disable")
        return v


class StrategyConfig(_Strict):
    name: str = Field(pattern=NAME_PATTERN)
    status: Literal["active", "paused", "retired"] = "active"  # retired keeps its history
    series: str = Field(pattern=r"^[A-Z0-9]+$")
    poll_seconds: PositiveFloat | None = None  # null = the portfolio's default
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
    poll_seconds: PositiveFloat = 5.0
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategies: list[StrategyConfig] = Field(default_factory=list[StrategyConfig])
    reporting: list[PluginSpec] = Field(default_factory=list[PluginSpec])

    @model_validator(mode="after")
    def _cross_checks(self) -> Self:
        if self.mode == "paper" and (self.starting_balance is None or self.budget is not None):
            raise ValueError("paper portfolios need starting_balance (and no budget)")
        if self.mode == "live" and (self.budget is None or self.starting_balance is not None):
            raise ValueError("live portfolios need budget (and no starting_balance)")
        names = [s.name for s in self.strategies]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate strategy names: {dupes}")
        return self

    def strategy(self, name: str) -> StrategyConfig:
        for s in self.strategies:
            if s.name == name:
                return s
        raise KeyError(name)

    def poll_seconds_for(self, strategy: StrategyConfig) -> float:
        return strategy.poll_seconds if strategy.poll_seconds is not None else self.poll_seconds


def load_portfolio(path: str | Path) -> PortfolioConfig:
    """Load one portfolio file. Its `name` must match the file name, so the two never drift."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
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
