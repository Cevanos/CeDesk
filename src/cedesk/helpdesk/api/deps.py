"""Request dependencies. Authenticated routes take `CallerDep`; none reads auth headers."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.service import auth
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import NotSignedIn

_bearer = HTTPBearer(auto_error=False, description="The token from POST /api/v1/auth/sign-in.")


def get_sessionmaker(request: Request) -> Sessionmaker:
    sm: Sessionmaker = request.app.state.sessionmaker
    return sm


SessionmakerDep = Annotated[Sessionmaker, Depends(get_sessionmaker)]


async def get_caller(
    sm: SessionmakerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Caller:
    if credentials is None or not credentials.credentials:
        raise NotSignedIn("Sign in first.")
    return await auth.resolve(sm, credentials.credentials.strip())


CallerDep = Annotated[Caller, Depends(get_caller)]
