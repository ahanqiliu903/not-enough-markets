"""Kalshi's public historical archive as replay data.

Settled markets, per-minute candlesticks (top of book only, no depth) and trade prints,
imported into a separate SQLite file and labeled provenance `historical_candles`. Never
mixed with data recorded by `nem run` / `nem record`. Details and limits:
docs/data-provenance.md (Kalshi history).
"""

from nem.history.db import PROVENANCE, HistoryDb

__all__ = ["PROVENANCE", "HistoryDb"]
