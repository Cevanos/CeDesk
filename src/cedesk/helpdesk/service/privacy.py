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
keys (deleting a missing key is not an error). A chat kept in Cevanos must be erased in
Cevanos too.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import archive as archive_repo
from cedesk.helpdesk.repo import auth as auth_repo
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


def _when(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


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
    by_ticket: dict[UUID, list[dict[str, Any]]] = {t["id"]: [] for t in tickets}
    files_by_message: dict[UUID | None, list[dict[str, Any]]] = {}
    for f in files:
        files_by_message.setdefault(f["message_id"], []).append(
            {"name": f["name"], "size": f["size"], "type": f["mime"]}
        )
    for m in messages:
        by_ticket[m["ticket_id"]].append(
            {
                "at": _when(m["created_at"]),
                "kind": m["kind"],
                "from": m["member_name"] or m["author_name"],
                "text": m["body"],
                "files": files_by_message.get(m["id"], []),
            }
        )
    rating_of = {r["ticket_id"]: r for r in ratings}
    return {
        "exported_at": _when(now),
        "workspace": workspace,
        "about": "Everything this help desk holds about one customer. Messages of kind "
        "'note' are the team's internal notes.",
        "customer": {
            "id": str(contact["id"]),
            "email": contact["email"],
            "name": contact["name"],
            "vip": contact["vip"],
            "customer_since": _when(contact["customer_since"]),
            "first_contact": _when(contact["created_at"]),
            "erased_at": _when(contact["erased_at"]),
        },
        "tickets": [
            {
                "number": t["number"],
                "subject": t["subject"],
                "status": t["status"],
                "priority": t["priority"],
                "channel": t["channel"],
                "type": t["type_name"],
                "fields": t["fields"],
                "tags": list(t["tags"]),
                "created_at": _when(t["created_at"]),
                "resolved_at": _when(t["resolved_at"]),
                "messages": by_ticket[t["id"]],
                "rating": (
                    {
                        "score": rating_of[t["id"]]["score"],
                        "comment": rating_of[t["id"]]["comment"],
                        "rated_at": _when(rating_of[t["id"]]["rated_at"]),
                    }
                    if t["id"] in rating_of
                    else None
                ),
            }
            for t in tickets
        ],
    }


def archived_export(a: RowMapping) -> dict[str, Any]:
    """An archived ticket (md O3) in the export's ticket shape, marked `archived`."""
    ticket = a["ticket"]
    files: dict[Any, list[dict[str, Any]]] = {}
    for f in a["files"]:
        files.setdefault(f.get("message_id"), []).append(
            {"name": f.get("name"), "size": f.get("size"), "type": f.get("mime")}
        )
    rating = a["rating"]
    return {
        "number": a["number"],
        "subject": a["subject"],
        "status": a["status"],
        "priority": a["priority"],
        "channel": a["channel"],
        "type": None,
        "fields": ticket.get("fields") or {},
        "tags": list(ticket.get("tags") or []),
        "created_at": _when(a["created_at"]),
        "resolved_at": _when(a["resolved_at"]),
        "archived": True,
        "messages": [
            {
                "at": m.get("created_at"),
                "kind": m.get("kind"),
                "from": m.get("author_name"),
                "text": m.get("body"),
                "files": files.get(m.get("id"), []),
            }
            for m in a["messages"]
        ],
        "rating": (
            {
                "score": rating.get("score"),
                "comment": rating.get("comment"),
                "rated_at": rating.get("rated_at"),
            }
            if rating
            else None
        ),
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
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        await store.put(key, body, content_type="application/json")
        try:
            await repo.insert_request(
                s, caller.workspace_id, request_id=request_id, kind="export",
                contact_id=contact["id"], requested_by=caller.member_id, object_key=key,
                size=len(body), expires_at=now + KEEP_EXPORT,
                counts={"tickets": len(tickets), "messages": len(messages)},
            )  # fmt: skip
            await audit.record(
                s,
                caller,
                Area.privacy,
                "exported",
                f"Customer {contact['id']}",
                target_id=contact["id"],
                detail={"tickets": len(tickets)},
            )
            row = await repo.request(s, request_id)
        except BaseException:
            await store.delete(key)
            raise
    assert row is not None
    return row


@dataclass(frozen=True)
class ExportFile:
    name: str
    data: bytes


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
        keys = await repo.file_keys(s, ids) + await repo.export_keys(s, contact_id)
        keys += await archive_repo.file_keys(s, contact_id)  # archived tickets (md O3)
        for key in keys:  # first: see the module note
            await store.delete(key)
        counts = await repo.erase_tickets(s, ids)
        counts["archived_tickets"] = await archive_repo.erase_for_contact(
            s, contact_id, erased=ERASED, name=ERASED_NAME
        )
        counts |= await repo.erase_contact(s, contact_id, str(contact["email"]))
        counts["exports"] = await repo.forget_exports(s, contact_id)
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
