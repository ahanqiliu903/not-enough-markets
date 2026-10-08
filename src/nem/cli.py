"""Command-line entry point."""

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from nem import __version__

DEFAULT_DB = Path("data/nem.db")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nem", description="NotEnoughMarkets")
    parser.add_argument("--version", action="version", version=f"nem {__version__}")
    sub = parser.add_subparsers(dest="command")

    rec = sub.add_parser("record", help="record live Kalshi snapshots and settlements")
    rec.add_argument(
        "--series", action="append", required=True, help="series ticker, e.g. KXBTC15M (repeatable)"
    )
    rec.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"default: {DEFAULT_DB}")
    rec.add_argument("--interval", type=float, default=5.0, help="seconds between polls")
    rec.add_argument("--depth", type=int, default=10, help="orderbook levels to keep (0 = none)")
    rec.add_argument("--once", action="store_true", help="poll once and exit")
    return parser


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is too noisy
    if args.command == "record":
        return cmd_record(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
