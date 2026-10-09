from pathlib import Path

import pytest

from nem import __version__
from nem.cli import main


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: nem" in capsys.readouterr().out


def test_record_requires_series(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["record"])
    assert exc.value.code == 2
    assert "--series" in capsys.readouterr().err


def run_cli(*args: str) -> int:
    return main(list(args))


def test_init_portfolio_and_strategy_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    d = str(tmp_path / "portfolios")
    answers = iter(["research", "500"])

    def fake_input(_prompt: str) -> str:
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)
    assert run_cli("init", "--dir", d) == 0
    assert run_cli("init", "--dir", d) == 1  # already set up
    assert run_cli("strategy", "add", "research", "btc", "--series", "KXBTC15M", "--dir", d) == 0
    assert run_cli("portfolio", "new", "second", "--balance", "50", "--dir", d) == 0
    capsys.readouterr()

    assert run_cli("validate", "--dir", d) == 0
    out = capsys.readouterr().out
    assert "ok  research: 1 strategies" in out
    assert "ok  second: 0 strategies" in out

    assert run_cli("portfolio", "list", "--dir", d) == 0
    assert "$500.00" in capsys.readouterr().out
    assert run_cli("portfolio", "show", "research", "--dir", d) == 0
    assert "signal=extreme_favorite" in capsys.readouterr().out


def test_errors_are_reported_not_raised(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d = str(tmp_path)
    assert run_cli("strategy", "add", "nope", "btc", "--series", "KXBTC15M", "--dir", d) == 1
    assert "error:" in capsys.readouterr().err


def test_replay_and_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from datetime import UTC, datetime

    from nem.store import Store

    from engine_helpers import settle, window

    d = str(tmp_path / "portfolios")
    run_cli("portfolio", "new", "research", "--dir", d)
    run_cli("strategy", "add", "research", "btc", "--series", "KXBTC15M", "--dir", d)
    data = tmp_path / "data.db"
    w = window(datetime(2026, 10, 7, 19, 0, tzinfo=UTC))
    with Store(data) as store:
        store.insert_snapshots(w)
        store.record_settlement(settle(w, "yes"))
    out_db = tmp_path / "results.db"
    capsys.readouterr()
    assert run_cli("replay", "--data", str(data), "--out", str(out_db), "--dir", d) == 0
    out = capsys.readouterr().out
    assert "btc  KXBTC15M  active  1 trades (1 won)" in out
    assert run_cli("summary", "--db", str(out_db), "--dir", d) == 0
    assert "Portfolio research (paper)" in capsys.readouterr().out

    # report publishes to the portfolio's reporters (csv here)
    yaml_path = tmp_path / "portfolios" / "research.yaml"
    csv_dir = tmp_path / "out"
    text = yaml_path.read_text().replace(
        "feeds: []", f"feeds: []\nreporting: [{{type: csv, path: '{csv_dir}'}}]"
    )
    yaml_path.write_text(text)
    assert run_cli("report", "--db", str(out_db), "--dir", d) == 0
    assert (csv_dir / "research" / "strategies.csv").exists()
    with Store(out_db) as store:
        assert store.last_heartbeat("reporter") is not None
