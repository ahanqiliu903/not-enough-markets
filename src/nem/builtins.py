"""Import every built-in plugin so its @register decorator runs."""

import importlib

BUILTIN_MODULES = (
    "nem.feeds.coinbase",
    "nem.feeds.nws",
    "nem.gates.feeds",
    "nem.gates.max_entry_price",
    "nem.gates.time_in_window",
    "nem.reporting.csv_reporter",
    "nem.reporting.sheets",
    "nem.signals.extreme_favorite",
    "nem.sizing.builtin",
)


def load_builtins() -> None:
    for module in BUILTIN_MODULES:
        importlib.import_module(module)
