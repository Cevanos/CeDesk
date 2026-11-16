"""The archive tier (md O3). Rules in domain/archive.py.

- `run_archive` (the worker, once a day): in each workspace, resolved tickets older than
  its archive age move to `hd_ticket_archive` in batches, each batch its own transaction
  with SKIP LOCKED, so several workers share the work and never move a ticket twice.
  Audited per run (area `archive`, by the server).
- `search` and `get`: the right to read tickets and the role's team scope, as for live
  tickets. Read only: nothing here changes an archived ticket.
- The setting: `settings` right, audited.
- Retention deletes from the archive too (service/limits.py), and privacy export and
  erase cover it (service/privacy.py).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import archive as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import archive as repo
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.service import audit, scope
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, NotFound
from cedesk.helpdesk.service.members import require

logger = logging.getLogger("cedesk.archive")
NOT_FOUND = "Archived ticket not found."


async def run_archive(sm: Sessionmaker, *, now: datetime | None = None) -> int:
    """Returns how many tickets moved, in every workspace."""
    now = now or datetime.now(UTC)
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    total = 0
    for workspace_id in workspace_ids:
        total += await archive_workspace(sm, workspace_id, now)
    return total


async def archive_workspace(sm: Sessionmaker, workspace_id: UUID, now: datetime) -> int:
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.setting(s)
    days = rules.archive_days(row["archive_after_days"] if row else None)
    cutoff = now - timedelta(days=days)
    moved = 0
    while True:
        async with workspace_session(sm, workspace_id) as s:
            batch = await repo.move(s, cutoff, rules.BATCH)
        moved += batch
        if batch < rules.BATCH:  # done, or the rest is locked by another worker
            break
    if moved:
        async with workspace_session(sm, workspace_id) as s:
            await audit.record_server(
                s, workspace_id, Area.archive, "archived",
                f"{moved} resolved tickets older than {days} days",
                detail={"tickets": moved, "days": days},
            )  # fmt: skip
        logger.info("archive", extra={"workspace_id": workspace_id, "event": "archive"})
    return moved


@dataclass(frozen=True)
class ArchivedSummary:
    id: UUID
    number: int
    label: str
    subject: str
    status: str
    priority: str
    channel: str
    contact_id: UUID | None
    contact_email: str | None
    contact_name: str | None
    team_name: str | None
    assignee_name: str | None
    created_at: datetime
    resolved_at: datetime | None
    archived_at: datetime


@dataclass(frozen=True)
class ArchivedMessage:
    kind: str
    author_name: str | None
    body: str
    created_at: str
    files: list[dict[str, Any]]
    email: dict[str, Any] | None  # an email's headers (from, to, subject, message ids)


@dataclass(frozen=True)
class ArchivedTicket(ArchivedSummary):
    tags: list[str]
    fields: dict[str, Any]
    messages: list[ArchivedMessage]
    files: list[dict[str, Any]]  # every file on the ticket (name, size, type)
    rating: dict[str, Any] | None
    time_log: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    approvals: list[dict[str, Any]]
    links: list[dict[str, Any]]
    side_conversations: list[dict[str, Any]]
    jira_issues: list[dict[str, Any]]
    emails: list[dict[str, Any]]


# What the archive read shows of the history kept with a ticket (never ids of rows that
# are gone, storage keys or delivery internals).
SHOWN: dict[str, tuple[str, ...]] = {
    "time_log": ("minutes", "billable", "note", "member_name", "logged_at"),
    "tasks": ("text", "owner_name", "due_at", "done_at", "created_at"),
    "approvals": ("what", "amount", "currency", "state", "decision_note", "requester_name",
                  "approver_name", "decided_by_name", "decided_at", "created_at"),
    "links": ("ticket_id", "number", "label", "kind", "created_at"),
    "side_conversations": ("subject", "via", "to_addresses", "from_address", "created_at",
                           "messages"),
    "jira_issues": ("issue_key", "url", "made_here", "created_at"),
    "emails": ("from_address", "to_addresses", "cc_addresses", "subject", "body_text",
               "status", "sent_at", "created_at"),
}  # fmt: skip
SIDE_MESSAGE = ("direction", "author_name", "from_address", "body", "created_at")


def _shown(kind: str, rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for row in rows or []:
        if kind == "links" and row.get("number") is not None:
            row = {**row, "label": label(int(row["number"]))}
        if kind == "side_conversations":
            row = {**row, "messages": [
                {k: m.get(k) for k in SIDE_MESSAGE} for m in row.get("messages") or []
            ]}  # fmt: skip
        out.append({k: row.get(k) for k in SHOWN[kind]})
    return out


def _summary(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "number": int(r["number"]),
        "label": label(int(r["number"])),
        "subject": str(r["subject"]),
        "status": str(r["status"]),
        "priority": str(r["priority"]),
        "channel": str(r["channel"]),
        "contact_id": r["contact_id"],
        "contact_email": r["contact_email"],
        "contact_name": r["contact_name"],
        "team_name": r["team_name"],
        "assignee_name": r["assignee_name"],
        "created_at": r["created_at"],
        "resolved_at": r["resolved_at"],
        "archived_at": r["archived_at"],
    }


async def search(
    sm: Sessionmaker,
    caller: Caller,
    *,
    number: str | None = None,
    contact_id: UUID | None = None,
    email: str | None = None,
) -> list[ArchivedSummary]:
    """By ticket number (1042, T-1042) or customer (contact id or email); at least one."""
    require(caller, Action.read_ticket)
    wanted = rules.ticket_number(number) if number else None
    if number and wanted is None:
        raise Invalid("A ticket number looks like 1042 or T-1042.")
    email = (email or "").strip() or None
    if wanted is None and contact_id is None and email is None:
        raise Invalid("Search the archive by ticket number or by customer.")
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = await repo.search(
            s, number=wanted, contact_id=contact_id, email=email,
            team_id=scope.visible_team(caller), limit=rules.SEARCH_LIMIT,
        )  # fmt: skip
    return [ArchivedSummary(**_summary(r)) for r in rows]


async def get(sm: Sessionmaker, caller: Caller, ticket_id: UUID) -> ArchivedTicket:
    """Read only, with its messages (notes too: agents only), files' names, rating and the
    rest of its history (time logged, tasks, approvals, links, side conversations, Jira
    issues, emails sent). The team's and assignee's names are as they were when it was
    archived. A role that can't see the ticket's team gets a 404, as for a live ticket."""
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        r = await repo.get(s, ticket_id)
    if r is None or not caller.access.sees(r["team_id"]):
        raise NotFound(NOT_FOUND)
    files: dict[str | None, list[dict[str, Any]]] = {}
    every: list[dict[str, Any]] = []
    for f in r["files"]:
        shown = {"name": f.get("name"), "size": f.get("size"), "type": f.get("mime")}
        files.setdefault(f.get("message_id"), []).append(shown)  # never the storage key
        every.append(shown)
    ticket = r["ticket"]
    history: dict[str, Any] = {k: _shown(k, (r["related"] or {}).get(k)) for k in SHOWN}
    return ArchivedTicket(
        **_summary(r),
        tags=list(ticket.get("tags") or []),
        fields=dict(ticket.get("fields") or {}),
        messages=[
            ArchivedMessage(
                kind=str(m.get("kind")),
                author_name=m.get("author_name"),
                body=str(m.get("body") or ""),
                created_at=str(m.get("created_at")),
                files=files.get(m.get("id"), []),
                email=m.get("email"),
            )
            for m in r["messages"]
        ],
        files=every,
        rating=_rating(r["rating"]),
        **history,
    )


def _rating(rating: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rating:
        return None
    return {k: rating.get(k) for k in ("score", "comment", "source", "rated_at")}


@dataclass(frozen=True)
class ArchiveSetting:
    after_days: int | None  # None: the default
    effective_days: int
    updated_at: datetime | None
    updated_by_name: str | None


def _setting(row: Any) -> ArchiveSetting:
    days = row["archive_after_days"] if row is not None else None
    return ArchiveSetting(
        after_days=days,
        effective_days=rules.archive_days(days),
        updated_at=row["updated_at"] if row is not None else None,
        updated_by_name=row["updated_by_name"] if row is not None else None,
    )


async def get_setting(sm: Sessionmaker, caller: Caller) -> ArchiveSetting:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        return _setting(await repo.setting(s))


async def set_setting(sm: Sessionmaker, caller: Caller, days: int | None) -> ArchiveSetting:
    require(caller, Action.settings)
    try:
        days = rules.check_days(days)
    except rules.BadArchive as exc:
        raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await repo.setting(s)
        old = before["archive_after_days"] if before is not None else None
        await repo.save_setting(s, caller.workspace_id, days, caller.member_id)
        if old != days:
            await audit.record(
                s, caller, Area.archive, "changed", "Archive resolved tickets after",
                detail={"changes": {"archive_after_days": [old, days]}},
            )  # fmt: skip
        return _setting(await repo.setting(s))
