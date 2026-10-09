"""Command-line entry point."""

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from nem import __version__

if TYPE_CHECKING:
    from nem.core.config import PortfolioConfig
    from nem.stats.report import PortfolioReport
    from nem.store import Store

log = logging.getLogger("nem")

DEFAULT_DB = Path("data/nem.db")
DEFAULT_DIR = Path("portfolios")
DEMO_DATA = Path("examples/data/sample.jsonl.gz")
DEMO_DIR = Path("examples/portfolios")


def _when(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nem", description="NotEnoughMarkets")
    parser.add_argument("--version", action="version", version=f"nem {__version__}")
    sub = parser.add_subparsers(dest="command")

    def with_dir(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--dir", type=Path, default=DEFAULT_DIR, help=f"default: {DEFAULT_DIR}")
        return p

    with_dir(sub.add_parser("init", help="create your first paper portfolio (interactive)"))

    port = sub.add_parser("portfolio", help="create, list and show portfolios")
    port_sub = port.add_subparsers(dest="action", required=True)
    new = with_dir(port_sub.add_parser("new", help="create a paper portfolio"))
    new.add_argument("name")
    new.add_argument("--balance", type=float, default=1000.0, help="starting paper balance")
    with_dir(port_sub.add_parser("list", help="list portfolios"))
    show = with_dir(port_sub.add_parser("show", help="show a portfolio's strategies"))
    show.add_argument("name")

    strat = sub.add_parser("strategy", help="add strategies to a portfolio")
    strat_sub = strat.add_subparsers(dest="action", required=True)
    add = with_dir(strat_sub.add_parser("add", help="append a strategy template to edit"))
    add.add_argument("portfolio")
    add.add_argument("name")
    add.add_argument("--series", required=True, help="e.g. KXBTC15M")

    with_dir(sub.add_parser("validate", help="check every portfolio file and plugin"))

    run = with_dir(sub.add_parser("run", help="paper-trade every portfolio on live data"))
    run.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"default: {DEFAULT_DB}")
    run.add_argument("--depth", type=int, default=10, help="orderbook levels for fills")
    run.add_argument("--once", action="store_true", help="one tick, then exit")

    rep = with_dir(sub.add_parser("replay", help="run portfolios over recorded data"))
    rep.add_argument("--data", type=Path, required=True, help="database recorded by `nem record`")
    rep.add_argument("--out", type=Path, help="results database (default: temporary)")
    rep.add_argument("--from", dest="start", type=_when, help="ISO time, e.g. 2026-10-08T18:00")
    rep.add_argument("--to", dest="end", type=_when)

    demo = sub.add_parser("demo", help="replay bundled sample data with example portfolios")
    demo.add_argument("--data", type=Path, default=DEMO_DATA, help="recorded fixture (.jsonl.gz)")
    demo.add_argument("--dir", type=Path, default=DEMO_DIR, help=f"default: {DEMO_DIR}")
    exp = sub.add_parser("export", help="save a recorded database as a portable fixture")
    exp.add_argument("--db", type=Path, default=DEFAULT_DB)
    exp.add_argument("--out", type=Path, required=True, help="e.g. sample.jsonl.gz")

    def with_stats(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"default: {DEFAULT_DB}")
        p.add_argument(
            "--edge", type=float, default=0.02, help="edge per contract to test for (0.02 = 2c)"
        )
        p.add_argument("--alpha", type=float, default=0.05, help="false-positive rate")
        p.add_argument("--power", type=float, default=0.8, help="chance of detecting the edge")
        return p

    with_stats(with_dir(sub.add_parser("summary", help="results and statistics so far")))
    rpt = with_stats(
        with_dir(
            sub.add_parser("report", help="statistics, published to each portfolio's reporters")
        )
    )
    rpt.add_argument("--every", help="repeat on this schedule, e.g. 5m (default: once)")
    rpt.add_argument("--no-publish", action="store_true", help="print only")

    rec = sub.add_parser("record", help="record live Kalshi snapshots and settlements")
    rec.add_argument(
        "--series", action="append", required=True, help="series ticker, e.g. KXBTC15M (repeatable)"
    )
    rec.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"default: {DEFAULT_DB}")
    rec.add_argument("--interval", type=float, default=5.0, help="seconds between polls")
    rec.add_argument("--depth", type=int, default=10, help="orderbook levels to keep (0 = none)")
    rec.add_argument("--once", action="store_true", help="poll once and exit")
    return parser


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


def cmd_validate(args: argparse.Namespace) -> int:
    from nem.engine.runtime import build_feeds, build_strategies

    portfolios = _load(args.dir)
    strategies = build_strategies(portfolios)
    build_feeds(portfolios)
    for p in portfolios:
        n = sum(1 for rt in strategies if rt.portfolio.name == p.name)
        print(f"ok  {p.name}: {n} strategies, {len(p.feeds)} feeds")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from nem.core.clock import SystemClock
    from nem.engine.runner import Runner, run_live
    from nem.market.kalshi import KalshiClient
    from nem.market.source import KalshiSource
    from nem.store import Store

    portfolios = _load(args.dir)
    db: Path = args.db
    db.parent.mkdir(parents=True, exist_ok=True)
    clock = SystemClock()
    client = KalshiClient("prod")  # market data is always real; orders are paper
    with Store(db) as store:
        source = KalshiSource(client, clock, depth=args.depth)
        runner = Runner(portfolios, source, store, clock)
        intervals = [p.check_every_for(s) for p in portfolios for s in p.strategies]
        interval = max(1.0, min(intervals, default=timedelta(seconds=5)).total_seconds())
        series = runner.series
        log.info("paper trading %s every %.0fs; Ctrl-C to stop", ", ".join(series), interval)
        try:
            run_live(runner, lambda: source.poll(series), interval, 1 if args.once else None)
        except KeyboardInterrupt:
            pass
        finally:
            client.close()
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    import tempfile

    from nem.core.clock import ManualClock
    from nem.engine.runner import Runner
    from nem.market.source import ReplaySource
    from nem.store import Store

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
    from nem.stats.report import build_report

    return [build_report(store, p, now, args.edge, args.alpha, args.power) for p in portfolios]


def _render(store: "Store", portfolios: "list[PortfolioConfig]", now: datetime) -> str:
    from nem.stats.report import build_report, render_all

    return render_all([build_report(store, p, now) for p in portfolios])


def cmd_summary(args: argparse.Namespace) -> int:
    from nem.stats.report import render_all
    from nem.store import Store

    with Store(args.db) as store:
        print(render_all(_reports(store, _load(args.dir), datetime.now(UTC), args)))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Separate from the runner on purpose: reports keep flowing whatever the bots do."""
    import time

    from nem.core.config import parse_duration
    from nem.core.registry import REGISTRY
    from nem.reporting.base import Reporter
    from nem.stats.report import render_all
    from nem.store import Store

    portfolios = _load(args.dir)
    reporters = {
        p.name: [REGISTRY.build("reporter", spec, Reporter) for spec in p.reporting]
        for p in portfolios
    }
    every = parse_duration(args.every).total_seconds() if args.every else None
    with Store(args.db) as store:
        while True:
            now = datetime.now(UTC)
            reports = _reports(store, portfolios, now, args)
            print(render_all(reports))
            if not args.no_publish:
                for report in reports:
                    for reporter in reporters[report.portfolio.name]:
                        try:
                            log.info("published %s", reporter.publish(report))
                        except Exception as e:
                            log.error("%s reporter for %s failed: %s", reporter.name,
                                      report.portfolio.name, e)  # fmt: skip
            store.heartbeat(now, "reporter", "ok")
            if every is None:
                return 0
            time.sleep(every)


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


COMMANDS = {
    "init": cmd_init,
    "portfolio": cmd_portfolio,
    "strategy": cmd_strategy,
    "validate": cmd_validate,
    "run": cmd_run,
    "replay": cmd_replay,
    "summary": cmd_summary,
    "report": cmd_report,
    "demo": cmd_demo,
    "export": cmd_export,
    "record": cmd_record,
}


def main(argv: Sequence[str] | None = None) -> int:
    from nem.core.config import ConfigError
    from nem.engine.runtime import BuildError

    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is too noisy
    command = COMMANDS.get(args.command or "")
    if command is None:
        parser.print_help()
        return 0
    try:
        return command(args)
    except (ConfigError, BuildError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
