"""Email transcript (feature-gap plan 1.6), the rules that need no database.

A ticket's conversation as the customer saw it: customer messages, agent replies and the
AI agent's chat lines, oldest first. Never private notes, system lines or side
conversations (those are separate tables and are never read here). Files are listed by
name only. The email is queued like any other and its text is wiped once sent.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC
from typing import Any

PUBLIC_KINDS = ("customer", "agent", "ai")
MAX_CHARS = 500_000  # of message text in one transcript; older messages past it are left out
PER_HOUR = 10  # transcripts per member per hour
PER_HOUR_WORKSPACE = 100


def subject(ticket_label: str, ticket_subject: str) -> str:
    return f"Conversation transcript: {ticket_subject} [{ticket_label}]"[:250]


def _who(row: Mapping[Any, Any], customer_name: str) -> str:
    if row["kind"] == "customer":
        return str(row["author_name"] or customer_name)
    if row["kind"] == "ai":
        return "AI agent"
    return str(row["author_name"] or "Support")


def body(
    *,
    ticket_label: str,
    ticket_subject: str,
    customer_name: str,
    messages: Sequence[Mapping[Any, Any]],
) -> tuple[str, int]:
    """(the email's text, how many messages it holds). `messages`: public ones, oldest
    first, each with kind, author_name, body, created_at and `files` (names). When the
    text would pass MAX_CHARS the newest messages are kept and a line says how many
    earlier ones were left out."""
    blocks: list[str] = []
    used = 0
    for row in reversed(messages):
        when = row["created_at"].astimezone(UTC).strftime("%d %b %Y %H:%M UTC")
        text = str(row["body"]).strip()
        files = [str(f) for f in row.get("files") or []]
        if files:
            text += ("\n" if text else "") + "Files: " + ", ".join(files)
        block = f"{_who(row, customer_name)} · {when}\n{text}"
        if used + len(block) > MAX_CHARS and blocks:
            break
        blocks.append(block)
        used += len(block)
    left_out = len(messages) - len(blocks)
    head = f"{ticket_subject} [{ticket_label}]"
    parts = [head, "=" * min(len(head), 70)]
    if left_out:
        parts.append(f"({left_out} earlier messages are not included.)")
    parts += list(reversed(blocks))
    return "\n\n".join(parts) + "\n", len(blocks)


def domain_of(address: str) -> str:
    """What the audit keeps of an address that isn't the customer's: the part after @."""
    return address.rsplit("@", 1)[-1].lower()
