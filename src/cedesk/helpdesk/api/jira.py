"""Jira issues on tickets (md X10). Install the Jira app first (`PUT /apps/jira`)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SealerDep, SessionmakerDep, SettingsDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import jira as rules
from cedesk.helpdesk.service import apps, jira
from cedesk.helpdesk.service.jira import JiraIssue, PostFactory

router = APIRouter(tags=["jira"])


def get_post(request: Request) -> PostFactory:
    post: PostFactory = request.app.state.app_post
    return post


PostDep = Annotated[PostFactory, Depends(get_post)]


class NewIssueIn(Body):
    summary: str | None = Field(
        default=None, max_length=rules.SUMMARY_MAX, description="Default: the ticket's subject."
    )
    description: str | None = Field(
        default=None,
        max_length=rules.DESCRIPTION_MAX,
        description="What the Jira team needs to know. A link back to the ticket is added; "
        "the customer's messages are not sent unless you put them here.",
    )


class LinkIssueIn(Body):
    key: str = Field(max_length=30, description="An issue that exists, e.g. SUP-123.")


class JiraIssueOut(BaseModel):
    key: str
    url: str
    made_here: bool = Field(description="Created from CeDesk; false: an existing one linked.")
    created_at: datetime
    created_by_name: str | None


def _out(issue: JiraIssue) -> JiraIssueOut:
    return JiraIssueOut(**issue.__dict__)


_ERRORS = {
    403: {"description": "Can't reply, or the ticket is another team's (view only)"},
    404: {"description": "No such ticket, or no such issue in Jira"},
    409: {"description": "Jira isn't installed, the issue is already on the ticket, or 10 are"},
    422: {"description": "Jira refused (bad key, no access to the project)"},
    503: {"description": "Jira didn't answer"},
}


@router.get("/tickets/{ticket_id}/jira", response_model=list[JiraIssueOut],
            summary="Jira issues on a ticket")  # fmt: skip
async def list_issues(
    ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep
) -> list[JiraIssueOut]:
    return [_out(i) for i in await jira.issues(sm, caller, ticket_id)]


@router.post(
    "/tickets/{ticket_id}/jira",
    response_model=JiraIssueOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a Jira issue from a ticket",
    description="In the app's project, type Task. The ticket gets a system line.",
    responses=_ERRORS,  # type: ignore[arg-type]
)
async def create_issue(
    ticket_id: UUID, body: NewIssueIn, caller: CallerDep, sm: SessionmakerDep, sealer: SealerDep,
    post: PostDep, settings: SettingsDep,
) -> JiraIssueOut:  # fmt: skip
    made = await jira.create_issue(
        sm, sealer, post, caller, ticket_id, summary=body.summary, description=body.description,
        app_url=settings.app_url, bound=apps.jira_bound,
    )  # fmt: skip
    return _out(made)


@router.post(
    "/tickets/{ticket_id}/jira/link",
    response_model=JiraIssueOut,
    status_code=status.HTTP_201_CREATED,
    summary="Link an existing Jira issue to a ticket",
    responses=_ERRORS,  # type: ignore[arg-type]
)
async def link_issue(
    ticket_id: UUID, body: LinkIssueIn, caller: CallerDep, sm: SessionmakerDep, sealer: SealerDep,
    post: PostDep,
) -> JiraIssueOut:  # fmt: skip
    linked = await jira.link_issue(
        sm, sealer, post, caller, ticket_id, key=body.key, bound=apps.jira_bound
    )
    return _out(linked)


@router.delete(
    "/tickets/{ticket_id}/jira/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Take a Jira issue off a ticket (it stays in Jira)",
    responses={404: {"description": "That issue isn't on this ticket"}},
)
async def unlink_issue(
    ticket_id: UUID, key: str, caller: CallerDep, sm: SessionmakerDep
) -> Response:
    await jira.unlink_issue(sm, caller, ticket_id, key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
