from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from nem.core.config import (
    ConfigError,
    PortfolioConfig,
    Secrets,
    load_portfolio,
    load_portfolios,
)

from factories import portfolio_dict


def strategy_dict(**kw: Any) -> dict[str, Any]:
    d = portfolio_dict()["strategies"][0]
    d.update(kw)
    return d


def test_valid_portfolio() -> None:
    p = PortfolioConfig.model_validate(portfolio_dict())
    s = p.strategy("fav90")
    assert (p.mode, p.env, s.status) == ("paper", "demo", "active")
    assert s.signal.type == "extreme_favorite"
    assert s.signal.params == {"threshold": 0.9}
    assert s.gates[0].params == {"value": 0.95}
    assert s.risk.max_open_positions == 1
    assert p.risk.max_open_positions is None


def test_poll_seconds_inherits_and_overrides() -> None:
    p = PortfolioConfig.model_validate(
        portfolio_dict(
            poll_seconds=7,
            strategies=[strategy_dict(), strategy_dict(name="fast", poll_seconds=1)],
        )
    )
    assert p.poll_seconds_for(p.strategy("fav90")) == 7
    assert p.poll_seconds_for(p.strategy("fast")) == 1


@pytest.mark.parametrize("cap", [0, -5])
def test_daily_loss_cap_zero_or_negative_rejected(cap: float) -> None:
    with pytest.raises(ValidationError, match="use null to disable"):
        PortfolioConfig.model_validate(portfolio_dict(risk={"daily_loss_cap": cap}))


def test_daily_loss_cap_null_disables() -> None:
    p = PortfolioConfig.model_validate(portfolio_dict(risk={"daily_loss_cap": None}))
    assert p.risk.daily_loss_cap is None


def test_unknown_keys_rejected_at_every_level() -> None:
    with pytest.raises(ValidationError, match="extra"):
        PortfolioConfig.model_validate(portfolio_dict(typo=1))
    with pytest.raises(ValidationError, match="extra"):
        PortfolioConfig.model_validate(portfolio_dict(strategies=[strategy_dict(typo=1)]))
    with pytest.raises(ValidationError, match="extra"):
        PortfolioConfig.model_validate(portfolio_dict(risk={"daily_loss_capp": 5}))


def test_paper_needs_starting_balance_and_live_needs_budget() -> None:
    with pytest.raises(ValidationError, match="starting_balance"):
        PortfolioConfig.model_validate(portfolio_dict(starting_balance=None))
    with pytest.raises(ValidationError, match="budget"):
        PortfolioConfig.model_validate(portfolio_dict(mode="live"))
    live = portfolio_dict(mode="live", starting_balance=None, budget=50)
    p = PortfolioConfig.model_validate(live)
    assert p.budget == 50


def test_duplicate_strategy_names_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        PortfolioConfig.model_validate(portfolio_dict(strategies=[strategy_dict()] * 2))


def test_empty_portfolio_allowed() -> None:
    # `nem init` creates a portfolio before any strategy is added.
    assert PortfolioConfig.model_validate(portfolio_dict(strategies=[])).strategies == []


@pytest.mark.parametrize(
    ("field", "value"), [("name", "Bad-Name"), ("series", "kxbtc"), ("status", "deleted")]
)
def test_strategy_field_validation(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        PortfolioConfig.model_validate(portfolio_dict(strategies=[strategy_dict(**{field: value})]))


def write(path: Path, data: object) -> Path:
    path.write_text(yaml.safe_dump(data))
    return path


def test_load_portfolio(tmp_path: Path) -> None:
    p = load_portfolio(write(tmp_path / "research.yaml", portfolio_dict()))
    assert p.name == "research"


def test_load_portfolio_name_must_match_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="match the file name"):
        load_portfolio(write(tmp_path / "other.yaml", portfolio_dict()))


def test_load_portfolio_errors_name_the_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"research\.yaml.*mapping"):
        load_portfolio(write(tmp_path / "research.yaml", ["not", "a", "mapping"]))
    with pytest.raises(ConfigError, match=r"research\.yaml"):
        load_portfolio(write(tmp_path / "research.yaml", portfolio_dict(typo=1)))
    with pytest.raises(ConfigError, match="missing"):
        load_portfolio(tmp_path / "missing.yaml")


def test_load_portfolios(tmp_path: Path) -> None:
    write(tmp_path / "research.yaml", portfolio_dict())
    write(tmp_path / "alpha.yaml", portfolio_dict(name="alpha"))
    (tmp_path / "notes.txt").write_text("ignored")
    assert [p.name for p in load_portfolios(tmp_path)] == ["alpha", "research"]
    with pytest.raises(ConfigError, match="not a directory"):
        load_portfolios(tmp_path / "nope")


def test_secrets_from_env_are_masked() -> None:
    s = Secrets.from_env({"KALSHI_API_KEY_ID": "super-secret-id", "SHEET_ID": ""})
    assert s.kalshi_api_key_id == "super-secret-id"
    assert s.sheet_id is None
    assert "super-secret-id" not in repr(s)
    assert s.masked()["kalshi_api_key_id"] == "set"
    assert s.masked()["sheet_id"] == "unset"
