"""Jira issues from tickets (md X10). Rules in domain/jira.py; installing the app is in
service/apps.py.

Agents who can reply create or link issues on tickets they may change (team scope
applies); each one writes a system line. CeDesk calls Jira outside any database
transaction: it reads the ticket and the credential, calls Jira, then saves the result.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from cedesk.crypto import Sealer
from cedesk.db import workspace_session
from cedesk.egress import account_host
from cedesk.helpdesk.domain import jira as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import apps as app_repo
from cedesk.helpdesk.repo import jira as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import scope
from cedesk.helpdesk.service.app_post import AppCallError, AppPost
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound, Unavailable
from cedesk.helpdesk.service.imports.http import basic
from cedesk.helpdesk.service.members import require

APP = "jira"
NOT_INSTALLED = "Install the Jira app first (Settings > Apps)."

PostFactory = Callable[[str, Mapping[str, str], str], AppPost]
"""(host, headers, label) -> a client. The app state holds the real one; tests a fake."""


def make_post(host: str, headers: Mapping[str, str], label: str) -> AppPost:
    return AppPost(host, headers=headers, label=label)


def ticket_link(app_url: str, ticket_id: UUID) -> str:
    """Where an agent opens the ticket in the CeDesk app."""
    return f"{app_url.rstrip('/')}/tickets/{ticket_id}"


@dataclass(frozen=True)
class JiraIssue:
    key: str
    url: str
    made_here: bool  # created from CeDesk (else an existing issue that was linked)
    created_at: datetime
    created_by_name: str | None


def client(post: PostFactory, settings: Mapping[str, Any], token: str) -> AppPost:
    """For the site in the app's settings; `token` is the Atlassian API token."""
    host = account_host("jira", str(settings["site"]))
    return post(host, basic(str(settings["email"]), token), "Jira")


async def check_install(post: PostFactory, settings: Mapping[str, Any], token: str) -> None:
    """The token works and can see the project. Raises `AppCallError`, saying which of
    the site or the project Jira doesn't have."""
    jira = client(post, settings, token)
    try:
        await jira.get(f"{rules.API}/myself")
    except AppCallError as exc:
        if exc.status == 404:
            raise AppCallError(rules.NO_SITE.format(host=jira.host), permanent=True) from None
        raise
    try:
        await jira.get(f"{rules.API}/project/{settings['project']}")
    except AppCallError as exc:
        if exc.status == 404:
            raise AppCallError(
                rules.NO_PROJECT.format(project=settings["project"]), permanent=True
            ) from None
        raise


def _issue(row: Any) -> JiraIssue:
    return JiraIssue(
        key=str(row["issue_key"]),
        url=str(row["url"]),
        made_here=bool(row["made_here"]),
        created_at=row["created_at"],
        created_by_name=row["created_by_name"],
    )


async def _prepare(
    sm: Sessionmaker, sealer: Sealer, caller: Caller, ticket_id: UUID, bound: Callable[[UUID], str]
) -> tuple[Any, dict[str, Any], str]:
    """The ticket, the app's settings and its token, after the permission checks."""
    require(caller, Action.reply)
    async with workspace_session(sm, caller.workspace_id) as s:
        ticket = await ticket_repo.ticket_detail(s, ticket_id)
        if ticket is None:
            raise NotFound("Ticket not found.")
        await scope.check(s, caller, ticket_id, edit=True)
        app = await app_repo.get(s, APP)
        if app is None:
            raise Conflict(NOT_INSTALLED)
        if await repo.count(s, ticket_id) >= rules.MAX_LINKS:
            raise Conflict(f"A ticket can have at most {rules.MAX_LINKS} Jira issues.")
    token = sealer.open(bytes(app["secret_sealed"]), bound_to=bound(caller.workspace_id))
    return ticket, dict(app["settings"]), token


def _failed(exc: AppCallError) -> Exception:
    return Invalid(str(exc)) if exc.permanent else Unavailable(str(exc))


async def _save(
    sm: Sessionmaker, caller: Caller, ticket_id: UUID, key: str, url: str, *, made_here: bool,
    line: str,
) -> JiraIssue:  # fmt: skip
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.add(
            s, caller.workspace_id, ticket_id=ticket_id, key=key, url=url,
            made_here=made_here, by=caller.member_id,
        ):  # fmt: skip
            raise Conflict(f"{key} is already on this ticket.")
        await ticket_repo.insert_message(
            s, workspace_id=caller.workspace_id, ticket_id=ticket_id, kind="system",
            body=line, author_member_id=None, author_name=None,
        )  # fmt: skip
        rows = await repo.for_ticket(s, ticket_id)
    return next(_issue(r) for r in rows if r["issue_key"] == key)


async def _who(sm: Sessionmaker, caller: Caller) -> str:
    async with workspace_session(sm, caller.workspace_id) as s:
        me = await ticket_repo.member_brief(s, caller.member_id)
    return caller.acting(str(me["name"]) if me else "someone")


async def create_issue(
    sm: Sessionmaker,
    sealer: Sealer,
    post: PostFactory,
    caller: Caller,
    ticket_id: UUID,
    *,
    summary: str | None,
    description: str | None,
    app_url: str,
    bound: Callable[[UUID], str],
) -> JiraIssue:
    ticket, settings, token = await _prepare(sm, sealer, caller, ticket_id, bound)
    try:
        text = rules.summary(str(ticket["subject"]), summary)
    except rules.JiraInputError as exc:
        raise Invalid(str(exc)) from exc
    body = rules.new_issue(
        str(settings["project"]), text, description, label(int(ticket["number"])),
        ticket_link(app_url, ticket_id),
    )  # fmt: skip
    jira = client(post, settings, token)
    try:
        made = await jira.post(f"{rules.API}/issue", body)
    except AppCallError as exc:
        raise _failed(exc) from None
    key = str(made.get("key") or "") if isinstance(made, dict) else ""
    try:
        key = rules.issue_key(key)
    except rules.JiraInputError:
        raise Unavailable("Jira didn't say which issue it made.") from None
    who = await _who(sm, caller)
    return await _save(
        sm, caller, ticket_id, key, rules.issue_url(jira.host, key), made_here=True,
        line=f"Jira issue {key} created by {who}",
    )  # fmt: skip


async def link_issue(
    sm: Sessionmaker,
    sealer: Sealer,
    post: PostFactory,
    caller: Caller,
    ticket_id: UUID,
    *,
    key: str,
    bound: Callable[[UUID], str],
) -> JiraIssue:
    try:
        key = rules.issue_key(key)
    except rules.JiraInputError as exc:
        raise Invalid(str(exc)) from exc
    _, settings, token = await _prepare(sm, sealer, caller, ticket_id, bound)
    jira = client(post, settings, token)
    try:
        await jira.get(f"{rules.API}/issue/{key}?fields=summary")
    except AppCallError as exc:
        if exc.status == 404:  # was "Jira has no such thing." (live, Phase 3 retest)
            raise Invalid(rules.NO_ISSUE.format(key=key)) from None
        raise _failed(exc) from None
    who = await _who(sm, caller)
    return await _save(
        sm, caller, ticket_id, key, rules.issue_url(jira.host, key), made_here=False,
        line=f"Jira issue {key} linked by {who}",
    )  # fmt: skip


async def issues(sm: Sessionmaker, caller: Caller, ticket_id: UUID) -> list[JiraIssue]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        if await ticket_repo.ticket_detail(s, ticket_id) is None:
            raise NotFound("Ticket not found.")
        await scope.check(s, caller, ticket_id, edit=False)
        return [_issue(r) for r in await repo.for_ticket(s, ticket_id)]


async def unlink_issue(sm: Sessionmaker, caller: Caller, ticket_id: UUID, key: str) -> None:
    """Takes it off the ticket; the issue stays in Jira."""
    require(caller, Action.reply)
    async with workspace_session(sm, caller.workspace_id) as s:
        if await ticket_repo.ticket_detail(s, ticket_id) is None:
            raise NotFound("Ticket not found.")
        await scope.check(s, caller, ticket_id, edit=True)
        if not await repo.remove(s, ticket_id, key.strip().upper()):
            raise NotFound("That issue isn't on this ticket.")
        me = await ticket_repo.member_brief(s, caller.member_id)
        who = caller.acting(str(me["name"]) if me else "someone")
        await ticket_repo.insert_message(
            s, workspace_id=caller.workspace_id, ticket_id=ticket_id, kind="system",
            body=f"Jira issue {key.strip().upper()} unlinked by {who}",
            author_member_id=None, author_name=None,
        )  # fmt: skip
