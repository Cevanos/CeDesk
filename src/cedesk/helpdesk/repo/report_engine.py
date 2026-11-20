"""SQL for the report engine (feature-gap plan 3.1, 3.2). RLS applies.

Nothing typed by a person is ever put into the SQL text. The metric and the grouping are
keys the domain already checked against fixed lists (`domain/report_defs.py`); each key
maps to a fixed fragment below, and every value (a team, a tag, a time) is a bound
parameter. `sql_for` builds the text from those fragments only.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import cache
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.report_defs import FIELD_GROUPS, MAX_GROUPS, TIME_GROUPS
from cedesk.helpdesk.domain.reports import IMPORT_SOURCES

Row = RowMapping

_IMPORTED = "coalesce(t.source = ANY(CAST(:imported_sources AS text[])), false)"
_TICKETS = "hd_ticket t"
_RATINGS = "hd_csat r JOIN hd_ticket t ON t.id = r.ticket_id"
_TIME_LOGS = "hd_time_log l JOIN hd_ticket t ON t.id = l.ticket_id"

# metric -> (FROM, time column, value, extra WHERE, who "assignee" means)
_METRIC_SQL: dict[str, tuple[str, str | None, str, str, str]] = {
    "created": (_TICKETS, "t.created_at", "count(*)", "true", "t.assignee_id"),
    "resolved": (_TICKETS, "t.resolved_at", "count(*)", "true", "t.assignee_id"),
    "avg_first_response": (
        _TICKETS, "t.created_at",
        "avg(extract(epoch FROM t.first_reply_at - t.created_at)) / 60",
        f"t.first_reply_at IS NOT NULL AND NOT {_IMPORTED}", "t.assignee_id",
    ),
    "avg_resolution": (
        _TICKETS, "t.resolved_at",
        "avg(extract(epoch FROM t.resolved_at - t.created_at)) / 60",
        f"NOT {_IMPORTED}", "t.assignee_id",
    ),
    "csat_average": (_RATINGS, "r.rated_at", "avg(r.score)", "true", "t.assignee_id"),
    "csat_count": (_RATINGS, "r.rated_at", "count(*)", "true", "t.assignee_id"),
    "reopen_rate": (
        _TICKETS, "t.created_at",
        "100.0 * count(*) FILTER (WHERE t.reopen_count > 0) / count(*)",
        f"(t.status = 'resolved' OR t.reopen_count > 0) AND NOT {_IMPORTED}", "t.assignee_id",
    ),
    # Time logged: "assignee" is who logged it.
    "time_logged": (
        _TIME_LOGS, "l.logged_at", "coalesce(sum(l.minutes), 0)", "true", "l.member_id"
    ),
    "open_now": (_TICKETS, None, "count(*)", "t.status <> 'resolved'", "t.assignee_id"),
}  # fmt: skip

_FIELD_GROUP_SQL = {
    "team": "CAST(t.team_id AS text)",
    "priority": "t.priority",
    "type": "CAST(t.type_id AS text)",
    "channel": "t.channel",
    "topic": "t.topic",
    "sentiment": "t.sentiment",
    "tag": "g.tag",
}

# Every filter is "parameter is NULL or matches", so one static text serves any mix.
_FILTERS = f"""
    (CAST(:scope_team AS uuid) IS NULL OR t.team_id = CAST(:scope_team AS uuid))
    AND (CAST(:f_team AS uuid) IS NULL OR t.team_id = CAST(:f_team AS uuid))
    AND (CAST(:f_assignee AS uuid) IS NULL OR {{assignee}} = CAST(:f_assignee AS uuid))
    AND (CAST(:f_priority AS text) IS NULL OR t.priority = CAST(:f_priority AS text))
    AND (CAST(:f_type AS uuid) IS NULL OR t.type_id = CAST(:f_type AS uuid))
    AND (CAST(:f_channel AS text) IS NULL OR t.channel = CAST(:f_channel AS text))
    AND (CAST(:f_tag AS text) IS NULL OR CAST(:f_tag AS text) = ANY(t.tags))
    AND (CAST(:f_topic AS text) IS NULL OR t.topic = CAST(:f_topic AS text))
    AND (CAST(:f_sentiment AS text) IS NULL OR t.sentiment = CAST(:f_sentiment AS text))
    AND (CAST(:f_imported AS text) IS NULL OR CAST(:f_imported AS text) = 'any'
         OR (CAST(:f_imported AS text) = 'only' AND {_IMPORTED})
         OR (CAST(:f_imported AS text) = 'exclude' AND NOT {_IMPORTED}))
"""


@cache
def sql_for(metric: str, group_by: str) -> str:
    """The query for one metric and grouping: rows of (`gkey`, `value`, `n`)."""
    if metric not in _METRIC_SQL or group_by not in ("none", *TIME_GROUPS, *FIELD_GROUPS):
        raise ValueError("not a checked metric or grouping")
    source, time_col, value, extra, assignee = _METRIC_SQL[metric]
    join = ""
    if group_by == "none":
        gkey = "CAST(NULL AS text)"
    elif group_by in TIME_GROUPS:
        if time_col is None:
            raise ValueError("an as-of-now metric has no time to group by")
        gkey = f"CAST(CAST(date_trunc('{group_by}', {time_col} AT TIME ZONE :tz) AS date) AS text)"
    elif group_by == "assignee":
        gkey = f"CAST({assignee} AS text)"
    else:
        gkey = _FIELD_GROUP_SQL[group_by]
        if group_by == "tag":  # a ticket with no tags still gives one row (tag NULL)
            join = " LEFT JOIN LATERAL unnest(t.tags) AS g(tag) ON true"
    when = f"{time_col} >= :start AND {time_col} < :end AND " if time_col else ""
    where = f"{when}t.merged_into_id IS NULL AND {extra} AND " + _FILTERS.format(assignee=assignee)
    tail = ""
    if group_by != "none":
        tail = (
            " GROUP BY 1 ORDER BY 1"
            if group_by in TIME_GROUPS
            else (f" GROUP BY 1 ORDER BY 3 DESC, 1 LIMIT {MAX_GROUPS + 1}")
        )
    return (
        f"SELECT {gkey} AS gkey, {value} AS value, count(*) AS n "  # noqa: S608
        f"FROM {source}{join} WHERE {where}{tail}"
    )


def _params(
    filters: Mapping[str, Any],
    *,
    scope_team: UUID | None,
    start: datetime | None,
    end: datetime | None,
    timezone: str,
) -> dict[str, Any]:
    return {
        "scope_team": scope_team,
        "f_team": filters.get("team"),
        "f_assignee": filters.get("assignee"),
        "f_priority": filters.get("priority"),
        "f_type": filters.get("type"),
        "f_channel": filters.get("channel"),
        "f_tag": filters.get("tag"),
        "f_topic": filters.get("topic"),
        "f_sentiment": filters.get("sentiment"),
        "f_imported": filters.get("imported"),
        "imported_sources": list(IMPORT_SOURCES),
        "start": start,
        "end": end,
        "tz": timezone,
    }


async def run(
    session: AsyncSession,
    metric: str,
    group_by: str,
    filters: Mapping[str, Any],
    *,
    scope_team: UUID | None,
    start: datetime | None,
    end: datetime | None,
    timezone: str,
) -> Sequence[Row]:
    """Rows of `gkey` (text: a day, an id, a name; NULL: none), `value` and `n` (how many
    tickets, ratings or log entries it rests on). A time grouping is oldest first; a field
    grouping is biggest first and holds at most `MAX_GROUPS + 1` rows (the caller trims)."""
    result = await session.execute(
        text(sql_for(metric, group_by)),
        _params(filters, scope_team=scope_team, start=start, end=end, timezone=timezone),
    )
    return result.mappings().all()


_NAME_TABLES = {"team": "hd_team", "type": "hd_ticket_type", "assignee": "member"}


async def names(session: AsyncSession, group_by: str, ids: Sequence[str]) -> dict[str, str]:
    """Names for the ids of a team, ticket type or member grouping."""
    table = _NAME_TABLES.get(group_by)
    if table is None or not ids:
        return {}
    result = await session.execute(
        text(f"SELECT CAST(id AS text) AS id, name FROM {table} "  # noqa: S608
             "WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": list(ids)},
    )  # fmt: skip
    return {str(r["id"]): str(r["name"]) for r in result.mappings().all()}


async def set_timeout(session: AsyncSession, milliseconds: int) -> None:
    """Cancels any statement of this transaction that runs longer than `milliseconds`."""
    await session.execute(
        text("SELECT set_config('statement_timeout', :ms, true)"), {"ms": str(int(milliseconds))}
    )


def metric_keys() -> tuple[str, ...]:
    """The metrics this module has SQL for (the rest are read elsewhere)."""
    return tuple(_METRIC_SQL)
