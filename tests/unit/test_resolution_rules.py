"""Resolution receipts' pure rules (differentiator plan #5)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cedesk.helpdesk.domain import resolution as r

NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)


def test_the_setting_is_off_by_default_and_required_can_name_teams() -> None:
    assert not r.Setting().asked() and not r.Setting().needed(None)
    billing, shipping = uuid4(), uuid4()
    optional = r.setting("optional", [])
    assert optional.asked() and not optional.needed(billing)
    every = r.setting("required", [])
    assert every.needed(billing) and every.needed(None)
    some = r.setting("required", [billing, billing])
    assert some.required_teams == (billing,)
    assert some.needed(billing) and not some.needed(shipping) and not some.needed(None)
    for mode, teams in (("sometimes", []), ("optional", [billing]), ("off", [billing])):
        with pytest.raises(r.ResolutionError):
            r.setting(mode, teams)


def test_a_receipt_needs_what_was_done_and_tidies_text() -> None:
    made = r.receipt({"action_taken": "  Refunded\n the   mug ", "request": " ", "result": None})
    assert made.action_taken == "Refunded the mug"
    assert made.request is None and made.result is None
    assert made.verified is r.Verified.unverified and made.customer_notified is None
    for bad in ({}, {"action_taken": ""}, {"action_taken": "x" * 501},
                {"action_taken": "ok", "result": "x" * 301},
                {"action_taken": "ok", "verified": "shopify"},
                {"action_taken": "ok", "verified": "made-up"}):  # fmt: skip
        with pytest.raises(ValueError):  # ResolutionError is a ValueError
            r.receipt(bad)


def test_changes_keep_only_the_keys_sent() -> None:
    assert r.changes({}) == {}
    assert r.changes({"pending_item": None, "verified": "agent"}) == {
        "pending_item": None, "verified": "agent",
    }  # fmt: skip
    with pytest.raises(r.ResolutionError):
        r.changes({"action_taken": None})
    with pytest.raises(r.ResolutionError):
        r.changes({"verified": "stripe"})


def test_edit_window_and_customer_told() -> None:
    assert r.editable(NOW - timedelta(days=7), "resolved", NOW)
    assert not r.editable(NOW - timedelta(days=7, seconds=1), "resolved", NOW)
    assert not r.editable(NOW, "open", NOW) and not r.editable(None, "resolved", NOW)
    assert r.notified(NOW - timedelta(hours=23), NOW)
    assert not r.notified(NOW - timedelta(hours=25), NOW) and not r.notified(None, NOW)


def test_the_webhook_part_holds_no_text_unless_asked() -> None:
    row = {"verified": "stripe", "verified_at": NOW, "customer_notified": True,
           "pending_item": "Bank settlement", "request": "Refund", "action_taken": "Refunded",
           "result": "Refund issued"}  # fmt: skip
    plain = r.webhook_part(row, with_text=False)
    assert plain == {"verified": "stripe", "verified_at": NOW.isoformat(),
                     "customer_notified": True, "pending": True}  # fmt: skip
    full = r.webhook_part(row, with_text=True)
    assert full is not None and full["action_taken"] == "Refunded"
    assert r.webhook_part(None, with_text=False) is None
