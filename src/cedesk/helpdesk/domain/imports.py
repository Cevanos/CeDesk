"""Import rules (md M1-M10, section 8) that need no database and no network.

An import reads another help desk through a `SourceConnector` (one class per source) and
the framework (`service/imports/framework.py`) writes what it reads. This module holds the
shapes they share and the rules that decide how a source record becomes a CeDesk one:

- records a connector hands over (`Contact`, `Ticket`, `Message`, `Article`, `Reply`);
- status, priority and channel mapping (a source's words onto CeDesk's few);
- the team suggested for a source group by its name (md 8.2 step 2);
- the options of a run (what to bring, which team each group goes to) and the cursor a
  run resumes from after a crash or a pause.
"""

import random
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from cedesk.helpdesk.domain.reports import IMPORT_SOURCES

SOURCES = IMPORT_SOURCES
AVAILABLE_SOURCES: tuple[str, ...] = (
    "intercom", "zendesk", "freshdesk", "gorgias", "helpscout", "hubspot", "zoho",
)  # fmt: skip
"""Sources an Admin can connect to (md M1: all 8 with CSV). A CSV file is uploaded, not
connected."""
KINDS: tuple[str, ...] = ("contacts", "tickets", "articles", "replies")  # the order of a run
PAGE_MAX = 100  # records written per transaction: a page is a commit
BUDGET_SECONDS = 20.0  # one worker pass works on imports this long, then lets the rest run
LEASE_SECONDS = 120  # a run not heard from for this long may be taken by another worker
DEFAULT_RATE_WAIT = 60  # seconds, when a 429 carries no Retry-After
MAX_RATE_WAIT = 3600
SUBJECT_MAX = 250  # hd_ticket.subject
BODY_MAX = 50_000  # hd_message.body
TAG_MAX = 20
FIELD_MAX = 50
FIELD_TEXT_MAX = 2000
FILE_MAX = 25 * 1024 * 1024  # a file bigger than this is kept as a link, not copied (md 8.2 step 5)
FILES_PER_MESSAGE = 20
SYNC_SOURCES: tuple[str, ...] = AVAILABLE_SOURCES  # a CSV file has nothing to ask again
SYNC_EVERY_SECONDS = 300  # "keep syncing until go-live": the worker looks every 5 minutes
SYNC_OVERLAP_SECONDS = 300  # each look starts this far before the last one, so none is missed

LABELS = {
    "intercom": "Intercom",
    "zendesk": "Zendesk",
    "freshdesk": "Freshdesk",
    "gorgias": "Gorgias",
    "helpscout": "Help Scout",
    "hubspot": "HubSpot",
    "zoho": "Zoho Desk",
    "csv": "CSV file",
}
GROUP_NAMES = {
    "intercom": "Teams",
    "zendesk": "Groups",
    "freshdesk": "Groups",
    "gorgias": "Teams",
    "helpscout": "Mailboxes",
    "hubspot": "Pipelines",
    "zoho": "Departments",
    "csv": "Teams",
}


def label(source: str) -> str:
    return LABELS.get(source, source)


# What a connector hands over


@dataclass(frozen=True)
class AccountInfo:
    name: str  # shown after connecting: "acme.zendesk.com"


@dataclass(frozen=True)
class Counts:
    contacts: int = 0
    tickets: int = 0
    articles: int = 0
    replies: int = 0
    groups: int = 0


@dataclass(frozen=True)
class Group:
    external_id: str
    name: str


@dataclass(frozen=True)
class Contact:
    external_id: str
    email: str | None  # None: the source has none; the framework makes a placeholder one
    name: str


@dataclass(frozen=True)
class Attachment:
    """A file on a source message. `url` is whatever the connector needs to fetch it (it
    decides how: some sources need the account's credentials, some hand out a signed link)."""

    name: str
    url: str
    size: int | None = None  # bytes, when the source says
    mime: str | None = None


@dataclass(frozen=True)
class Message:
    kind: str  # customer | agent | note
    body: str
    created_at: datetime
    external_id: str | None = None
    author_name: str | None = None
    author_email: str | None = None
    attachments: Sequence[Attachment] = ()


@dataclass(frozen=True)
class Csat:
    score: int
    comment: str | None = None
    rated_at: datetime | None = None


@dataclass(frozen=True)
class Ticket:
    external_id: str
    subject: str
    status: str  # the source's word; mapped by the connector's `status_map`
    created_at: datetime
    requester: Contact
    messages: Sequence[Message] = ()
    priority: str | None = None
    channel: str | None = None
    group_id: str | None = None
    assignee_email: str | None = None
    tags: Sequence[str] = ()
    fields: Mapping[str, Any] = field(default_factory=dict)
    updated_at: datetime | None = None
    resolved_at: datetime | None = None
    csat: Csat | None = None


@dataclass(frozen=True)
class Category:
    external_id: str
    name: str


@dataclass(frozen=True)
class Article:
    external_id: str
    title: str
    body: str
    category: Category | None = None
    published: bool = True


@dataclass(frozen=True)
class Reply:
    external_id: str
    shortcut: str
    body: str


@dataclass(frozen=True)
class Page[T]:
    items: Sequence[T]
    cursor: Mapping[str, Any] | None  # None: the last page


class RateLimited(Exception):
    """The source answered 429. The run pauses `retry_after` seconds, then goes on."""

    def __init__(self, retry_after: int | None = None) -> None:
        super().__init__("The source is rate limiting this import.")
        self.retry_after = retry_after


class SourceError(Exception):
    """The source can't be read. `permanent`: retrying won't help (bad credentials, no
    such account); otherwise it is down or slow and the run tries again, waiting at least
    `retry_after` seconds when the source said so."""

    def __init__(self, message: str, *, permanent: bool, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.retry_after = retry_after


class RecordGone(SourceError):
    """A 404 on a call about one record the source listed a moment ago (its messages, its
    details): it was deleted since. Only that record is left out (`RecordFailures`). Raised
    only for calls made with `record=True`; a 404 anywhere else, and a 401/403 always, is an
    account problem that stops the run."""

    def __init__(self, message: str) -> None:
        super().__init__(message, permanent=True)


# Records that could not be read (P0-2)

FAILURE_CATEGORIES: tuple[str, ...] = ("not_found", "temporary", "incomplete")
"""`not_found`: the source no longer has it (a 404 after listing it). `temporary`: it kept
failing (a 5xx, a timeout) after every retry of its page, so it was set aside. `incomplete`:
it was imported, but part of its history could not be read (a Zendesk ticket whose comments
answer 404 is imported with its description only); it stays open until a later read brings
the rest, or an Admin dismisses it."""


@dataclass(frozen=True)
class Failure:
    kind: str  # contact | ticket | article | reply
    external_id: str
    category: str  # one of FAILURE_CATEGORIES
    error: str  # our own words (`SourceError` messages): never a credential or customer text


class RecordFailures:
    """What a connector couldn't read, one record at a time, while it builds a page.

    A connector reads a ticket's messages with `guard(...)`. A record that is gone is noted
    and left out, and the rest of the page goes on. A temporary error is raised again, so the
    whole page is retried with backoff, until `isolate` is set: the framework sets it for the
    one last try after the run's retries ran out, and then the record that keeps failing is
    noted and left out too. Credentials refused (401/403), any other permanent error and a 429
    are never noted: they are about the account, not the record, and stop or pause the run.

    The framework takes the notes with `take()` and writes them in the same transaction as
    the page and its cursor, so a page never moves on without them."""

    def __init__(self, *, isolate: bool = False) -> None:
        self.isolate = isolate
        self._noted: list[Failure] = []

    def absorb(self, kind: str, external_id: str, exc: SourceError) -> bool:
        """Notes `exc` against the record when it is about that record alone; False when the
        caller must raise it."""
        if isinstance(exc, RecordGone):
            category = "not_found"
        elif not exc.permanent and self.isolate:
            category = "temporary"
        else:
            return False
        self._noted.append(Failure(kind, str(external_id)[:200], category, str(exc)[:500]))
        return True

    async def guard[T](
        self, kind: str, external_id: str, call: Callable[[], Awaitable[T]]
    ) -> T | None:
        """`call()`, or None when it failed for this record alone (and that was noted)."""
        try:
            return await call()
        except SourceError as exc:
            if not self.absorb(kind, external_id, exc):
                raise
        return None

    def incomplete(self, kind: str, external_id: str, error: str) -> None:
        """Notes a record that is imported without part of its history (the record is kept;
        what is missing is not passed off as complete)."""
        self._noted.append(Failure(kind, str(external_id)[:200], "incomplete", error[:500]))

    def take(self) -> list[Failure]:
        noted, self._noted = self._noted, []
        return noted


# Retrying a page that failed for a while (P0-2)


@dataclass(frozen=True)
class RetryPolicy:
    """How long a run keeps trying a page that fails with a temporary error (a 5xx, a
    timeout, a connection error) before it gives up on the record that fails.

    The wait doubles from `base_seconds` up to `max_seconds`, each time with jitter (between
    half and all of it, so many runs that failed together don't retry together), and never
    less than the source's own Retry-After. The defaults (8 tries: 30 s, 1, 2, 4, 8, 16 and
    32 min, then 1 h, about 1 to 2 h in all) ride out a typical outage of a help desk's API
    without holding a run for good. After `attempts` failed tries, one more try isolates the
    records that fail (`RecordFailures.isolate`); if the page itself still can't be read,
    the run stops with a message that says so. A 429 is not a failure: it waits for
    Retry-After and uses up no try. Set from `Settings.import_retry_*`."""

    attempts: int = 8
    base_seconds: float = 30.0
    max_seconds: float = 3600.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("A retry policy needs at least one attempt.")
        if self.base_seconds <= 0 or self.max_seconds < self.base_seconds:
            raise ValueError("A retry policy needs 0 < base_seconds <= max_seconds.")

    def delay(
        self, attempt: int, retry_after: int | None, *, rand: Callable[[], float] = random.random
    ) -> int:
        """Seconds to wait after the `attempt`-th failed try (1 is the first)."""
        backoff = min(self.max_seconds, self.base_seconds * 2 ** max(0, attempt - 1))
        wait = backoff * (0.5 + 0.5 * rand())
        if retry_after is not None and retry_after > 0:
            wait = max(wait, float(retry_after))
        return max(1, min(int(wait), MAX_RATE_WAIT))


class SourceConnector(Protocol):
    source: str
    status_map: Mapping[str, str]  # the source's status word -> open | pending | resolved

    async def check(self) -> AccountInfo:
        """Reads one record to prove the credentials work."""
        ...

    async def discover(self) -> Counts: ...

    async def groups(self) -> Sequence[Group]: ...

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        """One page of `kind` (one of `KINDS`) after `cursor` (None: the first page)."""
        ...


@runtime_checkable
class FileSource(Protocol):
    """A connector that can hand over the bytes of an attachment."""

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        """The file's bytes, or None when the source no longer has it or it is bigger than
        `limit`. Never reads more than `limit` bytes."""
        ...


@runtime_checkable
class ChangeSource(Protocol):
    """A connector that can say what changed in the source since a time (M9)."""

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        """Tickets created or updated at or after `since`, with all their messages, one page
        at a time (the same shape as `page("tickets", cursor)`)."""
        ...


def gave_up(source: str, tries: int, error: str, state: str) -> str:
    """What a run says when a page still can't be read after every retry and the isolating
    try (`RetryPolicy`). `state` `done` is a sync look (it is tried again later)."""
    head = f"{label(source)} kept failing after {tries} tries ({error[:200]})."
    if state == "done":
        return f"{head} This look at the source is tried again in an hour."
    return (
        f"{head} Nothing after the last saved page was imported. Start the import again "
        "when the source works: what is already here is skipped."
    )


def wait_seconds(retry_after: int | None) -> int:
    if retry_after is None or retry_after < 1:
        return DEFAULT_RATE_WAIT
    return min(retry_after, MAX_RATE_WAIT)


# Mapping a source's words onto CeDesk's


def status(raw: str | None, mapping: Mapping[str, str]) -> str:
    """open, pending or resolved. A word the map doesn't know is open: a ticket nobody
    can see because its status was unknown would be worse than one that is open."""
    found = mapping.get((raw or "").strip().lower())
    return found if found in ("open", "pending", "resolved") else "open"


_PRIORITIES = {
    "urgent": "urgent", "critical": "urgent", "p1": "urgent", "highest": "urgent",
    "high": "high", "p2": "high",
    "medium": "medium", "normal": "medium", "standard": "medium", "p3": "medium",
    "low": "low", "p4": "low", "lowest": "low",
}  # fmt: skip


def priority(raw: str | None) -> str:
    return _PRIORITIES.get((raw or "").strip().lower(), "medium")


_CHANNELS = {
    "email": "email", "mail": "email",
    "chat": "chat", "live_chat": "chat", "messenger": "chat", "whatsapp": "chat",
    "sms": "chat", "social": "chat", "twitter": "chat", "facebook": "chat", "instagram": "chat",
    "phone": "phone", "voice": "phone", "call": "phone",
    "web": "web", "form": "web", "web_form": "web", "contact_form": "web",
    "help_center": "web", "help-center": "web",
    "portal": "portal", "api": "api",
}  # fmt: skip


def channel(raw: str | None) -> str:
    """The nearest CeDesk channel. The source stays in `hd_ticket.source`, which is what
    shows "Zendesk (imported)"; CeDesk's channel list is short on purpose."""
    return _CHANNELS.get((raw or "").strip().lower().replace(" ", "_"), "api")


# Teams


_TEAM_WORDS = (
    ("Billing", ("billing", "pay", "account")),
    ("Shipping", ("ship", "deliver", "logistic", "return")),
    ("Tech", ("tier 2", "tech")),
)


def suggest_team(group_name: str, team_names: Sequence[str]) -> str | None:
    """The CeDesk team a source group probably maps to: the same name, else by words
    (billing, pay or account -> Billing; ship, deliver, logistic or return -> Shipping;
    tier 2 or tech -> Tech). Only a team that exists is suggested."""
    lowered = group_name.strip().lower()
    by_name = {t.lower(): t for t in team_names}
    if lowered in by_name:
        return by_name[lowered]
    for team, words in _TEAM_WORDS:
        if team.lower() in by_name and any(w in lowered for w in words):
            return by_name[team.lower()]
    return None


# Text


def subject(raw: str | None) -> str:
    text = " ".join((raw or "").split())
    return text[:SUBJECT_MAX] or "(no subject)"


def body(raw: str | None) -> str:
    return (raw or "").strip()[:BODY_MAX]


def unknown_agent(name: str | None, source: str) -> str:
    """How an agent who isn't in CeDesk is named on their messages (md 8.2 step 5)."""
    return f"{(name or 'An agent').strip()[:80]} ({label(source)}, not in CeDesk)"


def placeholder_email(source: str, external_id: str) -> str:
    """For a contact the source knows by no email: `<source>-<id>@<source>.invalid`, never
    a real address (the same idea as the Cevanos chat customer)."""
    safe = re.sub(r"[^a-z0-9._-]", "-", external_id.lower())[:60].strip("-.") or "unknown"
    return f"{source}-{safe}@{source}.invalid"


def clean_tags(tags: Sequence[str], source: str) -> list[str]:
    """The source's tags made fit CeDesk's tag rule (lower case, `a-z 0-9 _ -`, 40 long),
    plus `imported-<source>` first. More than 20 are cut."""
    out = [f"imported-{source}"]
    for raw in tags:
        tag = re.sub(r"[^a-z0-9_-]+", "-", raw.strip().lower()).strip("-_")[:40]
        if tag and tag not in out:
            out.append(tag)
    return out[:TAG_MAX]


def clean_fields(raw: Mapping[str, Any]) -> dict[str, str]:
    """Custom field values as plain text, under keys that fit `hd_ticket.fields` use."""
    out: dict[str, str] = {}
    for key, value in raw.items():
        name = re.sub(r"[^a-z0-9_]+", "_", str(key).strip().lower()).strip("_")[:40]
        if not name or not name[0].isalpha() or name in out or value in (None, ""):
            continue
        out[name] = str(value)[:FIELD_TEXT_MAX]
        if len(out) >= FIELD_MAX:
            break
    return out


def link_line(name: str, size: int | None, url: str, why: str) -> str:
    """The system line for a file kept as a link instead of a copy."""
    big = f" ({size / (1024 * 1024):.1f} MB)" if size else ""
    return f"Attachment kept as a link ({why}): {name[:120]}{big} · {url[:500]}"


def history_line(source: str, group: str | None, external_id: str, raw_status: str) -> str:
    """The first system line of an imported ticket (md 8.2 step 5)."""
    parts = [f"Imported from {label(source)} with full history"]
    if group:
        parts.append(group)
    parts.append(f"source ID {external_id}")
    parts.append(f"was {raw_status or 'open'}")
    return " · ".join(parts)


# Options of a run


class BadOptions(ValueError):
    pass


@dataclass(frozen=True)
class Options:
    kinds: tuple[str, ...]  # what to bring, in run order
    team_map: Mapping[str, UUID | None]  # source group id -> CeDesk team

    def as_json(self) -> dict[str, Any]:
        return {
            "kinds": list(self.kinds),
            "team_map": {k: None if v is None else str(v) for k, v in self.team_map.items()},
        }


def options(raw: Mapping[str, Any] | None) -> Options:
    """What the Admin chose: `kinds` (default all four) and `team_map` (a source group id
    to a team id, or null for "no team"). Raises `BadOptions`."""
    raw = raw or {}
    wanted = raw.get("kinds")
    if wanted is None:
        kinds = KINDS
    else:
        if not isinstance(wanted, list) or not all(isinstance(k, str) for k in wanted):
            raise BadOptions("kinds must be a list of names.")
        unknown = [k for k in wanted if k not in KINDS]
        if unknown:
            raise BadOptions(f"Unknown kind: {unknown[0]}.")
        kinds = tuple(k for k in KINDS if k in wanted)
    if not kinds:
        raise BadOptions("Pick at least one thing to bring.")
    team_map: dict[str, UUID | None] = {}
    given = raw.get("team_map") or {}
    if not isinstance(given, Mapping):
        raise BadOptions("team_map must map a group id to a team id.")
    for group, team in given.items():
        try:
            team_map[str(group)] = None if team is None else UUID(str(team))
        except ValueError:
            raise BadOptions(f"{team!r} is not a team id.") from None
    return Options(kinds, team_map)


def next_kind(kinds: Sequence[str], done: Sequence[str]) -> str | None:
    """The next kind a run brings, or None when all are done."""
    for kind in kinds:
        if kind not in done:
            return kind
    return None


def is_due(now: datetime, next_run_at: datetime, lease_until: datetime | None) -> bool:
    return next_run_at <= now and (lease_until is None or lease_until <= now)
