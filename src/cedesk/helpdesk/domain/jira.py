"""The Jira app (md X10, PL7), the rules that need no database.

- An Admin installs it with the site (`acme` for acme.atlassian.net), the Atlassian account
  email, an API token and the project key new issues go to.
- An agent creates an issue from a ticket, or links one that exists. CeDesk keeps the
  issue's key and link on the ticket (a "Jira" entry and a system line); the issue itself
  lives in Jira. What is sent: the summary (the ticket's subject unless the agent gives
  one), and a description the agent writes or, by default, the ticket's number and a link
  back to it. Never the customer's messages unless the agent puts them in.
"""

import re
from typing import Any

PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,19}$")
ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,19}-[1-9][0-9]{0,8}$")
SUMMARY_MAX = 250  # Jira's limit is 255
DESCRIPTION_MAX = 5000
API = "/rest/api/3"
ISSUE_TYPE = "Task"
MAX_LINKS = 10  # issues per ticket


class JiraInputError(ValueError):
    """The message is for the person who typed it."""


def project_key(raw: str) -> str:
    key = raw.strip().upper()
    if not PROJECT_KEY.match(key):
        raise JiraInputError("A project key is letters and digits, like SUP or OPS.")
    return key


def issue_key(raw: str) -> str:
    key = raw.strip().upper()
    if not ISSUE_KEY.match(key):
        raise JiraInputError("An issue key looks like SUP-123.")
    return key


def issue_url(host: str, key: str) -> str:
    return f"https://{host}/browse/{key}"


def summary(subject: str, given: str | None) -> str:
    text = " ".join((given if given and given.strip() else subject).split())
    if not text:
        raise JiraInputError("Give the issue a summary.")
    return text[:SUMMARY_MAX]


def _paragraphs(text: str) -> list[dict[str, Any]]:
    """Plain text as Atlassian Document Format: one paragraph per line."""
    return [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]}
        if line
        else {"type": "paragraph", "content": []}
        for line in text.splitlines()
    ]


def new_issue(
    project: str, summary_text: str, description: str | None, ticket_label: str, ticket_url: str
) -> dict[str, Any]:
    """The body of `POST /rest/api/3/issue`."""
    body = (description or "").strip()[:DESCRIPTION_MAX]
    text = (body + "\n\n" if body else "") + f"From CeDesk ticket {ticket_label}: {ticket_url}"
    return {
        "fields": {
            "project": {"key": project},
            "issuetype": {"name": ISSUE_TYPE},
            "summary": summary_text,
            "description": {"type": "doc", "version": 1, "content": _paragraphs(text)},
        }
    }
