"""Resolution receipts (differentiator plan #5): the workspace setting, a ticket's
receipt (read, change, Verify) and the Resolutions report."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.apps import HttpDep, _cevanos
from cedesk.helpdesk.api.deps import CallerDep, SealerDep, SessionmakerDep
from cedesk.helpdesk.api.reports import (
    CSV_OK,
    REPORT_ERRORS,
    TEAM_ERRORS,
    EndQuery,
    FormatQuery,
    StartQuery,
    TeamQuery,
    csv_response,
)
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.api.ticket_schemas import ReceiptPatch, ResolutionOut
from cedesk.helpdesk.domain import resolution as rules
from cedesk.helpdesk.domain.report_csv import Table
from cedesk.helpdesk.service import resolution, resolution_verify

router = APIRouter(tags=["resolutions"])


class ReceiptSettingOut(BaseModel):
    mode: Literal["off", "optional", "required"] = Field(
        description="off (default): no receipts. optional: an agent may add one when "
        "resolving. required: resolving needs one (for `required_teams`, or every team)."
    )
    required_teams: list[UUID]
    updated_at: datetime | None
    updated_by_name: str | None


class ReceiptSettingIn(Body):
    mode: Literal["off", "optional", "required"]
    required_teams: list[UUID] = Field(
        default=[],
        max_length=rules.TEAMS_MAX,
        description="With `required` only: the teams whose tickets need a receipt. "
        "Empty: every team.",
    )


@router.get(
    "/resolution-receipts",
    response_model=ReceiptSettingOut,
    summary="The resolution receipts setting (Settings)",
)
async def get_setting(caller: CallerDep, sm: SessionmakerDep) -> ReceiptSettingOut:
    return ReceiptSettingOut(**(await resolution.get_setting(sm, caller)).__dict__)


@router.put(
    "/resolution-receipts",
    response_model=ReceiptSettingOut,
    summary="Turn resolution receipts off, optional or required (Settings; audited)",
    description="Off by default. With `required`, resolving a ticket without a receipt "
    "is refused (422), for the listed teams or every team. Automatic closes (auto-close, "
    "rules, Cevanos) never need one.",
)
async def set_setting(
    body: ReceiptSettingIn, caller: CallerDep, sm: SessionmakerDep
) -> ReceiptSettingOut:
    saved = await resolution.set_setting(sm, caller, body.mode, body.required_teams)
    return ReceiptSettingOut(**saved.__dict__)


def _out(row: Any) -> ResolutionOut:
    return ResolutionOut.model_validate(dict(row))


@router.get(
    "/tickets/{ticket_id}/resolution",
    response_model=ResolutionOut,
    summary="A ticket's resolution receipt",
    responses={404: {"description": "No such ticket, or it has no receipt"}},
)
async def get_receipt(ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> ResolutionOut:
    return _out(await resolution.get(sm, caller, ticket_id))


@router.patch(
    "/tickets/{ticket_id}/resolution",
    response_model=ResolutionOut,
    summary="Change a ticket's resolution receipt (for 7 days after resolving)",
    description="Only the keys sent change. While the ticket is resolved, for 7 days after "
    "resolving; audited (field names only). `verified` can be set to `unverified` or "
    "`agent`; Shopify and Stripe come from Verify.",
    responses={
        404: {"description": "No such ticket, or it has no receipt"},
        409: {"description": "Not resolved, or resolved over 7 days ago"},
    },
)
async def change_receipt(
    ticket_id: UUID, body: ReceiptPatch, caller: CallerDep, sm: SessionmakerDep
) -> ResolutionOut:
    sent = body.model_dump(exclude_unset=True)
    return _out(await resolution.edit(sm, caller, ticket_id, sent))


class ReceiptVerifyIn(Body):
    order_id: str | None = Field(
        default=None, max_length=100, description="A Shopify order, from the orders card."
    )
    payment_id: str | None = Field(
        default=None, max_length=100, description="A Stripe payment, from the payments card."
    )


@router.post(
    "/tickets/{ticket_id}/resolution/verify",
    response_model=ResolutionOut,
    summary="Verify a receipt from the customer's Shopify order or Stripe payment",
    description="Send one of `order_id` or `payment_id` (as the ticket's orders or "
    "payments card lists them). CeDesk reads it from the installed app, read-only, and "
    "sets `result` to what it reports (payment and fulfilment state, or refunded), "
    "`verified` to shopify or stripe, and `verified_at`. Nothing is written to the shop "
    "or Stripe. Same 7-day window as changing the receipt.",
    responses={
        404: {"description": "No receipt, or not one of the customer's latest orders"},
        409: {"description": "The app isn't installed or couldn't be read; not resolved"},
        422: {"description": "Send exactly one of order_id and payment_id"},
    },
)
async def verify_receipt(
    ticket_id: UUID,
    body: ReceiptVerifyIn,
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
    sealer: SealerDep,
    http: HttpDep,
) -> ResolutionOut:
    found = await resolution_verify.verify(
        sm, sealer, http, caller, ticket_id,
        order_id=body.order_id, payment_id=body.payment_id, cevanos=_cevanos(request),
    )  # fmt: skip
    return _out(found)


class ResolutionCountsOut(BaseModel):
    resolved: int = Field(description="Tickets resolved in the period (still resolved).")
    with_receipt: int
    verified: int = Field(description="Verified by the agent, Shopify or Stripe.")
    verified_agent: int
    verified_shopify: int
    verified_stripe: int
    pending: int = Field(description="Receipts with a pending item.")
    notified: int = Field(description="Receipts saying the customer was told.")


class ResolutionReportRowOut(ResolutionCountsOut):
    id: UUID | None = Field(description="Null: no team, or unassigned.")
    name: str


class ResolutionReportOut(BaseModel):
    model_config = {"populate_by_name": True}

    from_: datetime = Field(alias="from", serialization_alias="from")
    to: datetime
    team_id: UUID | None
    total: ResolutionCountsOut
    teams: list[ResolutionReportRowOut]
    agents: list[ResolutionReportRowOut]


HEADER = ("Resolved", "With receipt", "Verified", "By agent", "Shopify", "Stripe",
          "Pending item", "Customer told")  # fmt: skip


def _tables(found: resolution.Report) -> list[Table]:
    def cells(c: dict[str, int]) -> tuple[int, ...]:
        return tuple(c[k] for k in resolution.COUNTS)

    return [
        Table("Summary", HEADER, [cells(found.total)]),
        Table("Teams", ("Team", *HEADER), [(r.name, *cells(r.counts)) for r in found.teams]),
        Table("Agents", ("Agent", *HEADER), [(r.name, *cells(r.counts)) for r in found.agents]),
    ]


@router.get(
    "/reports/resolutions",
    response_model=ResolutionReportOut,
    response_model_by_alias=True,
    summary="Resolutions: closed vs. with a receipt vs. verified (Supervisor, Admin)",
    description="Tickets resolved within [from, to) and still resolved (merged ones left "
    "out): how many have a receipt, how many were verified (by the agent, from Shopify or "
    "Stripe), have a pending item, or told the customer. Per team and per assignee.",
    responses=CSV_OK | REPORT_ERRORS | TEAM_ERRORS,
)
async def resolutions_report(
    caller: CallerDep,
    sm: SessionmakerDep,
    start: StartQuery = None,
    end: EndQuery = None,
    team_id: TeamQuery = None,
    fmt: FormatQuery = "json",
) -> ResolutionReportOut | Response:
    found = await resolution.report(sm, caller, start=start, end=end, team_id=team_id)
    if fmt == "csv":
        return csv_response(
            "resolutions", "CeDesk resolutions report", found.start, found.end,
            found.team_id, _tables(found),
        )  # fmt: skip
    return ResolutionReportOut(
        from_=found.start,
        to=found.end,
        team_id=found.team_id,
        total=ResolutionCountsOut(**found.total),
        teams=[ResolutionReportRowOut(id=r.id, name=r.name, **r.counts) for r in found.teams],
        agents=[ResolutionReportRowOut(id=r.id, name=r.name, **r.counts) for r in found.agents],
    )
