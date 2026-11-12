"""CSV export of the reports (md R7) and the report rules that need no database (R4, R5)."""

import csv
import io
from datetime import UTC, datetime

import pytest

from cedesk.helpdesk.domain import report_csv
from cedesk.helpdesk.domain.reports import AGE_BUCKETS, governance_url


@pytest.mark.parametrize(
    "text",
    ['=HYPERLINK("http://x")', "+1+1", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"],
)
def test_text_a_spreadsheet_would_run_stays_text(text: str) -> None:
    assert report_csv.cell(text) == "'" + text


def test_plain_values_are_written_as_they_are() -> None:
    assert report_csv.cell("Neha") == "Neha"
    assert report_csv.cell("नेहा") == "नेहा"
    assert report_csv.cell("a=b") == "a=b"  # only a leading sign matters
    assert report_csv.cell(None) == ""
    assert report_csv.cell(True) == "yes"
    assert report_csv.cell(False) == "no"
    assert report_csv.cell(0) == "0"
    assert report_csv.cell(-3) == "-3"  # a number, not text: no quote mark
    assert report_csv.cell(75.5) == "75.5"


def test_a_report_file_reads_back_as_its_sections() -> None:
    tables = [
        report_csv.Table("Summary", ("Metric", "Value"), [("Open", 4), ("Average", None)]),
        report_csv.Table("Agents", ("Name", "Note"), [("=evil()", 'says "hi", twice')]),
    ]
    out = report_csv.render("Team report", [("From", "2026-11-01"), ("Team", None)], tables)
    assert out.startswith(report_csv.BOM)
    rows = list(csv.reader(io.StringIO(out.removeprefix(report_csv.BOM))))
    assert rows == [
        ["Team report"],
        ["From", "2026-11-01"],
        ["Team", ""],
        [],
        ["Summary"],
        ["Metric", "Value"],
        ["Open", "4"],
        ["Average", ""],
        [],
        ["Agents"],
        ["Name", "Note"],
        ["'=evil()", 'says "hi", twice'],
    ]


def test_the_file_name_has_the_period() -> None:
    name = report_csv.filename(
        "team", datetime(2026, 10, 13, 9, tzinfo=UTC), datetime(2026, 11, 12, 9, tzinfo=UTC)
    )
    assert name == "cedesk-team-report-2026-10-13-to-2026-11-12.csv"


def test_backlog_buckets_cover_every_age_once() -> None:
    assert [b.key for b in AGE_BUCKETS] == ["under_1h", "1h_4h", "4h_24h", "over_1d"]
    bounds = [b.under_hours for b in AGE_BUCKETS]
    assert bounds[-1] is None  # the last one has no upper end
    upper = [b for b in bounds if b is not None]
    assert upper == sorted(upper) and len(set(upper)) == len(upper)


def test_the_governance_link_points_into_the_cevanos_app() -> None:
    assert governance_url("https://cevanos.ai") == "https://cevanos.ai/dashboard/governance"
    assert governance_url("https://cevanos.ai/") == "https://cevanos.ai/dashboard/governance"
