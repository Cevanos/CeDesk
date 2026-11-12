"""Side conversations (md X5), the rules that need no database.

- From a ticket, an agent emails another team or a supplier (warehouse@, the courier) to
  get an answer for the customer. The customer never sees it: it lives in its own thread
  on the ticket, which the portal and the customer's emails never read.
- It goes out from the ticket's support address, so the other side's reply comes back in
  through email; its reply headers (In-Reply-To, References) tie it to the side
  conversation before anything looks for a ticket, so it never lands on the customer's
  thread. The assignee is told.
- Not to the ticket's customer, nor to one of our own support addresses (their mail
  comes back in as "from us" and would be dropped).
- Slack side conversations (#warehouse) need a Slack bot to bring replies back; email
  first (md X5's other half is a follow-up).
"""

from collections.abc import Iterable

from cedesk.helpdesk.domain.email import normalise_address

MAX_TO = 10
SUBJECT_MAX = 200
BODY_MAX = 20_000
PER_TICKET = 20


class SideError(ValueError):
    """The message is for the agent."""


def recipients(raw: Iterable[str], *, customer: str, ours: set[str]) -> list[str]:
    """Each address once, lower case, in the order given."""
    out: list[str] = []
    for value in raw:
        address = normalise_address(value)
        if address is None:
            raise SideError(f"'{value.strip()[:80]}' isn't an email address.")
        if address == customer.lower():
            raise SideError("That is the customer: reply to them on the ticket instead.")
        if address in ours:
            raise SideError(f"{address} is one of this desk's own addresses.")
        if address not in out:
            out.append(address)
    if not out:
        raise SideError("Send it to at least one address.")
    if len(out) > MAX_TO:
        raise SideError(f"At most {MAX_TO} addresses.")
    return out


def subject(given: str | None, ticket_subject: str, ticket_label: str) -> str:
    """The agent's subject, or "About <ticket subject>", with the ticket's label."""
    text = " ".join((given or "").split()) or f"About {' '.join(ticket_subject.split())}"
    return f"{text[: SUBJECT_MAX - len(ticket_label) - 3]} [{ticket_label}]"


def body(text: str) -> str:
    value = text.strip()
    if not value:
        raise SideError("Write the message.")
    if len(value) > BODY_MAX:
        raise SideError(f"At most {BODY_MAX} characters.")
    return value


def follow_up_subject(first: str) -> str:
    return first if first.lower().startswith("re:") else f"Re: {first}"
