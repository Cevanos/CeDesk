"""SQL for the reports (md R1-R4, R8). RLS applies.

Team report: one pass over the tickets grouped three ways (all, per team, per assignee)
and one over the ratings. More metrics: one pass grouped by channel, topic and sentiment
(the backlog ages ride on the total row), one over the period's created and resolved
tickets for one-touch and reopen, then time logs and open tasks. Articles: the votes."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.reports import AGE_BUCKETS
from cedesk.helpdesk.repo.auth import TAKES_TICKETS

_TEAM = "(CAST(:team AS uuid) IS NULL OR t.team_id = CAST(:team AS uuid))"
_LIVE = "t.merged_into_id IS NULL"
_IMPORTED = "coalesce(t.source = ANY(CAST(:imported AS text[])), false)"
_CREATED = "(t.created_at >= :start AND t.created_at < :end)"
_RESOLVED = "(t.resolved_at >= :start AND t.resolved_at < :end)"

_TICKETS = f"""
SELECT
    GROUPING(t.team_id) AS by_all_teams, GROUPING(t.assignee_id) AS by_all_agents,
    t.team_id, t.assignee_id,
    count(*) FILTER (WHERE t.status <> 'resolved') AS open,
    count(*) FILTER (WHERE t.status <> 'resolved' AND t.sla_due_at < :now
                     AND NOT {_IMPORTED}) AS breaching,
    count(*) FILTER (WHERE {_CREATED}) AS created,
    count(*) FILTER (WHERE {_RESOLVED}) AS resolved,
    avg(extract(epoch FROM t.first_reply_at - t.created_at))
        FILTER (WHERE {_CREATED} AND t.first_reply_at IS NOT NULL AND NOT {_IMPORTED})
        AS first_response_seconds,
    avg(extract(epoch FROM t.resolved_at - t.created_at))
        FILTER (WHERE {_RESOLVED} AND NOT {_IMPORTED}) AS resolution_seconds
FROM hd_ticket t
WHERE {_LIVE} AND {_TEAM} AND (t.status <> 'resolved' OR {_CREATED} OR {_RESOLVED})
GROUP BY GROUPING SETS ((), (t.team_id), (t.assignee_id))
"""  # noqa: S608 - fixed strings only

_RATINGS = f"""
SELECT
    GROUPING(t.team_id) AS by_all_teams, GROUPING(t.assignee_id) AS by_all_agents,
    GROUPING(r.score) AS by_all_scores, t.team_id, t.assignee_id, r.score,
    count(*) AS ratings, avg(r.score) AS average, count(*) FILTER (WHERE r.score >= :happy)
        AS happy
FROM hd_csat r JOIN hd_ticket t ON t.id = r.ticket_id
WHERE r.rated_at >= :start AND r.rated_at < :end AND {_LIVE} AND {_TEAM}
GROUP BY GROUPING SETS ((), (t.team_id), (t.assignee_id), (r.score))
"""  # noqa: S608 - fixed strings only

_SLA_FACTS = f"""
SELECT t.id, t.team_id, t.priority, t.channel, t.created_at, t.first_reply_at, t.resolved_at,
       c.vip
FROM hd_ticket t JOIN hd_contact c ON c.id = t.contact_id
WHERE {_CREATED} AND {_LIVE} AND {_TEAM} AND NOT {_IMPORTED}
"""  # noqa: S608 - fixed strings only


def _age_column(index: int) -> str:
    """Open tickets in one backlog bucket: created at least the previous bound ago and
    less than this bucket's bound ago. Bounds are whole hours from the code."""
    bucket = AGE_BUCKETS[index]
    since = AGE_BUCKETS[index - 1].under_hours if index else None
    hours_ago = "CAST(:now AS timestamptz) - make_interval(hours => {})"
    parts = ["t.status <> 'resolved'"]
    if since is not None:
        parts.append("t.created_at <= " + hours_ago.format(int(since)))
    if bucket.under_hours is not None:
        parts.append("t.created_at > " + hours_ago.format(int(bucket.under_hours)))
    return f"count(*) FILTER (WHERE {' AND '.join(parts)}) AS age_{bucket.key}"


_BREAKDOWN = f"""
SELECT
    GROUPING(t.channel) AS by_all_channels, GROUPING(t.topic) AS by_all_topics,
    GROUPING(t.sentiment) AS by_all_sentiments, t.channel, t.topic, t.sentiment,
    count(*) FILTER (WHERE {_CREATED}) AS created,
    count(*) FILTER (WHERE t.status <> 'resolved') AS open,
    {", ".join(_age_column(i) for i in range(len(AGE_BUCKETS)))}
FROM hd_ticket t
WHERE {_LIVE} AND {_TEAM} AND (t.status <> 'resolved' OR {_CREATED})
GROUP BY GROUPING SETS ((), (t.channel), (t.topic), (t.sentiment))
"""  # noqa: S608 - fixed strings only

# Replies from a team member: the chat history copied in from Cevanos has no author.
# Counting stops at 2, which is all one-touch needs to know.
_REPLIES_UP_TO_TWO = """(SELECT count(*) FROM (
    SELECT 1 FROM hd_message m
    WHERE m.workspace_id = t.workspace_id AND m.ticket_id = t.id AND m.kind = 'agent'
      AND m.author_member_id IS NOT NULL
    LIMIT 2) up_to_two)"""

_FLOW = f"""
SELECT
    count(*) FILTER (WHERE {_RESOLVED}) AS resolved,
    count(*) FILTER (WHERE CASE WHEN {_RESOLVED} AND t.reopen_count = 0
                           THEN {_REPLIES_UP_TO_TWO} = 1 ELSE false END) AS one_touch,
    count(*) FILTER (WHERE {_CREATED} AND (t.status = 'resolved' OR t.reopen_count > 0))
        AS reopen_measured,
    count(*) FILTER (WHERE {_CREATED} AND t.reopen_count > 0) AS reopened
FROM hd_ticket t
WHERE {_LIVE} AND {_TEAM} AND NOT {_IMPORTED} AND ({_CREATED} OR {_RESOLVED})
"""  # noqa: S608 - fixed strings only

_TIME_LOGGED = f"""
SELECT GROUPING(l.member_id) AS by_all_members, l.member_id,
    coalesce(sum(l.minutes), 0) AS minutes,
    coalesce(sum(l.minutes) FILTER (WHERE l.billable), 0) AS billable
FROM hd_time_log l JOIN hd_ticket t ON t.id = l.ticket_id
WHERE l.logged_at >= :start AND l.logged_at < :end AND {_TEAM}
GROUP BY GROUPING SETS ((), (l.member_id))
"""  # noqa: S608 - fixed strings only

_OPEN_TASKS = f"""
SELECT k.owner_id, count(*) AS open, count(*) FILTER (WHERE k.due_at < :now) AS overdue
FROM hd_task k JOIN hd_ticket t ON t.id = k.ticket_id
WHERE k.done_at IS NULL AND {_TEAM}
GROUP BY k.owner_id
"""  # noqa: S608 - fixed strings only

_VOTED = "v.voted_at >= :start AND v.voted_at < :end"

_VOTE_TOTALS = f"""
SELECT count(*) AS votes, count(*) FILTER (WHERE v.helpful) AS helpful,
    count(DISTINCT v.article_id) AS articles
FROM hd_article_vote v WHERE {_VOTED}
"""  # noqa: S608 - fixed strings only

_VOTES_BY_ARTICLE = f"""
SELECT a.id, a.title, a.status, c.name AS category,
    count(*) AS votes, count(*) FILTER (WHERE v.helpful) AS helpful
FROM hd_article_vote v
JOIN hd_article a ON a.id = v.article_id
LEFT JOIN hd_article_category c ON c.id = a.category_id
WHERE {_VOTED}
GROUP BY a.id, a.title, a.status, c.name
ORDER BY count(*) DESC, lower(a.title), a.id
LIMIT :limit
"""  # noqa: S608 - fixed strings only


def _params(
    start: datetime, end: datetime, team_id: UUID | None, imported: Sequence[str]
) -> dict[str, Any]:
    return {"start": start, "end": end, "team": team_id, "imported": list(imported)}


async def tickets(
    session: AsyncSession, start: datetime, end: datetime, now: datetime,
    team_id: UUID | None, imported: Sequence[str],
) -> Sequence[RowMapping]:  # fmt: skip
    result = await session.execute(
        text(_TICKETS), _params(start, end, team_id, imported) | {"now": now}
    )
    return result.mappings().all()


async def ratings(
    session: AsyncSession, start: datetime, end: datetime, team_id: UUID | None, happy: int
) -> Sequence[RowMapping]:
    result = await session.execute(
        text(_RATINGS), _params(start, end, team_id, ()) | {"happy": happy}
    )
    return result.mappings().all()


async def sla_facts(
    session: AsyncSession, start: datetime, end: datetime, team_id: UUID | None,
    imported: Sequence[str],
) -> Sequence[RowMapping]:  # fmt: skip
    """Tickets created in the period (not imported), for the first-reply SLA met %, in
    total and per team (`team_id`)."""
    result = await session.execute(text(_SLA_FACTS), _params(start, end, team_id, imported))
    return result.mappings().all()


async def breakdown(
    session: AsyncSession, start: datetime, end: datetime, now: datetime, team_id: UUID | None
) -> Sequence[RowMapping]:
    """Created in the period and open now: in total (with the backlog ages), per channel,
    per topic, per sentiment."""
    result = await session.execute(
        text(_BREAKDOWN), _params(start, end, team_id, ()) | {"now": now}
    )
    return result.mappings().all()


async def flow(
    session: AsyncSession, start: datetime, end: datetime, team_id: UUID | None,
    imported: Sequence[str],
) -> RowMapping:  # fmt: skip
    """One-touch and reopen counts (not imported tickets)."""
    result = await session.execute(text(_FLOW), _params(start, end, team_id, imported))
    return result.mappings().one()


async def time_logged(
    session: AsyncSession, start: datetime, end: datetime, team_id: UUID | None
) -> Sequence[RowMapping]:
    """Minutes logged in the period: the total (`by_all_members` = 1) and per member."""
    result = await session.execute(text(_TIME_LOGGED), _params(start, end, team_id, ()))
    return result.mappings().all()


async def open_tasks(
    session: AsyncSession, now: datetime, team_id: UUID | None
) -> Sequence[RowMapping]:
    """Tasks not done yet, per owner, and how many of them are past due."""
    result = await session.execute(text(_OPEN_TASKS), {"now": now, "team": team_id})
    return result.mappings().all()


async def vote_totals(session: AsyncSession, start: datetime, end: datetime) -> RowMapping:
    result = await session.execute(text(_VOTE_TOTALS), {"start": start, "end": end})
    return result.mappings().one()


async def votes_by_article(
    session: AsyncSession, start: datetime, end: datetime, limit: int
) -> Sequence[RowMapping]:
    """Articles with a vote in the period, most votes first."""
    result = await session.execute(
        text(_VOTES_BY_ARTICLE), {"start": start, "end": end, "limit": limit}
    )
    return result.mappings().all()


async def teams(session: AsyncSession) -> Sequence[RowMapping]:
    result = await session.execute(text("SELECT id, name FROM hd_team ORDER BY lower(name)"))
    return result.mappings().all()


async def agents(session: AsyncSession, team_id: UUID | None) -> Sequence[RowMapping]:
    """Active members who can take tickets (not light agents); with a team, its members."""
    result = await session.execute(
        text(
            "SELECT m.id, m.name, m.role, coalesce(st.status, 'offline') AS status "  # noqa: S608
            "FROM member m LEFT JOIN hd_agent_state st ON st.member_id = m.id "
            "WHERE m.is_active AND " + TAKES_TICKETS + " "
            "AND (CAST(:team AS uuid) IS NULL OR EXISTS (SELECT 1 FROM hd_team_member tm "
            "     WHERE tm.member_id = m.id AND tm.team_id = CAST(:team AS uuid))) "
            "ORDER BY lower(m.name), m.id"
        ),
        {"team": team_id},
    )
    return result.mappings().all()


async def team_exists(session: AsyncSession, team_id: UUID) -> bool:
    result = await session.execute(text("SELECT 1 FROM hd_team WHERE id = :id"), {"id": team_id})
    return result.first() is not None
