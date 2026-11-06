"""Every row of md section 6.6 (built-in roles), checked against `permissions.py`."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cedesk.helpdesk.domain import auth
from cedesk.helpdesk.domain.permissions import Action, Role, can

A, S, G, L = Role.admin, Role.supervisor, Role.agent, Role.light_agent
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
}


def test_every_action_is_in_the_table() -> None:
    assert set(EXPECTED) == set(Action)


@pytest.mark.parametrize("action", list(Action))
@pytest.mark.parametrize("role", list(Role))
def test_role_action(role: Role, action: Action) -> None:
    assert can(role, action) is (role in EXPECTED[action])


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
