from datetime import timedelta
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
    load_yaml,
    parse_duration,
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


def test_check_every_inherits_and_overrides() -> None:
    p = PortfolioConfig.model_validate(
        portfolio_dict(
            check_every="7s",
            strategies=[strategy_dict(), strategy_dict(name="slow", check_every="2m")],
        )
    )
    assert p.check_every_for(p.strategy("fav90")) == timedelta(seconds=7)
    assert p.check_every_for(p.strategy("slow")) == timedelta(minutes=2)


@pytest.mark.parametrize(
    ("raw", "seconds"), [("5s", 5), ("2m", 120), ("1.5h", 5400), (30, 30), (0.5, 0.5)]
)
def test_parse_duration(raw: object, seconds: float) -> None:
    assert parse_duration(raw) == timedelta(seconds=seconds)


@pytest.mark.parametrize("raw", ["0s", -1, "5", "5 days", True, None])
def test_parse_duration_rejects(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_duration(raw)


def test_feeds() -> None:
    feed = {"name": "btc_spot", "type": "fake", "product": "BTC-USD", "max_age": "30s"}
    p = PortfolioConfig.model_validate(portfolio_dict(feeds=[feed]))
    spec = p.feed("btc_spot")
    assert spec.params == {"product": "BTC-USD"}
    assert (spec.every, spec.max_age) == (timedelta(minutes=1), timedelta(seconds=30))
    with pytest.raises(ValidationError, match="duplicate feed"):
        PortfolioConfig.model_validate(portfolio_dict(feeds=[feed, feed]))
    with pytest.raises(ValidationError, match="max_age"):
        PortfolioConfig.model_validate(portfolio_dict(feeds=[{"name": "x", "type": "fake"}]))


def test_yes_no_keys_stay_strings() -> None:
    # YAML 1.1 would turn these into True/False
    assert load_yaml("yes: 1\nno: 2\nside: yes\non: off\nok: true") == {
        "yes": 1,
        "no": 2,
        "side": "yes",
        "on": "off",
        "ok": True,
    }


@pytest.mark.parametrize("field", ["daily_loss_cap", "max_allocation", "max_drawdown"])
@pytest.mark.parametrize("cap", [0, -5])
def test_dollar_limits_zero_or_negative_rejected(field: str, cap: float) -> None:
    with pytest.raises(ValidationError, match="use null to disable"):
        PortfolioConfig.model_validate(portfolio_dict(risk={field: cap}))


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
