"""Slack alerts (md W20, PL7), the rules that need no database.

- An Admin installs the app with a Slack incoming-webhook URL (Slack > Apps > Incoming
  Webhooks); it is a secret (whoever has it can post to that channel), kept sealed.
- When a ticket is created Urgent or High, or a change makes it Urgent or High, one alert
  goes to that channel. The event comes from the outbox after the change commits, so it
  is what the rules made of the ticket, and it is retried if Slack doesn't answer.
- The alert has the ticket's number, subject, priority and team and a link to it; never
  the customer's messages.
"""

from typing import Any
from urllib.parse import urlsplit

HOST = "hooks.slack.com"
ALERT_PRIORITIES = frozenset({"urgent", "high"})
EVENTS = frozenset({"ticket.created", "ticket.updated"})
SUBJECT_MAX = 150


class BadWebhook(ValueError):
    """The message is for the Admin."""


def check_webhook(raw: str) -> str:
    url = raw.strip()
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != HOST
        or parts.port not in (None, 443)
        or not parts.path.startswith("/services/")
        or parts.username
        or len(url) > 300
    ):
        raise BadWebhook("Paste the Incoming Webhook URL: https://hooks.slack.com/services/...")
    return url


def path_of(url: str) -> str:
    return urlsplit(url).path


def alerts_for(event: str, payload: dict[str, Any]) -> bool:
    """Whether this outbox event is an Urgent or High ticket to tell the channel about."""
    ticket = payload.get("ticket")
    if event not in EVENTS or not isinstance(ticket, dict):
        return False
    if ticket.get("priority") not in ALERT_PRIORITIES:
        return False
    if event == "ticket.created":
        return True
    changes = payload.get("changes")
    return isinstance(changes, list) and "priority" in changes


def _escape(text: str) -> str:
    """Slack's own escaping, so a subject can't make a link or mention anyone."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def message(ticket: dict[str, Any], label: str, url: str | None) -> dict[str, Any]:
    priority = str(ticket.get("priority") or "").capitalize()
    subject = _escape(str(ticket.get("subject") or "")[:SUBJECT_MAX])
    team = _escape(str(ticket.get("team") or "No team"))
    head = f"<{url}|{label}>" if url else label
    icon = ":red_circle:" if ticket.get("priority") == "urgent" else ":large_orange_circle:"
    return {"text": f"{icon} {priority}: {head} {subject} ({team})"}
