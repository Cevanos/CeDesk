"""Audit log (md S9): the list with an area filter, Verify chain, CSV export."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.paging import DEFAULT_LIMIT, MAX_LIMIT
from cedesk.helpdesk.service import audit

router = APIRouter(tags=["audit log"])

AreaQuery = Annotated[
    Area | None, Query(description="Only this area (the filter on the audit page).")
]


class AuditEntryOut(BaseModel):
    seq: int = Field(description="Position in the workspace's chain (1, 2, 3, ...).")
    at: datetime
    actor_id: UUID | None = Field(description="Null for a change made on the server.")
    actor_name: str
    ip: str | None
    area: str
    action: str = Field(description="created, changed, deleted, reordered, ...")
    target: str = Field(description='What changed, e.g. "Macro Refund approved".')
    target_id: UUID | None
    detail: dict[str, Any] = Field(
        description='`changes`: {field: [before, after]}; secrets show "(hidden)".'
    )


class AuditPageOut(BaseModel):
    entries: list[AuditEntryOut]
    next_before: int | None = Field(description="Pass as `before` for the next page.")
    areas: list[str] = Field(description="Every area, for the filter.")


class VerifyOut(BaseModel):
    ok: bool
    checked: int = Field(description="Entries checked, oldest first.")
    broken_at: int | None = Field(description="The first entry that doesn't match (seq).")
    message: str


@router.get("/audit", response_model=AuditPageOut, summary="Audit log, newest first")
async def list_audit(
    caller: CallerDep,
    sm: SessionmakerDep,
    area: AreaQuery = None,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> AuditPageOut:
    found = await audit.page(
        sm, caller, area=area.value if area else None, before=before, limit=limit
    )
    return AuditPageOut(
        entries=[AuditEntryOut.model_validate(dict(row)) for row in found.entries],
        next_before=found.next_before,
        areas=[a.value for a in Area],
    )


@router.post("/audit/verify", response_model=VerifyOut, summary="Verify chain")
async def verify(caller: CallerDep, sm: SessionmakerDep) -> VerifyOut:
    result = await audit.verify(sm, caller)
    message = (
        f"All {result.checked} entries check out."
        if result.ok
        else f"Entry {result.broken_at} doesn't match: the log was changed at or after it."
    )
    return VerifyOut(
        ok=result.ok, checked=result.checked, broken_at=result.broken_at, message=message
    )


@router.get(
    "/audit/export.csv",
    response_class=StreamingResponse,
    summary="Audit log as CSV (oldest first)",
    responses={200: {"content": {"text/csv": {}}}},
)
async def export_csv(caller: CallerDep, sm: SessionmakerDep, area: AreaQuery = None) -> Any:
    chunks = await audit.export_csv(sm, caller, area=area.value if area else None)
    return StreamingResponse(
        chunks,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
    )
