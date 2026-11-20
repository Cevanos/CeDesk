"""Every row of md section 6.6 (built-in roles), checked against `permissions.py`."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cedesk.helpdesk.domain import auth
from cedesk.helpdesk.domain.permissions import (
    Access,
    Action,
    Role,
    Scope,
    access_of,
    can,
    view_only_reason,
)

A, S, G, L = Role.admin, Role.supervisor, Role.agent, Role.light_agent
BUILT_IN = [A, S, G, L]
EXPECTED: dict[Action, set[Role]] = {
    Action.read_ticket: {A, S, G, L},
    Action.create_ticket: {A, S, G},
    Action.reply: {A, S, G},
    Action.private_note: {A, S, G, L},
    Action.change_fields: {A, S, G},
    Action.assign_self: {A, S, G},
    Action.assign_others: {A, S},
    Action.approve: {A, S},
    Action.reports: {A, S},
    Action.settings: {A},
    Action.list_members: {A, S, G, L},
    Action.manage_access: {A},
    Action.bulk_edit: {A, S, G},
    Action.share_views: {A, S},
    Action.send_transcript: {A, S, G},
    Action.manage_contacts: {A},
    Action.merge_contacts: {A, S},
    Action.export_contacts: {A},
    Action.manage_segments: {A, S},
}


def test_every_action_is_in_the_table() -> None:
    assert set(EXPECTED) == set(Action)


@pytest.mark.parametrize("action", list(Action))
@pytest.mark.parametrize("role", BUILT_IN)
def test_role_action(role: Role, action: Action) -> None:
    assert can(role, action) is (role in EXPECTED[action])
    assert access_of(role, None).can(action) is (role in EXPECTED[action])


def _custom(**rights: object) -> Access:
    row = {
        "reply": False,
        "assign_any": False,
        "reports": False,
        "settings": False,
        "approve": False,
        "scope": "all",
        "team_id": None,
        "team_name": None,
        "role_name": "Desk",
    } | rights
    return access_of(Role.custom, row)


# Custom roles (md 6.6 last column, A3): each right adds exactly its actions.
CUSTOM_BASE = {
    Action.read_ticket,
    Action.private_note,
    Action.change_fields,
    Action.list_members,
    Action.bulk_edit,
}
RIGHT_ADDS = {
    "reply": {Action.reply, Action.create_ticket, Action.assign_self, Action.send_transcript},
    "assign_any": {Action.assign_self, Action.assign_others},
    "reports": {Action.reports},
    "settings": {
        Action.settings,
        Action.share_views,
        Action.manage_contacts,
        Action.merge_contacts,
        Action.manage_segments,
    },
    "approve": {Action.approve},
}


def test_a_custom_role_with_no_rights_reads_notes_and_edits_fields() -> None:
    access = _custom()
    assert set(access.actions) == CUSTOM_BASE
    assert not access.takes_tickets


@pytest.mark.parametrize("right", list(RIGHT_ADDS))
def test_each_custom_right_adds_its_actions(right: str) -> None:
    assert set(_custom(**{right: True}).actions) == CUSTOM_BASE | RIGHT_ADDS[right]


def test_no_custom_role_can_manage_access() -> None:
    every = _custom(**dict.fromkeys(RIGHT_ADDS, True))
    assert not every.can(Action.manage_access)
    assert not every.can(Action.export_contacts)  # the whole customer list: Admins only
    assert every.takes_tickets


def test_scopes_and_the_view_only_reason() -> None:
    billing, other = uuid4(), uuid4()
    edit = _custom(reply=True, scope="team_edit", team_id=billing, team_name="Billing")
    only = _custom(reply=True, scope="team_only", team_id=billing, team_name="Billing")
    assert edit.scope is Scope.team_edit
    assert (edit.sees(other), edit.edits(other), edit.edits(billing)) == (True, False, True)
    assert (only.sees(other), only.sees(billing), only.sees(None)) == (False, True, False)
    assert view_only_reason(edit, other) == "Your role Desk can edit Billing tickets only."
    assert view_only_reason(edit, billing) is None
    assert view_only_reason(access_of(L, None), billing) == (
        "Your role Light agent can read tickets and add notes only."
    )
    assert view_only_reason(access_of(A, None), other) is None


def test_token_carries_its_workspace() -> None:
    ws = uuid4()
    token = auth.new_token(ws)
    assert auth.token_workspace(token) == ws
    assert auth.token_workspace("not-a-token") is None
    assert auth.token_workspace("nope.abc") is None
    assert auth.token_hash(token) != auth.token_hash(auth.new_token(ws))


def test_tenth_failure_locks_for_15_minutes() -> None:
    now = datetime(2026, 11, 6, tzinfo=UTC)
    assert auth.after_failure(0, now) == (1, None)
    failed, locked = auth.after_failure(auth.MAX_FAILED_LOGINS - 1, now)
    assert failed == 0
    assert locked == now + auth.LOCKOUT
    assert auth.is_locked(locked, now)
