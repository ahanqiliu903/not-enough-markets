import json
from pathlib import Path

import pytest

from nem import __version__
from nem.cli import main


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_args_prints_overview(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "Paper trading" in capsys.readouterr().out
    assert main(["-h"]) == 0
    assert "nem help COMMAND" in capsys.readouterr().out


def test_help_overview_lists_every_command(capsys: pytest.CaptureFixture[str]) -> None:
    from nem.cli import COMMANDS, HELP_GROUPS, build_parser

    _, cmds = build_parser()
    grouped = {name for _, names in HELP_GROUPS for name in names}
    leaf = {n for n in cmds.parsers if not any(o.startswith(f"{n} ") for o in cmds.parsers)}
    assert grouped == leaf  # a new command must be added to `nem help`
    assert set(COMMANDS) | {"help"} == {n.split()[0] for n in cmds.parsers}
    assert main(["help"]) == 0
    out = capsys.readouterr().out
    assert "  nem run [--dir DIR] [--db DB]" in out
    assert "      paper-trade every portfolio on live Kalshi prices" in out
    assert "[-h]" not in out


def test_help_topic(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["help", "portfolio", "new"]) == 0
    out = capsys.readouterr().out
    assert "usage: nem portfolio new" in out
    assert "starting paper balance" in out
    assert main(["help", "bogus"]) == 1
    assert "unknown command 'bogus'" in capsys.readouterr().err


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
    assert run_cli("summary", "--json", "--db", str(out_db), "--dir", d) == 0
    summary = json.loads(capsys.readouterr().out)
    (strat,) = summary["portfolios"][0]["strategies"]
    assert (strat["strategy"], strat["trades"], strat["verdict"]) == ("btc", 1, "undecided")
    assert run_cli("status", "--json", "--expect", "", "--db", str(out_db), "--dir", d) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["healthy"] and status["strategies"][0]["settled"] == 1

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


def test_suggest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from datetime import UTC, datetime, timedelta

    from nem.core.config import load_portfolio
    from nem.store import Store

    from engine_helpers import settle, window

    d = tmp_path / "portfolios"
    run_cli("portfolio", "new", "research", "--dir", str(d))
    run_cli("strategy", "add", "research", "btc", "--series", "KXBTC15M", "--dir", str(d))
    data = tmp_path / "data.db"
    start = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    with Store(data) as store:
        for i in range(8):
            w = window(start + timedelta(minutes=15 * i), every=timedelta(minutes=1))
            store.insert_snapshots(w)
            store.record_settlement(settle(w, "yes"))
    capsys.readouterr()
    runs = tmp_path / "runs"
    args = ["suggest", "research", "btc", "--data", str(data), "--dir", str(d), "--save", str(runs)]
    grid = ["--grid", "signal.threshold=0.85,0.9,0.95", "--min-trades", "2"]
    assert run_cli(*args, *grid) == 0
    out = capsys.readouterr().out
    assert "Suggestion: suggested starting settings" in out
    assert "- name: btc_suggested" in out
    (saved,) = runs.glob("*.json")
    run = json.loads(saved.read_text())
    assert run["status"] == "suggested" and run["chosen"] is not None
    assert [v["test"] is not None for v in run["variants"]].count(True) == 1  # chosen only
    assert run_cli(*args, *grid, "--write") == 0
    assert "btc_suggested" in [s.name for s in load_portfolio(d / "research.yaml").strategies]

    assert run_cli(*args, "--grid", "signal.nope=1,2") == 1
    assert "no 'signal.nope'" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        run_cli(*args, "--grid", "signal.threshold")  # no values
    with pytest.raises(SystemExit, match="only once"):
        run_cli(*args, "--grid", "signal.threshold=0.9", "--grid", "signal.threshold=0.8")
    with pytest.raises(SystemExit, match="no strategy"):
        run_cli("suggest", "research", "eth", "--data", str(data), "--dir", str(d), *grid)


def test_report_loop_picks_up_portfolio_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    d = tmp_path / "portfolios"
    csv_dir = tmp_path / "out"
    run_cli("portfolio", "new", "research", "--dir", str(d))
    yaml_path = d / "research.yaml"
    yaml_path.write_text(
        yaml_path.read_text().replace(
            "feeds: []", f"feeds: []\nreporting: [{{type: csv, path: '{csv_dir}'}}]"
        )
    )
    strategies_csv = csv_dir / "research" / "strategies.csv"
    sleeps = 0

    def fake_sleep(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            assert "btc" not in strategies_csv.read_text()
            run_cli("strategy", "add", "research", "btc", "--series", "KXBTC15M", "--dir", str(d))
            st = yaml_path.stat()
            os.utime(yaml_path, (st.st_atime, st.st_mtime + 10))  # coarse mtime clocks
        elif sleeps == 2:
            yaml_path.write_text("name: research\nbogus: 1\n")  # bad edit: keep old config
            st = yaml_path.stat()
            os.utime(yaml_path, (st.st_atime, st.st_mtime + 20))
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", fake_sleep)
    db = str(tmp_path / "nem.db")
    with pytest.raises(KeyboardInterrupt):
        run_cli("report", "--every", "5m", "--db", db, "--dir", str(d))
    assert sleeps == 3
    assert "btc" in strategies_csv.read_text()


def test_run_once_records_and_trades_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nem.market.kalshi
    from nem.store import Store

    from kalshi_mock import mock_client

    def offline_client(_env: str) -> nem.market.kalshi.KalshiClient:
        return mock_client()

    monkeypatch.setattr(nem.market.kalshi, "KalshiClient", offline_client)
    d = tmp_path / "portfolios"
    run_cli("portfolio", "new", "research", "--dir", str(d))
    run_cli("strategy", "add", "research", "btc", "--series", "KXBTC15M", "--dir", str(d))
    db = tmp_path / "nem.db"
    args = ["run", "--once", "--dir", str(d), "--db", str(db), "--record-series", "KXETH15M"]
    assert run_cli(*args) == 0
    with Store(db) as store:
        assert store.last_heartbeat("runner") is not None
        assert store.last_heartbeat("recorder") is None  # embedded: no separate heartbeat
        assert {s.series for s in store.iter_snapshots()} == {"KXBTC15M", "KXETH15M"}


def test_status_halt_resume(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from datetime import UTC, datetime

    from nem.store import Store

    db = str(tmp_path / "nem.db")
    d = str(tmp_path / "portfolios")
    run_cli("portfolio", "new", "research", "--dir", d)
    assert run_cli("status", "--db", db, "--dir", d) == 2  # runner never heartbeat
    with Store(db) as store:
        store.heartbeat(datetime.now(UTC), "runner", "ok")
    assert run_cli("status", "--db", db, "--dir", d) == 0
    capsys.readouterr()

    assert run_cli("halt", "research", "--reason", "maintenance", "--db", db, "--dir", d) == 0
    assert run_cli("halt", "typo", "--reason", "x", "--db", db, "--dir", d) == 0
    assert "matches no portfolio" in capsys.readouterr().out
    run_cli("status", "--db", db, "--dir", d)
    assert "research  since 0s ago: maintenance" in capsys.readouterr().out
    assert run_cli("resume", "research", "--db", db, "--dir", d) == 0
    assert run_cli("resume", "research", "--db", db, "--dir", d) == 1
    assert "Active halts: typo" in capsys.readouterr().out
