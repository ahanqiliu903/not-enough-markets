from nem.core.registry import Plugin
from nem.stats.report import PortfolioReport


class Reporter(Plugin):
    """Publishes one portfolio's report. Configured per portfolio under `reporting:`."""

    def publish(self, report: PortfolioReport) -> str:
        """Publish and return a short description of where it went."""
        raise NotImplementedError
