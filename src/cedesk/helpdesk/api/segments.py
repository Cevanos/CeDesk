"""Segments (feature-gap plan 2.6): saved contact filters, a list of customers."""

from datetime import datetime
from typing import Annotated, Any, Self
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, StringConstraints, model_validator

from cedesk.helpdesk.api.contacts import ContactListOut, ContactRowOut, Cursor
from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import contacts as contact_rules
from cedesk.helpdesk.domain import saved_views as name_rules
from cedesk.helpdesk.service import segments

router = APIRouter(prefix="/segments", tags=["contacts"])

SegmentName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=name_rules.NAME_MAX)
]
FILTERS_HELP = (
    "`GET /contacts`' own filters, as an object: "
    + ", ".join(contact_rules.FILTER_KEYS)
    + ' (for example {"vip": true, "has_open": true, "attributes": {"plan": "pro"}, '
    '"sort": "recent"}). Dates as YYYY-MM-DD. Any other key or a value the list would '
    "refuse is 422."
)


class SegmentIn(Body):
    name: SegmentName
    filters: dict[str, Any] = Field(
        max_length=len(contact_rules.FILTER_KEYS), description=FILTERS_HELP
    )


class SegmentPatch(Body):
    """Send only what changes."""

    name: SegmentName | None = None
    filters: dict[str, Any] | None = Field(
        default=None, max_length=len(contact_rules.FILTER_KEYS), description="The whole new set."
    )

    @model_validator(mode="after")
    def no_nulls(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} can't be null")
        return self


class SegmentOut(BaseModel):
    id: UUID
    name: str
    filters: dict[str, Any] = Field(description="Only the filters that are set.")
    created_by: UUID | None
    created_by_name: str | None
    created_at: datetime
    updated_at: datetime


class SegmentDetailOut(SegmentOut):
    contact_count: int = Field(description="How many customers are in it now.")


class SegmentContactsOut(ContactListOut):
    segment: SegmentOut


@router.get("", response_model=list[SegmentOut], summary="The workspace's segments, by name")
async def list_segments(caller: CallerDep, sm: SessionmakerDep) -> list[SegmentOut]:
    return [SegmentOut.model_validate(dict(r)) for r in await segments.list_segments(sm, caller)]


@router.post(
    "",
    response_model=SegmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Save a segment (Admins and Supervisors; audited)",
    description=f"At most {segments.MAX_SEGMENTS} per workspace.",
    responses={409: {"description": "That name is taken"}, 422: {"description": "Bad filters"}},
)
async def create_segment(body: SegmentIn, caller: CallerDep, sm: SessionmakerDep) -> SegmentOut:
    row = await segments.create_segment(sm, caller, name=body.name, filters=body.filters)
    return SegmentOut.model_validate(dict(row))


@router.get("/{segment_id}", response_model=SegmentDetailOut, summary="One segment, with its count")
async def get_segment(segment_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> SegmentDetailOut:
    row, count = await segments.get_segment(sm, caller, segment_id)
    return SegmentDetailOut.model_validate(dict(row) | {"contact_count": count})


@router.patch(
    "/{segment_id}",
    response_model=SegmentOut,
    summary="Rename a segment or change its filters (Admins and Supervisors; audited)",
)
async def change_segment(
    segment_id: UUID, body: SegmentPatch, caller: CallerDep, sm: SessionmakerDep
) -> SegmentOut:
    row = await segments.change_segment(
        sm, caller, segment_id, name=body.name, filters=body.filters
    )
    return SegmentOut.model_validate(dict(row))


@router.delete(
    "/{segment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a segment (Admins and Supervisors; audited)",
)
async def delete_segment(segment_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await segments.delete_segment(sm, caller, segment_id)


@router.get(
    "/{segment_id}/contacts",
    response_model=SegmentContactsOut,
    summary="The customers in a segment now, a page at a time (a recipient list)",
    description="Worked out when asked: erased customers are never in it. Same rows and "
    "paging as `GET /contacts`.",
)
async def segment_contacts(
    segment_id: UUID,
    caller: CallerDep,
    sm: SessionmakerDep,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SegmentContactsOut:
    row, found = await segments.segment_contacts(sm, caller, segment_id, cursor=cursor, limit=limit)
    return SegmentContactsOut(
        segment=SegmentOut.model_validate(dict(row)),
        contacts=[ContactRowOut.model_validate(dict(r)) for r in found.contacts],
        next_cursor=found.next_cursor,
    )
