"""The CSV importer (md M1-M8, section 8.3): one row per message, grouped by ticket id.

The file is uploaded to the object store, read once per run slice and deleted when the run
ends. No account, no network: this is the importer the framework is proven on.

Columns (the header is matched ignoring case and spaces):

- required: `ticket_id`, `subject`, `requester_email`, `message_from` (customer, agent or
  note), `message_body`, `message_created_at` (ISO 8601: `2025-03-01T10:15:00Z`);
- optional, taken from the first row of a ticket that has them: `status`, `priority`,
  `channel`, `team`, `tags` (separated by `;`), `ticket_created_at`, `resolved_at`,
  `requester_name`, `assignee_email`, `csat_score`, `csat_comment`, and any number of
  `field.<key>` columns (custom field values);
- optional, per row: `message_id`, `author_name`, `author_email`.

A row that can't be read (no ticket id, no body, a date that isn't one) is reported with
its line number and left out; it never stops the import.
"""

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import (
    AccountInfo,
    Contact,
    Counts,
    Csat,
    Group,
    Message,
    Page,
    Ticket,
)
from cedesk.helpdesk.domain.portal import normalise_email

MAX_BYTES = 25 * 1024 * 1024
MAX_ROWS = 200_000
REQUIRED = (
    "ticket_id", "subject", "requester_email", "message_from", "message_body",
    "message_created_at",
)  # fmt: skip
STATUS_MAP = {
    "open": "open", "new": "open", "pending": "pending", "hold": "pending",
    "on-hold": "pending", "waiting": "pending", "solved": "resolved", "closed": "resolved",
    "resolved": "resolved",
}  # fmt: skip
_FROM = {
    "customer": "customer", "requester": "customer", "agent": "agent", "staff": "agent",
    "note": "note", "internal": "note",
}  # fmt: skip
_BAD_ROWS_SHOWN = 20


class BadFile(ValueError):
    """The file can't be read as this CSV at all (not UTF-8, no header, a column missing)."""


@dataclass(frozen=True)
class BadRow:
    line: int
    reason: str


@dataclass
class _Draft:
    external_id: str
    subject: str = ""
    status: str = ""
    priority: str | None = None
    channel: str | None = None
    team: str | None = None
    tags: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    requester_email: str = ""
    requester_name: str = ""
    assignee_email: str | None = None
    csat: Csat | None = None
    fields: dict[str, str] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)


def _date(raw: str) -> datetime:
    value = raw.strip()
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class CsvConnector:
    """Reads the whole file once (it is at most 25 MB) and serves it in pages."""

    source = "csv"
    status_map: Mapping[str, str] = STATUS_MAP

    def __init__(self, data: bytes, *, filename: str = "file.csv") -> None:
        self.filename = filename
        self.bad_rows: list[BadRow] = []
        self._tickets: dict[str, Ticket] = {}
        self._groups: dict[str, Group] = {}
        self._rows = 0
        self._parse(data)

    # Parsing

    def _parse(self, data: bytes) -> None:
        if len(data) > MAX_BYTES:
            raise BadFile("The file is larger than 25 MB.")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise BadFile("The file isn't UTF-8. Save it as CSV (UTF-8).") from None
        reader = csv.DictReader(io.StringIO(text))
        header = {
            (name or "").strip().lower().replace(" ", "_") for name in reader.fieldnames or []
        }
        missing = [c for c in REQUIRED if c not in header]
        if missing:
            raise BadFile("The header is missing: " + ", ".join(missing) + ".")
        drafts: dict[str, _Draft] = {}
        for row_number, raw in enumerate(reader, start=2):
            self._rows += 1
            if self._rows > MAX_ROWS:
                raise BadFile(f"More than {MAX_ROWS:,} rows. Split the file.")
            row = {(k or "").strip().lower().replace(" ", "_"): (v or "").strip()
                   for k, v in raw.items() if k}  # fmt: skip
            try:
                self._row(drafts, row)
            except ValueError as exc:
                self.bad_rows.append(BadRow(row_number, str(exc)))
        for draft in drafts.values():
            self._finish(draft)

    def _row(self, drafts: dict[str, _Draft], row: Mapping[str, str]) -> None:
        ticket_id = row.get("ticket_id", "")
        if not ticket_id:
            raise ValueError("no ticket_id")
        body = rules.body(row.get("message_body"))
        if not body:
            raise ValueError("no message_body")
        kind = _FROM.get(row.get("message_from", "").lower())
        if kind is None:
            raise ValueError("message_from must be customer, agent or note")
        try:
            at = _date(row.get("message_created_at", ""))
        except ValueError:
            raise ValueError("message_created_at isn't a date (use 2025-03-01T10:15:00Z)") from None
        # Everything in the row is checked before any of it is kept, so a row that is
        # reported as bad leaves no trace in its ticket.
        ticket_level = self._ticket_columns(row)
        draft = drafts.setdefault(ticket_id, _Draft(ticket_id))
        draft.messages.append(
            Message(
                kind=kind,
                body=body,
                created_at=at,
                external_id=row.get("message_id") or None,
                author_name=row.get("author_name") or None,
                author_email=row.get("author_email") or None,
            )
        )
        self._keep(draft, row, ticket_level)

    @staticmethod
    def _ticket_columns(row: Mapping[str, str]) -> dict[str, Any]:
        """The ticket's dates and rating in this row, read and checked (nothing is kept)."""
        found: dict[str, Any] = {}
        for column, attr in (("ticket_created_at", "created_at"), ("resolved_at", "resolved_at")):
            if row.get(column):
                try:
                    found[attr] = _date(row[column])
                except ValueError:
                    raise ValueError(f"{column} isn't a date (use 2025-03-01T10:15:00Z)") from None
        if row.get("csat_score"):
            if row["csat_score"] not in ("1", "2", "3", "4", "5"):
                raise ValueError("csat_score must be 1 to 5")
            found["csat"] = Csat(int(row["csat_score"]), row.get("csat_comment") or None)
        return found

    @staticmethod
    def _keep(draft: _Draft, row: Mapping[str, str], found: Mapping[str, Any]) -> None:
        """The ticket's own columns come from the first row of it that has them."""
        draft.subject = draft.subject or row.get("subject", "")
        draft.status = draft.status or row.get("status", "")
        draft.priority = draft.priority or row.get("priority") or None
        draft.channel = draft.channel or row.get("channel") or None
        draft.team = draft.team or row.get("team") or None
        draft.requester_email = draft.requester_email or row.get("requester_email", "")
        draft.requester_name = draft.requester_name or row.get("requester_name", "")
        draft.assignee_email = draft.assignee_email or row.get("assignee_email") or None
        if not draft.tags and row.get("tags"):
            draft.tags = [t.strip() for t in row["tags"].split(";") if t.strip()]
        if draft.created_at is None:
            draft.created_at = found.get("created_at")
        if draft.resolved_at is None:
            draft.resolved_at = found.get("resolved_at")
        if draft.csat is None:
            draft.csat = found.get("csat")
        for column, value in row.items():
            if column.startswith("field.") and value and column[6:] not in draft.fields:
                draft.fields[column[6:]] = value

    def _finish(self, draft: _Draft) -> None:
        if not draft.requester_email:
            self.bad_rows.append(BadRow(0, f"ticket {draft.external_id} has no requester_email"))
            return
        email = normalise_email(draft.requester_email)
        messages = sorted(draft.messages, key=lambda m: m.created_at)
        group = None
        if draft.team:
            group = self._groups.setdefault(draft.team, Group(draft.team, draft.team)).external_id
        name = draft.requester_name or (email or draft.requester_email).split("@")[0]
        requester = Contact(
            external_id=(email or draft.requester_email).lower(), email=email, name=name
        )
        self._tickets[draft.external_id] = Ticket(
            external_id=draft.external_id,
            subject=draft.subject,
            status=draft.status,
            created_at=draft.created_at or messages[0].created_at,
            requester=requester,
            messages=messages,
            priority=draft.priority,
            channel=draft.channel,
            group_id=group,
            assignee_email=draft.assignee_email,
            tags=draft.tags,
            fields=draft.fields,
            resolved_at=draft.resolved_at,
            csat=draft.csat,
        )

    # The connector interface

    @classmethod
    async def load(cls, store: Any, settings: Mapping[str, Any]) -> "CsvConnector":
        data = await store.get(str(settings["file_key"]))
        return cls(data, filename=str(settings.get("filename", "file.csv")))

    async def check(self) -> AccountInfo:
        return AccountInfo(self.filename)

    def _contacts(self) -> list[Contact]:
        seen: dict[str, Contact] = {}
        for ticket in self._tickets.values():
            seen.setdefault(ticket.requester.external_id, ticket.requester)
        return list(seen.values())

    async def discover(self) -> Counts:
        return Counts(
            contacts=len(self._contacts()), tickets=len(self._tickets), groups=len(self._groups)
        )

    async def groups(self) -> Sequence[Group]:
        return list(self._groups.values())

    def warnings(self) -> list[str]:
        """What the Admin should read before running: rows left out, with the first few."""
        if not self.bad_rows:
            return []
        shown = "; ".join(f"line {b.line}: {b.reason}" for b in self.bad_rows[:_BAD_ROWS_SHOWN])
        more = len(self.bad_rows) - _BAD_ROWS_SHOWN
        tail = f" (and {more} more)" if more > 0 else ""
        return [f"{len(self.bad_rows)} rows can't be imported and are left out: {shown}{tail}"]

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        if kind == "contacts":
            items: Sequence[Any] = self._contacts()
        elif kind == "tickets":
            items = list(self._tickets.values())
        else:
            return Page([], None)  # a CSV has no articles or saved replies
        start = int(cursor["offset"]) if cursor else 0
        chunk = items[start : start + rules.PAGE_MAX]
        end = start + len(chunk)
        return Page(chunk, {"offset": end} if end < len(items) else None)
