"""Command-line entry point."""

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

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


# Commands in the order `nem help` shows them.
HELP_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Getting started", ("init", "demo")),
    ("Portfolios and strategies", ("portfolio new", "portfolio list", "portfolio show",
                                   "strategy add", "validate")),
    ("Paper trading", ("run", "replay")),
    ("Results", ("summary", "report")),
    ("Operations (24/7)", ("status", "halt", "resume")),
    ("Market data", ("record", "export")),
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
    run.add_argument("--depth", type=int, default=10, help="orderbook levels kept for fills")
    run.add_argument("--once", action="store_true", help="one tick, then exit (smoke test)")
    run.add_argument(
        "--record-series", action="append", help="also record this series (repeatable)"
    )

    st = with_db(with_dir(cmds.add(sub, "status", "process health, halts, per-strategy activity")))
    st.add_argument(
        "--expect", default="runner", help="processes that should be up, comma-separated"
    )
    st.add_argument("--max-age", default="2m", help="heartbeat older than this is stale")

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

    with_stats(
        with_dir(cmds.add(sub, "summary", "statistics per strategy: win rate vs break-even"))
    )
    rpt = with_stats(with_dir(cmds.add(
        sub, "report", "statistics, published to each portfolio's reporters (CSV, Sheets)"
    )))  # fmt: skip
    rpt.add_argument("--every", help="repeat on this schedule, e.g. 5m (default: once)")
    rpt.add_argument("--no-publish", action="store_true", help="print only")

    rec = with_db(cmds.add(sub, "record", "record Kalshi market data without trading"))
    rec.add_argument(
        "--series", action="append", required=True, help="series ticker, e.g. KXBTC15M (repeatable)"
    )
    rec.add_argument("--interval", type=float, default=5.0, help="seconds between polls")
    rec.add_argument("--depth", type=int, default=10, help="orderbook levels to keep (0 = none)")
    rec.add_argument("--once", action="store_true", help="poll once and exit")

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


def cmd_validate(args: argparse.Namespace) -> int:
    from nem.engine.runtime import build_feeds, build_strategies

    portfolios = _load(args.dir)
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
    from nem.market.kalshi import KalshiClient
    from nem.market.recorder import Recorder
    from nem.market.source import KalshiSource, ReplaySource, Tick
    from nem.store import Store

    directory: Path = args.dir
    portfolios = _load(directory)
    db: Path = args.db
    db.parent.mkdir(parents=True, exist_ok=True)
    clock = SystemClock()
    client = KalshiClient("prod")  # market data is always real; orders are paper
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
                    runner.reload(load_portfolios(directory))
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
    from nem.ops import render_status
    from nem.store import Store

    portfolios = load_portfolios(args.dir) if args.dir.is_dir() else []
    expect = [x for x in args.expect.split(",") if x]
    with Store(args.db) as store:
        text, healthy = render_status(
            store, portfolios, datetime.now(UTC), expect, parse_duration(args.max_age)
        )
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
    "status": cmd_status,
    "halt": cmd_halt,
    "resume": cmd_resume,
    "demo": cmd_demo,
    "export": cmd_export,
    "record": cmd_record,
}


def main(argv: Sequence[str] | None = None) -> int:
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
    except (ConfigError, BuildError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
