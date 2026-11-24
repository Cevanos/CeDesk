"""Customer portal sign-in rules (md P1, S7) that need no database.

A customer signs in with a 6-digit code sent to their email. The code is stored only as
an HMAC bound to the workspace and the email (md section 12 rule 7: never in plain text,
never logged), lasts 10 minutes, and is burned after 5 wrong tries. A workspace can limit
the portal to some email domains ("Only acme.com emails can open tickets here").
"""

import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from uuid import UUID

CODE_LIFETIME = timedelta(minutes=10)
CODE_TRIES = 5  # wrong codes before that code stops working
CODES_PER_HOUR = 5  # codes sent per email per hour
SESSION_LIFETIME = timedelta(days=7)
MAX_DOMAINS = 50

EMAIL = re.compile(
    r"^[^@\s]{1,64}@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
DOMAIN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
CODE = re.compile(r"^\d{6}$")

BAD_CODE = "That code didn't work or has expired. Ask for a new one."


class BadSetting(ValueError):
    pass


def normalise_email(email: str) -> str | None:
    """Lower-case and trimmed, or None when it isn't an email address."""
    value = email.strip().lower()
    if len(value) > 254 or not EMAIL.match(value):
        return None
    return value


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def clean_code(code: str) -> str | None:
    """The 6 digits, allowing spaces or a dash as typed from an email ("123 456")."""
    value = re.sub(r"[\s-]", "", code)
    return value if CODE.match(value) else None


def code_hash(secret_key: str, workspace_id: UUID, email: str, code: str) -> bytes:
    """Keyed and bound to the workspace and email: a copied database row can't be tried
    offline (only a million codes), nor used for another email or workspace."""
    message = f"portal-code:{workspace_id}:{email}:{code}".encode()
    return hmac.new(secret_key.encode(), message, hashlib.sha256).digest()


def clean_domains(domains: list[str]) -> list[str]:
    """Lower-case, no `@` in front, no repeats, in the order given."""
    seen: list[str] = []
    for raw in domains:
        value = raw.strip().lower().removeprefix("@")
        if not value:
            continue
        if len(value) > 253 or not DOMAIN.match(value):
            raise BadSetting(f"{raw!r} is not a domain such as acme.com.")
        if value not in seen:
            seen.append(value)
    if len(seen) > MAX_DOMAINS:
        raise BadSetting(f"At most {MAX_DOMAINS} domains.")
    return seen


def domain_allowed(email: str, domains: list[str]) -> bool:
    """No domains: everyone. Otherwise the email's domain, or a subdomain of one."""
    if not domains:
        return True
    host = email.rpartition("@")[2]
    return any(host == d or host.endswith("." + d) for d in domains)


def domains_message(domains: list[str]) -> str:
    shown = ", ".join(domains[:5]) + (" …" if len(domains) > 5 else "")
    return f"Only {shown} emails can open tickets here."


def code_email(workspace_name: str, code: str) -> str:
    return (
        f"Your sign-in code for {workspace_name} support is:\n\n"
        f"    {code}\n\n"
        "It works for 10 minutes. If you didn't ask for it, ignore this email.\n"
    )


def code_subject(workspace_name: str) -> str:
    return f"Your sign-in code for {workspace_name} support"


def contact_name(email: str) -> str:
    """A name for a customer we haven't met yet, made from the part before `@`, so a
    reply's {{customer.first_name}} reads "Hi Priya", not "Hi priya.shah":
    priya.shah@ → "Priya Shah", ravi_k+orders@ → "Ravi K". A part with no letters (a
    number) is left out; with no letters at all, the part before `@` as it is."""
    local = email.partition("@")[0]
    words = [w for w in re.split(r"[._-]+", local.partition("+")[0]) if re.search(r"[^\W\d_]", w)]
    name = " ".join(w[:1].upper() + w[1:] for w in words)
    return (name or local or email)[:120]


# Portal tickets (md P2-P4)

TICKETS_PER_HOUR = 10  # new tickets per customer per hour (spam guard)
SUBJECT_LENGTH = 60  # P3: the subject is the first 60 characters of the message
VISIBLE_KINDS = ("customer", "agent", "ai")  # never notes or system lines (P4)

STATUS_LABELS = {"open": "Open", "pending": "Waiting on you", "resolved": "Resolved"}


def status_label(status: str) -> str:
    """What the customer sees: Pending reads "Waiting on you" (md T2)."""
    return STATUS_LABELS.get(status, status.title())


def subject_from(message: str) -> str:
    """The first 60 characters of the message, on one line, cut at a word if it can."""
    line = " ".join(message.split())
    if len(line) <= SUBJECT_LENGTH:
        return line
    cut = line[: SUBJECT_LENGTH - 1]
    space = cut.rfind(" ")
    if space >= SUBJECT_LENGTH // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"


def author_label(kind: str, author_name: str | None, you: str, workspace_name: str) -> str:
    """P4: "You", "Acme assistant", "Neha · Acme Support". `you`: the customer's own
    name, so a reply from someone else in CC shows that person's name."""
    if kind == "ai":
        return f"{workspace_name} assistant"
    if kind == "agent":
        return f"{author_name or 'Agent'} · {workspace_name} Support"
    if author_name is None or author_name == you:
        return "You"
    return author_name


# Web form (md C2)

# Where a ticket stands (differentiator plan #9): who owns it, what is still pending, and
# the checked outcome from the resolution receipt (plan #5).

WAITING_ON_YOU = "Waiting on you"


def first_name(name: str | None) -> str | None:
    """A member's first name for customers to see: never the full name or an email."""
    if not name or "@" in name:
        return None
    first = name.split()[0] if name.split() else ""
    return first[:40] or None


def owner(assignee_name: str | None, team_name: str | None, *, show_owner: bool) -> str | None:
    """The assignee's first name; the team's name when hidden or unassigned."""
    person = first_name(assignee_name) if show_owner else None
    return person or team_name or None


def pending_step(status: str, pending_item: str | None) -> str | None:
    """What the ticket waits on: the customer (Pending), or a receipt's pending item."""
    if status == "pending":
        return WAITING_ON_YOU
    if status == "resolved":
        return pending_item
    return None


def outcome(status: str, result: str | None, verified: str | None) -> str | None:
    """The receipt's result, shown only once it was checked (by the agent or an app)."""
    if status != "resolved" or not verified or verified == "unverified":
        return None
    return result


FORMS_PER_EMAIL_HOUR = 5
NAME_MAX = 100
FORM_RECEIVED = "Thanks, we got your message. We'll reply by email."


def clean_name(name: str, email: str) -> str:
    """The name the customer typed, or the part of the email before the @."""
    value = " ".join(name.split())[:NAME_MAX]
    return value or contact_name(email)


# "You have a reply" email for portal tickets

NOTICE_QUIET = timedelta(minutes=15)  # one notice per ticket in this time
NOTICE_PREFIX = "<portal-reply-"


def notice_header_id(effect_key: str, from_address: str) -> str:
    """The same for a retried event, so the notice is never queued twice."""
    digest = hashlib.sha256(effect_key.encode()).hexdigest()[:32]
    domain = from_address.rsplit("@", 1)[-1] or "cedesk.local"
    return f"{NOTICE_PREFIX}{digest}@{domain}>"


def ticket_link(base_url: str, workspace_slug: str, ticket_id: object) -> str:
    return f"{base_url.rstrip('/')}/{workspace_slug}/tickets/{ticket_id}"


def notice_subject(label: str, subject: str) -> str:
    return f"New reply on your request [{label}] {subject}"[:200]


def notice_email(contact: str, agent: str, workspace_name: str, label: str, link: str) -> str:
    """No message text: the customer reads it in the portal, signed in."""
    first = contact.split()[0] if contact.split() else contact
    return (
        f"Hi {first},\n\n"
        f"{agent} from {workspace_name} support replied to your request {label}.\n\n"
        f"Read it and reply here: {link}\n\n"
        f"You'll be asked for a sign-in code sent to this email address.\n"
    )
