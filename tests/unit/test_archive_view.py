"""What an archived ticket's page shows of the history kept with it (md O3): one list per
kind and the Cevanos chat, named as on a live ticket; never a secret email's body, the
chat's polling state or ids of rows that are gone."""

from cedesk.helpdesk.api.archive import ArchivedRelatedOut
from cedesk.helpdesk.service.archive import SHOWN, _related

KINDS = {*SHOWN, "cevanos_chat"}


def test_a_ticket_archived_before_related_was_kept_shows_empty_lists() -> None:
    for kept in (None, {}):
        shown = _related(kept, {})
        assert set(shown) == KINDS
        assert shown["cevanos_chat"] is None
        assert all(shown[k] == [] for k in SHOWN)
        ArchivedRelatedOut.model_validate(shown)  # a valid page, not a 500


def test_a_secret_emails_body_is_never_shown() -> None:
    email = {"id": "e-1", "from_address": "s@x.test", "to_addresses": ["p@x.test"],
             "cc_addresses": [], "subject": "Reset your password", "status": "failed",
             "body_text": "https://x.test/reset/SECRET", "sensitive": True,
             "created_at": "2026-01-02T03:04:05+00:00", "sent_at": None}  # fmt: skip
    plain = email | {"body_text": "Found it", "sensitive": False, "subject": "Re: parcel"}
    old = {k: v for k, v in plain.items() if k != "sensitive"}  # stored before the flag
    secret, sent, earlier = _related({"emails": [email, plain, old]}, {})["emails"]
    assert (secret["body_text"], secret["sensitive"]) == (None, True)
    assert (sent["body_text"], sent["sensitive"]) == ("Found it", False)
    assert (earlier["body_text"], earlier["sensitive"]) == ("Found it", False)
    assert "id" not in secret


def test_named_as_on_a_live_ticket() -> None:
    kept = {
        "side_conversations": [{"id": "s-1", "subject": "Claim", "via": "slack",
                                "to_addresses": ["C123"], "slack_ts": "1.2", "from_address": "",
                                "created_at": "2026-01-02T03:04:05+00:00", "messages": [
                                    {"id": "m-1", "direction": "in", "body": "OK",
                                     "author_name": "Ops", "from_address": "",
                                     "header_message_id": "slack:C123:1.3",
                                     "created_at": "2026-01-02T03:05:05+00:00"}]}],
        "jira_issues": [{"issue_key": "SHIP-1", "url": "https://j.test/SHIP-1",
                         "made_here": False, "created_at": "2026-01-02T03:04:05+00:00"}],
        "links": [{"id": "l-1", "ticket_id": "4c0a9a43-5f6e-4f55-9a43-3f2b3c1d0e11",
                   "number": 1042, "kind": "linked", "created_by": "x",
                   "created_at": "2026-01-02T03:04:05+00:00"}],
    }  # fmt: skip
    shown = _related(kept, {})
    [side] = shown["side_conversations"]
    assert side["to"] == ["C123"] and "slack_ts" not in side and "id" not in side
    assert "header_message_id" not in side["messages"][0]
    assert shown["jira_issues"][0]["key"] == "SHIP-1"
    assert shown["links"][0]["label"] == "T-1042" and "created_by" not in shown["links"][0]
    ArchivedRelatedOut.model_validate(shown)


def test_the_cevanos_chat_without_its_polling_state() -> None:
    chat = {"conversation_id": "conv-1", "agent_id": "a-1", "channel_kind": "web",
            "last_message_id": "m-9", "next_poll_at": "2026-01-02T03:04:05+00:00",
            "poll_failures": 3, "handover_event_id": "ev-1", "taken_over_at": None,
            "closed_at": None, "created_at": "2026-01-02T03:04:05+00:00"}  # fmt: skip
    handover = {"reason": "Asked for a person"}
    shown = _related({"cevanos_chat": chat}, {"handover": handover})["cevanos_chat"]
    assert shown == {"conversation_id": "conv-1", "agent_id": "a-1", "channel_kind": "web",
                     "taken_over_at": None, "closed_at": None,
                     "created_at": "2026-01-02T03:04:05+00:00", "handover": handover}  # fmt: skip
