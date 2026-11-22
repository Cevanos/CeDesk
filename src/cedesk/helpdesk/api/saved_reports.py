"""Saved reports (feature-gap plan 3.2): a named report definition, personal or shared."""

from datetime import UTC, datetime
from typing import Annotated, Any, Self
from uuid import UUID

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints, model_validator

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, SettingsDep
from cedesk.helpdesk.api.report_engine import DEFINITION_HELP, ResultOut, csv_for, result_out
from cedesk.helpdesk.api.reports import CSV_OK, REPORT_ERRORS, FormatQuery
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import saved_reports as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.service import saved_reports

router = APIRouter(prefix="/reports/saved", tags=["reports"])

ReportName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=rules.NAME_MAX)
]
SAVED_HELP = DEFINITION_HELP + " A saved report's range must be a preset, so it stays current."


class SavedIn(Body):
    name: ReportName
    definition: dict[str, Any] = Field(description=SAVED_HELP)
    shared: bool = Field(
        default=False,
        description="Shared with everyone who holds the reports right (Admins and "
        "Supervisors may share). false: only you see it.",
    )


class SavedPatch(Body):
    """Send only what changes."""

    name: ReportName | None = None
    definition: dict[str, Any] | None = Field(default=None, description="The whole new one.")
    shared: bool | None = None

    @model_validator(mode="after")
    def no_nulls(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} can't be null")
        return self


class SavedOut(BaseModel):
    id: UUID
    name: str
    definition: dict[str, Any]
    shared: bool
    owner_id: UUID
    owner_name: str
    can_edit: bool = Field(description="Whether you may change or delete it.")
    created_at: datetime
    updated_at: datetime


def _saved(caller: Caller, row: Any) -> SavedOut:
    return SavedOut.model_validate(dict(row) | {"can_edit": saved_reports.can_change(caller, row)})


@router.get(
    "",
    response_model=list[SavedOut],
    summary="Your personal reports and the workspace's shared ones (shared first, by name)",
)
async def list_reports(caller: CallerDep, sm: SessionmakerDep) -> list[SavedOut]:
    return [_saved(caller, r) for r in await saved_reports.list_reports(sm, caller)]


@router.post(
    "",
    response_model=SavedOut,
    status_code=status.HTTP_201_CREATED,
    summary="Save a report (audited)",
    description=f"At most {rules.MAX_PERSONAL} personal reports per person and "
    f"{rules.MAX_SHARED} shared reports per workspace.",
    responses={
        403: {"description": "shared: true without the right to share, or no reports right"},
        409: {"description": "You (or the shared reports) already have one with that name"},
        422: {"description": "A definition the report engine refuses, or a fixed range"},
    },
)
async def create_report(body: SavedIn, caller: CallerDep, sm: SessionmakerDep) -> SavedOut:
    row = await saved_reports.create_report(
        sm, caller, name=body.name, definition=body.definition, shared=body.shared
    )
    return _saved(caller, row)


@router.get("/{report_id}", response_model=SavedOut, summary="One saved report")
async def get_report(report_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> SavedOut:
    return _saved(caller, await saved_reports.get_report(sm, caller, report_id))


@router.patch(
    "/{report_id}",
    response_model=SavedOut,
    summary="Rename a report, change its definition, or share or unshare it (audited)",
    description="Your own personal reports; shared reports need the right to share. "
    "Unsharing makes it its owner's personal report again.",
    responses={404: {"description": "No such report, or someone else's personal report"}},
)
async def change_report(
    report_id: UUID, body: SavedPatch, caller: CallerDep, sm: SessionmakerDep
) -> SavedOut:
    row = await saved_reports.change_report(
        sm, caller, report_id, name=body.name, definition=body.definition, shared=body.shared
    )
    return _saved(caller, row)


@router.delete(
    "/{report_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a saved report (audited; dashboard cards and schedules using it go too)",
    responses={404: {"description": "No such report, or someone else's personal report"}},
)
async def delete_report(report_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await saved_reports.delete_report(sm, caller, report_id)


@router.post(
    "/{report_id}/run",
    response_model=ResultOut,
    response_model_by_alias=True,
    summary="Run a saved report now",
    description="Worked out afresh from its preset range, under *your* scope: a role limited "
    "to one team sees that team's numbers whoever saved the report.",
    responses=CSV_OK
    | REPORT_ERRORS
    | {
        404: {"description": "No such report, or someone else's personal report"},
        429: {"description": "Too many reports are running at once"},
    },
)
async def run_report(
    report_id: UUID,
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
    settings: SettingsDep,
    fmt: FormatQuery = "json",
) -> ResultOut | Response:
    found = await saved_reports.run_report(
        sm,
        caller,
        report_id,
        timeout_ms=settings.report_timeout_seconds * 1000,
        slots=request.app.state.report_slots,
    )
    return csv_for(found, datetime.now(UTC)) if fmt == "csv" else result_out(found)
