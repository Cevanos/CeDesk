"""Built-in and custom reports over time and by group (feature-gap plan 3.1, 3.2).

`POST /reports/run` runs a built-in report (`preset`) or a definition (`definition`) and
answers JSON, or a CSV file with `?format=csv`. `GET /reports/options` lists what a report
builder can offer. A definition is checked against fixed lists before anything runs; see
`domain/report_defs.py`.
"""

from datetime import UTC, date, datetime
from typing import Any, Self

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, model_validator

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, SettingsDep
from cedesk.helpdesk.api.reports import CSV_OK, REPORT_ERRORS, FormatQuery
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import report_csv
from cedesk.helpdesk.domain import report_defs as defs
from cedesk.helpdesk.domain.report_csv import Table
from cedesk.helpdesk.service import report_engine

router = APIRouter(tags=["reports"])

DEFINITION_HELP = (
    "`metric` ("
    + ", ".join(defs.METRICS)
    + "), `group_by` ("
    + ", ".join(defs.GROUP_BYS)
    + "), `filters` ("
    + ", ".join(defs.FILTER_KEYS)
    + ") and `range` (`{preset}`: "
    + ", ".join(defs.PRESET_RANGES)
    + "; or `{from, to}` with a time zone). Anything else is 422."
)


class RunIn(Body):
    preset: str | None = Field(
        default=None, description="A built-in report from `GET /reports/options`."
    )
    definition: dict[str, Any] | None = Field(default=None, description=DEFINITION_HELP)
    range: dict[str, Any] | None = Field(
        default=None, description="With `preset`: another range than the report's own."
    )

    @model_validator(mode="after")
    def one_of(self) -> Self:
        if (self.preset is None) == (self.definition is None):
            raise ValueError("Send a preset or a definition, not both and not neither.")
        if self.range is not None and self.preset is None:
            raise ValueError("range goes with a preset; a definition holds its own range.")
        return self


class GroupOut(BaseModel):
    key: str | None = Field(description="A day (YYYY-MM-DD), an id or a name; null: not set.")
    label: str
    value: float | int | None = Field(description="Null: nothing to measure there.")
    n: int = Field(description="Tickets, ratings or log entries the value rests on.")


class ResultOut(BaseModel):
    metric: str
    label: str
    unit: str = Field(description="count, minutes, percent or score")
    group_by: str
    from_: datetime | None = Field(
        alias="from", serialization_alias="from", description="Null: an as-of-now metric."
    )
    to: datetime | None
    timezone: str = Field(description="The workspace's time zone: what a day or week means.")
    total: float | int | None = Field(description="The whole range, ungrouped.")
    total_n: int
    groups: list[GroupOut] = Field(
        description="A time series is oldest first and zero-filled; other groupings are "
        "biggest first, at most 50."
    )
    truncated: bool = Field(description="More groups than the 50 shown.")
    incomplete: bool = Field(
        description="The range reaches back past the archive age: older numbers can be low."
    )
    complete_from: date | None
    first_day: date | None = Field(description="Backlog: where the snapshots start.")
    notes: list[str]

    model_config = {"populate_by_name": True}


class MetricOut(BaseModel):
    key: str
    label: str
    unit: str
    kind: str = Field(description="period, now (as of now: no time grouping) or backlog")


class PresetOut(BaseModel):
    key: str
    label: str
    definition: dict[str, Any]


class OptionsOut(BaseModel):
    metrics: list[MetricOut]
    group_by: list[str]
    filters: list[str]
    ranges: list[str]
    max_groups: int
    presets: list[PresetOut]


def result_out(found: report_engine.Result) -> ResultOut:
    return ResultOut(
        metric=found.metric,
        label=found.label,
        unit=found.unit,
        group_by=found.group_by,
        from_=found.start,
        to=found.end,
        timezone=found.timezone,
        total=found.total,
        total_n=found.total_n,
        groups=[GroupOut(**g.__dict__) for g in found.groups],
        truncated=found.truncated,
        incomplete=found.incomplete,
        complete_from=found.complete_from,
        first_day=found.first_day,
        notes=found.notes,
    )


def csv_for(found: report_engine.Result, now: datetime) -> Response:
    """The result as a CSV file: a summary table, then one row per group."""
    about: list[tuple[str, report_csv.Cell]] = [
        ("Report", found.label),
        ("Grouped by", found.group_by),
        ("Time zone", found.timezone),
    ]
    if found.start and found.end:
        about += [("From", found.start.isoformat()), ("To", found.end.isoformat())]
    summary = Table(
        "Summary", ("Metric", "Value", "Based on"), [(found.label, found.total, found.total_n)]
    )
    groups = Table(
        "Groups",
        (found.group_by.capitalize(), found.label, "Based on"),
        [(g.label, g.value, g.n) for g in found.groups],
    )
    tables = [summary, groups] if found.group_by != "none" else [summary]
    if found.notes:
        tables.append(Table("Notes", ("Note",), [(n,) for n in found.notes]))
    body = report_csv.render("CeDesk report", about, tables)
    name = f"cedesk-report-{found.metric}-{found.group_by}-{now:%Y-%m-%d}.csv"
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get(
    "/reports/options",
    response_model=OptionsOut,
    summary="What a report builder can offer: metrics, groupings, filters, ranges, built-ins",
    responses={403: {"description": "Not allowed to see reports"}},
)
async def report_options(caller: CallerDep) -> OptionsOut:
    report_engine.require_reports(caller)
    return OptionsOut(
        metrics=[MetricOut(**m.__dict__) for m in defs.METRICS.values()],
        group_by=list(defs.GROUP_BYS),
        filters=list(defs.FILTER_KEYS),
        ranges=list(defs.PRESET_RANGES),
        max_groups=defs.MAX_GROUPS,
        presets=[PresetOut(**p.__dict__) for p in defs.PRESETS.values()],
    )


@router.post(
    "/reports/run",
    response_model=ResultOut,
    response_model_by_alias=True,
    summary="Run a built-in report or a custom definition (Supervisor, Admin)",
    description="A time series is zero-filled and bucketed in the workspace's time zone "
    "(weeks start on Monday). Imported tickets stay out of the time metrics, merged tickets "
    "out of everything, as in the other reports. A role limited to one team is held to it. "
    "A run longer than the server's limit is 422; too many at once is 429 with Retry-After.",
    responses=CSV_OK
    | REPORT_ERRORS
    | {
        404: {"description": "No such built-in report, or no such team"},
        429: {"description": "Too many reports are running at once"},
    },
)
async def run_report(
    body: RunIn,
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
    settings: SettingsDep,
    fmt: FormatQuery = "json",
) -> ResultOut | Response:
    timeout_ms = settings.report_timeout_seconds * 1000
    slots = request.app.state.report_slots
    if body.preset is not None:
        found = await report_engine.run_preset(
            sm, caller, body.preset, timeout_ms=timeout_ms, slots=slots, range_=body.range
        )
    else:
        assert body.definition is not None
        found = await report_engine.run(
            sm, caller, body.definition, timeout_ms=timeout_ms, slots=slots
        )
    if fmt == "csv":
        return csv_for(found, datetime.now(UTC))
    return result_out(found)
