"""Email out (md C9, 7.4).

- Admins manage the workspace's support addresses (name, signature, one default), and
  check them (C10): a forwarding check (an email with a one-time code that must come back
  in through CeDesk) and a DNS check (SPF, DKIM, DMARC). Neither stops an address working.
- A reply on an email ticket is queued in the reply's own transaction (`queue_reply`):
  From = the address the customer wrote to (else the default), To = the customer, CC kept
  from their first email with a CC, the signature added, and the threading headers set.
- `send_due` (run by `cedesk worker`) sends what is due and retries with a growing wait;
  when it gives up, the ticket gets a system line.
"""

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.dnscheck import LookupFailed, TxtLookup
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import email as rules
from cedesk.helpdesk.domain import email_verify as verify
from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.domain.attachments import BLOCKED, ready
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.placeholders import render
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import attachments as file_repo
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import email as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.repo.email import Row
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require
from cedesk.mail import Mailer, MailFile, OutgoingEmail, SendError
from cedesk.storage import ObjectMissing, ObjectStore

ADDRESS_FIELDS = ("name", "signature", "is_default", "dkim_selector")

logger = logging.getLogger("cedesk.email")

MAX_ADDRESSES = 20
TAKEN = "That address is already used, in this workspace or another one."
NO_ADDRESS_LINE = "Not emailed: add a support email address in Settings first."
LEASE_SECONDS = 300  # longer than one SMTP attempt can take
BATCH = 20


# Support addresses (Admin settings)


def _address(raw: str) -> str:
    value = rules.normalise_address(raw)
    if value is None:
        raise Invalid("Enter a valid email address, e.g. support@yourcompany.com.")
    return value


def _name(raw: str) -> str:
    value = raw.strip()
    if not 1 <= len(value) <= rules.NAME_MAX:
        raise Invalid(f"The name is 1-{rules.NAME_MAX} characters, e.g. 'Acme Support'.")
    return value


def _signature(raw: str) -> str:
    value = raw.strip()
    if len(value) > rules.SIGNATURE_MAX:
        raise Invalid(f"A signature is at most {rules.SIGNATURE_MAX} characters.")
    return value


async def list_addresses(sm: Sessionmaker, caller: Caller) -> Sequence[Row]:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.list_addresses(s)


async def create_address(
    sm: Sessionmaker,
    caller: Caller,
    *,
    address: str,
    name: str,
    signature: str = "",
    is_default: bool = False,
) -> Row:
    require(caller, Action.settings)
    value, shown, sig = _address(address), _name(name), _signature(signature)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            await repo.lock_addresses(s)
            count = await repo.count_addresses(s)
            if count >= MAX_ADDRESSES:
                raise Invalid(f"A workspace can have at most {MAX_ADDRESSES} addresses.")
            make_default = is_default or count == 0  # the first one is the default
            if make_default:
                await repo.clear_default(s)
            address_id = await repo.insert_address(
                s,
                caller.workspace_id,
                address=value,
                name=shown,
                signature=sig,
                is_default=make_default,
            )
            row = await repo.get_address(s, address_id)
            await audit.record(
                s,
                caller,
                Area.email,
                "created",
                f"Email address {value}",
                target_id=address_id,
                detail=audit_rules.snapshot(row, ADDRESS_FIELDS),
            )
    except IntegrityError as exc:
        raise Conflict(TAKEN) from exc
    assert row is not None
    return row


async def update_address(
    sm: Sessionmaker,
    caller: Caller,
    address_id: UUID,
    *,
    name: str | None = None,
    signature: str | None = None,
    is_default: bool | None = None,
    dkim_selector: str | None = None,
) -> Row:
    """`dkim_selector`: "" clears it. The address itself can't change (replies already
    sent came from it): add a new one and delete the old one instead."""
    require(caller, Action.settings)
    if is_default is False:
        raise Invalid("Make another address the default instead.")
    shown = None if name is None else _name(name)
    sig = None if signature is None else _signature(signature)
    async with workspace_session(sm, caller.workspace_id) as s:
        await repo.lock_addresses(s)
        before = await repo.get_address(s, address_id)
        if before is None:
            raise NotFound("Email address not found.")
        await repo.update_address(s, address_id, name=shown, signature=sig)
        if dkim_selector is not None:
            await repo.set_dkim_selector(s, address_id, _selector(dkim_selector))
        if is_default:
            await repo.clear_default(s)
            await repo.make_default(s, address_id)
        row = await repo.get_address(s, address_id)
        assert row is not None
        changed = audit_rules.changes(before, row, fields=ADDRESS_FIELDS)
        if changed:
            await audit.record(
                s,
                caller,
                Area.email,
                "changed",
                f"Email address {row['address']}",
                target_id=address_id,
                detail={"changes": changed},
            )
    return row


async def delete_address(sm: Sessionmaker, caller: Caller, address_id: UUID) -> None:
    """Emails already queued keep their From. If the default goes, the oldest remaining
    address becomes the default."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        await repo.lock_addresses(s)
        row = await repo.get_address(s, address_id)
        if row is None or not await repo.delete_address(s, address_id):
            raise NotFound("Email address not found.")
        await audit.record(
            s,
            caller,
            Area.email,
            "deleted",
            f"Email address {row['address']}",
            target_id=address_id,
            detail=audit_rules.snapshot(row, ADDRESS_FIELDS),
        )
        if row["is_default"]:
            oldest = await repo.oldest_address(s)
            if oldest is not None:
                await repo.make_default(s, oldest)


async def send_test(sm: Sessionmaker, caller: Caller, address_id: UUID) -> str:
    """Queues a short email from this address to the Admin, to check the mail server.
    Returns where it goes."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get_address(s, address_id)
        if row is None:
            raise NotFound("Email address not found.")
        me = await auth_repo.member_email(s, caller.member_id)
        brief = await ticket_repo.member_brief(s, caller.member_id)
        if me is None or brief is None:
            raise NotFound("Member not found.")
        # Filled as if the Admin replied to themselves, so they see the real signature.
        name = str(brief["name"])
        signature = render(str(row["signature"]), customer_name=name, agent_name=name, fields={})
        await repo.enqueue(
            s,
            caller.workspace_id,
            ticket_id=None,
            message_id=None,
            from_address=str(row["address"]),
            from_name=str(row["name"]),
            to=[me],
            cc=[],
            subject="CeDesk test email",
            body=rules.with_signature(
                "This is a test from CeDesk. If you can read it, replies from this address "
                "will reach your customers.",
                signature,
            ),
            header_message_id=rules.new_message_id(str(row["address"])),
            in_reply_to=None,
            references=[],
        )
    return me


# Checking an address (C10)

CHECK_WAIT = timedelta(minutes=1)
DNS_BUDGET_SECONDS = 15  # no new lookup starts after this; each one stops after 5 s


def _selector(value: str) -> str | None:
    value = value.strip().lower()
    if not value:
        return None
    if not verify.SELECTOR.fullmatch(value):
        raise Invalid("A DKIM selector is letters, digits, dots, dashes or underscores.")
    return value


async def send_forwarding_check(sm: Sessionmaker, caller: Caller, address_id: UUID) -> Row:
    """Emails the address a one-time code (from itself, as replies are). When the email
    comes back in through CeDesk, `receive` marks the address verified. A new check
    replaces the last one's code; at most one a minute."""
    require(caller, Action.settings)
    code = verify.new_code()
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get_address(s, address_id)
        if row is None:
            raise NotFound("Email address not found.")
        sent = row["verify_sent_at"]
        if sent is not None and datetime.now(UTC) - sent < CHECK_WAIT:
            raise Conflict("A check was sent less than a minute ago. Wait for it first.")
        address = str(row["address"])
        email_id = await repo.enqueue(
            s,
            caller.workspace_id,
            ticket_id=None,
            message_id=None,
            from_address=address,
            from_name=str(row["name"]),
            to=[address],
            cc=[],
            subject=verify.CODE_SUBJECT.format(code=code),
            body=(
                f"CeDesk sent this to {address} to check that emails to it reach CeDesk. "
                "Nothing to do: when it arrives through your forwarding (or the mailbox "
                "CeDesk reads), the address shows as verified. It makes no ticket."
            ),
            header_message_id=rules.new_message_id(address),
            in_reply_to=None,
            references=[],
        )
        await repo.set_verify_code(s, address_id, verify.code_hash(code), email_id)
        found = await repo.get_address(s, address_id)
    assert found is not None
    return found


async def verify_forwarding(sm: Sessionmaker, workspace_id: UUID, code: str) -> bool:
    """A forwarding-check email came in (email in calls this). True: an address was
    verified; False: the code is unknown, used or too old."""
    since = datetime.now(UTC) - verify.CODE_LIFETIME
    async with workspace_session(sm, workspace_id) as s:
        found = await repo.verify_by_code(s, verify.code_hash(code), since)
    if found is not None:
        logger.info("support address verified", extra={"event": "email_verify"})
    return found is not None


def _as_dict(result: verify.Result) -> dict[str, Any]:
    return {"status": result.status.value, "record": result.record, "note": result.note}


async def check_dns(
    sm: Sessionmaker,
    caller: Caller,
    address_id: UUID,
    lookup: TxtLookup,
    *,
    spf_include: str | None = None,
) -> Row:
    """Reads the address's SPF, DKIM and DMARC records now and keeps what was found.
    The lookups run outside any transaction."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get_address(s, address_id)
    if row is None:
        raise NotFound("Email address not found.")
    domain = verify.domain_of(str(row["address"]))
    selector = row["dkim_selector"]

    deadline = time.monotonic() + DNS_BUDGET_SECONDS

    async def records(name: str) -> list[str] | None:
        """None: DNS didn't answer, or the check already took its time."""
        if time.monotonic() > deadline:
            return None
        try:
            return await lookup.txt(name)
        except LookupFailed:
            return None

    def failed(name: str) -> verify.Result:
        return verify.Result(verify.Check.error, None, f"DNS didn't answer for {name}; try again.")

    async def spf_reached(first: list[str]) -> frozenset[str]:
        """Every name the SPF record leads to, at most SPF_LOOKUPS lookups deep."""
        record = verify.spf_record(first)
        todo = verify.spf_links(record) if record else []
        reached: set[str] = set()
        while todo and len(reached) < verify.SPF_LOOKUPS:
            name = todo.pop(0)
            if name in reached:
                continue
            reached.add(name)
            found = verify.spf_record(await records(name) or [])
            todo += verify.spf_links(found) if found else []
        return frozenset(reached)

    spf_txt = await records(domain)
    reached = await spf_reached(spf_txt) if spf_txt is not None and spf_include else frozenset()
    dmarc_txt = await records(f"_dmarc.{domain}")
    dkim_name = f"{selector}._domainkey.{domain}"
    dkim_txt = await records(dkim_name) if selector else []
    found = {
        "domain": domain,
        "spf": _as_dict(failed(domain) if spf_txt is None
                        else verify.spf(domain, spf_txt, spf_include, reached)),
        "dkim": _as_dict(failed(dkim_name) if dkim_txt is None
                         else verify.dkim(domain, selector, dkim_txt)),
        "dmarc": _as_dict(failed(f"_dmarc.{domain}") if dmarc_txt is None
                          else verify.dmarc(domain, dmarc_txt)),
    }  # fmt: skip
    async with workspace_session(sm, caller.workspace_id) as s:
        await repo.save_dns(s, address_id, found)
        saved = await repo.get_address(s, address_id)
    if saved is None:  # deleted meanwhile
        raise NotFound("Email address not found.")
    return saved


# Replies


def _stored(row: Row) -> rules.Address:
    return rules.Address(
        address=str(row["address"]),
        name=str(row["name"]),
        signature=str(row["signature"]),
        is_default=bool(row["is_default"]),
    )


async def reply_cc(s: AsyncSession, ticket_id: UUID, customer: str) -> list[str]:
    """Who a reply on this ticket will copy: the same answer `queue_reply` uses, so the
    reply box can show it before the agent sends. Empty for a ticket with no email."""
    _first, first_cc, _last = await repo.customer_emails(s, ticket_id)
    if not isinstance(first_cc, dict):
        return []
    ours = {a.address for a in (_stored(r) for r in await repo.list_addresses(s))}
    return rules.cc_for_reply(first_cc, ours, customer)


async def queue_reply(
    s: AsyncSession,
    workspace_id: UUID,
    detail: Mapping[Any, Any],
    message_id: UUID,
    body: str,
    who: str,
) -> None:
    """Inside the reply's transaction: queues the email for a reply on an email ticket, and
    records its headers on the message so the customer's answer threads back. With no
    support address yet, the ticket gets a system line instead."""
    ticket_id = detail["id"]
    addresses = [_stored(r) for r in await repo.list_addresses(s)]
    first, first_cc, last = await repo.customer_emails(s, ticket_id)
    written_to = rules.addresses_in(first.get("to")) if isinstance(first, dict) else []
    sender = rules.pick_from(addresses, written_to)
    if sender is None:
        await ticket_repo.insert_message(
            s,
            workspace_id=workspace_id,
            ticket_id=ticket_id,
            kind="system",
            body=NO_ADDRESS_LINE,
            author_member_id=None,
            author_name=None,
        )
        return

    customer = str(detail["contact_email"])
    cc = rules.cc_for_reply(
        first_cc if isinstance(first_cc, dict) else None,
        {a.address for a in addresses},
        customer,
    )
    last_email = last if isinstance(last, dict) else None
    subject = rules.reply_subject(
        str((last_email or {}).get("subject") or detail["subject"]), label(detail["number"])
    )
    in_reply_to, references = rules.thread(last_email)
    header_id = rules.new_message_id(sender.address)
    signature = render(
        sender.signature,
        customer_name=str(detail["contact_name"]),
        agent_name=who,
        fields=detail["fields"],
    )
    await repo.enqueue(
        s,
        workspace_id,
        ticket_id=ticket_id,
        message_id=message_id,
        from_address=sender.address,
        from_name=sender.name,
        to=[customer],
        cc=cc,
        subject=subject,
        body=rules.with_signature(body, signature),
        header_message_id=header_id,
        in_reply_to=in_reply_to,
        references=references,
    )
    await repo.set_message_email(
        s,
        message_id,
        {
            "message_id": header_id,
            "from": sender.address,
            "to": [customer],
            "cc": cc,
            "subject": subject,
            "in_reply_to": in_reply_to,
            "references": references,
        },
    )


# Sending (the worker)


@dataclass
class SendResult:
    sent: int = 0
    retrying: int = 0
    failed: int = 0


async def _files(
    store: ObjectStore | None, rows: Sequence[Row], *, scanning: bool
) -> list[MailFile]:
    """A reply's files, read from storage just before sending. With scanning on, a file
    not checked yet holds the email back (tried again later)."""
    if rows and store is None:
        raise SendError("This worker has no file storage set up.")
    files = []
    for f in rows:
        if f["scan_status"] in BLOCKED:
            raise SendError(f"{f['name']} failed the virus scan.", permanent=True)
        if not ready(f["scan_status"], scanning=scanning):
            raise SendError(f"{f['name']} is waiting for the virus scan.")
        assert store is not None
        try:
            data = await store.get(str(f["object_key"]))
        except ObjectMissing:
            raise SendError(f"{f['name']} is missing from storage.", permanent=True) from None
        files.append(MailFile(name=str(f["name"]), mime=str(f["mime"]), data=data))
    return files


def _outgoing(row: Row, files: Sequence[MailFile] = ()) -> OutgoingEmail:
    return OutgoingEmail(
        from_address=str(row["from_address"]),
        from_name=str(row["from_name"]),
        to=list(row["to_addresses"]),
        cc=list(row["cc_addresses"]),
        subject=str(row["subject"]),
        text=str(row["body_text"]),
        message_id=row["header_message_id"],
        in_reply_to=row["in_reply_to"],
        references=list(row["references_ids"]),
        attachments=list(files),
        html=row.get("body_html"),
    )


async def _give_up(s: AsyncSession, row: Row, error: str) -> None:
    await repo.mark_failed(s, row["id"], error)
    if row["ticket_id"] is not None:
        to = ", ".join(row["to_addresses"])
        await ticket_repo.insert_message(
            s,
            workspace_id=row["workspace_id"],
            ticket_id=row["ticket_id"],
            kind="system",
            body=f"Email to {to} could not be sent: {error}"[:500],
            author_member_id=None,
            author_name=None,
        )


async def _send_workspace(
    sm: Sessionmaker,
    mailer: Mailer,
    store: ObjectStore | None,
    workspace_id: UUID,
    result: SendResult,
    batch: int,
    scanning: bool,
) -> None:
    async with workspace_session(sm, workspace_id) as s:
        if not await repo.has_due(s):
            return
        rows = await repo.claim_due(s, batch, LEASE_SECONDS)
        message_ids = [r["message_id"] for r in rows if r["message_id"] is not None]
        file_rows = await file_repo.for_messages(s, message_ids) if message_ids else []
    files_of: dict[UUID, list[Row]] = {}
    for f in file_rows:
        files_of.setdefault(f["message_id"], []).append(f)
    # Sent outside any transaction: a slow mail server never holds a database lock.
    for row in rows:
        error: SendError | None = None
        try:
            files = await _files(store, files_of.get(row["message_id"], []), scanning=scanning)
            await mailer.send(_outgoing(row, files))
        except SendError as exc:
            error = exc
        async with workspace_session(sm, workspace_id) as s:
            if error is None:
                await repo.mark_sent(s, row["id"])
                result.sent += 1
                continue
            wait = None if error.permanent else rules.retry_after(int(row["attempts"]))
            logger.warning(
                "email not sent",
                extra={"workspace_id": workspace_id, "error": str(error), "event": "email"},
            )
            if wait is None:
                await _give_up(s, row, str(error))
                result.failed += 1
            else:
                at = datetime.now(UTC) + timedelta(seconds=wait)
                await repo.mark_retry(s, row["id"], str(error), at)
                result.retrying += 1


async def send_due(
    sm: Sessionmaker,
    mailer: Mailer,
    *,
    store: ObjectStore | None = None,
    scanning: bool = False,
    batch: int = BATCH,
) -> SendResult:
    """One pass over every workspace: sends what is due, each workspace under its own RLS.
    `store`: where the files sent with replies are read from. `scanning`: virus scan is
    on, so only files found clean are sent."""
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    result = SendResult()
    for workspace_id in workspace_ids:
        try:
            await _send_workspace(sm, mailer, store, workspace_id, result, batch, scanning)
        except Exception:
            # One workspace's trouble must not stop the others; its claimed emails are
            # tried again when their lease runs out.
            logger.exception("email pass failed", extra={"workspace_id": workspace_id})
    return result


# Failed emails (Admins: md 2B "Reliability", the dead-letter list)


@dataclass(frozen=True)
class FailedEmails:
    emails: Sequence[Row]  # newest first
    next_cursor: str | None


async def failed_emails(
    sm: Sessionmaker, caller: Caller, *, cursor: str | None = None, limit: int = 50
) -> FailedEmails:
    require(caller, Action.settings)
    before = None
    if cursor:
        try:
            before = paging.decode_time(cursor, "failed_email")
        except paging.CursorError as exc:
            raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = await repo.failed(s, before=before, limit=limit + 1)
    page, next_cursor = paging.time_page(rows, limit, "failed_email")
    return FailedEmails(emails=page, next_cursor=next_cursor)


async def retry_email(sm: Sessionmaker, caller: Caller, email_id: UUID) -> None:
    """Puts a failed email back in the queue; the worker sends it on its next pass. The
    ticket gets a line saying who did it."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.retry_failed(s, email_id)
        if row is None:
            raise NotFound("No failed email with that id that can be sent again.")
        if row["ticket_id"] is not None:
            brief = await ticket_repo.member_brief(s, caller.member_id)
            who = str(brief["name"]) if brief else "An Admin"
            await ticket_repo.insert_message(
                s, workspace_id=caller.workspace_id, ticket_id=row["ticket_id"], kind="system",
                body=f"{who} sent the failed email again."[:500],
                author_member_id=None, author_name=None,
            )  # fmt: skip
