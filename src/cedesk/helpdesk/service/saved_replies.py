"""Saved replies (md W19): everyone who can open tickets reads them; only Admins change them."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import saved_replies as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import saved_replies as repo
from cedesk.helpdesk.repo.saved_replies import Row
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

TAKEN = "Another saved reply already uses that shortcut."


def clean_shortcut(raw: str) -> str:
    try:
        return rules.normalise_shortcut(raw)
    except rules.ShortcutError as exc:
        raise Invalid(str(exc)) from exc


def clean_body(raw: str) -> str:
    body = raw.strip()
    if not body:
        raise Invalid("Write the reply text.")
    if len(body) > rules.MAX_BODY:
        raise Invalid(f"A saved reply is at most {rules.MAX_BODY} characters.")
    return body


async def list_replies(sm: Sessionmaker, caller: Caller, *, prefix: str | None) -> Sequence[Row]:
    """`prefix` is what the agent typed after `/`, so the box can narrow the list."""
    require(caller, Action.read_ticket)
    wanted = (prefix or "").strip().removeprefix("/").lower() or None
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.list_replies(s, wanted)


async def create_reply(sm: Sessionmaker, caller: Caller, *, shortcut: str, body: str) -> Row:
    require(caller, Action.settings)
    key, text = clean_shortcut(shortcut), clean_body(body)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            return await create_in(s, caller, key, text)
    except IntegrityError as exc:
        raise Conflict(TAKEN) from exc


async def create_in(s: AsyncSession, caller: Caller, key: str, text: str) -> Row:
    """Adds a checked reply and its audit entry, in the caller's transaction."""
    # A soft cap: two Admins adding at the same moment can pass it by one each.
    if await repo.count(s) >= rules.MAX_PER_WORKSPACE:
        raise Invalid(f"A workspace can have at most {rules.MAX_PER_WORKSPACE} replies.")
    reply_id = await repo.insert_reply(
        s,
        caller.workspace_id,
        shortcut=key,
        body=text,
        source="manual",
        created_by=caller.member_id,
    )
    assert reply_id is not None
    row = await repo.get_reply(s, reply_id)
    assert row is not None
    await audit.record(
        s,
        caller,
        Area.saved_replies,
        "created",
        f"Saved reply /{key}",
        target_id=reply_id,
        detail=audit_rules.snapshot(row, ["body"]),
    )
    return row


async def change_reply(
    sm: Sessionmaker,
    caller: Caller,
    reply_id: UUID,
    *,
    shortcut: str | None,
    body: str | None,
) -> Row:
    require(caller, Action.settings)
    key = None if shortcut is None else clean_shortcut(shortcut)
    text = None if body is None else clean_body(body)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            before = await repo.get_reply(s, reply_id)
            if before is None:
                raise NotFound("Saved reply not found.")
            return await change_in(s, caller, before, key, text)
    except IntegrityError as exc:
        raise Conflict(TAKEN) from exc


async def change_in(
    s: AsyncSession, caller: Caller, before: Row, key: str | None, text: str | None
) -> Row:
    """Saves checked values over `before` (None keeps one) and the audit entry, in the
    caller's transaction."""
    if not await repo.update_reply(s, before["id"], shortcut=key, body=text):
        raise NotFound("Saved reply not found.")
    row = await repo.get_reply(s, before["id"])
    assert row is not None
    await audit.record(
        s,
        caller,
        Area.saved_replies,
        "changed",
        f"Saved reply /{row['shortcut']}",
        target_id=before["id"],
        detail={"changes": audit_rules.changes(before, row, fields=("shortcut", "body"))},
    )
    return row


async def delete_reply(sm: Sessionmaker, caller: Caller, reply_id: UUID) -> None:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await repo.get_reply(s, reply_id)
        if before is None:
            raise NotFound("Saved reply not found.")
        await delete_in(s, caller, before)


async def delete_in(s: AsyncSession, caller: Caller, before: Row) -> None:
    if not await repo.delete_reply(s, before["id"]):
        raise NotFound("Saved reply not found.")
    await audit.record(
        s,
        caller,
        Area.saved_replies,
        "deleted",
        f"Saved reply /{before['shortcut']}",
        target_id=before["id"],
        detail=audit_rules.snapshot(before, ["body"]),
    )
