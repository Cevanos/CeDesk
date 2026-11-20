"""Contact fields (feature-gap plan 2.3): the workspace's custom attributes on contacts;
rules in domain/contact_fields.py.

- Everyone who reads tickets lists them (the customer page shows them).
- Making, changing and deleting them needs `manage_contacts` (Admins; a custom role with
  the settings right). Audited (area `contact_fields`); a definition is not customer data.
- A field's key and kind are fixed once made. Deleting a field removes its values from
  every contact; a field a segment filters on can't be deleted (409 naming the segments).
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import contact_fields as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.contact_fields import ContactFieldKind
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import contact_fields as repo
from cedesk.helpdesk.repo import segments as segment_repo
from cedesk.helpdesk.repo.contact_fields import Row
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

NOT_FOUND = "Contact field not found."
FIELDS = ["key", "label", "kind", "options", "position"]


@contextmanager
def _rules() -> Iterator[None]:
    try:
        yield
    except rules.ContactFieldError as exc:
        raise Invalid(str(exc)) from exc


async def list_fields(sm: Sessionmaker, caller: Caller) -> Sequence[Row]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.all_fields(s)


async def create_field(
    sm: Sessionmaker,
    caller: Caller,
    *,
    key: str,
    label: str,
    kind: ContactFieldKind,
    options: Sequence[str],
    position: int,
) -> Row:
    require(caller, Action.manage_contacts)
    with _rules():
        key = rules.check_key(key)
        clean_label = rules.clean_label(label)
        clean_options = rules.clean_options(kind, options)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            if await repo.count(s) >= rules.MAX_FIELDS:
                raise Invalid(f"A workspace can have at most {rules.MAX_FIELDS} contact fields.")
            field_id = await repo.insert(
                s,
                caller.workspace_id,
                key=key,
                label=clean_label,
                kind=kind.value,
                options=clean_options,
                position=position,
            )
            row = await repo.get(s, field_id)
            assert row is not None
            await audit.record(
                s,
                caller,
                Area.contact_fields,
                "created",
                f"Contact field {key}",
                target_id=field_id,
                detail=audit_rules.snapshot(row, FIELDS),
            )
            return row
    except IntegrityError as exc:
        raise Conflict("A contact field with that key already exists.") from exc


async def change_field(
    sm: Sessionmaker,
    caller: Caller,
    field_id: UUID,
    *,
    label: str | None,
    options: Sequence[str] | None,
    position: int | None,
) -> Row:
    """Label, options (select fields) and position; never the key or kind."""
    require(caller, Action.manage_contacts)
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await repo.get(s, field_id)
        if before is None:
            raise NotFound(NOT_FOUND)
        kind = ContactFieldKind(before["kind"])
        with _rules():
            new_label = rules.clean_label(label) if label is not None else str(before["label"])
            new_options = (
                rules.clean_options(kind, options)
                if options is not None
                else tuple(before["options"])
            )
        await repo.update(
            s,
            field_id,
            label=new_label,
            options=new_options,
            position=before["position"] if position is None else position,
        )
        row = await repo.get(s, field_id)
        assert row is not None
        changed = audit_rules.changes(before, row, fields=("label", "options", "position"))
        if changed:
            await audit.record(
                s,
                caller,
                Area.contact_fields,
                "changed",
                f"Contact field {row['key']}",
                target_id=field_id,
                detail={"changes": changed},
            )
        return row


async def delete_field(sm: Sessionmaker, caller: Caller, field_id: UUID) -> None:
    require(caller, Action.manage_contacts)
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await repo.get(s, field_id)
        if before is None:
            raise NotFound(NOT_FOUND)
        key = str(before["key"])
        used = await segment_repo.using_field(s, key)
        if used:
            raise Conflict(
                "Segments filter on this field: " + ", ".join(used) + ". Change them first."
            )
        await repo.delete(s, field_id)
        cleared = await repo.drop_values(s, key)
        await audit.record(
            s,
            caller,
            Area.contact_fields,
            "deleted",
            f"Contact field {key}",
            target_id=field_id,
            detail=audit_rules.snapshot(before, FIELDS) | {"values_removed": cleared},
        )
