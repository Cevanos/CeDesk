"""Files on a ticket: upload, list, and a short-lived link to download one.

Anyone who can write on a ticket can attach a file (light agents attach to their notes);
anyone who can read the ticket can download it. Sending a file with a reply or note, which
fills `message_id`, comes with the next change to messages.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.domain.attachments import ScanStatus, clean_mime, clean_name, object_key
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import attachments as repo
from cedesk.helpdesk.repo.attachments import Row
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound, TooLarge
from cedesk.helpdesk.service.members import require
from cedesk.storage import ObjectStore


@dataclass(frozen=True)
class AttachmentPage:
    attachments: Sequence[Row]  # newest first
    next_cursor: str | None


@dataclass(frozen=True)
class Download:
    attachment: Row
    url: str
    expires_at: datetime


def too_large(max_bytes: int) -> TooLarge:
    mib = 1024 * 1024
    limit = f"{max_bytes // mib} MB" if max_bytes >= mib else f"{max_bytes} bytes"
    return TooLarge(f"A file can be at most {limit}.")


async def upload(
    sm: Sessionmaker,
    store: ObjectStore,
    caller: Caller,
    ticket_id: UUID,
    *,
    name: str,
    mime: str | None,
    data: bytes,
    max_bytes: int,
) -> Row:
    require(caller, Action.private_note)
    if not data:
        raise Invalid("The file is empty.")
    if len(data) > max_bytes:
        raise too_large(max_bytes)
    attachment_id = uuid4()
    key = object_key(caller.workspace_id, ticket_id, attachment_id)
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.ticket_exists(s, ticket_id):
            raise NotFound("Ticket not found.")
        row = await repo.insert(
            s,
            attachment_id=attachment_id,
            workspace_id=caller.workspace_id,
            ticket_id=ticket_id,
            object_key=key,
            name=clean_name(name),
            size=len(data),
            mime=clean_mime(mime),
            uploaded_by=caller.member_id,
        )
        # Stored inside the transaction: if storage fails, the row rolls back with it.
        # The reverse (stored, then the commit fails) leaves an unreferenced object, which
        # is harmless and is what a retention sweep removes.
        await store.put(key, data, content_type=row["mime"])
    return row


async def ticket_attachments(
    sm: Sessionmaker,
    caller: Caller,
    ticket_id: UUID,
    *,
    cursor: str | None = None,
    limit: int = paging.DEFAULT_LIMIT,
) -> AttachmentPage:
    require(caller, Action.read_ticket)
    before = None
    if cursor:
        try:
            before = paging.decode_time(cursor, "attachment")
        except paging.CursorError as exc:
            raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.ticket_exists(s, ticket_id):
            raise NotFound("Ticket not found.")
        rows = await repo.for_ticket(s, ticket_id, before=before, limit=limit + 1)
    page, next_cursor = paging.time_page(rows, limit, "attachment")
    return AttachmentPage(attachments=page, next_cursor=next_cursor)


async def download_link(
    sm: Sessionmaker,
    store: ObjectStore,
    caller: Caller,
    attachment_id: UUID,
    *,
    seconds: int,
) -> Download:
    """A link anyone holding it can use until it expires, so it is handed out only after
    the signed-in member's own read of the row (RLS, role)."""
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get(s, attachment_id)
    if row is None:
        raise NotFound("File not found.")
    if row["scan_status"] == ScanStatus.infected:
        raise Conflict("This file failed the virus scan, so it can't be downloaded.")
    expires_at = datetime.now(UTC) + timedelta(seconds=seconds)
    url = await store.download_url(
        row["object_key"], name=row["name"], content_type=row["mime"], expires_in=seconds
    )
    return Download(attachment=row, url=url, expires_at=expires_at)
