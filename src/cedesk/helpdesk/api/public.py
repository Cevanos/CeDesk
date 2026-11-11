"""Public routes with no sign-in (md 2B "Public"): the web form channel (C2) and the
help center (P6). The CSAT link routes are in `portal.py`.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import FormLimit, PublicLimit, SessionmakerDep, VoteLimit
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.api.ticket_schemas import FieldDefOut, FieldValue
from cedesk.helpdesk.domain.paging import DEFAULT_LIMIT, MAX_LIMIT
from cedesk.helpdesk.domain.portal import FORM_RECEIVED
from cedesk.helpdesk.service import articles, web_form

router = APIRouter(tags=["public"])


class FormTypeOut(BaseModel):
    id: UUID
    name: str
    fields: list[FieldDefOut]


class FormOut(BaseModel):
    workspace_name: str
    types: list[FormTypeOut] = Field(description="Show a type picker when there are any.")


class FormIn(Body):
    name: Annotated[str, StringConstraints(max_length=100)] = ""
    email: Annotated[str, StringConstraints(min_length=3, max_length=320)]
    message: Annotated[str, StringConstraints(min_length=1, max_length=50_000)] = Field(
        description="Its first 60 characters become the subject."
    )
    type_id: UUID | None = None
    fields: dict[str, FieldValue] = Field(default={}, max_length=30)
    website: Annotated[str, StringConstraints(max_length=500)] = Field(
        default="",
        description="Honeypot: render it hidden (off-screen, `tabindex=-1`, "
        '`autocomplete="off"`) and always send it empty. Filled in: nothing is made.',
    )


class FormReceivedOut(BaseModel):
    message: str = Field(description="Show this to the customer.")


_NO_FORM: dict[int | str, dict[str, Any]] = {
    404: {"description": "Unknown workspace, or its web form is off"}
}


@router.get(
    "/public/{workspace}/form",
    response_model=FormOut,
    summary="The web form: ticket types and their fields (no sign-in)",
    responses=_NO_FORM,
)
async def get_form(workspace: str, sm: SessionmakerDep) -> FormOut:
    found = await web_form.form(sm, workspace)
    return FormOut(
        workspace_name=found.workspace_name,
        types=[FormTypeOut(id=t["id"], name=t["name"], fields=t["fields"]) for t in found.types],
    )


@router.post(
    "/public/{workspace}/form",
    response_model=FormReceivedOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send the web form: makes a ticket (no sign-in)",
    description="Channel `web`; rules, auto-assignment and SLA run as for any new "
    "ticket. Agent replies reach the customer by email. No ticket number comes back "
    "(anyone can post here).",
    responses={
        **_NO_FORM,
        422: {"description": "Bad email, empty message, unknown type, or a field wrong"},
        429: {"description": "Too many posts from this IP, or over 5 an hour from this email"},
    },
    dependencies=[FormLimit],
)
async def send_form(workspace: str, body: FormIn, sm: SessionmakerDep) -> FormReceivedOut:
    await web_form.submit(
        sm,
        workspace,
        name=body.name,
        email=body.email,
        message=body.message,
        type_id=body.type_id,
        fields=body.fields,
        honeypot=body.website,
    )
    return FormReceivedOut(message=FORM_RECEIVED)


# Help center (md P6): published articles only.


class PublicCategoryOut(BaseModel):
    id: UUID
    name: str
    article_count: int = Field(description="Published articles in it.")


class PublicArticleRow(BaseModel):
    id: UUID
    title: str
    category_id: UUID | None
    category_name: str | None
    updated_at: datetime


class HelpCenterOut(BaseModel):
    workspace_name: str
    categories: list[PublicCategoryOut] = Field(description="Only ones with articles.")
    articles: list[PublicArticleRow] = Field(description="Newest first; with `q`, best first.")
    next_cursor: str | None = Field(description="null with `q` (the best 20 only).")


class PublicArticleOut(PublicArticleRow):
    body: str = Field(description="Markdown. Render it without raw HTML.")


class VoteIn(Body):
    helpful: bool
    visitor: Annotated[str, StringConstraints(min_length=8, max_length=100)] = Field(
        description="A random id the page keeps for this browser (e.g. a UUID in "
        "localStorage). One vote per visitor per article; voting again changes it."
    )


_NO_CENTER: dict[int | str, dict[str, Any]] = {404: {"description": "Unknown workspace"}}


@router.get(
    "/public/{workspace}/articles",
    response_model=HelpCenterOut,
    summary="The help center: categories, articles, search (no sign-in)",
    responses={**_NO_CENTER, 429: {"description": "Too many requests from this IP"}},
    dependencies=[PublicLimit],
)
async def help_center(
    workspace: str,
    sm: SessionmakerDep,
    q: Annotated[str | None, Query(max_length=200, description="Search words")] = None,
    category_id: UUID | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> HelpCenterOut:
    found = await articles.help_center(
        sm, workspace, q=q, category_id=category_id, cursor=cursor, limit=limit
    )
    return HelpCenterOut(
        workspace_name=found.workspace_name,
        categories=[PublicCategoryOut.model_validate(dict(c)) for c in found.categories],
        articles=[PublicArticleRow.model_validate(dict(a)) for a in found.page.articles],
        next_cursor=found.page.next_cursor,
    )


@router.get(
    "/public/{workspace}/articles/{article_id}",
    response_model=PublicArticleOut,
    summary="One published article (no sign-in)",
    responses={
        404: {"description": "Unknown workspace, or not a published article"},
        429: {"description": "Too many requests from this IP"},
    },
    dependencies=[PublicLimit],
)
async def public_article(workspace: str, article_id: UUID, sm: SessionmakerDep) -> PublicArticleOut:
    row = await articles.public_article(sm, workspace, article_id)
    return PublicArticleOut.model_validate(dict(row))


@router.post(
    "/public/{workspace}/articles/{article_id}/vote",
    status_code=status.HTTP_204_NO_CONTENT,
    summary='"Was this helpful?" (no sign-in)',
    responses={404: {"description": "Not a published article"}, 429: {"description": "Too many"}},
    dependencies=[VoteLimit],
)
async def vote(workspace: str, article_id: UUID, body: VoteIn, sm: SessionmakerDep) -> None:
    await articles.vote(sm, workspace, article_id, helpful=body.helpful, visitor=body.visitor)
