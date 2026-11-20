"""Who may do what: the single source of truth for roles (md 6.6, A3).

Routes and services ask `access.can(action)` (or `require`); nothing else compares role
names. A member's `Access` comes from their built-in role, or from the custom role an
Admin built from five rights and a ticket scope.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID


class Role(StrEnum):
    admin = "admin"
    supervisor = "supervisor"
    agent = "agent"
    # Free seat: reads everything and adds notes, changes nothing.
    light_agent = "light_agent"
    # Built by an Admin (`hd_role`); its rights and scope come from there.
    custom = "custom"


class Action(StrEnum):
    read_ticket = "read_ticket"
    create_ticket = "create_ticket"
    reply = "reply"  # also macros, accept chat and resolve
    private_note = "private_note"
    change_fields = "change_fields"
    assign_self = "assign_self"
    assign_others = "assign_others"
    approve = "approve"
    reports = "reports"
    # Settings, imports, audit, sandbox, API keys, webhooks, apps.
    settings = "settings"
    list_members = "list_members"
    # Members, roles and security settings: Admins only, never given to a custom role,
    # so a role with `settings` can't make anyone (or itself) an Admin.
    manage_access = "manage_access"
    # One change on many tickets at once (plan 1.3). Each ticket is still checked as a
    # single edit (`change_fields`, `assign_*`, the role's team scope).
    bulk_edit = "bulk_edit"
    # Make, change and delete the workspace's shared ticket views (plan 1.2). Everyone who
    # reads tickets keeps personal views of their own.
    share_views = "share_views"
    # Email a ticket's public messages to the customer or another address (plan 1.6).
    send_transcript = "send_transcript"


class Scope(StrEnum):
    """Which tickets a role sees and edits (md A3)."""

    all = "all"
    team_edit = "team_edit"  # edits its team's tickets, views the rest
    team_only = "team_only"  # its team's tickets only; the rest are hidden (404)


_AGENT = frozenset(
    {
        Action.read_ticket,
        Action.create_ticket,
        Action.reply,
        Action.private_note,
        Action.change_fields,
        Action.assign_self,
        Action.list_members,
        Action.bulk_edit,
        Action.send_transcript,
    }
)
_SUPERVISOR = _AGENT | {
    Action.assign_others,
    Action.approve,
    Action.reports,
    Action.share_views,
}

ROLE_ACTIONS: dict[Role, frozenset[Action]] = {
    Role.light_agent: frozenset({Action.read_ticket, Action.private_note, Action.list_members}),
    Role.agent: _AGENT,
    Role.supervisor: _SUPERVISOR,
    Role.admin: _SUPERVISOR | {Action.settings, Action.manage_access},
}

# Every custom role reads, adds notes and changes fields (within its scope), so it may
# also change many at once: each ticket is checked as a single edit.
_CUSTOM_BASE = frozenset(
    {
        Action.read_ticket,
        Action.private_note,
        Action.change_fields,
        Action.list_members,
        Action.bulk_edit,
    }
)
# The five rights an Admin ticks (md A3), and what each one allows.
RIGHTS: dict[str, frozenset[Action]] = {
    "reply": frozenset(
        {Action.reply, Action.create_ticket, Action.assign_self, Action.send_transcript}
    ),
    "assign_any": frozenset({Action.assign_self, Action.assign_others}),
    "reports": frozenset({Action.reports}),
    "settings": frozenset({Action.settings, Action.share_views}),
    "approve": frozenset({Action.approve}),
}


@dataclass(frozen=True)
class Access:
    """What one member may do, and on which tickets."""

    role: Role
    actions: frozenset[Action]
    scope: Scope = Scope.all
    team_id: UUID | None = None  # the role's team, for a scoped role
    team_name: str | None = None
    role_name: str | None = None  # a custom role's name

    def can(self, action: Action) -> bool:
        return action in self.actions

    def sees(self, team_id: UUID | None) -> bool:
        """May see a ticket of this team (a `team_only` role sees only its team's)."""
        return self.scope is not Scope.team_only or team_id == self.team_id

    def edits(self, team_id: UUID | None) -> bool:
        """May change a ticket of this team (fields, replies, status, tools)."""
        return self.scope is Scope.all or team_id == self.team_id

    @property
    def label(self) -> str:
        return self.role_name or self.role.value.replace("_", " ").capitalize()

    @property
    def takes_tickets(self) -> bool:
        """Can be assigned tickets, has an agent status and is routed to."""
        return self.can(Action.reply)


def built_in(role: Role) -> Access:
    return Access(role=role, actions=ROLE_ACTIONS[role])


def custom(row: Mapping[Any, Any]) -> Access:
    """From an `hd_role` row (columns reply, assign_any, reports, settings, approve,
    scope, team_id, and the joined team_name and role_name)."""
    actions = set(_CUSTOM_BASE)
    for right, allows in RIGHTS.items():
        if row[right]:
            actions |= allows
    return Access(
        role=Role.custom,
        actions=frozenset(actions),
        scope=Scope(row["scope"]),
        team_id=row["team_id"],
        team_name=row.get("team_name"),
        role_name=str(row["role_name"]),
    )


def access_of(role: Role, custom_row: Mapping[Any, Any] | None) -> Access:
    if role is Role.custom:
        if custom_row is None:
            raise ValueError("A custom role needs its hd_role row.")
        return custom(custom_row)
    return built_in(role)


def can(role: Role, action: Action) -> bool:
    """For a built-in role. A custom role's rights are on its `Access`."""
    return action in ROLE_ACTIONS.get(role, frozenset())


def view_only_reason(access: Access, team_id: UUID | None) -> str | None:
    """Why this member can't change the ticket (md A5), or None when they can."""
    if not access.can(Action.change_fields):
        return f"Your role {access.label} can read tickets and add notes only."
    if not access.edits(team_id):
        return f"Your role {access.label} can edit {access.team_name or 'its team'} tickets only."
    return None
