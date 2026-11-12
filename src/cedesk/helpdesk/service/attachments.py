"""Files on a ticket: upload, list, and a short-lived link to download one.

Anyone who can write on a ticket can attach a file (light agents attach to their notes);
anyone who can read the ticket can download it. A file is uploaded first, then sent with a
reply or note (`attach_to_message`, which fills `message_id`). Files in a customer's email
are saved with their message (`save_received`).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.domain.attachments import (
    BLOCKED,
    EMAIL_TOTAL_MAX,
    PER_MESSAGE_MAX,
    RECEIVED_MAX,
    ReceivedFile,
    ScanStatus,
    clean_mime,
    clean_name,
    object_key,
    ready,
)
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import attachments as repo
from cedesk.helpdesk.repo import security as security_repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.repo.attachments import Row
from cedesk.helpdesk.service import idempotency, scope
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound, TooLarge
from cedesk.helpdesk.service.idempotency import Idempotency
from cedesk.helpdesk.service.members import require
from cedesk.storage import ObjectMissing, ObjectStore


@dataclass(frozen=True)
class AttachmentPage:
    attachments: Sequence[Row]  # newest first
    next_cursor: str | None


@dataclass(frozen=True)
class Download:
    attachment: Row
    url: str
    expires_at: datetime
    # The workspace wants files opened only by signed-in people (S5): `url` is an API
    # path that needs the same Bearer token, not a link anyone can open.
    needs_sign_in: bool = False


@dataclass(frozen=True)
class FileContent:
    attachment: Row
    data: bytes


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
    idem: Idempotency | None = None,
) -> Row:
    """`idem`: with an Idempotency-Key, a repeat returns the file the first one stored."""
    require(caller, Action.private_note)
    if not data:
        raise Invalid("The file is empty.")
    if len(data) > max_bytes:
        raise too_large(max_bytes)
    attachment_id = uuid4()
    key = object_key(caller.workspace_id, ticket_id, attachment_id)
    async with workspace_session(sm, caller.workspace_id) as s:
        await scope.check(s, caller, ticket_id, edit=False)
        made = await idempotency.begin(s, caller, idem)
        if made is not None:
            earlier = await repo.get(s, made)
            if earlier is None:
                raise NotFound("File not found.")
            return earlier
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
        await idempotency.remember(s, caller, idem, attachment_id)
    return row


async def attach_to_message(
    s: AsyncSession,
    caller: Caller,
    ticket_id: UUID,
    message_id: UUID,
    attachment_ids: Sequence[UUID],
    *,
    emailed: bool,
) -> None:
    """Sends the caller's uploaded files with their new reply or note, in its transaction.
    Each file must be on this ticket, uploaded by the caller, not sent before, and not
    infected. `emailed`: the reply goes out by email, so the files' total size is capped."""
    ids = list(dict.fromkeys(attachment_ids))
    if not ids:
        return
    if len(ids) > PER_MESSAGE_MAX:
        raise Invalid(f"Send at most {PER_MESSAGE_MAX} files with one message.")
    rows = {r["id"]: r for r in await repo.lock_many(s, ids)}
    for attachment_id in ids:
        row = rows.get(attachment_id)
        if row is None or row["ticket_id"] != ticket_id:
            raise Invalid("A file isn't on this ticket. Upload it to this ticket first.")
        if row["uploaded_by"] != caller.member_id:
            raise Invalid("You can only send files you uploaded.")
        if row["message_id"] is not None:
            raise Invalid(f"{row['name']} was already sent. Upload it again to send it again.")
        if row["scan_status"] in BLOCKED:
            raise Invalid(f"{row['name']} is blocked (virus scan or erased) and can't be sent.")
    if emailed and sum(int(r["size"]) for r in rows.values()) > EMAIL_TOTAL_MAX:
        mb = EMAIL_TOTAL_MAX // (1024 * 1024)
        raise Invalid(f"Files on an emailed reply can be at most {mb} MB together.")
    await repo.link_to_message(s, ids, message_id)


@dataclass(frozen=True)
class IncomingFiles:
    """Files that came with a customer's message, and where to keep them."""

    store: ObjectStore
    files: Sequence[ReceivedFile]
    max_bytes: int


async def save_incoming(
    s: AsyncSession,
    incoming: IncomingFiles | None,
    *,
    workspace_id: UUID,
    ticket_id: UUID,
    message_id: UUID,
) -> None:
    """Keeps a customer message's files, in its transaction. Files not kept (empty, too
    large, or past the first 20) are named in a system line on the ticket."""
    if incoming is None or not incoming.files:
        return
    skipped: list[str] = []
    for i, file in enumerate(incoming.files):
        name = clean_name(file.name)
        if i >= RECEIVED_MAX or not file.data or len(file.data) > incoming.max_bytes:
            skipped.append(name)
            continue
        attachment_id = uuid4()
        key = object_key(workspace_id, ticket_id, attachment_id)
        row = await repo.insert(
            s,
            attachment_id=attachment_id,
            workspace_id=workspace_id,
            ticket_id=ticket_id,
            object_key=key,
            name=name,
            size=len(file.data),
            mime=clean_mime(file.mime),
            uploaded_by=None,
            message_id=message_id,
        )
        await incoming.store.put(key, file.data, content_type=row["mime"])  # as in `upload`
    if skipped:
        shown = ", ".join(skipped[:5]) + (
            f" and {len(skipped) - 5} more" if len(skipped) > 5 else ""
        )
        await ticket_repo.insert_message(
            s,
            workspace_id=workspace_id,
            ticket_id=ticket_id,
            kind="system",
            body=f"Not saved from the customer's email (empty, too large, or too many): {shown}"[
                :500
            ],
            author_member_id=None,
            author_name=None,
        )


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
        await scope.check(s, caller, ticket_id, edit=False)
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
    scanning: bool = False,
) -> Download:
    """A link anyone holding it can use until it expires, so it is handed out only after
    the signed-in member's own read of the row (RLS, role)."""
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get(s, attachment_id)
        if row is not None:
            await scope.check(s, caller, row["ticket_id"], edit=False)
        need_sign_in = (await security_repo.current(s)).attachments_need_sign_in
    if row is None:
        raise NotFound("File not found.")
    return await link_for(
        store,
        row,
        seconds=seconds,
        scanning=scanning,
        sign_in_url=f"/api/v1/attachments/{attachment_id}/content" if need_sign_in else None,
    )


async def file_content(
    sm: Sessionmaker, store: ObjectStore, caller: Caller, attachment_id: UUID, *, scanning: bool
) -> FileContent:
    """The file itself, for a signed-in member (S5). Same checks as `download_link`."""
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get(s, attachment_id)
        if row is not None:
            await scope.check(s, caller, row["ticket_id"], edit=False)
    if row is None:
        raise NotFound("File not found.")
    return await content_of(store, row, scanning=scanning)


async def content_of(store: ObjectStore, row: Row, *, scanning: bool) -> FileContent:
    """The bytes of a file the caller was already allowed to read (agent or portal)."""
    _check_scan(row, scanning)
    try:
        data = await store.get(str(row["object_key"]))
    except ObjectMissing as exc:
        raise NotFound("File not found.") from exc
    return FileContent(row, data)


async def link_for(
    store: ObjectStore, row: Row, *, seconds: int, scanning: bool, sign_in_url: str | None = None
) -> Download:
    """The link for a file the caller was already allowed to read (agent or portal).
    `sign_in_url`: the workspace wants sign-in for files (S5), so that path is given
    instead of a link anyone can open."""
    _check_scan(row, scanning)
    expires_at = datetime.now(UTC) + timedelta(seconds=seconds)
    if sign_in_url is not None:
        return Download(row, sign_in_url, expires_at, needs_sign_in=True)
    url = await store.download_url(
        row["object_key"], name=row["name"], content_type=row["mime"], expires_in=seconds
    )
    return Download(attachment=row, url=url, expires_at=expires_at)


def _check_scan(row: Row, scanning: bool) -> None:
    if row["scan_status"] == ScanStatus.erased:
        raise Conflict("This file was erased at the customer's request.")
    if row["scan_status"] == ScanStatus.infected:
        raise Conflict("This file failed the virus scan, so it can't be downloaded.")
    if row["scan_status"] == ScanStatus.scan_failed:
        raise Conflict("This file could not be checked for viruses, so it can't be downloaded.")
    if not ready(row["scan_status"], scanning=scanning):
        raise Conflict("This file is still being checked for viruses. Try again in a moment.")
