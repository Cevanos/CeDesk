"""Contact fields (feature-gap plan 2.3): the workspace's custom attributes on contacts."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, StringConstraints, model_validator

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import contact_fields as rules
from cedesk.helpdesk.domain.contact_fields import ContactFieldKind
from cedesk.helpdesk.service import contact_fields

router = APIRouter(prefix="/contact-fields", tags=["contacts"])

Label = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=rules.LABEL_MAX)
]
Options = Annotated[list[str], Field(max_length=50)]
Position = Annotated[int, Field(ge=0, le=10_000)]


class ContactFieldIn(Body):
    key: Annotated[str, StringConstraints(min_length=1, max_length=40)] = Field(
        description="a-z, 0-9 and _, starting with a letter. Never changes."
    )
    label: Label
    kind: ContactFieldKind = Field(description="Never changes.")
    options: Options = Field(default_factory=list, description="select only: 1-50 choices.")
    position: Position = 0


class ContactFieldPatch(Body):
    """Send only what changes; the key and kind can't."""

    label: Label | None = None
    options: Options | None = Field(
        default=None, description="The whole new list. Values already set stay as they are."
    )
    position: Position | None = None

    @model_validator(mode="after")
    def no_nulls(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} can't be null")
        return self


class ContactFieldOut(BaseModel):
    id: UUID
    key: str
    label: str
    kind: ContactFieldKind
    options: list[str]
    position: int
    created_at: datetime
    updated_at: datetime


@router.get(
    "",
    response_model=list[ContactFieldOut],
    summary="The workspace's contact fields, in order",
)
async def list_fields(caller: CallerDep, sm: SessionmakerDep) -> list[ContactFieldOut]:
    return [
        ContactFieldOut.model_validate(dict(r))
        for r in await contact_fields.list_fields(sm, caller)
    ]


@router.post(
    "",
    response_model=ContactFieldOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a contact field (Admin; audited)",
    description=f"At most {rules.MAX_FIELDS} per workspace.",
    responses={409: {"description": "That key is taken"}},
)
async def create_field(
    body: ContactFieldIn, caller: CallerDep, sm: SessionmakerDep
) -> ContactFieldOut:
    row = await contact_fields.create_field(
        sm,
        caller,
        key=body.key,
        label=body.label,
        kind=body.kind,
        options=body.options,
        position=body.position,
    )
    return ContactFieldOut.model_validate(dict(row))


@router.patch(
    "/{field_id}",
    response_model=ContactFieldOut,
    summary="Rename a contact field, change its options or position (Admin; audited)",
)
async def change_field(
    field_id: UUID, body: ContactFieldPatch, caller: CallerDep, sm: SessionmakerDep
) -> ContactFieldOut:
    row = await contact_fields.change_field(
        sm, caller, field_id, label=body.label, options=body.options, position=body.position
    )
    return ContactFieldOut.model_validate(dict(row))


@router.delete(
    "/{field_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a contact field and every customer's value of it (Admin; audited)",
    responses={409: {"description": "A segment filters on it"}},
)
async def delete_field(field_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await contact_fields.delete_field(sm, caller, field_id)
