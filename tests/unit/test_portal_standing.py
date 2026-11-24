"""Where a portal ticket stands (differentiator plan #9): pure rules."""

from cedesk.helpdesk.domain import portal as r


def test_owner_is_a_first_name_or_the_team_never_an_email() -> None:
    assert r.owner("Priyanka Rao", "Billing", show_owner=True) == "Priyanka"
    assert r.owner("Priyanka Rao", "Billing", show_owner=False) == "Billing"
    assert r.owner(None, "Billing", show_owner=True) == "Billing"
    assert r.owner("neha@example.com", "Billing", show_owner=True) == "Billing"
    assert r.owner("neha@example.com", None, show_owner=True) is None
    assert (
        r.owner("  ", None, show_owner=True) is None
        and r.owner(None, None, show_owner=True) is None
    )


def test_pending_step_and_outcome_follow_the_status() -> None:
    assert r.pending_step("pending", "Bank settlement") == "Waiting on you"
    assert r.pending_step("resolved", "Bank settlement") == "Bank settlement"
    assert r.pending_step("open", "Bank settlement") is None  # reopened: an old receipt
    assert r.pending_step("resolved", None) is None
    assert r.outcome("resolved", "Refund issued", "stripe") == "Refund issued"
    assert r.outcome("resolved", "Refund issued", "agent") == "Refund issued"
    assert r.outcome("resolved", "Refund issued", "unverified") is None
    assert r.outcome("resolved", "Refund issued", None) is None
    assert r.outcome("open", "Refund issued", "stripe") is None
