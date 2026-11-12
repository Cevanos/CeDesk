"""Sandbox (md S10): copy, edit, compare, try on samples, deploy, delete."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy.engine import RowMapping

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.domain.sandbox import Diff, Outcome, Section
from cedesk.helpdesk.service import sandbox

router = APIRouter(tags=["sandbox"])


class SandboxOut(BaseModel):
    rules: list[dict[str, Any]] = Field(
        description="In order. {id, name, enabled, event, conditions, actions}; `id` null for "
        "a rule added in the sandbox. Same shapes as /rules."
    )
    macros: list[dict[str, Any]] = Field(description="In order. {id, name, steps}.")
    saved_replies: list[dict[str, Any]] = Field(description="{id, shortcut, body}.")
    created_at: datetime
    updated_at: datetime
    updated_by_name: str | None

    @classmethod
    def of(cls, row: RowMapping) -> "SandboxOut":
        config = row["config"]
        return cls(
            rules=config.get("rules", []),
            macros=config.get("macros", []),
            saved_replies=config.get("saved_replies", []),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            updated_by_name=row["updated_by_name"],
        )


class SectionBody(BaseModel):
    items: list[dict[str, Any]] = Field(
        description="The whole section, in order. Keep an item's `id` to change that live "
        "item, leave it out to add one, drop an item to remove it."
    )


class ChangedOut(BaseModel):
    id: UUID
    label: str
    changes: dict[str, list[Any]] = Field(description="{field: [live, sandbox]}")


class DiffOut(BaseModel):
    added: list[str]
    changed: list[ChangedOut]
    removed: list[str]
    order_changed: bool

    @classmethod
    def of(cls, d: Diff) -> "DiffOut":
        return cls(
            added=d.added,
            changed=[
                ChangedOut(id=UUID(c.id), label=c.label, changes=c.changes) for c in d.changed
            ],
            removed=d.removed,
            order_changed=d.order_changed,
        )


class CompareOut(BaseModel):
    rules: DiffOut
    macros: DiffOut
    saved_replies: DiffOut
    same: bool = Field(description="Nothing to deploy.")

    @classmethod
    def of(cls, diffs: dict[Section, Diff]) -> "CompareOut":
        return cls(
            rules=DiffOut.of(diffs[Section.rules]),
            macros=DiffOut.of(diffs[Section.macros]),
            saved_replies=DiffOut.of(diffs[Section.saved_replies]),
            same=all(d.empty for d in diffs.values()),
        )


class ResultOut(BaseModel):
    lines: list[str] = Field(description='"Rule applied: Refunds → Team Billing", one per rule.')
    type: str | None
    team: str | None
    priority: str
    status: str
    tags: list[str]


class TriedOut(BaseModel):
    sample: str
    text: str
    channel: str
    live: ResultOut
    sandbox: ResultOut
    same: bool


def _result(o: Outcome, trial: sandbox.Trial) -> ResultOut:
    return ResultOut(
        lines=o.lines,
        type=trial.types.get(o.state.type_id) if o.state.type_id else None,
        team=trial.teams.get(o.state.team_id) if o.state.team_id else None,
        priority=o.state.priority.value,
        status=o.state.status.value,
        tags=list(o.state.tags),
    )


@router.get("/sandbox", response_model=SandboxOut, summary="The sandbox (404 when none)")
async def get_sandbox(caller: CallerDep, sm: SessionmakerDep) -> SandboxOut:
    return SandboxOut.of(await sandbox.get(sm, caller))


@router.post(
    "/sandbox",
    status_code=status.HTTP_201_CREATED,
    response_model=SandboxOut,
    summary="Make a sandbox: a copy of the live rules, macros and saved replies",
    description="409 when the workspace already has one.",
)
async def create_sandbox(caller: CallerDep, sm: SessionmakerDep) -> SandboxOut:
    return SandboxOut.of(await sandbox.create(sm, caller))


@router.put(
    "/sandbox/{section}",
    response_model=SandboxOut,
    summary="Replace one section of the sandbox",
    description="Each item is checked as on the live settings page (422 names the item). "
    "Nothing live changes until Deploy.",
)
async def set_section(
    section: Section, body: SectionBody, caller: CallerDep, sm: SessionmakerDep
) -> SandboxOut:
    return SandboxOut.of(await sandbox.set_section(sm, caller, section, body.items))


@router.get("/sandbox/compare", response_model=CompareOut, summary="What Deploy would change")
async def compare(caller: CallerDep, sm: SessionmakerDep) -> CompareOut:
    return CompareOut.of(await sandbox.compare(sm, caller))


@router.post(
    "/sandbox/try",
    response_model=list[TriedOut],
    summary="Try the sandbox rules on sample tickets",
    description="Runs the live rules and the sandbox rules on built-in sample new tickets "
    "(email refund, angry chat, web order question, phone invoice, portal thanks) and "
    "shows both results side by side. Nothing is saved.",
)
async def try_rules(caller: CallerDep, sm: SessionmakerDep) -> list[TriedOut]:
    trial = await sandbox.try_rules(sm, caller)
    out = []
    for t in trial.results:
        live, box = _result(t.live, trial), _result(t.sandbox, trial)
        out.append(
            TriedOut(
                sample=t.sample.name,
                text=t.sample.text,
                channel=t.sample.channel.value,
                live=live,
                sandbox=box,
                same=live.model_dump(exclude={"lines"}) == box.model_dump(exclude={"lines"}),
            )
        )
    return out


@router.post(
    "/sandbox/deploy",
    response_model=CompareOut,
    summary="Deploy the sandbox over the live settings",
    description="All or nothing: every added, changed and removed rule, macro and saved "
    "reply is saved in one go, each with its own audit entry. The sandbox then holds a "
    "fresh copy of the live settings. Returns what changed. 422 when there is nothing to "
    "deploy or an item no longer fits (e.g. its team was deleted).",
)
async def deploy(caller: CallerDep, sm: SessionmakerDep) -> CompareOut:
    return CompareOut.of(await sandbox.deploy(sm, caller))


@router.delete("/sandbox", status_code=status.HTTP_204_NO_CONTENT, summary="Delete the sandbox")
async def delete_sandbox(caller: CallerDep, sm: SessionmakerDep) -> None:
    await sandbox.delete(sm, caller)
