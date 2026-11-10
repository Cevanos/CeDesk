"""Saved replies (md W19): the `/` list in the reply box, and its settings page."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.workflow_schemas import SavedReplyIn, SavedReplyOut, SavedReplyPatch
from cedesk.helpdesk.service import saved_replies

router = APIRouter(tags=["saved replies"])


@router.get(
    "/saved-replies",
    response_model=list[SavedReplyOut],
    summary="Saved replies by shortcut; `q` narrows to shortcuts starting with it",
)
async def list_replies(
    caller: CallerDep,
    sm: SessionmakerDep,
    q: Annotated[
        str | None,
        Query(max_length=33, description="What was typed after `/`, e.g. `ref`."),
    ] = None,
) -> list[SavedReplyOut]:
    rows = await saved_replies.list_replies(sm, caller, prefix=q)
    return [SavedReplyOut.model_validate(dict(r)) for r in rows]


@router.post(
    "/saved-replies",
    response_model=SavedReplyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a saved reply (Admin)",
)
async def create_reply(body: SavedReplyIn, caller: CallerDep, sm: SessionmakerDep) -> SavedReplyOut:
    row = await saved_replies.create_reply(sm, caller, shortcut=body.shortcut, body=body.body)
    return SavedReplyOut.model_validate(dict(row))


@router.patch(
    "/saved-replies/{reply_id}",
    response_model=SavedReplyOut,
    summary="Change a saved reply's shortcut or text (Admin)",
)
async def change_reply(
    reply_id: UUID, body: SavedReplyPatch, caller: CallerDep, sm: SessionmakerDep
) -> SavedReplyOut:
    row = await saved_replies.change_reply(
        sm, caller, reply_id, shortcut=body.shortcut, body=body.body
    )
    return SavedReplyOut.model_validate(dict(row))


@router.delete(
    "/saved-replies/{reply_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a saved reply (Admin)",
)
async def delete_reply(reply_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await saved_replies.delete_reply(sm, caller, reply_id)
