"""Replay (differentiator plan #3): a rule or workflow tried on past tickets, Admin only.
Started from Settings > Rules (`POST /rules/replay`, `POST /rules/{id}/replay`) or a
workflow (`POST /flows/{id}/replay`); read and cancelled here."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, require_settings
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import replay as rules
from cedesk.helpdesk.repo.replay import Row
from cedesk.helpdesk.service import replay

router = APIRouter(tags=["rules"], dependencies=[Depends(require_settings)])


class ReplayBody(Body):
    ticket_limit: int = Field(
        default=rules.LIMIT_DEFAULT, ge=rules.LIMIT_MIN, le=rules.LIMIT_MAX,
        description="How many of the newest tickets to try it on (1-1000).",
    )  # fmt: skip


class ChangeOut(BaseModel):
    field: str = Field(description="status, priority, type, team or tags (tags it would add).")
    before: str | None
    after: str | None


class SampleOut(BaseModel):
    ticket_id: UUID
    number: int
    subject: str = Field(description="The first 120 characters.")
    changes: list[ChangeOut]
    said: list[str] = Field(
        description='Every line it would write, effects too: "Tag vip", "Email sent", ...'
    )


class ConflictOut(BaseModel):
    ticket_id: UUID
    number: int
    field: Literal["status", "priority", "type", "team"]
    tested_value: str | None
    other_rule_id: UUID
    other_rule: str
    other_value: str | None
    winner: Literal["tested", "other"] = Field(
        description="Which one the ticket would end with: the later one in run order."
    )


class ReplayOut(BaseModel):
    id: UUID
    kind: Literal["rule", "flow"]
    target_id: UUID | None = Field(description="The saved rule or workflow; null: unsaved.")
    target_version: int | None = Field(description="The workflow version tried.")
    name: str
    ticket_limit: int
    state: Literal["queued", "running", "done", "failed", "cancelled"]
    summary: str = Field(description="One line for the screen.")
    checked: int = Field(description="Tickets tried so far.")
    matched: int = Field(description="Tickets its conditions matched (a workflow: it acted).")
    would_change: int = Field(description="Tickets it would change or act on.")
    would_wait: int = Field(description="A workflow: tickets it would wait on.")
    conflicts: int
    samples: list[SampleOut] = Field(description="Up to 200 tickets it would change.")
    conflict_samples: list[ConflictOut] = Field(description="Up to 200 conflicts.")
    error: str | None
    requested_by_name: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def replay_out(row: Row) -> ReplayOut:
    data: dict[str, Any] = dict(row)
    data["summary"] = rules.summary(
        row["state"], row["checked"], row["ticket_limit"], row["would_change"], row["conflicts"]
    )
    return ReplayOut.model_validate(data)


@router.get(
    "/replays/{replay_id}",
    response_model=ReplayOut,
    summary="A replay's progress and what it found (Admin)",
)
async def get_replay(replay_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> ReplayOut:
    return replay_out(await replay.get(sm, caller, replay_id))


@router.post(
    "/replays/{replay_id}/cancel",
    response_model=ReplayOut,
    summary="Stop a queued or running replay; what it found so far stays (Admin)",
    description="409 when it has already finished.",
)
async def cancel_replay(replay_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> ReplayOut:
    return replay_out(await replay.cancel(sm, caller, replay_id))
