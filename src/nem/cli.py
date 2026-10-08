"""Command-line entry point. Subcommands (run, backtest, record, ...) land in later milestones."""

import argparse
from collections.abc import Sequence

from nem import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nem", description="NotEnoughMarkets")
    parser.add_argument("--version", action="version", version=f"nem {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
