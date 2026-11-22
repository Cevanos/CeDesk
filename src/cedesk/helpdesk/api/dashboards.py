"""Dashboards (feature-gap plan 3.3): a saved, ordered set of report cards."""

from datetime import UTC, datetime
from typing import Annotated, Any, Self
from uuid import UUID

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints, model_validator

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, SettingsDep
from cedesk.helpdesk.api.report_engine import ResultOut, csv_for, result_out
from cedesk.helpdesk.api.reports import CSV_OK, REPORT_ERRORS, FormatQuery
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import dashboards as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.service import dashboards

router = APIRouter(prefix="/dashboards", tags=["reports"])

DashboardName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=rules.NAME_MAX)
]
CARDS_HELP = (
    f"Up to {rules.MAX_CARDS} cards in order; each is `{{report_id}}` (a saved report you may "
    "see) or `{preset}` (a built-in report from `GET /reports/options`). A shared dashboard "
    "may only hold built-in and shared reports."
)


class DashboardIn(Body):
    name: DashboardName
    shared: bool = Field(
        default=False, description="Shared with everyone who holds the reports right."
    )
    cards: list[dict[str, Any]] = Field(
        default=[], max_length=rules.MAX_CARDS, description=CARDS_HELP
    )


class DashboardPatch(Body):
    """Send only what changes."""

    name: DashboardName | None = None
    shared: bool | None = None
    cards: list[dict[str, Any]] | None = Field(
        default=None, max_length=rules.MAX_CARDS, description="The whole new list of cards."
    )

    @model_validator(mode="after")
    def no_nulls(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} can't be null")
        return self


class CardOut(BaseModel):
    id: UUID
    position: int
    report_id: UUID | None = Field(description="A saved report; null for a built-in one.")
    preset: str | None = Field(description="A built-in report's key; null for a saved one.")
    label: str = Field(description="The report's name.")


class DashboardOut(BaseModel):
    id: UUID
    name: str
    shared: bool
    owner_id: UUID
    owner_name: str
    can_edit: bool = Field(description="Whether you may change or delete it.")
    cards: list[CardOut]
    created_at: datetime
    updated_at: datetime


class RunCardIn(Body):
    range: dict[str, Any] | None = Field(
        default=None, description="Replaces the card's range: `{preset}` or `{from, to}`."
    )
    team_id: UUID | None = Field(default=None, description="Replaces the card's team filter.")


def _out(caller: Caller, found: dashboards.Dashboard) -> DashboardOut:
    return DashboardOut(
        **dict(found.row),
        can_edit=dashboards.can_change(caller, found.row),
        cards=[
            CardOut(
                id=c["id"], position=c["position"], report_id=c["report_id"],
                preset=c["preset"], label=dashboards.card_label(c),
            )
            for c in found.cards
        ],
    )  # fmt: skip


@router.get(
    "",
    response_model=list[DashboardOut],
    summary="Your personal dashboards and the workspace's shared ones (shared first, by name)",
)
async def list_dashboards(caller: CallerDep, sm: SessionmakerDep) -> list[DashboardOut]:
    return [_out(caller, d) for d in await dashboards.list_dashboards(sm, caller)]


@router.post(
    "",
    response_model=DashboardOut,
    status_code=status.HTTP_201_CREATED,
    summary="Make a dashboard (audited)",
    description=f"At most {rules.MAX_PERSONAL} personal dashboards per person and "
    f"{rules.MAX_SHARED} shared dashboards per workspace.",
    responses={
        403: {"description": "shared: true without the right to share, or no reports right"},
        409: {"description": "You (or the shared dashboards) already have one with that name"},
        422: {"description": "A bad card, or a personal report on a shared dashboard"},
    },
)
async def create_dashboard(
    body: DashboardIn, caller: CallerDep, sm: SessionmakerDep
) -> DashboardOut:
    made = await dashboards.create_dashboard(
        sm, caller, name=body.name, shared=body.shared, cards=body.cards
    )
    return _out(caller, made)


@router.get("/{dashboard_id}", response_model=DashboardOut, summary="One dashboard")
async def get_dashboard(dashboard_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> DashboardOut:
    return _out(caller, await dashboards.get_dashboard(sm, caller, dashboard_id))


@router.patch(
    "/{dashboard_id}",
    response_model=DashboardOut,
    summary="Rename a dashboard, change its cards, or share or unshare it (audited)",
    responses={404: {"description": "No such dashboard, or someone else's personal one"}},
)
async def change_dashboard(
    dashboard_id: UUID, body: DashboardPatch, caller: CallerDep, sm: SessionmakerDep
) -> DashboardOut:
    made = await dashboards.change_dashboard(
        sm, caller, dashboard_id, name=body.name, shared=body.shared, cards=body.cards
    )
    return _out(caller, made)


@router.delete(
    "/{dashboard_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a dashboard (audited)",
    responses={404: {"description": "No such dashboard, or someone else's personal one"}},
)
async def delete_dashboard(dashboard_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await dashboards.delete_dashboard(sm, caller, dashboard_id)


@router.post(
    "/{dashboard_id}/cards/{card_id}/run",
    response_model=ResultOut,
    response_model_by_alias=True,
    summary="Run one card of a dashboard",
    description="Each card is run on its own, under *your* scope. Send `range` and `team_id` "
    "to apply the dashboard page's pickers to the card.",
    responses=CSV_OK
    | REPORT_ERRORS
    | {
        404: {"description": "No such dashboard or card, or no report you may see"},
        429: {"description": "Too many reports are running at once"},
    },
)
async def run_card(
    dashboard_id: UUID,
    card_id: UUID,
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
    settings: SettingsDep,
    body: RunCardIn | None = None,
    fmt: FormatQuery = "json",
) -> ResultOut | Response:
    given = body or RunCardIn()
    found = await dashboards.run_card(
        sm,
        caller,
        dashboard_id,
        card_id,
        range_=given.range,
        team_id=given.team_id,
        timeout_ms=settings.report_timeout_seconds * 1000,
        slots=request.app.state.report_slots,
    )
    return csv_for(found, datetime.now(UTC)) if fmt == "csv" else result_out(found)
