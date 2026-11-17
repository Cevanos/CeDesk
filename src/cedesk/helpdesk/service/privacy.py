"""Privacy (md S11, India DPDP and GDPR): export everything about one customer, or erase
them. Admins only (`manage_access`); every request is in the audit log, by contact id
only (the log can't be changed later, so it never holds the customer's address).

Export: one JSON file (the customer, their tickets, archived ones too (md O3), with
every message, file names and ratings), kept in storage for 7 days and downloaded signed
in. Internal notes are included: review them before sending the file to the customer.

Erase: the words that could identify the customer are replaced and their files are
deleted from storage; every row stays, so ticket counts, reply times, ratings and time
logged are unchanged. The stored files go first (as in core-api's erasure.py): if the
erase then fails, nothing in the database changed and running it again finds the same
keys (deleting a missing key is not an error). A retention export (md O2) that holds the
customer's tickets is deleted whole (one file names many customers). A chat kept in
Cevanos must be erased in Cevanos too.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.exports import ExportFile, archived_export, export_tickets, when
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import archive as archive_repo
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import limits as limits_repo
from cedesk.helpdesk.repo import privacy as repo
from cedesk.helpdesk.repo.privacy import ERASED, ERASED_NAME
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require
from cedesk.storage import ObjectMissing, ObjectStore

KEEP_EXPORT = timedelta(days=7)
NO_CUSTOMER = "Customer not found."


def export_key(workspace_id: UUID, request_id: UUID) -> str:
    return f"{workspace_id}/privacy/{request_id}.json"


def file_name(contact_id: UUID) -> str:
    return f"customer-{contact_id}.json"


async def _contact(s: AsyncSession, contact_id: UUID | None, email: str | None) -> RowMapping:
    if (contact_id is None) == (email is None):
        raise Invalid("Send the customer's contact_id or their email, not both.")
    if contact_id is None:
        assert email is not None
        found = await repo.contact_by_email(s, email.strip().lower())
        if found is None:
            raise NotFound(NO_CUSTOMER)
        contact_id = found["id"]
    row = await repo.contact(s, contact_id, lock=True)
    if row is None:
        raise NotFound(NO_CUSTOMER)
    return row


def build_export(
    contact: RowMapping,
    tickets: Sequence[RowMapping],
    messages: Sequence[RowMapping],
    files: Sequence[RowMapping],
    ratings: Sequence[RowMapping],
    *,
    workspace: str,
    now: datetime,
) -> dict[str, Any]:
    return {
        "exported_at": when(now),
        "workspace": workspace,
        "about": "Everything this help desk holds about one customer. Messages of kind "
        "'note' are the team's internal notes.",
        "customer": {
            "id": str(contact["id"]),
            "email": contact["email"],
            "name": contact["name"],
            "vip": contact["vip"],
            "phone": contact.get("phone"),
            "organization": contact.get("organization"),
            "customer_since": when(contact["customer_since"]),
            "first_contact": when(contact["created_at"]),
            "erased_at": when(contact["erased_at"]),
        },
        "tickets": export_tickets(tickets, messages, files, ratings),
    }


async def export(
    sm: Sessionmaker,
    store: ObjectStore,
    caller: Caller,
    *,
    contact_id: UUID | None = None,
    email: str | None = None,
) -> RowMapping:
    require(caller, Action.manage_access)
    now = datetime.now(UTC)
    request_id = uuid4()
    key = export_key(caller.workspace_id, request_id)
    async with workspace_session(sm, caller.workspace_id) as s:
        contact = await _contact(s, contact_id, email)
        tickets = await repo.tickets_of(s, contact["id"])
        ids = [t["id"] for t in tickets]
        messages = await repo.messages_of(s, ids)
        data = build_export(
            contact,
            tickets,
            messages,
            await repo.files_of(s, ids),
            await repo.ratings_of(s, contact["id"]),
            workspace=await repo.workspace_name(s),
            now=now,
        )
        archived = await archive_repo.for_contact(s, contact["id"])
        data["tickets"] += [archived_export(a) for a in archived]  # md O3
        n_tickets = len(tickets) + len(archived)
        n_messages = len(messages) + sum(len(a["messages"]) for a in archived)
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        await store.put(key, body, content_type="application/json")
        try:
            await repo.insert_request(
                s, caller.workspace_id, request_id=request_id, kind="export",
                contact_id=contact["id"], requested_by=caller.member_id, object_key=key,
                size=len(body), expires_at=now + KEEP_EXPORT,
                counts={"tickets": n_tickets, "messages": n_messages},
            )  # fmt: skip
            await audit.record(
                s,
                caller,
                Area.privacy,
                "exported",
                f"Customer {contact['id']}",
                target_id=contact["id"],
                detail={"tickets": n_tickets},
            )
            row = await repo.request(s, request_id)
        except BaseException:
            await store.delete(key)
            raise
    assert row is not None
    return row


async def export_file(
    sm: Sessionmaker, store: ObjectStore, caller: Caller, request_id: UUID
) -> ExportFile:
    require(caller, Action.manage_access)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.request(s, request_id)
    if row is None or row["kind"] != "export":
        raise NotFound("Export not found.")
    if row["object_key"] is None:
        raise Conflict("This export was removed (after 7 days, or the customer was erased).")
    try:
        data = await store.get(str(row["object_key"]))
    except ObjectMissing as exc:
        raise Conflict("This export was removed. Make a new one.") from exc
    return ExportFile(file_name(row["contact_id"]), data)


async def erase(
    sm: Sessionmaker, store: ObjectStore, caller: Caller, contact_id: UUID, *, confirm_email: str
) -> RowMapping:
    """`confirm_email`: the customer's address typed again, so the wrong customer isn't
    erased by a slip (it can't be undone)."""
    require(caller, Action.manage_access)
    request_id = uuid4()
    async with workspace_session(sm, caller.workspace_id) as s:
        contact = await repo.contact(s, contact_id, lock=True)
        if contact is None:
            raise NotFound(NO_CUSTOMER)
        if contact["erased_at"] is not None:
            raise Conflict("This customer was already erased.")
        if confirm_email.strip().lower() != contact["email"]:
            raise Invalid("Type the customer's email address exactly to confirm.")
        ids = await repo.ticket_ids(s, contact_id)
        copies = await repo.merged_copies(s, contact_id)  # before their messages change
        keys = await repo.file_keys(s, ids) + await repo.export_keys(s, contact_id)
        keys += await repo.message_file_keys(s, copies)
        keys += await archive_repo.file_keys(s, contact_id)  # archived tickets (md O3)
        # Retention exports (md O2) that hold their tickets go whole: one file names many.
        retained = await limits_repo.exports_naming(s, contact_id)
        keys += [str(r["object_key"]) for r in retained]
        for key in keys:  # first: see the module note
            await store.delete(key)
        counts = await repo.erase_tickets(s, ids)
        counts |= await repo.erase_messages(s, copies)
        counts["archived_tickets"] = await archive_repo.erase_for_contact(
            s, contact_id, erased=ERASED, name=ERASED_NAME
        )
        counts |= await repo.erase_contact(s, contact_id, str(contact["email"]))
        counts["exports"] = await repo.forget_exports(s, contact_id)
        for r in retained:
            await limits_repo.forget_export(s, r["id"])
        counts["retention_exports"] = len(retained)
        await repo.insert_request(
            s, caller.workspace_id, request_id=request_id, kind="erase",
            contact_id=contact_id, requested_by=caller.member_id, counts=counts,
        )  # fmt: skip
        await audit.record(
            s,
            caller,
            Area.privacy,
            "erased",
            f"Customer {contact_id}",
            target_id=contact_id,
            detail=counts,
        )
        row = await repo.request(s, request_id)
    assert row is not None
    return row


async def requests(sm: Sessionmaker, caller: Caller, *, limit: int = 50) -> Sequence[RowMapping]:
    require(caller, Action.manage_access)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.requests(s, limit=limit)


async def remove_expired_exports(sm: Sessionmaker, store: ObjectStore) -> int:
    """Deletes export files past their 7 days, in every workspace (the worker)."""
    now = datetime.now(UTC)
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    removed = 0
    for workspace_id in workspace_ids:
        async with workspace_session(sm, workspace_id) as s:
            for row in await repo.expired_exports(s, now):
                await store.delete(str(row["object_key"]))
                await repo.forget_export(s, row["id"])
                removed += 1
    return removed
