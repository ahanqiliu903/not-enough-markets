from pathlib import Path

import pytest

from nem.core.config import ConfigError, load_portfolio
from nem.manage import add_strategy, new_portfolio


def test_new_portfolio_then_add_strategies(tmp_path: Path) -> None:
    path = new_portfolio(tmp_path, "research", 750)
    assert load_portfolio(path).starting_balance == 750
    assert load_portfolio(path).strategies == []

    add_strategy(tmp_path, "research", "btc", "KXBTC15M")
    add_strategy(tmp_path, "research", "eth", "KXETH15M")
    p = load_portfolio(path)
    assert [(s.name, s.series) for s in p.strategies] == [("btc", "KXBTC15M"), ("eth", "KXETH15M")]
    assert p.strategy("btc").signal.params["priors"] == {
        "yes": {"wins": 93, "n": 100},
        "no": {"wins": 93, "n": 100},
    }
    assert "# Keep `strategies` last" in path.read_text()  # comments survive edits


def test_new_portfolio_refuses_to_overwrite(tmp_path: Path) -> None:
    new_portfolio(tmp_path, "research", 100)
    with pytest.raises(ConfigError, match="already exists"):
        new_portfolio(tmp_path, "research", 100)


def test_invalid_names_rejected_before_writing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid"):
        new_portfolio(tmp_path, "Bad Name", 100)
    path = new_portfolio(tmp_path, "research", 100)
    with pytest.raises(ConfigError, match="invalid"):
        add_strategy(tmp_path, "research", "btc", "lowercase")
    with pytest.raises(ConfigError, match="invalid"):  # duplicate name
        add_strategy(tmp_path, "research", "x", "KXBTC15M")
        add_strategy(tmp_path, "research", "x", "KXBTC15M")
    assert "lowercase" not in path.read_text()
    assert path.read_text().count("- name: x") == 1  # the duplicate was never written


def test_add_strategy_needs_strategies_last(tmp_path: Path) -> None:
    path = new_portfolio(tmp_path, "research", 100)
    path.write_text(path.read_text() + "feeds: []\n")
    with pytest.raises(ConfigError, match="must be the last"):
        add_strategy(tmp_path, "research", "btc", "KXBTC15M")


def test_add_strategy_to_missing_portfolio(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="nem portfolio new"):
        add_strategy(tmp_path, "nope", "btc", "KXBTC15M")
