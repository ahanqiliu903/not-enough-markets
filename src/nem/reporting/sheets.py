"""Google Sheets reporter. Needs `uv sync --extra sheets` (gspread).

Credentials come from the environment only: `SHEETS_CREDENTIALS` is the path to a Google
Cloud service-account JSON key, and the spreadsheet ID is read from the env var named by
`sheet_id_env` (default `SHEET_ID`). Share the spreadsheet with the service account's
email so it can write.

Each portfolio writes only its own tabs, `<portfolio> summary`, `<portfolio> strategies`
and `<portfolio> trades`, so several portfolios can share one spreadsheet.
"""

import os
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from pydantic import Field

from nem.core.registry import PluginParams, register
from nem.reporting.base import Reporter
from nem.stats.report import Cell, PortfolioReport, tables


class SheetsError(RuntimeError):
    pass


class Worksheet(Protocol):
    def clear(self) -> Any: ...
    def update(self, values: Sequence[Sequence[Any]], range_name: str | None = None) -> Any: ...


class Spreadsheet(Protocol):
    def worksheet(self, title: str) -> Worksheet: ...
    def add_worksheet(self, title: str, rows: int, cols: int) -> Worksheet: ...


def open_with_gspread(credentials_path: str, sheet_id: str) -> Spreadsheet:
    try:
        import gspread
    except ImportError as e:
        raise SheetsError("the sheets reporter needs gspread: run `uv sync --extra sheets`") from e
    client = gspread.service_account(filename=credentials_path)
    sheet: Spreadsheet = client.open_by_key(sheet_id)  # pyright: ignore[reportAssignmentType]
    return sheet


def _cell(v: Cell) -> str | int | float:
    return "" if v is None else v


@register("reporter", "sheets")
class SheetsReporter(Reporter):
    class Params(PluginParams):
        sheet_id_env: str = Field(default="SHEET_ID", pattern=r"^[A-Z][A-Z0-9_]*$")

    def __init__(
        self,
        params: Params,
        opener: Callable[[str, str], Spreadsheet] = open_with_gspread,
    ) -> None:
        self.sheet_id_env = params.sheet_id_env
        self._opener = opener

    def _worksheet(self, sheet: Spreadsheet, title: str, rows: int, cols: int) -> Worksheet:
        try:
            return sheet.worksheet(title)
        except Exception:  # gspread.WorksheetNotFound; avoid importing gspread here
            return sheet.add_worksheet(title, rows=max(rows, 10), cols=max(cols, 5))

    def publish(self, report: PortfolioReport) -> str:
        creds = os.environ.get("SHEETS_CREDENTIALS")
        sheet_id = os.environ.get(self.sheet_id_env)
        if not creds or not sheet_id:
            raise SheetsError(
                f"set SHEETS_CREDENTIALS and {self.sheet_id_env} to publish to Sheets"
            )
        sheet = self._opener(creds, sheet_id)
        for table in tables(report):
            values = [table.columns, *[[_cell(c) for c in row] for row in table.rows]]
            title = f"{report.portfolio.name} {table.name}"
            ws = self._worksheet(sheet, title, len(values) + 10, len(table.columns))
            ws.clear()
            ws.update(values, "A1")
        return f"Google Sheet ${self.sheet_id_env} ({report.portfolio.name} tabs)"
