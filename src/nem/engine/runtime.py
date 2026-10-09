"""Turn portfolio configs into live objects: built plugins plus per-strategy state."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from nem.core.config import FeedSpec, PortfolioConfig, StrategyConfig
from nem.core.registry import REGISTRY, PluginError, Registry
from nem.core.types import StrategyKey
from nem.execution.paper_broker import PaperBroker
from nem.feeds.base import Feed
from nem.gates.base import Gate
from nem.signals.base import SignalPlugin
from nem.sizing.base import Sizer


class BuildError(ValueError):
    pass


@dataclass
class StrategyRuntime:
    portfolio: PortfolioConfig
    config: StrategyConfig
    signal: SignalPlugin
    gates: list[Gate]
    sizer: Sizer
    broker: PaperBroker
    last_check: datetime | None = None
    # (window_id, decision, reason) already logged, so a gate that blocks every tick of a
    # window logs once instead of hundreds of times
    logged: set[tuple[str, str, str]] = field(default_factory=set[tuple[str, str, str]])

    @property
    def key(self) -> StrategyKey:
        return (self.portfolio.name, self.config.name)


@dataclass
class FeedRuntime:
    spec: FeedSpec
    feed: Feed
    last_fetch: datetime | None = None


def _where(portfolio: PortfolioConfig, strategy: StrategyConfig | None, what: str) -> str:
    return f"{portfolio.name}/{strategy.name} {what}" if strategy else f"{portfolio.name} {what}"


def build_strategies(
    portfolios: Sequence[PortfolioConfig], registry: Registry = REGISTRY
) -> list[StrategyRuntime]:
    """Build every non-retired strategy. Errors name the portfolio, strategy and part."""
    out: list[StrategyRuntime] = []
    for p in portfolios:
        if p.mode == "live":
            raise BuildError(f"{p.name}: live trading isn't implemented yet (planned for M8)")
        feed_names = {f.name for f in p.feeds}
        for s in p.strategies:
            if s.status == "retired":
                continue
            try:
                signal = registry.build("signal", s.signal, SignalPlugin)
                gates = [registry.build("gate", g, Gate) for g in s.gates]
                sizer = registry.build("sizer", s.sizing, Sizer)
            except PluginError as e:
                raise BuildError(_where(p, s, str(e))) from e
            for plugin in (signal, *gates):
                missing = sorted(set(plugin.feeds_used()) - feed_names)
                if missing:
                    raise BuildError(_where(p, s, f"{plugin.name} uses undeclared feeds {missing}"))
            out.append(StrategyRuntime(p, s, signal, gates, sizer, PaperBroker(s.taker_fee_rate)))
    return out


def build_feeds(
    portfolios: Sequence[PortfolioConfig], registry: Registry = REGISTRY
) -> list[FeedRuntime]:
    """One runtime per feed name. Values are stored by name, so two portfolios declaring the
    same name must declare the same source."""
    by_name: dict[str, FeedRuntime] = {}
    for p in portfolios:
        for spec in p.feeds:
            if spec.name in by_name:
                if by_name[spec.name].spec.model_dump() != spec.model_dump():
                    raise BuildError(
                        f"feed {spec.name!r} is declared differently across portfolios"
                    )
                continue
            try:
                feed = registry.build("feed", spec, Feed)
            except PluginError as e:
                raise BuildError(_where(p, None, str(e))) from e
            by_name[spec.name] = FeedRuntime(spec, feed)
    return list(by_name.values())
