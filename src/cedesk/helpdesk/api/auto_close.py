"""Auto-close inactive tickets (feature-gap plan 1.4): the workspace's setting."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import auto_close as rules
from cedesk.helpdesk.service import auto_close

router = APIRouter(prefix="/auto-close", tags=["auto-close"])

Notice = Annotated[str, StringConstraints(max_length=rules.NOTICE_MAX)]


class AutoCloseOut(BaseModel):
    enabled: bool
    after_days: int
    notice: str | None = Field(description="Sent to the customer as a reply before closing.")
    updated_at: datetime | None
    updated_by_name: str | None


class AutoCloseIn(Body):
    enabled: bool
    after_days: int = Field(
        ge=rules.MIN_DAYS,
        le=rules.MAX_DAYS,
        description="Days a Pending ticket waits with no reply from the customer.",
    )
    notice: Notice | None = Field(
        default=None,
        description="Optional message to the customer, sent as a reply from the workspace "
        "just before the ticket is resolved (placeholders such as {{customer.first_name}} "
        "work). Empty or null: none.",
    )


DESCRIPTION = (
    "Once a day, every Pending ticket with no reply from the customer (and no other change) "
    "for `after_days` is resolved, with a line on the ticket and an audit entry; the notice "
    "goes out first if set, and the usual CSAT survey after. Never closed: snoozed tickets, "
    "chat handovers waiting for Accept, tickets with an approval waiting, an open child "
    "ticket, merged and imported tickets. Off by default."
)


@router.get("", response_model=AutoCloseOut, summary="The auto-close setting (Settings)")
async def get_setting(caller: CallerDep, sm: SessionmakerDep) -> AutoCloseOut:
    return AutoCloseOut(**(await auto_close.get_setting(sm, caller)).__dict__)


@router.put(
    "",
    response_model=AutoCloseOut,
    summary="Turn auto-close on or off, set its days and notice (Settings; audited)",
    description=DESCRIPTION,
)
async def set_setting(body: AutoCloseIn, caller: CallerDep, sm: SessionmakerDep) -> AutoCloseOut:
    saved = await auto_close.set_setting(
        sm, caller, enabled=body.enabled, after_days=body.after_days, notice=body.notice
    )
    return AutoCloseOut(**saved.__dict__)
