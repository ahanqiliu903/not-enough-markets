"""Command-line entry point."""

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from nem import __version__

if TYPE_CHECKING:
    from nem.backtest.types import Grid, ParamValue
    from nem.core.config import PortfolioConfig
    from nem.core.types import Trade
    from nem.market.kalshi import KalshiClient, SeriesFees
    from nem.stats.report import PortfolioReport
    from nem.store import Store

log = logging.getLogger("nem")

DEFAULT_DB = Path("data/nem.db")
DEFAULT_DIR = Path("portfolios")
DEMO_DATA = Path("examples/data/sample.jsonl.gz")
DEMO_DIR = Path("examples/portfolios")
# `nem import kalshi-history`: a separate file, never the engine's recorded data
DEFAULT_HISTORY_DB = Path("data/private/kalshi_history.db")
DEFAULT_HISTORY_CACHE = Path("data/private/cache/kalshi")


def _when(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _grid_arg(s: str) -> "tuple[str, list[ParamValue]]":
    from nem.core.config import load_yaml

    path, sep, raw = s.partition("=")
    if not sep or not path.strip() or not raw.strip():
        raise argparse.ArgumentTypeError(f"{s!r}: expected PATH=V1,V2,...")
    values: list[ParamValue] = []
    for v in raw.split(","):
        value = load_yaml(v.strip())  # YAML scalars: 0.9, 3, true, null, 2m
        if not isinstance(value, float | int | str | bool | None):
            raise argparse.ArgumentTypeError(f"{s!r}: {v!r} is not a single value")
        values.append(value)
    return path.strip(), values


# Commands in the order `nem help` shows them.
HELP_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Getting started", ("init", "demo")),
    ("Portfolios and strategies", ("portfolio new", "portfolio list", "portfolio show",
                                   "strategy add", "validate")),
    ("Backtest", ("replay", "suggest", "import kalshi-history", "history replay",
                  "history suggest")),
    ("Paper trading", ("run",)),
    ("Results", ("summary", "report", "trade exclude", "trade include", "trade list")),
    ("Manual trading history", ("import kalshi-pnl", "settle verify", "backfill forecasts",
                                "backfill coverage")),
    ("Operations (24/7)", ("status", "halt", "resume")),
    ("Market data", ("record", "export", "series uncovered")),
    ("Help", ("help",)),
)  # fmt: skip


class SubParsers(Protocol):
    """What argparse's (private) subparsers action offers."""

    def add_parser(self, name: str, **kwargs: Any) -> argparse.ArgumentParser: ...


class Commands:
    """Every (sub)command parser by name, e.g. "run" or "portfolio new", for `nem help`."""

    def __init__(self) -> None:
        self.parsers: dict[str, argparse.ArgumentParser] = {}
        self.helps: dict[str, str] = {}

    def add(
        self,
        sub: "SubParsers",
        name: str,
        help_text: str,
        prefix: str = "",
    ) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text)
        full = f"{prefix} {name}".strip()
        self.parsers[full] = p
        self.helps[full] = help_text
        return p


def build_parser() -> tuple[argparse.ArgumentParser, Commands]:
    parser = argparse.ArgumentParser(
        prog="nem", description="NotEnoughMarkets: test Kalshi trading ideas", add_help=False
    )
    parser.add_argument("-h", "--help", action="store_true", help="show all commands")
    parser.add_argument("--version", action="version", version=f"nem {__version__}")
    sub = parser.add_subparsers(dest="command")
    cmds = Commands()

    def with_dir(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument(
            "--dir",
            type=Path,
            default=DEFAULT_DIR,
            help=f"portfolio files (default: {DEFAULT_DIR})",
        )
        return p

    def with_db(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument(
            "--db", type=Path, default=DEFAULT_DB, help=f"database (default: {DEFAULT_DB})"
        )
        return p

    with_dir(cmds.add(sub, "init", "create your first paper portfolio (asks name and balance)"))

    port = cmds.add(sub, "portfolio", "create, list and show portfolios")
    port_sub = port.add_subparsers(dest="action", required=True)
    new = with_dir(cmds.add(port_sub, "new", "create a paper portfolio file", "portfolio"))
    new.add_argument("name", help="lowercase name, e.g. research")
    new.add_argument("--balance", type=float, default=1000.0, help="starting paper balance in $")
    with_dir(
        cmds.add(port_sub, "list", "list portfolios: mode, capital, active strategies", "portfolio")
    )
    show = with_dir(
        cmds.add(port_sub, "show", "show a portfolio's strategies and plugins", "portfolio")
    )
    show.add_argument("name")

    strat = cmds.add(sub, "strategy", "add strategies to a portfolio")
    strat_sub = strat.add_subparsers(dest="action", required=True)
    add = with_dir(
        cmds.add(strat_sub, "add", "append a strategy template to a portfolio file", "strategy")
    )
    add.add_argument("portfolio", help="portfolio name")
    add.add_argument("name", help="strategy name, e.g. fav90")
    add.add_argument("--series", required=True, help="Kalshi series, e.g. KXBTC15M")

    with_dir(cmds.add(sub, "validate", "check every portfolio file and plugin before running"))

    run = with_db(with_dir(cmds.add(
        sub, "run", "paper-trade every portfolio on live Kalshi prices (records data too)"
    )))  # fmt: skip
    run.add_argument(
        "--depth",
        type=int,
        default=10,
        help="orderbook levels kept for fills (0: none, so nothing fills unless a portfolio"
        " sets fills.missing_depth: quote)",
    )
    run.add_argument("--once", action="store_true", help="one tick, then exit (smoke test)")
    run.add_argument(
        "--record-series", action="append", help="also record this series (repeatable)"
    )

    st = with_db(with_dir(cmds.add(sub, "status", "process health, halts, per-strategy activity")))
    st.add_argument(
        "--expect", default="runner", help="processes that should be up, comma-separated"
    )
    st.add_argument("--max-age", default="2m", help="heartbeat older than this is stale")
    st.add_argument("--json", action="store_true", help="machine-readable output")

    for name, help_text in (
        ("halt", "kill switch: stop new entries (everything, a portfolio, or one strategy)"),
        ("resume", "undo a halt (same scope)"),
    ):
        h = with_db(with_dir(cmds.add(sub, name, help_text)))
        h.add_argument("scope", nargs="?", default="*", help='"*" (default), NAME or NAME/STRATEGY')
        if name == "halt":
            h.add_argument("--reason", required=True, help="why (shown by `nem status`)")

    rep = with_dir(cmds.add(sub, "replay", "run portfolios over recorded data (backtest)"))
    rep.add_argument("--data", type=Path, required=True, help="database recorded by nem run/record")
    rep.add_argument("--out", type=Path, help="results database (default: temporary)")
    rep.add_argument("--from", dest="start", type=_when, help="ISO time, e.g. 2026-10-08T18:00")
    rep.add_argument("--to", dest="end", type=_when, help="ISO time (exclusive)")

    sug = with_dir(cmds.add(
        sub, "suggest", "sweep a few settings on recorded data; suggest, check on later data"
    ))  # fmt: skip
    sug.add_argument("portfolio", help="portfolio holding the base strategy")
    sug.add_argument("strategy", help="strategy whose settings to sweep")
    sug.add_argument("--data", type=Path, required=True, help="database recorded by nem run/record")
    sug.add_argument(
        "--grid", type=_grid_arg, action="append", required=True, metavar="PATH=V1,V2,...",
        help="setting and values to try (repeatable), e.g. signal.threshold=0.88,0.90,0.92",
    )  # fmt: skip
    sug.add_argument(
        "--holdout", type=float, default=0.3, help="latest share of the data kept for the check"
    )
    sug.add_argument(
        "--min-trades", type=int, default=30, help="fewer trades than this counts as unknown"
    )
    sug.add_argument(
        "--write", action="store_true", help="append the suggestion to the portfolio file"
    )
    sug.add_argument(
        "--save", type=Path, default=Path("out/backtests"),
        help="folder for the run's JSON, read by NEM Desk (default: out/backtests)",
    )  # fmt: skip

    demo = cmds.add(sub, "demo", "replay the bundled sample data with the example portfolios")
    demo.add_argument("--data", type=Path, default=DEMO_DATA, help="recorded fixture (.jsonl.gz)")
    demo.add_argument("--dir", type=Path, default=DEMO_DIR, help=f"default: {DEMO_DIR}")
    exp = with_db(cmds.add(sub, "export", "save recorded data as a portable .jsonl.gz file"))
    exp.add_argument("--out", type=Path, required=True, help="e.g. sample.jsonl.gz")

    def with_stats(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        with_db(p)
        p.add_argument(
            "--edge", type=float, default=0.02, help="edge per contract to test for (0.02 = 2c)"
        )
        p.add_argument("--alpha", type=float, default=0.05, help="false-positive rate")
        p.add_argument("--power", type=float, default=0.8, help="chance of detecting the edge")
        return p

    summ = with_stats(
        with_dir(cmds.add(sub, "summary", "statistics per strategy: win rate vs break-even"))
    )
    summ.add_argument("--json", action="store_true", help="machine-readable output")
    rpt = with_stats(with_dir(cmds.add(
        sub, "report", "statistics, published to each portfolio's reporters (CSV, Sheets)"
    )))  # fmt: skip
    rpt.add_argument("--every", help="repeat on this schedule, e.g. 5m (default: once)")
    rpt.add_argument("--no-publish", action="store_true", help="print only")
    rpt.add_argument(
        "--json-dir", type=Path, default=Path("out"),
        help="also write summary.json and status.json here, for NEM Desk (default: out)",
    )  # fmt: skip

    trd = cmds.add(sub, "trade", "exclude trades (as if never taken) or undo")
    trd_sub = trd.add_subparsers(dest="action", required=True)
    for name, help_text in (
        ("exclude", "leave a market's trade out of statistics, P&L, equity and risk limits"),
        ("include", "count an excluded trade again"),
    ):
        te = with_db(with_dir(cmds.add(trd_sub, name, help_text, "trade")))
        te.add_argument("ticker", help="market ticker of the trade")
        te.add_argument("--portfolio", help="needed when several portfolios traded the market")
        te.add_argument("--strategy", help="needed when several strategies traded the market")
        if name == "exclude":
            te.add_argument("--reason", required=True, help="why (kept with the trade, reported)")
    tl = with_db(cmds.add(trd_sub, "list", "list trades, or only excluded ones", "trade"))
    tl.add_argument("--excluded", action="store_true", help="only excluded trades, with reasons")
    tl.add_argument("--portfolio", help="one portfolio only")

    rec = with_db(cmds.add(sub, "record", "record Kalshi market data without trading"))
    rec.add_argument(
        "--series", action="append", required=True, help="series ticker, e.g. KXBTC15M (repeatable)"
    )
    rec.add_argument("--interval", type=float, default=5.0, help="seconds between polls")
    rec.add_argument("--depth", type=int, default=10, help="orderbook levels to keep (0 = none)")
    rec.add_argument("--once", action="store_true", help="poll once and exit")

    imp = cmds.add(sub, "import", "import trading done outside NEM, as a read-only portfolio")
    imp_sub = imp.add_subparsers(dest="source", required=True)
    kp = with_db(with_dir(cmds.add(
        imp_sub, "kalshi-pnl",
        "import Kalshi realized-PnL exports as a read-only manual baseline (never traded)",
        "import",
    )))  # fmt: skip
    kp.add_argument("files", nargs="+", type=Path, help="realized_pnl_*.csv from Kalshi")
    kp.add_argument("--activity", type=Path, help="Kalshi activity CSV, for fill times")
    kp.add_argument(
        "--from", dest="start", type=date.fromisoformat,
        help="first close date, inclusive, in the file's offset (default: no limit)",
    )  # fmt: skip
    kp.add_argument(
        "--to", dest="end", type=date.fromisoformat,
        help="last close date, inclusive (default: no limit)",
    )  # fmt: skip
    kp.add_argument(
        "--include-non-weather", action="store_true", help="keep markets other than KXHIGH*"
    )
    kp.add_argument(
        "--portfolio", default="manual_baseline", help="portfolio name (default: manual_baseline)"
    )

    kh = cmds.add(imp_sub, "kalshi-history", (
        "import settled markets from Kalshi's public historical API (minute candles, no"
        " depth, trade prints) into a separate history database"
    ), "import")  # fmt: skip
    kh.add_argument(
        "--series", action="append", required=True, help="series ticker, e.g. KXHIGHNY (repeatable)"
    )
    kh.add_argument(
        "--from", dest="start", type=date.fromisoformat, required=True,
        help="first market date, inclusive",
    )  # fmt: skip
    kh.add_argument(
        "--to", dest="end", type=date.fromisoformat, required=True,
        help="last market date, inclusive",
    )  # fmt: skip
    kh.add_argument(
        "--db", type=Path, default=DEFAULT_HISTORY_DB,
        help=f"history database, never data/nem.db (default: {DEFAULT_HISTORY_DB})",
    )  # fmt: skip
    kh.add_argument(
        "--cache", type=Path, default=DEFAULT_HISTORY_CACHE,
        help=f"raw response cache (default: {DEFAULT_HISTORY_CACHE})",
    )  # fmt: skip
    kh.add_argument(
        "--pause", type=float, default=0.25, help="seconds between requests (default: 0.25)"
    )
    kh.add_argument(
        "--strike-type", dest="strike_types", action="append", default=None,
        choices=["greater", "greater_or_equal", "less", "less_or_equal", "between"],
        help="only markets of this strike type, e.g. less = the lowest bracket (repeatable;"
        " default: all)",
    )  # fmt: skip

    stl = cmds.add(sub, "settle", "check Kalshi's settlements against independent readings")
    stl_sub = stl.add_subparsers(dest="action", required=True)
    sv = with_db(cmds.add(stl_sub, "verify", (
        "compare each daily temperature market's Kalshi result with the NWS climate report"
    ), "settle"))  # fmt: skip
    sv.add_argument("--tickers", type=Path, help="file with one market ticker per line")
    sv.add_argument(
        "--from-pnl", nargs="+", type=Path, default=[], metavar="FILE",
        help="Kalshi realized-PnL CSVs: check their weather tickers, show their P&L",
    )  # fmt: skip
    sv.add_argument(
        "--report", type=Path, default=Path("reports/settlement_disagreements.csv"),
        help="CSV of every market needing review (default: reports/settlement_disagreements.csv)",
    )  # fmt: skip
    sv.add_argument(
        "--pause", type=float, default=1.0, help="seconds between requests to IEM (default: 1)"
    )

    bf = cmds.add(sub, "backfill", "archived forecasts fetched after the fact (approximate)")
    bf_sub = bf.add_subparsers(dest="action", required=True)
    bff = with_db(cmds.add(bf_sub, "forecasts", (
        "store archived model-run forecasts for traded markets, known_at = run + lag"
    ), "backfill"))  # fmt: skip
    bff_in = bff.add_mutually_exclusive_group()
    bff_in.add_argument(
        "--from-db", type=Path, metavar="DB",
        help="markets = the manual lots in this database (default: --db's own lots)",
    )  # fmt: skip
    bff_in.add_argument("--tickers", type=Path, help="file with one market ticker per line")
    bff.add_argument(
        "--sources", nargs="+", default=None, metavar="SOURCE",
        help="iem_mos_gfs iem_mos_nam iem_mos_nbs openmeteo_ecmwf_ifs openmeteo_gfs_global"
        " (default: all but openmeteo_gfs_global)",
    )  # fmt: skip
    bff.add_argument(
        "--lookback", type=int, default=2, help="days of runs before each market day (default: 2)"
    )
    bff.add_argument("--cache", type=Path, help="directory to cache raw responses in")
    bfc = with_db(cmds.add(bf_sub, "coverage", (
        "which backfilled sources had a forecast before each manual entry"
    ), "backfill"))  # fmt: skip
    bfc.add_argument(
        "--report", type=Path, default=Path("reports/forecast_coverage.csv"),
        help="CSV, one row per entry (default: reports/forecast_coverage.csv)",
    )  # fmt: skip

    ser = cmds.add(sub, "series", "Kalshi series and what the portfolios cover")
    ser_sub = ser.add_subparsers(dest="action", required=True)
    unc = with_dir(cmds.add(ser_sub, "uncovered", (
        "series matching a prefix with open markets that no strategy trades (exit 1 if any)"
    ), "series"))  # fmt: skip
    unc.add_argument(
        "--prefix", action="append", required=True,
        help="series ticker prefix (repeatable), e.g. KXHIGH --prefix KXLOWT",
    )  # fmt: skip

    hist = cmds.add(sub, "history", (
        "replay and sweep over a Kalshi history file (nem import kalshi-history)"
    ))  # fmt: skip
    hist_sub = hist.add_subparsers(dest="action", required=True)

    def with_history(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        with_dir(p)
        p.add_argument(
            "--data", type=Path, default=DEFAULT_HISTORY_DB,
            help=f"history database (default: {DEFAULT_HISTORY_DB})",
        )  # fmt: skip
        p.add_argument(
            "--step", type=int, default=1, help="minutes between replayed ticks (default: 1)"
        )
        return p

    hr = with_history(cmds.add(hist_sub, "replay", (
        "replay portfolios over the markets dated in a period; per trade, date, city, exit"
    ), "history"))  # fmt: skip
    hr.add_argument("portfolios", nargs="*", help="portfolio names (default: all in --dir)")
    hr.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    hr.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    hs = with_history(cmds.add(hist_sub, "suggest", (
        "sweep a few settings of every strategy of a portfolio on train dates; check once on"
        " later test dates"
    ), "history"))  # fmt: skip
    hs.add_argument("portfolio")
    hs.add_argument(
        "--grid", type=_grid_arg, action="append", required=True, metavar="PATH=V1,V2,...",
        help="setting and values (repeatable); applied to every strategy, null = off",
    )  # fmt: skip
    for flag in ("--train-from", "--train-to", "--test-from", "--test-to"):
        hs.add_argument(flag, type=date.fromisoformat, required=True)
    hs.add_argument(
        "--min-trades", type=int, default=30, help="fewer trades than this counts as unknown"
    )

    hlp = cmds.add(sub, "help", "list commands, or show every option of one: nem help run")
    hlp.add_argument("topic", nargs="*", help="a command, e.g. run or portfolio new")
    return parser, cmds


def _usage(p: argparse.ArgumentParser) -> str:
    """`nem run [--dir DIR] ...` without argparse's `usage:` prefix, -h, or line wrapping."""
    text = " ".join(p.format_usage().split())
    return text.removeprefix("usage: ").replace(" [-h]", "")


def overview(cmds: Commands) -> str:
    lines = [
        f"NotEnoughMarkets {__version__}: test Kalshi trading ideas with paper trading and",
        "honest statistics. Defaults: portfolios in portfolios/, data in data/nem.db.",
    ]
    for group, names in HELP_GROUPS:
        lines += ["", group]
        for name in names:
            lines.append(f"  {_usage(cmds.parsers[name])}")
            lines.append(f"      {cmds.helps[name]}")
    lines += [
        "",
        "Every option of a command: nem help COMMAND (e.g. nem help run, nem help portfolio new)",
        "Docs: README.md, deploy/RUNBOOK.md, AGENTS.md, docs/kalshi-tickers.txt",
    ]
    return "\n".join(lines)


def cmd_help(args: argparse.Namespace, cmds: Commands) -> int:
    topic = " ".join(args.topic)
    if not topic:
        print(overview(cmds))
        return 0
    parser = cmds.parsers.get(topic)
    if parser is None:
        known = ", ".join(sorted(n for n in cmds.parsers if " " not in n))
        print(f"unknown command {topic!r}. Commands: {known}", file=sys.stderr)
        return 1
    print(parser.format_help())
    return 0


def _load(directory: Path) -> "list[PortfolioConfig]":
    from nem.builtins import load_builtins
    from nem.core.config import load_portfolios

    load_builtins()
    portfolios = load_portfolios(directory)
    if not portfolios:
        raise SystemExit(f"no portfolios in {directory}/; create one with `nem init`")
    return portfolios


def cmd_init(args: argparse.Namespace) -> int:
    from nem.manage import new_portfolio

    directory: Path = args.dir
    existing = sorted(directory.glob("*.yaml")) if directory.is_dir() else []
    if existing:
        print(f"{directory}/ already has portfolios: {', '.join(p.stem for p in existing)}")
        print("Use `nem portfolio new NAME` to add another.")
        return 1
    name = input("Portfolio name [paper]: ").strip() or "paper"
    balance = float(input("Starting paper balance in dollars [1000]: ").strip() or 1000)
    path = new_portfolio(directory, name, balance)
    print(f"Created {path}")
    print(f"Next: nem strategy add {name} my_first --series KXBTC15M")
    return 0


def cmd_portfolio(args: argparse.Namespace) -> int:
    from nem.core.config import load_portfolio, load_portfolios
    from nem.manage import new_portfolio, portfolio_path

    if args.action == "new":
        print(f"Created {new_portfolio(args.dir, args.name, args.balance)}")
    elif args.action == "list":
        for p in load_portfolios(args.dir):
            active = sum(1 for s in p.strategies if s.status == "active")
            money = f"${p.starting_balance or p.budget:,.2f}"
            print(f"{p.name:<20}{p.mode:<7}{money:>12}  {active}/{len(p.strategies)} active")
    else:
        p = load_portfolio(portfolio_path(args.dir, args.name))
        print(f"{p.name} ({p.mode}), check every {p.check_every}, feeds: "
              f"{', '.join(f.name for f in p.feeds) or 'none'}")  # fmt: skip
        for s in p.strategies:
            gates = ", ".join(g.type for g in s.gates) or "none"
            print(f"  {s.name:<16}{s.status:<8}{s.series:<12}signal={s.signal.type} "
                  f"gates=[{gates}] sizing={s.sizing.type}")  # fmt: skip
    return 0


def cmd_strategy(args: argparse.Namespace) -> int:
    from nem.manage import add_strategy

    path = add_strategy(args.dir, args.portfolio, args.name, args.series)
    print(f"Added {args.name} to {path}. Edit it there, then run `nem validate`.")
    return 0


def _series_fees(series: str) -> "SeriesFees":
    """One public GET /series/{series}, for strategies whose maker rate is null."""
    from nem.market.kalshi import KalshiClient

    client = KalshiClient("prod")
    try:
        return client.series_fees(series)
    finally:
        client.close()


def _public_client() -> "KalshiClient":
    from nem.market.kalshi import KalshiClient

    return KalshiClient("prod")


def cmd_series(args: argparse.Namespace) -> int:
    from nem.market.coverage import uncovered

    used = {s.series for p in _load(args.dir) for s in p.strategies}
    client = _public_client()
    try:
        found = uncovered(client, args.prefix, used)
    finally:
        client.close()
    if not found:
        print(f"every open {', '.join(args.prefix)} series is covered")
        return 0
    print(f"{len(found)} open series not traded by any portfolio in {args.dir}/:")
    for u in found:
        note = "" if u.mapped else "  (no station mapping: nem.settle.stations)"
        print(
            f"  {u.series:<14} {u.open_markets:>3} open  {u.settlement or '?':<20} {u.title}{note}"
        )
    print("Read each series' settlement rules before adding it (AGENTS.md).")
    return 1


def cmd_validate(args: argparse.Namespace) -> int:
    from nem.engine.runtime import build_feeds, build_strategies
    from nem.market.fees import resolve_fees

    # only null maker rates are looked up here (taker rates are at `nem run` start), so
    # validating portfolios without take-profits stays offline
    portfolios = resolve_fees(_load(args.dir), _series_fees, taker=False)
    strategies = build_strategies(portfolios)
    build_feeds(portfolios)
    for p in portfolios:
        n = sum(1 for rt in strategies if rt.portfolio.name == p.name)
        print(f"ok  {p.name}: {n} strategies, {len(p.feeds)} feeds")
    return 0


def _config_signature(directory: Path) -> tuple[tuple[str, float], ...]:
    return tuple((p.name, p.stat().st_mtime) for p in sorted(directory.glob("*.yaml")))


def cmd_run(args: argparse.Namespace) -> int:
    """Paper-trade every portfolio. Also records every snapshot and result it sees (plus
    any `--record-series`), so one process does it all and the data can be replayed."""
    from nem.core.clock import SystemClock
    from nem.core.config import ConfigError, load_portfolios
    from nem.engine.runner import Runner, run_live
    from nem.engine.runtime import BuildError
    from nem.market.fees import resolve_fees
    from nem.market.kalshi import KalshiClient
    from nem.market.recorder import Recorder
    from nem.market.source import KalshiSource, ReplaySource, Tick
    from nem.store import Store

    directory: Path = args.dir
    db: Path = args.db
    db.parent.mkdir(parents=True, exist_ok=True)
    clock = SystemClock()
    client = KalshiClient("prod")  # market data is always real; orders are paper
    # null fee rates come from each series' fee schedule; unreadable = refuse to start
    try:
        portfolios = resolve_fees(_load(directory), client.series_fees)
    except BaseException:
        client.close()
        raise
    extra: list[str] = args.record_series or []
    with Store(db) as store:
        source = KalshiSource(client, clock, depth=args.depth)
        # the embedded recorder stores results, so settlement reads them from the store
        runner = Runner(portfolios, ReplaySource(store), store, clock)
        recorder = Recorder(source, store, runner.series, clock, process=None)
        intervals = [p.check_every_for(s) for p in portfolios for s in p.strategies]
        interval = max(1.0, min(intervals, default=timedelta(seconds=5)).total_seconds())
        signature = _config_signature(directory)

        def poll() -> Tick:
            nonlocal signature
            current = _config_signature(directory)
            if current != signature:
                signature = current
                try:
                    edited = resolve_fees(load_portfolios(directory), client.series_fees)
                    runner.reload(edited)
                    log.info("reloaded portfolios: trading %s", ", ".join(runner.series))
                except (ConfigError, BuildError) as e:
                    log.error("portfolio edit rejected, still running the old config: %s", e)
            recorder.series = sorted({*runner.series, *extra})
            return recorder.step().tick

        log.info(
            "paper trading %s every %.0fs (recording %s); Ctrl-C to stop",
            ", ".join(runner.series) or "nothing", interval,
            ", ".join(sorted({*runner.series, *extra})) or "nothing",
        )  # fmt: skip
        try:
            run_live(runner, poll, interval, 1 if args.once else None)
        except KeyboardInterrupt:
            pass
        finally:
            client.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from nem.core.config import load_portfolios, parse_duration
    from nem.ops import render_status, status_data
    from nem.store import Store

    portfolios = load_portfolios(args.dir) if args.dir.is_dir() else []
    expect = [x for x in args.expect.split(",") if x]
    now, max_age = datetime.now(UTC), parse_duration(args.max_age)
    with Store(args.db) as store:
        if args.json:
            data = status_data(store, portfolios, now, expect, max_age)
            print(json.dumps(data, indent=2))
            return 0 if data["healthy"] else 2
        text, healthy = render_status(store, portfolios, now, expect, max_age)
    print(text)
    return 0 if healthy else 2


def _check_scope(scope: str, directory: Path) -> None:
    """Warn (don't fail) if a halt scope names nothing in the portfolio files."""
    from nem.core.config import load_portfolios

    if scope == "*" or not directory.is_dir():
        return
    known = {p.name for p in load_portfolios(directory)} | {
        f"{p.name}/{s.name}" for p in load_portfolios(directory) for s in p.strategies
    }
    if scope not in known:
        print(f"warning: {scope!r} matches no portfolio or strategy in {directory}/")


def cmd_halt(args: argparse.Namespace) -> int:
    from nem.store import Store

    _check_scope(args.scope, args.dir)
    with Store(args.db) as store:
        store.halt(args.scope, args.reason, datetime.now(UTC))
    what = "everything" if args.scope == "*" else args.scope
    print(f"Halted {what}: no new entries. Open positions still settle. Undo: nem resume")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    from nem.store import Store

    with Store(args.db) as store:
        if not store.resume(args.scope):
            active = ", ".join(scope for scope, _, _ in store.halts()) or "none"
            print(f"No halt on {args.scope!r}. Active halts: {active}")
            return 1
    print(f"Resumed {'everything' if args.scope == '*' else args.scope}")
    return 0


def _describe(t: "Trade") -> str:
    state = "open" if t.won is None else f"{'won' if t.won else 'lost'} {t.realized_pnl or 0:+.2f}"
    return f"{t.portfolio}/{t.strategy} {t.ticker} ({t.side} x{t.qty}, {state})"


def cmd_trade(args: argparse.Namespace) -> int:
    from nem.store import Store

    if args.action == "list":
        with Store(args.db) as store:
            if args.excluded:
                trades = store.excluded_trades(args.portfolio)
            else:
                settled = store.settled_trades(portfolio=args.portfolio)
                trades = [*settled, *store.open_trades(portfolio=args.portfolio)]
        if not trades:
            print("No excluded trades." if args.excluded else "No trades.")
            return 0
        n = sum(1 for t in trades if t.excluded)
        print(f"{len(trades)} trades, {n} excluded (left out of statistics and money):")
        for t in trades:
            when = f"{t.excluded_at:%Y-%m-%d %H:%M} UTC" if t.excluded_at else ""
            flag = f"  excluded {when}: {t.exclude_reason}" if t.excluded else ""
            print(f"  {_describe(t)}{flag}")
        return 0
    if args.portfolio is not None:
        _check_scope(
            args.portfolio if args.strategy is None else f"{args.portfolio}/{args.strategy}",
            args.dir,
        )
    with Store(args.db) as store:
        found = store.trades_for_ticker(args.ticker, args.portfolio, args.strategy)
        if args.action == "include":
            found = [t for t in found if t.excluded]
        if not found:
            what = "excluded trade" if args.action == "include" else "trade"
            narrowed = "/".join(x for x in (args.portfolio, args.strategy) if x)
            print(
                f"error: no {what} on {args.ticker}" + (f" in {narrowed}" if narrowed else ""),
                file=sys.stderr,
            )
            return 1
        if len(found) > 1:
            names = ", ".join(f"{t.portfolio}/{t.strategy}" for t in found)
            print(
                f"error: {args.ticker} was traded by {names}; pick one with --portfolio"
                " and/or --strategy",
                file=sys.stderr,
            )
            return 1
        (t,) = found
        if args.action == "include":
            store.set_excluded(t, None, None)
            print(f"Included {_describe(t)} again")
            return 0
        store.set_excluded(t, args.reason, datetime.now(UTC))
    verb = "Updated the reason for" if t.excluded else "Excluded"
    print(
        f"{verb} {_describe(t)}: {args.reason}. Left out of statistics, P&L, equity and"
        " risk limits. Undo: nem trade include"
    )
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    import tempfile

    from nem.core.clock import ManualClock
    from nem.engine.runner import Runner
    from nem.history.db import is_history_db
    from nem.market.source import ReplaySource
    from nem.store import Store

    if is_history_db(args.data):
        raise SystemExit(f"{args.data} is a history file (changed quotes only); use nem history")
    portfolios = _load(args.dir)
    with Store(args.data) as data:
        source = ReplaySource(data, args.start, args.end)
        first = next(iter(data.iter_snapshots(None, args.start, args.end)), None)
        if first is None:
            raise SystemExit(f"{args.data}: no snapshots in that time range")
        out: Path = args.out or Path(tempfile.mkdtemp(prefix="nem-replay-")) / "results.db"
        if out.exists():
            raise SystemExit(f"{out} already exists; pick a new --out")
        with Store(out) as store:
            clock = ManualClock(first.ts)
            runner = Runner(portfolios, source, store, clock, data=data, fetch_feeds=False)
            runner.run(source.ticks(runner.series))
            runner.settle_remaining(clock.now() + timedelta(hours=1))
            print(_render(store, portfolios, clock.now()))
        print(f"\nresults: {out}")
    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    from nem.backtest.export import run_data, save_run
    from nem.backtest.suggest import render_suggestion, strategy_yaml, suggest
    from nem.backtest.sweep import run_sweep, split
    from nem.manage import append_strategy, portfolio_path
    from nem.stats.provenance import QUOTE_FILLS_LABEL, uses_quote_fills
    from nem.store import Store

    pairs: list[tuple[str, list[ParamValue]]] = args.grid
    grid: Grid = dict(pairs)
    if len(grid) != len(pairs):
        raise SystemExit("each --grid setting may appear only once")
    base = next((p for p in _load(args.dir) if p.name == args.portfolio), None)
    if base is None:
        raise SystemExit(f"no portfolio {args.portfolio!r} in {args.dir}/")
    try:
        strategy = base.strategy(args.strategy)
    except KeyError:
        raise SystemExit(f"{base.name} has no strategy {args.strategy!r}") from None
    # every variant logs every fill; the summary below is what matters
    logging.getLogger("nem.engine").setLevel(logging.WARNING)
    with Store(args.data) as data:
        train, test = split(data, [strategy.series], args.holdout)
        print(f"train {train.start:%Y-%m-%d %H:%M} to {train.end:%Y-%m-%d %H:%M} UTC, "
              f"test {test.start:%Y-%m-%d %H:%M} to {test.end:%Y-%m-%d %H:%M} UTC")  # fmt: skip
        results = run_sweep(data, base, args.strategy, grid, [train, test])
    s = suggest(grid, results[train], results[test], min_trades=args.min_trades)
    if uses_quote_fills(base):
        print(f"[{QUOTE_FILLS_LABEL}]")
    print(render_suggestion(s, grid))
    run = run_data(
        s, grid, portfolio=base.name, strategy=args.strategy, series=strategy.series,
        train=train, test=test, train_results=results[train], min_trades=args.min_trades,
        created_at=datetime.now(UTC).replace(microsecond=0),
    )  # fmt: skip
    print(f"\nrun saved: {save_run(run, args.save)}")
    if s.status != "suggested":
        return 0
    block = strategy_yaml(s, args.strategy)
    if args.write:
        path = append_strategy(args.dir, base.name, block)
        print(f"\nAdded {args.strategy}_suggested to {path}; `nem run` paper-trades it.")
    else:
        where = portfolio_path(args.dir, base.name)
        print(f"\nTo paper-trade it, rerun with --write, or add this under strategies: in {where}")
        print(block)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    import tempfile

    from nem.market.fixtures import import_fixture
    from nem.store import Store

    data = Path(tempfile.mkdtemp(prefix="nem-demo-")) / "data.db"
    with Store(data) as store:
        rows = import_fixture(args.data, store)
    print(f"Loaded {rows} recorded rows from {args.data}; replaying {args.dir}/\n")
    replay_args = argparse.Namespace(dir=args.dir, data=data, out=None, start=None, end=None)
    return cmd_replay(replay_args)


def cmd_export(args: argparse.Namespace) -> int:
    from nem.market.fixtures import export_fixture
    from nem.store import Store

    with Store(args.db) as store:
        print(f"Wrote {export_fixture(store, args.out)} rows to {args.out}")
    return 0


def _reports(
    store: "Store", portfolios: "list[PortfolioConfig]", now: datetime, args: argparse.Namespace
) -> "list[PortfolioReport]":
    """Every portfolio file's report, then imported manual portfolios (read-only)."""
    from nem.stats.report import build_report, manual_reports

    reports = [build_report(store, p, now, args.edge, args.alpha, args.power) for p in portfolios]
    names = [p.name for p in portfolios]
    return reports + manual_reports(store, now, names, args.edge, args.alpha, args.power)


def _render(store: "Store", portfolios: "list[PortfolioConfig]", now: datetime) -> str:
    from nem.stats.report import build_report, render_all

    return render_all([build_report(store, p, now) for p in portfolios])


def cmd_summary(args: argparse.Namespace) -> int:
    from nem.stats.report import SCHEMA_VERSION, render_all, report_data
    from nem.store import Store

    directory: Path = args.dir
    has_files = directory.is_dir() and any(directory.glob("*.yaml"))
    with Store(args.db) as store:
        reports = _reports(store, _load(directory) if has_files else [], datetime.now(UTC), args)
    if not reports:
        raise SystemExit(f"no portfolios in {directory}/; create one with `nem init`")
    if args.json:
        data = {"schema_version": SCHEMA_VERSION, "portfolios": [report_data(r) for r in reports]}
        print(json.dumps(data, indent=2))
    else:
        print(render_all(reports))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Separate from the runner on purpose: reports keep flowing whatever the bots do."""
    import time

    from nem.core.config import ConfigError, PortfolioConfig, load_portfolios, parse_duration
    from nem.core.registry import REGISTRY, PluginError
    from nem.reporting.base import Reporter
    from nem.stats.report import render_all
    from nem.store import Store

    def build(ps: list[PortfolioConfig]) -> dict[str, list[Reporter]]:
        return {
            p.name: [REGISTRY.build("reporter", spec, Reporter) for spec in p.reporting] for p in ps
        }

    directory: Path = args.dir
    portfolios = _load(directory)
    reporters = build(portfolios)
    signature = _config_signature(directory)
    every = parse_duration(args.every).total_seconds() if args.every else None
    with Store(args.db) as store:
        while True:
            # pick up portfolio edits (new strategies, reporters) like the runner does
            current = _config_signature(directory)
            if current != signature:
                signature = current
                try:
                    loaded = load_portfolios(directory)
                    portfolios, reporters = loaded, build(loaded)
                    log.info("reloaded portfolios for reporting")
                except (ConfigError, PluginError) as e:
                    log.error("portfolio edit rejected, still reporting the old config: %s", e)
            now = datetime.now(UTC)
            reports = _reports(store, portfolios, now, args)
            print(render_all(reports))
            if not args.no_publish:
                for report in reports:
                    # imported manual portfolios have no reporters: summary.json only
                    for reporter in reporters.get(report.portfolio.name, []):
                        try:
                            log.info("published %s", reporter.publish(report))
                        except Exception as e:
                            log.error("%s reporter for %s failed: %s", reporter.name,
                                      report.portfolio.name, e)  # fmt: skip
                try:
                    _write_json_reports(args.json_dir, store, portfolios, reports, now)
                except OSError as e:
                    log.error("writing JSON reports to %s failed: %s", args.json_dir, e)
            store.heartbeat(now, "reporter", "ok")
            if every is None:
                return 0
            time.sleep(every)


def _write_json_reports(
    directory: Path,
    store: "Store",
    portfolios: "list[PortfolioConfig]",
    reports: "list[PortfolioReport]",
    now: datetime,
) -> None:
    """The same documents as `nem summary --json` and `nem status --json`."""
    from datetime import timedelta

    from nem.ops import status_data
    from nem.stats.report import SCHEMA_VERSION, report_data

    directory.mkdir(parents=True, exist_ok=True)
    docs = {
        "summary.json": {
            "schema_version": SCHEMA_VERSION, "portfolios": [report_data(r) for r in reports],
        },
        "status.json": status_data(store, portfolios, now, [], timedelta(minutes=2)),
    }  # fmt: skip
    for name, doc in docs.items():
        target = directory / name
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2) + "\n")
        tmp.replace(target)  # readers never see a half-written file


def cmd_import_history(args: argparse.Namespace) -> int:
    from nem.history.db import HistoryDb, is_history_db
    from nem.history.fetch import HistoryClient, import_history
    from nem.store import Store

    db: Path = args.db
    if db.exists() and not is_history_db(db):
        with Store(db) as existing:
            has_data = next(iter(existing.iter_snapshots()), None) is not None
        if has_data:
            raise SystemExit(f"{db} holds recorded data; history goes in its own file")
    if args.start > args.end:
        raise SystemExit("--from must not be after --to")
    db.parent.mkdir(parents=True, exist_ok=True)
    client = HistoryClient.create(args.cache, args.pause)
    try:
        with HistoryDb(db) as hdb:
            res = import_history(
                client, hdb, args.series, args.start, args.end, datetime.now(UTC),
                progress=lambda m: print(m, flush=True), strike_types=args.strike_types,
            )  # fmt: skip
            counts = hdb.counts()
    finally:
        client.close()
    print(
        f"{res.imported} markets imported ({res.skipped_existing} already there) of"
        f" {res.markets_in_window} in {args.start}..{args.end}; {res.candles} candles ->"
        f" {res.snapshots} snapshots, {res.prints} trade prints; {res.unsettled} without a"
        f" yes/no result; {client.requests} requests, {client.cache_hits} cached"
    )
    print(f"{db}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    print("provenance: historical_candles (top of book per minute, no depth)")
    for line in res.problems[:20]:
        print(f"  {line}")
    if res.problems:
        print(f"  ({len(res.problems)} markets failed; run again to retry them)")
        return 1
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    import re

    if args.source == "kalshi-history":
        return cmd_import_history(args)

    from nem.core.config import NAME_PATTERN
    from nem.manual import ManualImportError, import_kalshi_pnl, render_import
    from nem.stats.report import build_manual_report, render_text
    from nem.store import Store

    name: str = args.portfolio
    if not re.match(NAME_PATTERN, name):
        raise SystemExit(f"portfolio name {name!r} must be lowercase letters, digits and _")
    directory: Path = args.dir
    if (directory / f"{name}.yaml").exists():
        raise SystemExit(f"{directory / name}.yaml exists; imported history needs its own name")
    if args.start is not None and args.end is not None and args.start > args.end:
        raise SystemExit("--from must not be after --to")
    db: Path = args.db
    db.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    with Store(db) as store:
        try:
            result = import_kalshi_pnl(
                store, args.files, now, args.activity, args.start, args.end,
                args.include_non_weather, name,
            )  # fmt: skip
        except ManualImportError as e:
            print(f"error: import rejected, nothing written. {e}", file=sys.stderr)
            return 1
        print(render_import(result, args.start, args.end))
        report = build_manual_report(store, name, now)
    if report is not None:
        print()
        print(render_text(report))
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    from nem.market.kalshi import KalshiClient
    from nem.market.recorder import Recorder
    from nem.market.source import KalshiSource
    from nem.store import Store

    db: Path = args.db
    db.parent.mkdir(parents=True, exist_ok=True)
    # Market data always comes from prod: demo-exchange prices aren't real.
    client = KalshiClient("prod")
    with Store(db) as store:
        source = KalshiSource(client, interval=args.interval, depth=args.depth)
        recorder = Recorder(source, store, args.series)
        try:
            recorder.run(args.interval, iterations=1 if args.once else None)
        except KeyboardInterrupt:
            pass
        finally:
            client.close()
    return 0


def cmd_settle(args: argparse.Namespace) -> int:
    from nem.market.kalshi import KalshiClient
    from nem.settle.observed import Iem
    from nem.settle.verify import (
        is_weather,
        pnl_by_ticker,
        render_counts,
        tickers_from_file,
        verify,
        write_report,
    )  # fmt: skip
    from nem.store import Store

    tickers: list[str] = []
    if args.tickers:
        tickers += tickers_from_file(args.tickers)
    pnl = pnl_by_ticker(args.from_pnl) if args.from_pnl else None
    if pnl is not None:
        tickers += list(pnl)
    db: Path = args.db
    db.parent.mkdir(parents=True, exist_ok=True)
    with Store(db) as store:
        if not args.tickers and pnl is None:
            tickers = store.settlement_check_candidates()
        weather = sorted({t for t in tickers if is_weather(t)})
        if not weather:
            print("no KXHIGH*/KXLOW* tickers to check (use --tickers or --from-pnl)")
            return 1
        print(f"checking {len(weather)} markets (Kalshi public API, IEM)...", flush=True)
        kalshi, iem = KalshiClient("prod"), Iem(pause=args.pause)
        try:
            checks = verify(weather, kalshi.market_detail, iem, datetime.now(UTC))
        finally:
            kalshi.close()
            iem.close()
        store.save_settlement_checks(checks)
    rows = write_report(args.report, checks, pnl)
    print(render_counts(checks))
    print(f"{rows} markets need review -> {args.report}")
    print("Money questions: the realized-PnL file. Strategy replay: derived_result.")
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    from nem.backfill.coverage import render, write_csv
    from nem.backfill.run import backfill, coverage_for, manual_entries, targets_for
    from nem.backfill.sources import DEFAULT_SOURCES, ArchiveClient
    from nem.settle.verify import is_weather, tickers_from_file
    from nem.store import Store

    db: Path = args.db
    db.parent.mkdir(parents=True, exist_ok=True)
    if args.action == "coverage":
        with Store(db) as store:
            rows = coverage_for(store)
            sources = sorted({s for r in store.backfill_rows() for s in [r.source]})
        if not rows:
            print("no manual entries in the database (nem import kalshi-pnl first)")
            return 1
        write_csv(args.report, rows, sources)
        print(render(rows, sources))
        print(f"{len(rows)} entries -> {args.report}")
        return 0
    entries = []
    if args.tickers:
        tickers = tickers_from_file(args.tickers)
    else:
        with Store(args.from_db or db) as src:
            entries = manual_entries(src)
        tickers = [e.ticker for e in entries]
    targets = targets_for([t for t in tickers if is_weather(t)], entries)
    if not targets:
        print("no mapped KXHIGH*/KXLOW* markets (use --tickers or --from-db)")
        return 1
    sources: list[str] = args.sources or list(DEFAULT_SOURCES)
    print(f"backfilling {len(targets)} station-days from {', '.join(sources)}...", flush=True)
    client = ArchiveClient(args.cache)
    try:
        res = backfill(client, targets, sources, datetime.now(UTC), args.lookback)
    finally:
        client.close()
    with Store(db) as store:
        store.save_backfill(res.rows)
    print(f"{len(res.rows)} rows stored (provenance=backfill); {client.requests} requests,"
          f" {client.cache_hits} cached")  # fmt: skip
    for line in res.problems[:20] + res.invalid[:20]:
        print(f"  {line}")
    if res.problems or res.invalid:
        print(f"  ({len(res.problems)} unavailable runs, {len(res.invalid)} rows rejected)")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    from nem.backtest.suggest import render_suggestion, suggest
    from nem.history.backtest import (
        DatePeriod,
        expand_portfolio,
        labels,
        period_report,
        render_period,
        replay,
        sweep,
    )
    from nem.history.db import HistoryDb, is_history_db
    from nem.store import Store

    if not is_history_db(args.data):
        raise SystemExit(f"{args.data}: not a history file (nem import kalshi-history)")
    logging.getLogger("nem.engine").setLevel(logging.WARNING)
    logging.getLogger("nem.engine.step").setLevel(logging.WARNING)
    step = timedelta(minutes=args.step)
    portfolios = _load(args.dir)
    with HistoryDb(args.data) as db:
        if args.action == "replay":
            chosen = [p for p in portfolios if not args.portfolios or p.name in args.portfolios]
            if not chosen:
                raise SystemExit(f"no matching portfolios in {args.dir}/")
            period = DatePeriod(args.start, args.end)
            with Store() as results:
                replay(db, chosen, period, results, step=step)
                for p in chosen:
                    print(render_period(period_report(results, p, period, p.name)))
                    print()
            return 0
        base = next((p for p in portfolios if p.name == args.portfolio), None)
        if base is None:
            raise SystemExit(f"no portfolio {args.portfolio!r} in {args.dir}/")
        pairs: list[tuple[str, list[ParamValue]]] = args.grid
        grid: Grid = dict(pairs)
        train = DatePeriod(args.train_from, args.train_to)
        test = DatePeriod(args.test_from, args.test_to)
        if test.start <= train.end:
            raise SystemExit("the test period must start after the train period ends")
        print(f"{labels([base])} train {train.start}..{train.end}, test {test.start}..{test.end}")
        results = sweep(db, base, grid, [train, test], step=step)
        s = suggest(grid, results[train], results[test], min_trades=args.min_trades)
        print(render_suggestion(s, grid))
        if s.variant is None:
            return 0
        print("\nVerdicts are capped: a backtest can reject an idea, not confirm it.\n")
        variant = next(p for v, p in expand_portfolio(base, grid) if v.name == s.variant.name)
        for title, period in (("train (chosen variant)", train), ("test (once)", test)):
            with Store() as out:
                replay(db, [variant], period, out, step=step)
                print(render_period(period_report(out, variant, period, title)))
                print()
    return 0


COMMANDS = {
    "init": cmd_init,
    "portfolio": cmd_portfolio,
    "strategy": cmd_strategy,
    "validate": cmd_validate,
    "run": cmd_run,
    "replay": cmd_replay,
    "suggest": cmd_suggest,
    "summary": cmd_summary,
    "report": cmd_report,
    "status": cmd_status,
    "halt": cmd_halt,
    "resume": cmd_resume,
    "trade": cmd_trade,
    "demo": cmd_demo,
    "export": cmd_export,
    "import": cmd_import,
    "settle": cmd_settle,
    "record": cmd_record,
    "backfill": cmd_backfill,
    "history": cmd_history,
    "series": cmd_series,
}


def main(argv: Sequence[str] | None = None) -> int:
    from nem.backtest.sweep import SweepError
    from nem.core.config import ConfigError
    from nem.engine.runtime import BuildError

    parser, cmds = build_parser()
    args = parser.parse_args(argv)
    if args.help or args.command is None:
        print(overview(cmds))
        return 0
    if args.command == "help":
        return cmd_help(args, cmds)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is too noisy
    command = COMMANDS[args.command]
    try:
        return command(args)
    except (ConfigError, BuildError, SweepError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
