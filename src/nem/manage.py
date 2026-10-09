"""Create and edit portfolio files.

Edits append text instead of re-serializing YAML, so comments and formatting survive. That
needs `strategies:` to be the last top-level key, which every generated file guarantees.
Each edit is validated before it's written; an invalid result leaves the file untouched.
"""

import re
from pathlib import Path

from nem.core.config import ConfigError, PortfolioConfig, load_yaml

PORTFOLIO_TEMPLATE = """\
# NotEnoughMarkets portfolio: one pool of capital and the strategies that trade it.
name: {name}
mode: paper
starting_balance: {balance}
interest_apy: null        # Kalshi's APY on cash + positions, e.g. 0.0325. It changes; check it.
check_every: 5s           # how often strategies look for entries (override per strategy)

risk:                     # portfolio-wide limits; null = disabled (never 0)
  daily_loss_cap: null
  max_drawdown: null
  max_open_positions: null

feeds: []                 # external data, e.g. spot prices or forecasts (see README)

# Keep `strategies` last: `nem strategy add` appends here.
strategies: []
"""

STRATEGY_TEMPLATE = """\

  - name: {name}
    series: {series}
    status: active        # active | paused | retired
    check_every: null     # null = portfolio default
    signal:
      type: extreme_favorite
      threshold: 0.90     # consider the favorite once its ask is >= 90c
      min_edge: 0.01      # required p_model - price - fee
      # Your starting belief about the favorite's win rate, as if you'd seen n trades.
      # This is the hypothesis being tested, so keep it honest and small.
      priors: {{yes: {{wins: 93, n: 100}}, no: {{wins: 93, n: 100}}}}
    gates:
      - {{type: max_entry_price, value: 0.95}}
    sizing: {{type: fixed, contracts: 1}}
    risk: {{max_open_positions: 1, stop_if_losing_after: 50}}
"""


def portfolio_path(directory: Path, name: str) -> Path:
    return directory / f"{name}.yaml"


def _validate(text: str, path: Path) -> PortfolioConfig:
    try:
        return PortfolioConfig.model_validate(load_yaml(text))
    except Exception as e:
        raise ConfigError(f"{path}: edit would make the file invalid: {e}") from e


def new_portfolio(directory: Path, name: str, balance: float) -> Path:
    path = portfolio_path(directory, name)
    if path.exists():
        raise ConfigError(f"{path} already exists")
    text = PORTFOLIO_TEMPLATE.format(name=name, balance=f"{balance:g}")
    _validate(text, path)
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def add_strategy(directory: Path, portfolio: str, name: str, series: str) -> Path:
    path = portfolio_path(directory, portfolio)
    if not path.exists():
        raise ConfigError(f"{path} not found; create it with `nem portfolio new {portfolio}`")
    text = path.read_text()
    top_level = re.findall(r"^([A-Za-z_]+):", text, flags=re.MULTILINE)
    if not top_level or top_level[-1] != "strategies":
        raise ConfigError(f"{path}: `strategies:` must be the last top-level key to append")
    text = re.sub(r"^strategies:\s*\[\]\s*$", "strategies:", text, flags=re.MULTILINE)
    text = text.rstrip("\n") + "\n" + STRATEGY_TEMPLATE.format(name=name, series=series)
    _validate(text, path)
    path.write_text(text)
    return path
