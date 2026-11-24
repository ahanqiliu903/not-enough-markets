"""Import every built-in plugin so its @register decorator runs."""

import importlib

BUILTIN_MODULES = (
    "nem.feeds.coinbase",
    "nem.feeds.model_forecast",
    "nem.feeds.nws",
    "nem.gates.city_filter",
    "nem.gates.feeds",
    "nem.gates.forecast",
    "nem.gates.market_day_window",
    "nem.gates.max_entry_price",
    "nem.gates.strike_filter",
    "nem.gates.time_in_window",
    "nem.reporting.csv_reporter",
    "nem.reporting.sheets",
    "nem.signals.extreme_favorite",
    "nem.sizing.builtin",
)


def load_builtins() -> None:
    for module in BUILTIN_MODULES:
        importlib.import_module(module)
