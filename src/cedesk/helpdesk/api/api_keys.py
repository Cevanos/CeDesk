"""API keys for other systems (md PL1). Admins make, list and revoke them while signed in;
a key itself can't use these routes. Send a key as `Authorization: Bearer cdk_...`."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain.api_keys import NAME_MAX, KeyAccess
from cedesk.helpdesk.service import api_keys
from cedesk.helpdesk.service.api_keys import KeyView

router = APIRouter(tags=["api keys"])

_ACCESS = (
    "read: GET only. read_write: also tickets, customers, notes, tags, tasks, time and "
    "customer messages in (/inbound/tickets). admin: also teams, ticket types, saved "
    "replies, macros, rules and the help center. A key acts as the Admin who made it, "
    "within that level; it never reaches sign-in, members, roles, security, SSO, Cevanos, "
    "imports, privacy or API keys, and never approves."
)


class KeyBody(Body):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX)
    ]
    access: KeyAccess = Field(description=_ACCESS)


class KeyOut(BaseModel):
    id: UUID
    name: str
    access: KeyAccess
    hint: str = Field(description="cdk_… and the key's last 4 characters.")
    created_at: datetime
    created_by: UUID = Field(description="The member the key acts as.")
    created_by_name: str
    last_used_at: datetime | None = Field(description="To the minute.")
    revoked_at: datetime | None
    revoked_by_name: str | None


class NewKeyOut(KeyOut):
    key: str = Field(description="The key. Shown this once: CeDesk keeps only its hash.")


def _out(view: KeyView) -> KeyOut:
    return KeyOut(**view.__dict__)


@router.get(
    "/api-keys",
    response_model=list[KeyOut],
    summary="API keys (Admin)",
    description="Live keys first, newest first; then revoked ones.",
    responses={403: {"description": "Admins only, signed in (not with a key)"}},
)
async def list_keys(caller: CallerDep, sm: SessionmakerDep) -> list[KeyOut]:
    return [_out(v) for v in await api_keys.all_keys(sm, caller)]


@router.post(
    "/api-keys",
    response_model=NewKeyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Make an API key (Admin; the key is shown once)",
    description=_ACCESS + " Audited (area api_keys, action created).",
    responses={403: {"description": "Admins only, signed in (not with a key)"}},
)
async def create_key(body: KeyBody, caller: CallerDep, sm: SessionmakerDep) -> NewKeyOut:
    made = await api_keys.create_key(sm, caller, name=body.name, access=body.access)
    return NewKeyOut(**made.view.__dict__, key=made.key)


@router.delete(
    "/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key (Admin)",
    description="The key answers 401 from now on. Revoking it again changes nothing. "
    "Audited (area api_keys, action revoked).",
    responses={404: {"description": "No such key"}},
)
async def revoke_key(key_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> Response:
    await api_keys.revoke_key(sm, caller, key_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
