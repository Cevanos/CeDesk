"""Reports (md R1-R8): Supervisors, Admins and custom roles with `reports`.

- Team report (R1, R2, R6). The first-reply SLA met % uses the same SLA rules as each
  ticket's SLA pill (service/sla.py), so the two always agree: of the tickets created in
  the period whose first-reply clock has stopped or run out, the share answered in time.
- More metrics (R3, R4): one-touch, reopen rate, backlog by age, channels, topics,
  sentiment, time logged and open tasks; with Cevanos connected, the link to its
  Governance report (R5).
- Articles (R8): help-center votes.

Rules for what counts are in domain/reports.py. A `team_only` role reports on its own
team only (md A3), in every ticket report.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import reports as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.tickets import Channel, Sentiment
from cedesk.helpdesk.domain.triage import GENERAL, TOPICS
from cedesk.helpdesk.repo import cevanos as cevanos_repo
from cedesk.helpdesk.repo import reports as repo
from cedesk.helpdesk.service import scope, sla
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, NotFound
from cedesk.helpdesk.service.members import require

NO_TEAM = "No team"


@dataclass(frozen=True)
class Csat:
    ratings: int
    average: float | None
    happy_percent: float | None
    by_score: dict[int, int]  # 5 down to 1


@dataclass(frozen=True)
class Summary:
    open: int
    open_breaching: int
    created: int
    resolved: int
    avg_first_response_minutes: float | None
    avg_resolution_minutes: float | None
    first_reply_sla_met_percent: float | None
    first_reply_sla_measured: int
    csat: Csat


@dataclass(frozen=True)
class TeamRow:
    team_id: UUID | None
    name: str
    open: int
    created: int
    resolved: int
    avg_first_response_minutes: float | None
    first_reply_sla_met_percent: float | None
    first_reply_sla_measured: int
    csat_ratings: int
    csat_average: float | None


@dataclass(frozen=True)
class AgentRow:
    member_id: UUID
    name: str
    role: str
    status: str
    open: int
    resolved: int
    avg_first_response_minutes: float | None
    csat_ratings: int
    csat_average: float | None


@dataclass(frozen=True)
class TeamReport:
    start: datetime
    end: datetime
    team_id: UUID | None
    summary: Summary
    teams: list[TeamRow]
    agents: list[AgentRow]


_EMPTY: Mapping[str, Any] = {
    "open": 0, "breaching": 0, "created": 0, "resolved": 0,
    "first_response_seconds": None, "resolution_seconds": None,
}  # fmt: skip
_NO_RATINGS: Mapping[str, Any] = {"ratings": 0, "average": None, "happy": 0}


def _avg(value: Any) -> float | None:
    return round(float(value), 2) if value is not None else None


def _split(
    rows: Sequence[Mapping[Any, Any]],
) -> tuple[Mapping[str, Any] | None, dict[Any, Mapping[str, Any]], dict[Any, Mapping[str, Any]]]:
    """GROUPING SETS rows → (the total, per team, per assignee)."""
    total = None
    by_team: dict[Any, Mapping[str, Any]] = {}
    by_agent: dict[Any, Mapping[str, Any]] = {}
    for r in rows:
        if r.get("by_all_scores", 1) == 0:
            continue  # a per-score row
        if r["by_all_teams"] and r["by_all_agents"]:
            total = r
        elif not r["by_all_teams"]:
            by_team[r["team_id"]] = r
        else:
            by_agent[r["assignee_id"]] = r
    return total, by_team, by_agent


def _period(
    start: datetime | None, end: datetime | None, now: datetime
) -> tuple[datetime, datetime]:
    try:
        return rules.period(start, end, now)
    except rules.BadPeriod as exc:
        raise Invalid(str(exc)) from exc


def _team_filter(caller: Caller, team_id: UUID | None) -> UUID | None:
    """The team a ticket report covers. A `team_only` role reports on its own team
    (md A3); asking for another is a team it can't see."""
    only = scope.visible_team(caller)
    if only is None:
        return team_id
    if team_id not in (None, only):
        raise NotFound("Team not found.")
    return only


async def team_report(
    sm: Sessionmaker,
    caller: Caller,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    team_id: UUID | None = None,
) -> TeamReport:
    require(caller, Action.reports)
    team_id = _team_filter(caller, team_id)
    now = datetime.now(UTC)
    start, end = _period(start, end, now)
    async with workspace_session(sm, caller.workspace_id) as s:
        if team_id is not None and not await repo.team_exists(s, team_id):
            raise NotFound("Team not found.")
        ticket_rows = await repo.tickets(s, start, end, now, team_id, rules.IMPORT_SOURCES)
        rating_rows = await repo.ratings(s, start, end, team_id, rules.HAPPY)
        facts = await repo.sla_facts(s, start, end, team_id, rules.IMPORT_SOURCES)
        cfg = await sla.config(s)
        teams = await repo.teams(s)
        agents = await repo.agents(s, team_id)

    total, ticket_by_team, ticket_by_agent = _split(ticket_rows)
    rated, rated_by_team, rated_by_agent = _split(rating_rows)
    by_score = {score: 0 for score in range(5, 0, -1)}
    for r in rating_rows:
        if r["by_all_scores"] == 0:
            by_score[int(r["score"])] = int(r["ratings"])
    t = total or _EMPTY
    c = rated or _NO_RATINGS

    met = missed = 0
    team_met: dict[UUID | None, int] = {}
    team_missed: dict[UUID | None, int] = {}
    for row in facts:
        answer = sla.first_reply_met(row, cfg, now)
        if answer is None:
            continue  # its first-reply clock is still running
        tally = team_met if answer else team_missed
        tally[row["team_id"]] = tally.get(row["team_id"], 0) + 1
        met += answer
        missed += not answer

    summary = Summary(
        open=int(t["open"]),
        open_breaching=int(t["breaching"]),
        created=int(t["created"]),
        resolved=int(t["resolved"]),
        avg_first_response_minutes=rules.minutes(t["first_response_seconds"]),
        avg_resolution_minutes=rules.minutes(t["resolution_seconds"]),
        first_reply_sla_met_percent=rules.percent(met, met + missed),
        first_reply_sla_measured=met + missed,
        csat=Csat(
            ratings=int(c["ratings"]),
            average=_avg(c["average"]),
            happy_percent=rules.percent(int(c["happy"]), int(c["ratings"])),
            by_score=by_score,
        ),
    )

    shown: list[tuple[UUID | None, str]] = [
        (r["id"], str(r["name"])) for r in teams if team_id is None or r["id"] == team_id
    ]
    if team_id is None and None in ticket_by_team:
        shown.append((None, NO_TEAM))
    team_rows = []
    for tid, name in shown:
        tr = ticket_by_team.get(tid, _EMPTY)
        cr = rated_by_team.get(tid, _NO_RATINGS)
        in_time, late = team_met.get(tid, 0), team_missed.get(tid, 0)
        team_rows.append(
            TeamRow(
                team_id=tid,
                name=name,
                open=int(tr["open"]),
                created=int(tr["created"]),
                resolved=int(tr["resolved"]),
                avg_first_response_minutes=rules.minutes(tr["first_response_seconds"]),
                first_reply_sla_met_percent=rules.percent(in_time, in_time + late),
                first_reply_sla_measured=in_time + late,
                csat_ratings=int(cr["ratings"]),
                csat_average=_avg(cr["average"]),
            )
        )

    agent_rows = []
    for a in agents:
        ar = ticket_by_agent.get(a["id"], _EMPTY)
        cr = rated_by_agent.get(a["id"], _NO_RATINGS)
        agent_rows.append(
            AgentRow(
                member_id=a["id"],
                name=str(a["name"]),
                role=str(a["role"]),
                status=str(a["status"]),
                open=int(ar["open"]),
                resolved=int(ar["resolved"]),
                avg_first_response_minutes=rules.minutes(ar["first_response_seconds"]),
                csat_ratings=int(cr["ratings"]),
                csat_average=_avg(cr["average"]),
            )
        )
    return TeamReport(start, end, team_id, summary, team_rows, agent_rows)


# More metrics (md R3, R4, R5)


@dataclass(frozen=True)
class MetricsSummary:
    open: int  # the backlog: Open or Pending now
    one_touch: int
    one_touch_measured: int  # resolved in the period, not imported
    one_touch_percent: float | None
    reopened: int
    reopen_measured: int  # created in the period, not imported, resolved at least once
    reopen_rate_percent: float | None
    time_logged_minutes: int
    billable_minutes: int


@dataclass(frozen=True)
class AgeRow:
    bucket: str
    label: str
    open: int


@dataclass(frozen=True)
class CountRow:
    """One bar: a channel, a topic or a sentiment. `value` None: not set yet."""

    value: str | None
    label: str
    created: int
    open: int


@dataclass(frozen=True)
class AgentTime:
    member_id: UUID
    name: str
    role: str
    logged_minutes: int
    billable_minutes: int
    open_tasks: int
    overdue_tasks: int


@dataclass(frozen=True)
class MetricsReport:
    start: datetime
    end: datetime
    team_id: UUID | None
    governance_url: str | None
    summary: MetricsSummary
    backlog_by_age: list[AgeRow]
    channels: list[CountRow]
    topics: list[CountRow]
    sentiment: list[CountRow]
    agents: list[AgentTime]


_NO_COUNT: Mapping[str, Any] = {"created": 0, "open": 0}


def _counts(rows: Sequence[Mapping[Any, Any]], column: str, known: Sequence[str]) -> list[CountRow]:
    """Every known value in its order (zeros included, so the bars don't jump about),
    then any other value found, then "Not set" when some tickets have none."""
    found = {r[column]: r for r in rows if r[f"by_all_{column}s"] == 0}
    others = sorted(v for v in found if v is not None and v not in known)
    values: list[str | None] = [*known, *others]
    if None in found:
        values.append(None)
    out = []
    for value in values:
        r = found.get(value, _NO_COUNT)
        label = rules.NOT_SET if value is None else str(value)
        out.append(CountRow(value, label, int(r["created"]), int(r["open"])))
    return out


async def metrics_report(
    sm: Sessionmaker,
    caller: Caller,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    team_id: UUID | None = None,
    cevanos_app_url: str | None = None,
) -> MetricsReport:
    """`cevanos_app_url`: the Cevanos web app (a deployment setting). The Governance link
    is given only when this workspace has connected Cevanos."""
    require(caller, Action.reports)
    team_id = _team_filter(caller, team_id)
    now = datetime.now(UTC)
    start, end = _period(start, end, now)
    async with workspace_session(sm, caller.workspace_id) as s:
        if team_id is not None and not await repo.team_exists(s, team_id):
            raise NotFound("Team not found.")
        counted = await repo.breakdown(s, start, end, now, team_id)
        flow = await repo.flow(s, start, end, team_id, rules.IMPORT_SOURCES)
        logged = await repo.time_logged(s, start, end, team_id)
        tasks = await repo.open_tasks(s, now, team_id)
        agents = await repo.agents(s, team_id)
        connected = await cevanos_repo.connection(s) is not None

    total = next(
        (r for r in counted
         if r["by_all_channels"] and r["by_all_topics"] and r["by_all_sentiments"]),
        None,
    )  # fmt: skip
    time_total = next((r for r in logged if r["by_all_members"]), None)
    time_by_member = {r["member_id"]: r for r in logged if not r["by_all_members"]}
    task_by_owner = {r["owner_id"]: r for r in tasks}

    one_touch, measured = int(flow["one_touch"]), int(flow["resolved"])
    reopened, reopen_measured = int(flow["reopened"]), int(flow["reopen_measured"])
    summary = MetricsSummary(
        open=int(total["open"]) if total else 0,
        one_touch=one_touch,
        one_touch_measured=measured,
        one_touch_percent=rules.percent(one_touch, measured),
        reopened=reopened,
        reopen_measured=reopen_measured,
        reopen_rate_percent=rules.percent(reopened, reopen_measured),
        time_logged_minutes=int(time_total["minutes"]) if time_total else 0,
        billable_minutes=int(time_total["billable"]) if time_total else 0,
    )
    ages = [
        AgeRow(b.key, b.label, int(total[f"age_{b.key}"]) if total else 0)
        for b in rules.AGE_BUCKETS
    ]
    agent_rows = []
    for a in agents:
        logs = time_by_member.get(a["id"])
        owned = task_by_owner.get(a["id"])
        agent_rows.append(
            AgentTime(
                member_id=a["id"],
                name=str(a["name"]),
                role=str(a["role"]),
                logged_minutes=int(logs["minutes"]) if logs else 0,
                billable_minutes=int(logs["billable"]) if logs else 0,
                open_tasks=int(owned["open"]) if owned else 0,
                overdue_tasks=int(owned["overdue"]) if owned else 0,
            )
        )
    link = None
    if connected and cevanos_app_url:
        link = rules.governance_url(cevanos_app_url)
    return MetricsReport(
        start=start,
        end=end,
        team_id=team_id,
        governance_url=link,
        summary=summary,
        backlog_by_age=ages,
        channels=_counts(counted, "channel", [c.value for c in Channel]),
        topics=_counts(counted, "topic", [*TOPICS, GENERAL]),
        sentiment=_counts(counted, "sentiment", [v.value for v in Sentiment]),
        agents=agent_rows,
    )


# Help-article votes (md R8)

ARTICLE_ROWS = 500  # most-voted articles listed; the totals count every vote


@dataclass(frozen=True)
class ArticleRow:
    article_id: UUID
    title: str
    category: str | None
    status: str
    votes: int
    helpful: int
    not_helpful: int
    helpful_percent: float | None


@dataclass(frozen=True)
class ArticleReport:
    start: datetime
    end: datetime
    votes: int
    helpful: int
    not_helpful: int
    helpful_percent: float | None
    articles_voted: int
    articles: list[ArticleRow]
    truncated: bool


async def article_report(
    sm: Sessionmaker,
    caller: Caller,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> ArticleReport:
    """Votes cast (or last changed) in the period. A visitor has one vote per article, so
    their latest answer is the one that counts. Articles aren't per team, so a
    `team_only` role sees them all, as it does in the help center."""
    require(caller, Action.reports)
    start, end = _period(start, end, datetime.now(UTC))
    async with workspace_session(sm, caller.workspace_id) as s:
        totals = await repo.vote_totals(s, start, end)
        rows = await repo.votes_by_article(s, start, end, ARTICLE_ROWS)
    votes, helpful = int(totals["votes"]), int(totals["helpful"])
    articles = [
        ArticleRow(
            article_id=r["id"],
            title=str(r["title"]),
            category=r["category"],
            status=str(r["status"]),
            votes=int(r["votes"]),
            helpful=int(r["helpful"]),
            not_helpful=int(r["votes"]) - int(r["helpful"]),
            helpful_percent=rules.percent(int(r["helpful"]), int(r["votes"])),
        )
        for r in rows
    ]
    return ArticleReport(
        start=start,
        end=end,
        votes=votes,
        helpful=helpful,
        not_helpful=votes - helpful,
        helpful_percent=rules.percent(helpful, votes),
        articles_voted=int(totals["articles"]),
        articles=articles,
        truncated=int(totals["articles"]) > len(articles),
    )
