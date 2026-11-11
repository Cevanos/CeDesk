"""Triage and redaction settings (md I1, I5): the keyword lists behind each ticket's topic,
and whether card numbers are removed from customer messages. Admin only."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import triage as rules
from cedesk.helpdesk.service import triage
from cedesk.helpdesk.service.errors import Invalid

router = APIRouter(tags=["triage"])

Keyword = Annotated[str, StringConstraints(max_length=rules.KEYWORD_MAX)]


class TopicIn(Body):
    name: str = Field(description=f"One of: {', '.join(rules.TOPICS)}")
    keywords: list[Keyword] = Field(max_length=rules.MAX_KEYWORDS)


class TriageSettingsIn(Body):
    topics: list[TopicIn] | None = Field(
        description="Each of the four topics once, with its keywords. null: back to the "
        "built-in lists. A ticket with none of the words is "
        f'"{rules.GENERAL}".',
        max_length=len(rules.TOPICS),
    )
    redact_cards: bool = Field(
        default=True,
        description="Remove card numbers from customer messages before saving. "
        "Removed numbers can't be brought back.",
    )


class TopicOut(BaseModel):
    name: str
    keywords: list[str]


class TriageSettingsOut(BaseModel):
    topics: list[TopicOut] = Field(description="In tie order: on a tie the first one wins.")
    general_topic: str = Field(description="The topic when no keyword matches.")
    keywords_are_default: bool
    redact_cards: bool
    updated_at: datetime | None
    updated_by_name: str | None


def _out(found: triage.TriageSettings) -> TriageSettingsOut:
    return TriageSettingsOut(
        topics=[TopicOut(name=t, keywords=found.keywords[t]) for t in rules.TOPICS],
        general_topic=rules.GENERAL,
        keywords_are_default=found.keywords_are_default,
        redact_cards=found.redact_cards,
        updated_at=found.updated_at,
        updated_by_name=found.updated_by_name,
    )


@router.get(
    "/triage-settings",
    response_model=TriageSettingsOut,
    summary="Topic keywords and card redaction (Admin)",
    responses={403: {"description": "Admins only"}},
)
async def get_settings(caller: CallerDep, sm: SessionmakerDep) -> TriageSettingsOut:
    return _out(await triage.get_settings(sm, caller))


@router.put(
    "/triage-settings",
    response_model=TriageSettingsOut,
    summary="Save topic keywords and card redaction (Admin)",
    description="New keywords apply from the next customer message; a ticket's topic is "
    "worked out again when its customer writes again.",
    responses={
        403: {"description": "Admins only"},
        422: {"description": "Not each topic once, or a keyword too long"},
    },
)
async def save_settings(
    body: TriageSettingsIn, caller: CallerDep, sm: SessionmakerDep
) -> TriageSettingsOut:
    keywords = None
    if body.topics is not None:
        keywords = {t.name: t.keywords for t in body.topics}
        if len(keywords) != len(body.topics):
            raise Invalid("Each topic once.")
    return _out(
        await triage.save_settings(sm, caller, keywords=keywords, redact_cards=body.redact_cards)
    )
