"""A custom role's ticket scope (md A3), checked on every route that opens one ticket.

`team_only`: tickets of other teams are hidden, so they answer 404 like a ticket that
doesn't exist. `team_edit`: they can be read (and noted) but not changed: 403 with the
reason the ticket page shows (A5). Built-in roles see and edit every ticket, so for them
this costs nothing. Lists filter the same way in their queries.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Scope, view_only_reason
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service.errors import Forbidden, NotFound

NOT_FOUND = "Ticket not found."


async def check(s: AsyncSession, caller: Caller, ticket_id: UUID, *, edit: bool) -> None:
    """Raises when the caller's scope hides the ticket, or (`edit`) only lets them view
    it. A ticket that doesn't exist is left to the caller's own 404."""
    access = caller.access
    if access.scope is Scope.all:
        return
    found, team_id = await ticket_repo.ticket_team(s, ticket_id)
    if not found:
        return
    if not access.sees(team_id):
        raise NotFound(NOT_FOUND)
    if edit and not access.edits(team_id):
        raise Forbidden(view_only_reason(access, team_id) or "You can't change this ticket.")


def visible_team(caller: Caller) -> UUID | None:
    """For list queries: the only team whose tickets a `team_only` role sees, else None
    (no filter)."""
    access = caller.access
    return access.team_id if access.scope is Scope.team_only else None
