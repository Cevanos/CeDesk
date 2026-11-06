"""Who may do what: the single source of truth for roles.

Routes and services ask `can(role, action)`; nothing else compares role names.
"""

from enum import StrEnum


class Role(StrEnum):
    admin = "admin"
    supervisor = "supervisor"
    agent = "agent"
    # Free seat: reads everything and adds notes, changes nothing.
    light_agent = "light_agent"


class Action(StrEnum):
    read_ticket = "read_ticket"
    reply = "reply"  # also macros, accept chat and resolve
    private_note = "private_note"
    change_fields = "change_fields"
    assign_self = "assign_self"
    assign_others = "assign_others"
    approve = "approve"
    reports = "reports"
    # Settings, members, imports, audit, sandbox, API keys, webhooks, apps.
    settings = "settings"
    list_members = "list_members"


_AGENT = frozenset(
    {
        Action.read_ticket,
        Action.reply,
        Action.private_note,
        Action.change_fields,
        Action.assign_self,
        Action.list_members,
    }
)
_SUPERVISOR = _AGENT | {Action.assign_others, Action.approve, Action.reports}

ROLE_ACTIONS: dict[Role, frozenset[Action]] = {
    Role.light_agent: frozenset({Action.read_ticket, Action.private_note, Action.list_members}),
    Role.agent: _AGENT,
    Role.supervisor: _SUPERVISOR,
    Role.admin: _SUPERVISOR | {Action.settings},
}


def can(role: Role, action: Action) -> bool:
    return action in ROLE_ACTIONS[role]
