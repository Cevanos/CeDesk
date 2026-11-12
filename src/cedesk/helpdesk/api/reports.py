"""Reports (md R1-R8): Supervisors, Admins and custom roles with `reports`.

Every report takes a period (`from`, `to`) and answers JSON, or a CSV file with
`?format=csv` (R7). The ticket reports also take `team_id`.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, SettingsDep
from cedesk.helpdesk.domain import report_csv
from cedesk.helpdesk.domain import reports as rules
from cedesk.helpdesk.domain.report_csv import Table
from cedesk.helpdesk.service import reports

router = APIRouter(tags=["reports"])

Format = Literal["json", "csv"]
FormatQuery = Annotated[
    Format, Query(alias="format", description="`csv`: the same report as a CSV file (md R7).")
]
StartQuery = Annotated[
    datetime | None,
    Query(alias="from", description="Default: 30 days before `to`. With a time zone."),
]
EndQuery = Annotated[datetime | None, Query(alias="to", description="Default: now.")]
TeamQuery = Annotated[UUID | None, Query(description="Only this team's tickets.")]

_CSV: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The report; with `format=csv`, a CSV file (UTF-8 with a byte order "
        "mark, one section per table) sent as an attachment.",
        "content": {"text/csv": {"schema": {"type": "string"}}},
    }
}
_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Not allowed to see reports"},
    422: {"description": "from after to, no time zone, or over 366 days"},
}
_TEAM_ERRORS: dict[int | str, dict[str, Any]] = {404: {"description": "No such team"}}


def _csv(
    report: str, title: str, start: datetime, end: datetime, team_id: UUID | None,
    tables: list[Table],
) -> Response:  # fmt: skip
    about: list[tuple[str, report_csv.Cell]] = [
        ("From", start.isoformat()),
        ("To", end.isoformat()),
    ]
    if report != "articles":
        about.append(("Team", str(team_id) if team_id else "All teams"))
    body = report_csv.render(title, about, tables)
    name = report_csv.filename(report, start, end)
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# Team report (md R1, R2, R6)


class CsatSummaryOut(BaseModel):
    ratings: int
    average: float | None
    happy_percent: float | None = Field(description=f"Share of ratings {rules.HAPPY} or 5.")
    by_score: dict[int, int] = Field(description="Ratings per score, 5 down to 1 (bars).")


class SummaryOut(BaseModel):
    open: int = Field(description="Open or Pending now.")
    open_breaching: int = Field(description="Open or Pending now and past their SLA time.")
    created: int
    resolved: int
    avg_first_response_minutes: float | None = Field(
        description="Tickets created in the period: creation to the first agent reply."
    )
    avg_resolution_minutes: float | None = Field(
        description="Tickets resolved in the period: creation to resolution."
    )
    first_reply_sla_met_percent: float | None = Field(
        description="Of the tickets created in the period whose first-reply clock stopped "
        "or ran out, the share answered in time (business hours, as the SLA pill)."
    )
    first_reply_sla_measured: int = Field(description="How many tickets that % is out of.")
    csat: CsatSummaryOut


class TeamRowOut(BaseModel):
    team_id: UUID | None = Field(description='null: the "No team" row.')
    name: str
    open: int
    created: int
    resolved: int
    avg_first_response_minutes: float | None
    csat_ratings: int
    csat_average: float | None


class AgentRowOut(BaseModel):
    member_id: UUID
    name: str
    role: str
    status: str = Field(description="available, on_break, transfers_only or offline (now)")
    open: int = Field(description="Assigned to them, Open or Pending now.")
    resolved: int = Field(description="Assigned to them and resolved in the period.")
    avg_first_response_minutes: float | None
    csat_ratings: int
    csat_average: float | None


class TeamReportOut(BaseModel):
    from_: datetime = Field(alias="from", serialization_alias="from")
    to: datetime
    team_id: UUID | None
    summary: SummaryOut
    teams: list[TeamRowOut]
    agents: list[AgentRowOut] = Field(description="Active members except light agents.")

    model_config = {"populate_by_name": True}


def _team_tables(found: reports.TeamReport) -> list[Table]:
    s = found.summary
    summary = Table(
        "Summary",
        ("Metric", "Value"),
        [
            ("Open now", s.open),
            ("Open and breaching now", s.open_breaching),
            ("Created", s.created),
            ("Resolved", s.resolved),
            ("Average first response (minutes)", s.avg_first_response_minutes),
            ("Average resolution (minutes)", s.avg_resolution_minutes),
            ("First-reply SLA met (%)", s.first_reply_sla_met_percent),
            ("First-reply SLA measured (tickets)", s.first_reply_sla_measured),
            ("CSAT ratings", s.csat.ratings),
            ("CSAT average", s.csat.average),
            ("CSAT happy (%)", s.csat.happy_percent),
        ],
    )
    scores = Table("CSAT by score", ("Score", "Ratings"), list(s.csat.by_score.items()))
    teams = Table(
        "Teams",
        ("Team", "Open", "Created", "Resolved", "Average first response (minutes)",
         "CSAT ratings", "CSAT average"),
        [(t.name, t.open, t.created, t.resolved, t.avg_first_response_minutes, t.csat_ratings,
          t.csat_average) for t in found.teams],
    )  # fmt: skip
    agents = Table(
        "Agents",
        ("Agent", "Role", "Status", "Open", "Resolved", "Average first response (minutes)",
         "CSAT ratings", "CSAT average"),
        [(a.name, a.role, a.status, a.open, a.resolved, a.avg_first_response_minutes,
          a.csat_ratings, a.csat_average) for a in found.agents],
    )  # fmt: skip
    return [summary, scores, teams, agents]


@router.get(
    "/reports/team",
    response_model=TeamReportOut,
    response_model_by_alias=True,
    summary="Team report (Supervisor, Admin)",
    description="Created, resolved and CSAT count within [from, to); open and breaching "
    "are as of now. Imported tickets are left out of the time metrics; their CSAT counts. "
    "Times are wall-clock minutes. Tickets merged into another are left out.",
    responses=_CSV | _ERRORS | _TEAM_ERRORS,
)
async def team_report(
    caller: CallerDep,
    sm: SessionmakerDep,
    start: StartQuery = None,
    end: EndQuery = None,
    team_id: TeamQuery = None,
    fmt: FormatQuery = "json",
) -> TeamReportOut | Response:
    found = await reports.team_report(sm, caller, start=start, end=end, team_id=team_id)
    if fmt == "csv":
        return _csv(
            "team", "CeDesk team report", found.start, found.end, found.team_id,
            _team_tables(found),
        )  # fmt: skip
    return TeamReportOut(
        from_=found.start,
        to=found.end,
        team_id=found.team_id,
        summary=SummaryOut(
            **{k: v for k, v in found.summary.__dict__.items() if k != "csat"},
            csat=CsatSummaryOut(**found.summary.csat.__dict__),
        ),
        teams=[TeamRowOut(**r.__dict__) for r in found.teams],
        agents=[AgentRowOut(**r.__dict__) for r in found.agents],
    )


# More metrics (md R3, R4, R5)


class MetricsSummaryOut(BaseModel):
    open: int = Field(description="The backlog: Open or Pending now.")
    one_touch: int = Field(
        description="Resolved in the period with exactly one reply from a team member, "
        "never reopened."
    )
    one_touch_measured: int = Field(
        description="Tickets resolved in the period (not imported): what one-touch is out of."
    )
    one_touch_percent: float | None
    reopened: int = Field(description="Of `reopen_measured`, reopened at least once.")
    reopen_measured: int = Field(
        description="Created in the period (not imported) and resolved at least once."
    )
    reopen_rate_percent: float | None
    time_logged_minutes: int = Field(description="Logged in the period, by anyone.")
    billable_minutes: int = Field(description="Of that, billable.")


class AgeRowOut(BaseModel):
    bucket: str = Field(description="under_1h, 1h_4h, 4h_24h or over_1d")
    label: str
    open: int = Field(description="Open or Pending now, created that long ago.")


class CountRowOut(BaseModel):
    value: str | None = Field(description="The channel, topic or sentiment; null: not set.")
    label: str
    created: int = Field(description="Created in the period.")
    open: int = Field(description="Open or Pending now.")


class AgentTimeOut(BaseModel):
    member_id: UUID
    name: str
    role: str
    logged_minutes: int = Field(description="Logged in the period.")
    billable_minutes: int
    open_tasks: int = Field(description="Owned and not done, now.")
    overdue_tasks: int = Field(description="Of those, past their due time.")


class MetricsReportOut(BaseModel):
    from_: datetime = Field(alias="from", serialization_alias="from")
    to: datetime
    team_id: UUID | None
    governance_url: str | None = Field(
        description="The Governance report in Cevanos (md R5), when this workspace has "
        "connected Cevanos; null otherwise. CeDesk only links to it."
    )
    summary: MetricsSummaryOut
    backlog_by_age: list[AgeRowOut]
    channels: list[CountRowOut] = Field(description="Every channel, zeros included.")
    topics: list[CountRowOut] = Field(
        description='The keyword topics, then "General question"; "Not set" last when some '
        "tickets have no topic."
    )
    sentiment: list[CountRowOut]
    agents: list[AgentTimeOut] = Field(description="Active members except light agents.")

    model_config = {"populate_by_name": True}


def _metrics_tables(found: reports.MetricsReport) -> list[Table]:
    s = found.summary
    summary = Table(
        "Summary",
        ("Metric", "Value"),
        [
            ("Open now (backlog)", s.open),
            ("One-touch resolution (%)", s.one_touch_percent),
            ("One-touch resolved (tickets)", s.one_touch),
            ("Resolved, measured for one-touch (tickets)", s.one_touch_measured),
            ("Reopen rate (%)", s.reopen_rate_percent),
            ("Reopened (tickets)", s.reopened),
            ("Resolved at least once, measured for reopen (tickets)", s.reopen_measured),
            ("Time logged (minutes)", s.time_logged_minutes),
            ("Billable (minutes)", s.billable_minutes),
        ],
    )
    ages = Table(
        "Backlog by age", ("Age", "Open now"), [(a.label, a.open) for a in found.backlog_by_age]
    )

    def counts(title: str, column: str, rows: list[reports.CountRow]) -> Table:
        return Table(
            title, (column, "Created", "Open now"), [(r.label, r.created, r.open) for r in rows]
        )

    agents = Table(
        "Handling time per agent",
        ("Agent", "Role", "Logged (minutes)", "Billable (minutes)", "Open tasks",
         "Overdue tasks"),
        [(a.name, a.role, a.logged_minutes, a.billable_minutes, a.open_tasks, a.overdue_tasks)
         for a in found.agents],
    )  # fmt: skip
    return [
        summary,
        ages,
        counts("Tickets by channel", "Channel", found.channels),
        counts("Tickets by topic", "Topic", found.topics),
        counts("Tickets by sentiment", "Sentiment", found.sentiment),
        agents,
    ]


@router.get(
    "/reports/metrics",
    response_model=MetricsReportOut,
    response_model_by_alias=True,
    summary="More metrics (Supervisor, Admin)",
    description="One-touch resolution, reopen rate, backlog by age, tickets by channel, "
    "topic and sentiment, time logged and open tasks per agent, and the link to Cevanos's "
    "Governance report when connected. Created and resolved count within [from, to); the "
    "backlog, open counts and open tasks are as of now. Merged tickets are left out; "
    "imported tickets are left out of one-touch and reopen.",
    responses=_CSV | _ERRORS | _TEAM_ERRORS,
)
async def metrics_report(
    caller: CallerDep,
    sm: SessionmakerDep,
    settings: SettingsDep,
    start: StartQuery = None,
    end: EndQuery = None,
    team_id: TeamQuery = None,
    fmt: FormatQuery = "json",
) -> MetricsReportOut | Response:
    found = await reports.metrics_report(
        sm, caller, start=start, end=end, team_id=team_id,
        cevanos_app_url=settings.cevanos_app_url,
    )  # fmt: skip
    if fmt == "csv":
        return _csv(
            "metrics", "CeDesk more metrics", found.start, found.end, found.team_id,
            _metrics_tables(found),
        )  # fmt: skip
    s = found.summary
    return MetricsReportOut(
        from_=found.start,
        to=found.end,
        team_id=found.team_id,
        governance_url=found.governance_url,
        summary=MetricsSummaryOut(**s.__dict__),
        backlog_by_age=[AgeRowOut(**a.__dict__) for a in found.backlog_by_age],
        channels=[CountRowOut(**r.__dict__) for r in found.channels],
        topics=[CountRowOut(**r.__dict__) for r in found.topics],
        sentiment=[CountRowOut(**r.__dict__) for r in found.sentiment],
        agents=[AgentTimeOut(**a.__dict__) for a in found.agents],
    )


# Help-article votes (md R8)


class ArticleRowOut(BaseModel):
    article_id: UUID
    title: str
    category: str | None
    status: str = Field(description="draft or published (now)")
    votes: int
    helpful: int
    not_helpful: int
    helpful_percent: float | None


class ArticleReportOut(BaseModel):
    from_: datetime = Field(alias="from", serialization_alias="from")
    to: datetime
    votes: int = Field(description="Votes cast or changed in the period, every article.")
    helpful: int
    not_helpful: int
    helpful_percent: float | None
    articles_voted: int
    articles: list[ArticleRowOut] = Field(
        description=f"Articles with a vote in the period, most votes first; at most "
        f"{reports.ARTICLE_ROWS}."
    )
    truncated: bool = Field(description="More articles had votes than are listed.")

    model_config = {"populate_by_name": True}


def _article_tables(found: reports.ArticleReport) -> list[Table]:
    summary = Table(
        "Summary",
        ("Metric", "Value"),
        [
            ("Votes", found.votes),
            ("Helpful", found.helpful),
            ("Not helpful", found.not_helpful),
            ("Helpful (%)", found.helpful_percent),
            ("Articles voted on", found.articles_voted),
        ],
    )
    articles = Table(
        "Articles",
        ("Article", "Category", "Status", "Votes", "Helpful", "Not helpful", "Helpful (%)"),
        [(a.title, a.category, a.status, a.votes, a.helpful, a.not_helpful, a.helpful_percent)
         for a in found.articles],
    )  # fmt: skip
    return [summary, articles]


@router.get(
    "/reports/articles",
    response_model=ArticleReportOut,
    response_model_by_alias=True,
    summary="Help-article votes (Supervisor, Admin)",
    description='"Was this helpful?" answers given (or changed) within [from, to). A visitor '
    "has one vote per article: their latest answer counts.",
    responses=_CSV | _ERRORS,
)
async def article_report(
    caller: CallerDep,
    sm: SessionmakerDep,
    start: StartQuery = None,
    end: EndQuery = None,
    fmt: FormatQuery = "json",
) -> ArticleReportOut | Response:
    found = await reports.article_report(sm, caller, start=start, end=end)
    if fmt == "csv":
        return _csv(
            "articles", "CeDesk help-article votes", found.start, found.end, None,
            _article_tables(found),
        )  # fmt: skip
    return ArticleReportOut(
        from_=found.start,
        to=found.end,
        votes=found.votes,
        helpful=found.helpful,
        not_helpful=found.not_helpful,
        helpful_percent=found.helpful_percent,
        articles_voted=found.articles_voted,
        articles=[ArticleRowOut(**a.__dict__) for a in found.articles],
        truncated=found.truncated,
    )
