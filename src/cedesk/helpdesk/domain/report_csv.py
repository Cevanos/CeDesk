"""CSV export of every report (md R7).

One file per report, in sections a spreadsheet opens as they are: the report's name and
period at the top, then each table with a title row and a header row, a blank row between.

- Text that a spreadsheet would run as a formula (it starts with `=`, `+`, `-`, `@`, a tab
  or a carriage return) gets a `'` in front, so an agent named "=HYPERLINK(...)" or an
  article titled "-1+1" stays text (OWASP "CSV injection"). Numbers are written as numbers.
- UTF-8 with a byte order mark, so Excel shows Hindi names and ₹ correctly.
"""

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

Cell = str | int | float | Decimal | bool | None

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
BOM = "﻿"


@dataclass(frozen=True)
class Table:
    title: str
    header: tuple[str, ...]
    rows: Sequence[Sequence[Cell]]


def cell(value: Cell) -> str:
    """One value as the spreadsheet should see it. None is an empty cell."""
    if value is None:
        return ""
    if isinstance(value, bool):  # before int: True is an int
        return "yes" if value else "no"
    if isinstance(value, int | float | Decimal):
        return str(value)
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def render(title: str, about: Sequence[tuple[str, Cell]], tables: Sequence[Table]) -> str:
    """The whole file: `title`, then one `name, value` row per item of `about` (period,
    team), then the tables."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([cell(title)])
    for name, value in about:
        writer.writerow([cell(name), cell(value)])
    for table in tables:
        writer.writerow([])
        writer.writerow([cell(table.title)])
        writer.writerow([cell(h) for h in table.header])
        for row in table.rows:
            writer.writerow([cell(v) for v in row])
    return BOM + out.getvalue()


def filename(report: str, start: datetime, end: datetime) -> str:
    """`cedesk-team-report-2026-10-13-to-2026-11-12.csv`. `report` is a fixed name from
    the code, never typed by a user."""
    return f"cedesk-{report}-report-{start:%Y-%m-%d}-to-{end:%Y-%m-%d}.csv"
