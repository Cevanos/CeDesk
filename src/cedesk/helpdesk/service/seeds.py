"""Puts the default teams and ticket types into a new workspace."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain import seeds
from cedesk.helpdesk.domain.tickets import check_definitions
from cedesk.helpdesk.repo import catalog


async def apply(s: AsyncSession, workspace_id: UUID) -> None:
    """Runs inside the transaction that creates the workspace, so a workspace never
    exists half-seeded."""
    for team in seeds.TEAMS:
        await catalog.insert_team(
            s, workspace_id, team.name, team.auto_assign, team.max_open_per_agent
        )
    for ticket_type in seeds.TICKET_TYPES:
        check_definitions(ticket_type.fields)
        fields = [
            {
                "key": f.key,
                "label": f.label,
                "kind": f.kind.value,
                "options": list(f.options),
                "required": f.required,
            }
            for f in ticket_type.fields
        ]
        await catalog.insert_type(s, workspace_id, ticket_type.name, fields, None)
