"""Jira and Slack alert rules (md X10, W20), no database: keys, the issue body, which
events alert, the message, and the webhook URL check."""

import pytest

from cedesk.helpdesk.domain import jira, slack
from cedesk.helpdesk.domain.delivery import ERROR_MAX


def test_jira_keys() -> None:
    assert jira.project_key(" sup ") == "SUP"
    assert jira.issue_key("sup-12") == "SUP-12"
    for bad in ("", "1SUP", "SUP-0", "SUP-", "SUP 12", "S" * 21 + "-1"):
        with pytest.raises(jira.JiraInputError):
            jira.issue_key(bad)
    for bad in ("", "su-p", "1AB"):
        with pytest.raises(jira.JiraInputError):
            jira.project_key(bad)


def test_the_issue_carries_a_link_back_and_no_customer_text() -> None:
    body = jira.new_issue("SUP", "Refund stuck", "Check payout\nfor order 1234", "T-1042",
                          "https://app.cedesk.in/tickets/abc")  # fmt: skip
    fields = body["fields"]
    assert fields["project"] == {"key": "SUP"} and fields["summary"] == "Refund stuck"
    lines = ["".join(n["text"] for n in p["content"]) for p in fields["description"]["content"]]
    assert lines == ["Check payout", "for order 1234", "",
                     "From CeDesk ticket T-1042: https://app.cedesk.in/tickets/abc"]  # fmt: skip
    last = fields["description"]["content"][-1]["content"]
    assert last == [
        {"type": "text", "text": "From CeDesk ticket T-1042: "},
        {"type": "text", "text": "https://app.cedesk.in/tickets/abc",
         "marks": [{"type": "link", "attrs": {"href": "https://app.cedesk.in/tickets/abc"}}]},
    ]  # fmt: skip
    plain = jira.new_issue("SUP", "x", None, "T-1", "https://x/t")
    assert len(plain["fields"]["description"]["content"]) == 1


def test_links_in_the_agents_text_are_links_too() -> None:
    """Live retest: Jira doesn't turn plain text into links, so the link back to the ticket
    couldn't be clicked. Every http(s) address is sent as a link; the punctuation after it
    stays text."""
    body = jira.new_issue("SUP", "x", "See http://a.example/x?y=1, and (https://b.example/z).",
                          "T-1", "http://cedesk.test/t/1")  # fmt: skip
    first = body["fields"]["description"]["content"][0]["content"]
    assert [(n["text"], [m["attrs"]["href"] for m in n.get("marks", [])]) for n in first] == [
        ("See ", []),
        ("http://a.example/x?y=1", ["http://a.example/x?y=1"]),
        (", and (", []),
        ("https://b.example/z", ["https://b.example/z"]),
        (").", []),
    ]
    no_link = jira.new_issue("SUP", "x", "ftp://old.example and mailto:a@b.co", "T-1", "")
    assert all("marks" not in n for n in no_link["fields"]["description"]["content"][0]["content"])


def test_the_summary_defaults_to_the_subject() -> None:
    assert jira.summary("  Order   late ", None) == "Order late"
    assert jira.summary("Order late", "Payout bug") == "Payout bug"
    assert len(jira.summary("x" * 400, None)) == jira.SUMMARY_MAX
    with pytest.raises(jira.JiraInputError):
        jira.summary(" ", " ")
    assert (
        jira.issue_url("acme.atlassian.net", "SUP-1") == "https://acme.atlassian.net/browse/SUP-1"
    )


def _event(priority: str, changes: list[str] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"ticket": {"priority": priority}}
    if changes is not None:
        payload["changes"] = changes
    return payload


def test_which_events_alert() -> None:
    assert slack.alerts_for("ticket.created", _event("urgent"))
    assert slack.alerts_for("ticket.created", _event("high"))
    assert not slack.alerts_for("ticket.created", _event("medium"))
    assert slack.alerts_for("ticket.updated", _event("high", ["priority"]))
    assert not slack.alerts_for("ticket.updated", _event("high", ["subject"]))  # already alerted
    assert not slack.alerts_for("ticket.updated", _event("low", ["priority"]))
    assert not slack.alerts_for("ticket.resolved", _event("urgent"))
    assert not slack.alerts_for("ticket.created", {})


def test_the_message_escapes_what_the_customer_wrote() -> None:
    ticket = {"priority": "urgent", "subject": "<!channel> refund & <https://evil|click>",
              "team": "Billing"}  # fmt: skip
    msg = slack.message(ticket, "T-7", "https://app/t/1")["text"]
    assert msg.startswith(":red_circle: Urgent: <https://app/t/1|T-7> ")
    assert "&lt;!channel&gt; refund &amp; &lt;https://evil|click&gt;" in msg
    assert "<!channel>" not in msg and msg.endswith("(Billing)")
    assert slack.message({"priority": "high"}, "T-8", None)["text"] == (
        ":large_orange_circle: High: T-8  (No team)"
    )


@pytest.mark.parametrize(
    "url",
    ["http://hooks.slack.com/services/x", "https://hooks.slack.com.evil.com/services/x",
     "https://evil.com/services/x", "https://hooks.slack.com/other", "https://u@hooks.slack.com/services/x",
     "https://hooks.slack.com:8443/services/x", ""],
)  # fmt: skip
def test_bad_webhook_urls(url: str) -> None:
    with pytest.raises(slack.BadWebhook):
        slack.check_webhook(url)


def test_a_good_webhook_url() -> None:
    url = "https://hooks.slack.com/services/T000/B000/XXXX"
    assert slack.check_webhook(f"  {url} ") == url
    assert slack.path_of(url) == "/services/T000/B000/XXXX"


def test_a_refused_webhook_says_what_happened_and_what_to_do() -> None:
    """Live retest: a webhook removed in Slack showed only "Slack has no such thing."."""
    assert slack.webhook_problem(404) == slack.WEBHOOK_GONE
    assert slack.webhook_problem(410) == slack.WEBHOOK_ARCHIVED
    assert slack.webhook_problem(403) == slack.webhook_problem(401) == slack.WEBHOOK_REFUSED
    for other in (None, 400, 429, 500, 503):  # says nothing about the webhook
        assert slack.webhook_problem(other) is None
    for said in (slack.WEBHOOK_GONE, slack.WEBHOOK_ARCHIVED, slack.WEBHOOK_REFUSED):
        assert len(f"slack_alerts: {said}") <= ERROR_MAX  # a failed event keeps all of it
