"""Reporters: publish portfolio reports (CSV files, Google Sheets, ...).

Reporting runs as its own job (`nem report`), reading the store. It never depends on a
strategy or the runner being up: a P&L sheet once stopped updating because the one bot
that wrote it was turned off.
"""
