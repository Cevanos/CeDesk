"""Cevanos AI features (md section 16): summary, improve writing, translate, suggested
reply, and the AI card in Reports. Only when the workspace has a Cevanos connection."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from cedesk.cevanos_client import Cevanos
from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.reports import FormatQuery, csv_response
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import ai as rules
from cedesk.helpdesk.domain.report_csv import Table
from cedesk.helpdesk.service import ai

router = APIRouter(tags=["ai"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "An API key (AI help is for people), or the role can't"},
    409: {
        "description": "`cevanos_not_connected` (connect Cevanos in Settings), "
        "`cevanos_scope_missing` (the key lacks a scope; the message names it) or "
        "`cevanos_refused`"
    },
    503: {"description": "Cevanos is down or slow: try again"},
}
_TICKET_ERRORS = _ERRORS | {404: {"description": "No such ticket, or another team's"}}
_UNTRANSLATED: dict[int | str, dict[str, Any]] = {
    503: {
        "description": "Cevanos is down or slow, or sent the text back untranslated "
        '("Cevanos didn\'t translate this. Try again later.")'
    }
}
_ONLY = "Only when Cevanos is connected (`GET /me` `ai`); nothing is stored."


def _cevanos(request: Request) -> Cevanos:
    found: Cevanos = request.app.state.cevanos
    return found


class SummaryOut(BaseModel):
    summary: str


class TextOut(BaseModel):
    text: str


class ImproveIn(Body):
    text: str = Field(min_length=1, max_length=rules.TEXT_MAX)
    tone: str | None = Field(
        default=None, description=f"One of {', '.join(rules.TONES)}; default {rules.TONES[0]}."
    )


class TranslateIn(Body):
    text: str = Field(min_length=1, max_length=rules.TEXT_MAX)
    to: str = Field(
        max_length=20,
        description="The language to translate into, as a code: en, hi, pt-BR. A ticket's "
        "`language` (from triage) says what the customer writes in.",
    )


class ImprovedOut(BaseModel):
    text: str
    note: str | None = Field(
        default=None,
        description='Show it to the agent when set: "No changes suggested. Try another '
        'tone." (Cevanos sent the text back as it was; `text` is that text).',
    )


class TranslationOut(BaseModel):
    text: str
    from_: str | None = Field(
        alias="from", serialization_alias="from", description="The language found, e.g. hi."
    )
    note: str | None = Field(
        default=None,
        description='Show it to the agent when set, instead of "Translated from …": '
        '"This is already in English." or "Cevanos found nothing to translate in this '
        'text." (`text` is the text as it was). A text Cevanos should have translated but '
        "sent back as it was is a 503 instead.",
    )

    model_config = {"populate_by_name": True}


@router.post(
    "/tickets/{ticket_id}/summary",
    response_model=SummaryOut,
    summary="Summarise the ticket (Cevanos AI)",
    description="Cevanos summarises the conversation (internal notes too; never system "
    "lines; card numbers taken out). Shown to the agent, never stored. " + _ONLY,
    responses=_TICKET_ERRORS,
)
async def summarise(
    ticket_id: UUID, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> SummaryOut:
    return SummaryOut(summary=await ai.summary(sm, _cevanos(request), caller, ticket_id))


@router.post(
    "/tickets/{ticket_id}/draft",
    response_model=TextOut,
    summary="Suggest a reply (Cevanos AI)",
    description="A suggested reply for the reply box, written from the conversation (no "
    "internal notes) by the chat's Cevanos AI agent when the ticket came from a Cevanos "
    "chat. It is **never sent by itself**: the agent edits and sends it. Needs `reply`. " + _ONLY,
    responses=_TICKET_ERRORS,
)
async def suggest(
    ticket_id: UUID, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> TextOut:
    return TextOut(text=await ai.draft(sm, _cevanos(request), caller, ticket_id))


@router.post(
    "/assist/improve",
    response_model=ImprovedOut,
    summary="Improve the writing of a reply or note (Cevanos AI)",
    description="Returns the text rewritten in the tone, for the agent to edit and send; "
    "`note` says when nothing changed. " + _ONLY,
    responses=_ERRORS,
)
async def improve(
    body: ImproveIn, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> ImprovedOut:
    found = await ai.improve(sm, _cevanos(request), caller, text=body.text, tone=body.tone)
    return ImprovedOut(text=found.text, note=found.note)


@router.post(
    "/assist/translate",
    response_model=TranslationOut,
    summary="Translate a message or a reply (Cevanos AI)",
    description="Returns the text in `to` and the language it was in; `note` says when "
    "there was nothing to translate. " + _ONLY,
    responses=_ERRORS | _UNTRANSLATED,
)
async def translate(
    body: TranslateIn, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> TranslationOut:
    found = await ai.translate(sm, _cevanos(request), caller, text=body.text, to=body.to)
    return TranslationOut(text=found.text, from_=found.source, note=found.note)


class AiNumbersOut(BaseModel):
    conversations: int = Field(description="Cevanos chats in the period.")
    handed_over: int = Field(description="Of them, handed over to a person.")
    handover_rate: float | None = Field(description="0-1; null with no chats.")
    ai_resolved: int = Field(description="Resolved by the AI alone.")
    ai_resolved_rate: float | None = Field(description="0-1; null with no chats.")
    ai_csat: float | None = Field(description="Average rating of AI answers, 1-5.")
    ai_csat_responses: int


class AiReportOut(BaseModel):
    from_: datetime = Field(alias="from", serialization_alias="from")
    to: datetime
    ai: AiNumbersOut

    model_config = {"populate_by_name": True}


@router.get(
    "/reports/ai",
    response_model=AiReportOut,
    summary="The AI card: handover rate, AI-resolved, CSAT of AI answers (Cevanos)",
    description="Read live from Cevanos for the period (default: the last 30 days), for "
    "the whole workspace. Needs `reports`. " + _ONLY,
    responses=_ERRORS | {422: {"description": "A bad period"}},
)
async def ai_report(
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
    start: Annotated[
        datetime | None,
        Query(alias="from", description="Default: 30 days before `to`. With a time zone."),
    ] = None,
    end: Annotated[datetime | None, Query(alias="to", description="Default: now.")] = None,
    fmt: FormatQuery = "json",
) -> AiReportOut | Response:
    found = await ai.numbers(sm, _cevanos(request), caller, start=start, end=end)
    if fmt == "csv":
        n = found.numbers
        table = Table(
            "AI",
            ("Metric", "Value"),
            [
                ("Cevanos chats", n.conversations),
                ("Handed over to a person", n.handed_over),
                ("Handover rate (0-1)", n.handover_rate),
                ("Resolved by the AI alone", n.ai_resolved),
                ("AI resolved rate (0-1)", n.ai_resolved_rate),
                ("CSAT of AI answers (1-5)", n.ai_csat),
                ("CSAT responses", n.ai_csat_responses),
            ],
        )
        return csv_response("ai", "CeDesk AI report", found.start, found.end, None, [table])
    return AiReportOut(from_=found.start, to=found.end, ai=AiNumbersOut(**found.numbers.__dict__))
