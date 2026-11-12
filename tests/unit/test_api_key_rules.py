"""API key rules that need no database (md PL1, PL2): the key's shape, what each level
may do, and which routes a key reaches."""

from uuid import uuid4

import pytest

from cedesk.helpdesk.domain import api_keys as rules
from cedesk.helpdesk.domain.api_keys import KeyAccess
from cedesk.helpdesk.domain.permissions import ROLE_ACTIONS, Action, Role

R, RW, ADMIN = KeyAccess.read, KeyAccess.read_write, KeyAccess.admin


def test_a_key_names_its_workspace_and_hides_the_rest() -> None:
    ws = uuid4()
    key = rules.new_key(ws)
    assert key.startswith("cdk_") and rules.is_key(key)
    assert rules.key_workspace(key) == ws
    assert rules.new_key(ws) != key  # random each time
    assert len(rules.key_hash(key)) == 32 and rules.key_hash(key) != key.encode()
    assert rules.hint(rules.last4(key)) == "cdk_…" + key[-4:]


@pytest.mark.parametrize(
    "bad",
    ["", "cdk_", "cdk_xyz_abc", f"cdk_{uuid4().hex}", f"cdk_{uuid4().hex}_",
     f"{uuid4()}.secret", "cdk_" + "g" * 32 + "_abc", "cdk_" + "a" * 300],
)  # fmt: skip
def test_anything_else_is_not_a_key(bad: str) -> None:
    assert rules.key_workspace(bad) is None


def test_a_session_token_is_not_taken_for_a_key() -> None:
    assert not rules.is_key(f"{uuid4()}.abc")


def test_levels_cap_the_creators_rights() -> None:
    admin = ROLE_ACTIONS[Role.admin]
    assert rules.capped(R, admin) == {Action.read_ticket, Action.list_members, Action.reports}
    rw = rules.capped(RW, admin)
    assert Action.reply in rw and Action.assign_others in rw
    assert Action.settings not in rw
    full = rules.capped(ADMIN, admin)
    assert Action.settings in full
    for level in KeyAccess:  # never, at any level
        assert not {Action.manage_access, Action.approve} & rules.capped(level, admin)
    # A creator's missing right stays missing.
    agent = ROLE_ACTIONS[Role.agent]
    assert Action.reports not in rules.capped(ADMIN, agent)


def test_who_may_make_which_key() -> None:
    admin, supervisor = ROLE_ACTIONS[Role.admin], ROLE_ACTIONS[Role.supervisor]
    assert all(rules.can_make(level, admin) for level in KeyAccess)
    assert not any(rules.can_make(level, supervisor) for level in KeyAccess)


V1 = "/api/v1"


@pytest.mark.parametrize(
    ("level", "method", "path", "allowed"),
    [
        (R, "GET", f"{V1}/tickets", True),
        (R, "GET", f"{V1}/tickets/abc/related", True),
        (R, "POST", f"{V1}/tickets", False),
        (RW, "POST", f"{V1}/tickets", True),
        (RW, "PATCH", f"{V1}/tickets/abc", True),
        (RW, "POST", f"{V1}/tickets/abc/messages", True),
        (RW, "PATCH", f"{V1}/contacts/abc/profile", True),
        (RW, "POST", f"{V1}/inbound/tickets", True),
        (RW, "POST", f"{V1}/inbound/tickets/abc/messages", True),
        (R, "POST", f"{V1}/inbound/tickets", False),
        (ADMIN, "POST", f"{V1}/inbound/email", False),  # the mail service's, not a key's
        (R, "GET", f"{V1}/reports/metrics", True),
        (ADMIN, "POST", f"{V1}/reports/team", False),
        (RW, "POST", f"{V1}/teams", False),
        (ADMIN, "POST", f"{V1}/teams", True),
        (ADMIN, "PUT", f"{V1}/ticket-types/order", True),
        (ADMIN, "POST", f"{V1}/articles", True),
        (R, "GET", f"{V1}/me", True),
        (ADMIN, "GET", f"{V1}/me/2fa", False),  # nothing under /me
        (ADMIN, "PUT", f"{V1}/me/status", False),
        (ADMIN, "POST", f"{V1}/me/password", False),
        (ADMIN, "GET", f"{V1}/members", True),
        (ADMIN, "POST", f"{V1}/members", False),
        (ADMIN, "GET", f"{V1}/api-keys", False),
        (ADMIN, "POST", f"{V1}/api-keys", False),
        (ADMIN, "POST", f"{V1}/auth/sign-out", False),
        (ADMIN, "GET", f"{V1}/security-settings", False),
        (ADMIN, "PUT", f"{V1}/sso-settings", False),
        (ADMIN, "GET", f"{V1}/roles", False),
        (ADMIN, "PUT", f"{V1}/cevanos/connection", False),
        (ADMIN, "POST", f"{V1}/imports/connections", False),
        (ADMIN, "POST", f"{V1}/privacy/export", False),
        (ADMIN, "GET", f"{V1}/audit", False),
        (ADMIN, "POST", f"{V1}/approvals/abc/decide", False),
        (ADMIN, "GET", f"{V1}/stream", False),
        (ADMIN, "GET", "/healthz", False),
    ],
)
def test_where_a_key_may_go(level: KeyAccess, method: str, path: str, allowed: bool) -> None:
    assert (rules.refusal(level, method, path) is None) is allowed
