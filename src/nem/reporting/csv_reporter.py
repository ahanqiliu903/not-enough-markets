"""Writes `<path>/<portfolio>/{summary,strategies,trades}.csv`, plus `equity.csv` (value over
time) when the report has it (`nem report`), replacing old files."""

import csv
import os
from pathlib import Path

from nem.core.registry import PluginParams, register
from nem.reporting.base import Reporter
from nem.stats.report import PortfolioReport, report_tables


@register("reporter", "csv")
class CsvReporter(Reporter):
    class Params(PluginParams):
        path: str = "out"

    def __init__(self, params: Params) -> None:
        self.root = Path(params.path)

    def publish(self, report: PortfolioReport) -> str:
        directory = self.root / report.portfolio.name
        directory.mkdir(parents=True, exist_ok=True)
        for table in report_tables(report):
            target = directory / f"{table.name}.csv"
            tmp = target.with_suffix(".csv.tmp")
            with tmp.open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(table.columns)
                writer.writerows(table.rows)
            os.replace(tmp, target)  # readers never see a half-written file
        return str(directory)
