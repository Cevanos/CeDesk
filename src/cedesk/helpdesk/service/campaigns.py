"""Email campaigns (feature-gap plan phase 6): one email to the customers of a segment,
with an unsubscribe link and a suppression list, and its results.

Who: `manage_campaigns`, Admins only (never a custom role or an API key). Audited (area
`campaigns`) with ids and counts only: never an address or the email's text.

Before any send (`send`): a subject, a body, a segment, a support address that passed
the forwarding check and whose domain has SPF and DKIM right, the workspace's postal
address (Campaigns > Settings; it is in every email's footer), a public https address
for the links, an explicit `confirm`, and the caps (per campaign and per 24 hours per
workspace). Recipients are copied from the segment when it starts: erased customers,
placeholder and role addresses, support addresses of any workspace and suppressed
addresses (or a customer any of whose addresses is) are left out.

Sending (`run_due`, the worker): each recipient row is claimed with FOR UPDATE SKIP
LOCKED and marked `sending` (with its link tokens' hashes) in its own transaction before
the mail server gets it, and marked sent after. A temporary error is retried up to 3
times; a worker that stopped mid-send leaves the row `failed` (it may have gone out, so
it is never sent twice). A recipient suppressed or erased since it was queued is skipped.
At most `per_minute` a minute and `daily_cap` a day per workspace go out.

Results: bounces and spam reports come back through email in (`handle_report`): a hard
bounce or a complaint puts the address on the suppression list; too many hard bounces
pause the campaign. Unsubscribes (the page or the mail app's one-click) and opens (an
image, if tracking is on) come in through the public routes.

Transactional email is separate on purpose: ticket replies, transcripts, CSAT surveys,
auto-close notices and account emails go through `hd_email_out` as before and never read
the suppression list. A customer who unsubscribed from campaigns still gets answers to
their tickets. Campaign emails are not in `hd_email_out` (they never hold up a reply).
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import auth as token_rules
from cedesk.helpdesk.domain import bounces, paging, report_csv
from cedesk.helpdesk.domain import campaigns as rules
from cedesk.helpdesk.domain import contacts as contact_rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.campaigns import Limits, Links, Reason, Source, State
from cedesk.helpdesk.domain.email import new_message_id, normalise_address
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import audit as audit_repo
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import campaigns as repo
from cedesk.helpdesk.repo import email as email_repo
from cedesk.helpdesk.repo import segments as segment_repo
from cedesk.helpdesk.repo.campaigns import Row
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service import contacts as contact_service
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require
from cedesk.mail import Mailer, OutgoingEmail, SendError

logger = logging.getLogger("cedesk.campaigns")

NOT_FOUND = "Campaign not found."
SAMPLE_NAME = "Alex Sample"
PREVIEW_TOKEN = "preview"  # noqa: S105 (not a secret: a link that goes nowhere)


@dataclass(frozen=True)
class Config:
    limits: Limits
    links: Links
    require_https: bool = True


def _clean(fn: Any, value: Any) -> Any:
    try:
        return fn(value)
    except rules.CampaignError as exc:
        raise Invalid(str(exc)) from exc


async def _campaign(s: AsyncSession, campaign_id: UUID, *, lock: bool = False) -> Row:
    row = await repo.get_campaign(s, campaign_id, lock=lock)
    if row is None:
        raise NotFound(NOT_FOUND)
    return row


async def _workspace_name(s: AsyncSession) -> str:
    row = await auth_repo.workspace_row(s)
    return str(row["name"]) if row else ""


# Settings: the postal address


async def get_settings(sm: Sessionmaker, caller: Caller) -> Row | None:
    require(caller, Action.manage_campaigns)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.get_settings(s)


async def save_settings(sm: Sessionmaker, caller: Caller, *, postal_address: str) -> Row:
    require(caller, Action.manage_campaigns)
    value = _clean(rules.clean_postal, postal_address)
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await repo.get_settings(s)
        await repo.save_settings(s, caller.workspace_id, postal_address=value,
                                 by=caller.member_id)  # fmt: skip
        if before is None or before["postal_address"] != value:
            await audit.record(s, caller, Area.campaigns, "settings_changed",
                               "Campaign settings", detail={"postal_address": True})  # fmt: skip
        row = await repo.get_settings(s)
    assert row is not None
    return row


# The suppression list


@dataclass(frozen=True)
class SuppressionPage:
    entries: Sequence[Row]
    counts: Mapping[str, int]
    next_cursor: str | None


async def list_suppressions(
    sm: Sessionmaker,
    caller: Caller,
    *,
    reason: Reason | None = None,
    email: str | None = None,
    cursor: str | None = None,
    limit: int = paging.DEFAULT_LIMIT,
) -> SuppressionPage:
    """`email`: is this one address on the list (exact, also after an erase)."""
    require(caller, Action.manage_campaigns)
    before = None
    if cursor:
        try:
            before = paging.decode_time(cursor, "suppression")
        except paging.CursorError as exc:
            raise Invalid(str(exc)) from exc
    wanted = None
    if email is not None:
        address = normalise_address(email)
        if address is None:
            raise Invalid("Enter a whole email address to look one up.")
        wanted = rules.email_hash(caller.workspace_id, address)
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = await repo.suppression_page(
            s, before=before, limit=limit + 1,
            reason=reason.value if reason else None, email_hash=wanted,
        )  # fmt: skip
        counts = await repo.suppression_counts(s)
    found, next_cursor = paging.time_page(rows, limit, "suppression")
    return SuppressionPage(found, counts, next_cursor)


async def add_suppression(
    sm: Sessionmaker, caller: Caller, *, email: str, note: str | None = None
) -> Row:
    """An Admin adds an address (reason `manual`): campaigns never go to it."""
    require(caller, Action.manage_campaigns)
    address = normalise_address(email)
    if address is None:
        raise Invalid("Enter a valid email address.")
    clean = _clean(rules.clean_note, note)
    async with workspace_session(sm, caller.workspace_id) as s:
        new_id = await repo.suppress(
            s, caller.workspace_id, email_hash=rules.email_hash(caller.workspace_id, address),
            email=address, reason=Reason.manual.value, source=Source.admin.value, note=clean,
            created_by=caller.member_id,
        )  # fmt: skip
        if new_id is None:
            raise Conflict("That address is already on the suppression list.")
        await audit.record(s, caller, Area.campaigns, "suppression_added",
                           "Suppression list", target_id=new_id,
                           detail={"reason": Reason.manual.value})  # fmt: skip
        row = await repo.get_suppression(s, new_id)
    assert row is not None
    return row


async def remove_suppression(
    sm: Sessionmaker,
    caller: Caller,
    suppression_id: UUID,
    *,
    confirm_consent: bool = False,
    justification: str | None = None,
) -> None:
    """Takes an address off the list, so campaigns may go to it again.

    - `manual` and `hard_bounce`: just removed (a bounce may have been a full mailbox).
    - `unsubscribed` and `complaint`: the person asked not to get campaigns. Only with
      `confirm_consent` and a `justification` (10+ characters: when and how they asked
      to get them again), kept in the audit log (any address in it hidden).
    - `erased`, or any entry left as a hash by an erase: never (the person is gone; this
      entry is what keeps them from being mailed).
    """
    require(caller, Action.manage_campaigns)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get_suppression(s, suppression_id)
        if row is None:
            raise NotFound("Suppression entry not found.")
        reason = Reason(row["reason"])
        if reason is Reason.erased or row["email"] is None:
            raise Conflict(
                "This entry keeps an erased customer from ever being emailed; it can't be removed."
            )
        detail: dict[str, Any] = {"reason": reason.value}
        if reason in rules.PROTECTED:
            why = " ".join((justification or "").split())
            if not confirm_consent or len(why) < rules.JUSTIFY_MIN:
                raise Conflict(
                    "This person asked not to get campaigns. Remove it only if they asked to "
                    "get them again: send confirm_consent true and a justification (when "
                    "and how they asked)."
                )
            detail["justification"] = rules.without_addresses(why)
        await repo.delete_suppression(s, suppression_id)
        await audit.record(s, caller, Area.campaigns, "suppression_removed",
                           "Suppression list", target_id=suppression_id,
                           detail=detail)  # fmt: skip


# Campaigns: drafts


@dataclass(frozen=True)
class Stats:
    recipients: int = 0
    queued: int = 0
    sending: int = 0
    sent: int = 0
    failed: int = 0
    bounced: int = 0
    skipped: int = 0
    cancelled: int = 0
    opened: int = 0
    opens: int = 0
    unsubscribed: int = 0
    complained: int = 0


def _stats(row: Mapping[Any, Any] | None) -> Stats:
    if row is None:
        return Stats()
    return Stats(**{k: int(row[k]) for k in Stats.__dataclass_fields__})


@dataclass(frozen=True)
class CampaignView:
    campaign: Row
    stats: Stats
    problems: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CampaignPage:
    campaigns: Sequence[CampaignView]
    next_cursor: str | None


async def _fields(s: AsyncSession, raw: Mapping[str, Any]) -> dict[str, Any]:
    """The given draft fields, checked (the segment and address must be this workspace's)."""
    out: dict[str, Any] = {}
    if "name" in raw:
        out["name"] = _clean(rules.clean_name, raw["name"])
    if "subject" in raw:
        out["subject"] = _clean(rules.clean_subject, raw["subject"])
    if "body" in raw:
        out["body"] = _clean(rules.clean_body, raw["body"])
    if "track_opens" in raw:
        out["track_opens"] = bool(raw["track_opens"])
    if "segment_id" in raw:
        if raw["segment_id"] is not None and await segment_repo.get(s, raw["segment_id"]) is None:
            raise Invalid("That segment doesn't exist.")
        out["segment_id"] = raw["segment_id"]
    if "from_address_id" in raw:
        if (
            raw["from_address_id"] is not None
            and await email_repo.get_address(s, raw["from_address_id"]) is None
        ):
            raise Invalid("Pick one of this workspace's support addresses.")
        out["from_address_id"] = raw["from_address_id"]
    return out


async def _problems(s: AsyncSession, c: Row, config: Config) -> list[str]:
    """What stops this campaign being sent now (empty: it can)."""
    address = (
        await email_repo.get_address(s, c["from_address_id"]) if c["from_address_id"] else None
    )
    found = rules.ready_problems(
        subject=str(c["subject"]),
        body=str(c["body"]),
        segment=c["segment_id"] is not None,
        sender=address is not None,
        sender_verified=address is not None and address["forwarding_verified_at"] is not None,
        dns_ok=address is not None and rules.dns_ok(address["dns_result"]),
        postal_address=await repo.get_settings(s) is not None,
    )
    links = config.links.problem(require_https=config.require_https)
    return found + ([links] if links else [])


async def _view(s: AsyncSession, c: Row, config: Config | None) -> CampaignView:
    found = await repo.stats(s, [c["id"]])
    problems = (
        await _problems(s, c, config) if config is not None and c["state"] == State.draft else []
    )
    return CampaignView(c, _stats(found.get(c["id"])), problems)


async def list_campaigns(
    sm: Sessionmaker,
    caller: Caller,
    *,
    state: State | None = None,
    cursor: str | None = None,
    limit: int = paging.DEFAULT_LIMIT,
) -> CampaignPage:
    require(caller, Action.manage_campaigns)
    before = None
    if cursor:
        try:
            before = paging.decode_time(cursor, "campaign")
        except paging.CursorError as exc:
            raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = await repo.campaign_page(
            s, before=before, limit=limit + 1, state=state.value if state else None
        )
        found, next_cursor = paging.time_page(rows, limit, "campaign")
        totals = await repo.stats(s, [r["id"] for r in found])
    views = [CampaignView(r, _stats(totals.get(r["id"]))) for r in found]
    return CampaignPage(views, next_cursor)


async def create_campaign(
    sm: Sessionmaker, caller: Caller, raw: Mapping[str, Any], config: Config
) -> CampaignView:
    require(caller, Action.manage_campaigns)
    async with workspace_session(sm, caller.workspace_id) as s:
        if await repo.count_campaigns(s) >= rules.MAX_CAMPAIGNS:
            raise Invalid(f"A workspace can have at most {rules.MAX_CAMPAIGNS} campaigns.")
        fields = {"segment_id": None, "subject": "", "body": "", "from_address_id": None,
                  "track_opens": False} | await _fields(s, raw)  # fmt: skip
        campaign_id = await repo.insert_campaign(
            s, caller.workspace_id, fields=fields, created_by=caller.member_id
        )
        await audit.record(s, caller, Area.campaigns, "created", f"Campaign {fields['name']}",
                           target_id=campaign_id,
                           detail={"name": fields["name"], "segment_id": fields["segment_id"],
                                   "track_opens": fields["track_opens"]})  # fmt: skip
        return await _view(s, await _campaign(s, campaign_id), config)


async def get_campaign(
    sm: Sessionmaker, caller: Caller, campaign_id: UUID, config: Config
) -> CampaignView:
    require(caller, Action.manage_campaigns)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await _view(s, await _campaign(s, campaign_id), config)


async def update_campaign(
    sm: Sessionmaker, caller: Caller, campaign_id: UUID, raw: Mapping[str, Any], config: Config
) -> CampaignView:
    """Drafts only. The audit entry says which fields changed, never the text."""
    require(caller, Action.manage_campaigns)
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await _campaign(s, campaign_id, lock=True)
        if before["state"] not in rules.EDITABLE:
            raise Conflict("Only a draft can be changed. Cancel a scheduled campaign first.")
        fields = await _fields(s, raw)
        await repo.update_campaign(s, campaign_id, fields)
        changed = sorted(k for k, v in fields.items() if before[k] != v)
        if changed:
            await audit.record(s, caller, Area.campaigns, "changed", f"Campaign {before['name']}",
                               target_id=campaign_id, detail={"fields": changed})  # fmt: skip
        return await _view(s, await _campaign(s, campaign_id), config)


async def delete_campaign(sm: Sessionmaker, caller: Caller, campaign_id: UUID) -> None:
    """Drafts only: a campaign that sent anything keeps its results."""
    require(caller, Action.manage_campaigns)
    async with workspace_session(sm, caller.workspace_id) as s:
        c = await _campaign(s, campaign_id, lock=True)
        if c["state"] != State.draft or c["started_at"] is not None:
            raise Conflict("Only a draft that never started can be deleted.")
        await repo.delete_campaign(s, campaign_id)
        await audit.record(s, caller, Area.campaigns, "deleted", f"Campaign {c['name']}",
                           target_id=campaign_id, detail={"name": c["name"]})  # fmt: skip


# Who it goes to


@dataclass(frozen=True)
class Audience:
    in_segment: int
    not_mailable: int
    suppressed: int
    recipients: int
    sent_last_24h: int
    queued: int  # in other campaigns, still to go
    daily_cap: int
    max_recipients: int
    problem: str | None


async def _segment_filters(s: AsyncSession, segment_id: UUID | None) -> tuple[Row, Any]:
    if segment_id is None:
        raise Invalid("Pick a segment to send to.")
    segment = await segment_repo.get(s, segment_id)
    if segment is None:
        raise Invalid("The campaign's segment was deleted. Pick another one.")
    try:
        f = contact_rules.filters_from_json(await contact_service.fields(s), segment["filters"])
    except contact_rules.ContactError as exc:
        raise Invalid(f"The segment's filters no longer work: {exc}") from exc
    return segment, contact_service.query_filters(f)


async def _audience(
    s: AsyncSession, workspace_id: UUID, segment_id: UUID | None, limits: Limits
) -> Audience:
    _segment, filters = await _segment_filters(s, segment_id)
    found = await repo.audience(s, workspace_id, filters)
    today = await repo.sent_since(s, datetime.now(UTC) - timedelta(days=1))
    queued = await repo.queued_total(s)
    n = int(found["recipients"])
    return Audience(
        in_segment=int(found["in_segment"]),
        not_mailable=int(found["not_mailable"]),
        suppressed=int(found["suppressed"]),
        recipients=n,
        sent_last_24h=today,
        queued=queued,
        daily_cap=limits.daily_cap,
        max_recipients=limits.max_recipients,
        problem=rules.cap_problem(n, today + queued, limits),
    )


async def audience(sm: Sessionmaker, caller: Caller, campaign_id: UUID, config: Config) -> Audience:
    """Who it would go to if sent now: the segment, minus erased customers (never in a
    segment), placeholder, role and support addresses, minus the suppression list."""
    require(caller, Action.manage_campaigns)
    async with workspace_session(sm, caller.workspace_id) as s:
        c = await _campaign(s, campaign_id)
        return await _audience(s, caller.workspace_id, c["segment_id"], config.limits)


# Content: preview and test


@dataclass(frozen=True)
class Preview:
    rendered: rules.Rendered
    contact_id: UUID | None
    contact_name: str


def _render(
    c: Mapping[Any, Any],
    *,
    customer_name: str,
    fields: Mapping[str, object],
    workspace_name: str,
    postal_address: str,
    unsubscribe_url: str,
    pixel_url: str | None,
) -> rules.Rendered:
    sender_name = str(c["from_name"] or workspace_name)
    return rules.render(
        subject=str(c["subject"]),
        body=str(c["body"]),
        customer_name=customer_name,
        sender_name=sender_name,
        fields=fields,
        footer=rules.Footer(workspace_name, postal_address, unsubscribe_url, pixel_url),
    )


async def _postal(s: AsyncSession) -> str:
    found = await repo.get_settings(s)
    return str(found["postal_address"]) if found else "(your postal address: Campaigns > Settings)"


async def preview(
    sm: Sessionmaker,
    caller: Caller,
    campaign_id: UUID,
    config: Config,
    *,
    contact_id: UUID | None = None,
) -> Preview:
    """The email as one customer would get it: that contact, else the first the campaign
    would go to, else a made-up "Alex Sample". Its links go nowhere."""
    require(caller, Action.manage_campaigns)
    async with workspace_session(sm, caller.workspace_id) as s:
        c = await _campaign(s, campaign_id)
        if contact_id is None and c["segment_id"] is not None:
            segment = await segment_repo.get(s, c["segment_id"])
            if segment is not None:
                _seg, filters = await _segment_filters(s, c["segment_id"])
                contact_id = await repo.first_in_segment(s, caller.workspace_id, filters)
        contact = await repo.contact_for_preview(s, contact_id) if contact_id else None
        if contact_id is not None and contact is None:
            raise NotFound("Customer not found.")
        workspace = await _workspace_name(s)
        postal = await _postal(s)
    name = str(contact["name"]) if contact else SAMPLE_NAME
    links = config.links
    rendered = _render(
        c,
        customer_name=name,
        fields=dict(contact["attributes"] or {}) if contact else {},
        workspace_name=workspace,
        postal_address=postal,
        unsubscribe_url=links.unsubscribe_page(PREVIEW_TOKEN),
        pixel_url=None,
    )
    return Preview(rendered, contact["id"] if contact else None, name)


async def send_test(sm: Sessionmaker, caller: Caller, campaign_id: UUID, config: Config) -> str:
    """Queues the email to the signed-in Admin's own address only (filled in as if they
    were the customer), from the campaign's address. Returns where it went."""
    require(caller, Action.manage_campaigns)
    async with workspace_session(sm, caller.workspace_id) as s:
        c = await _campaign(s, campaign_id)
        if c["from_address_id"] is None:
            raise Invalid("Pick the support address it comes from first.")
        if not str(c["subject"]) or not str(c["body"]):
            raise Invalid("Add a subject and write the email first.")
        address = await email_repo.get_address(s, c["from_address_id"])
        me = await auth_repo.member_email(s, caller.member_id)
        name = await audit_name(s, caller.member_id)
        if address is None or me is None:
            raise NotFound("Member or address not found.")
        rendered = _render(
            c, customer_name=name, fields={}, workspace_name=await _workspace_name(s),
            postal_address=await _postal(s),
            unsubscribe_url=config.links.unsubscribe_page(PREVIEW_TOKEN), pixel_url=None,
        )  # fmt: skip
        sender = str(address["address"])
        await email_repo.enqueue(
            s, caller.workspace_id, ticket_id=None, message_id=None, from_address=sender,
            from_name=str(c["from_name"] or address["name"]), to=[me], cc=[],
            subject=rules.TEST_PREFIX + rendered.subject, body=rendered.text,
            header_message_id=new_message_id(sender), in_reply_to=None, references=[],
            body_html=rendered.html,
        )  # fmt: skip
        await audit.record(s, caller, Area.campaigns, "test_sent", f"Campaign {c['name']}",
                           target_id=campaign_id, detail={"to_self": True})  # fmt: skip
    return me


async def audit_name(s: AsyncSession, member_id: UUID) -> str:
    return await audit_repo.member_name(s, member_id) or "there"


# Sending: confirm, schedule, start


async def _start(
    s: AsyncSession, workspace_id: UUID, c: Row, config: Config
) -> tuple[int | None, str | None]:
    """Copies the recipients and starts sending, in the caller's transaction. (count,
    None) when it started; (None, why) when it can't."""
    problems = await _problems(s, c, config)
    if problems:
        return None, " ".join(problems)
    try:
        segment, filters = await _segment_filters(s, c["segment_id"])
    except Invalid as exc:
        return None, str(exc)
    found = await _audience(s, workspace_id, c["segment_id"], config.limits)
    if found.problem:
        return None, found.problem
    address = await email_repo.get_address(s, c["from_address_id"])
    assert address is not None  # _problems checked it
    n = await repo.copy_recipients(s, workspace_id, c["id"], filters)
    await repo.mark_started(
        s, c["id"], segment_name=str(segment["name"]), from_address=str(address["address"]),
        from_name=str(address["name"]), recipients=n,
    )  # fmt: skip
    return n, None


async def send(
    sm: Sessionmaker,
    caller: Caller,
    campaign_id: UUID,
    config: Config,
    *,
    confirm: bool,
    scheduled_at: datetime | None = None,
) -> CampaignView:
    """Sends the draft now, or schedules it. `confirm` must be true (the screen asks "Send
    to N people?" first). A schedule is checked now and again when its time comes."""
    require(caller, Action.manage_campaigns)
    if not confirm:
        raise Invalid("Confirm the send: it emails every recipient and can't be undone.")
    now = datetime.now(UTC)
    at = _clean(lambda v: rules.check_schedule(v, now), scheduled_at)
    async with workspace_session(sm, caller.workspace_id) as s:
        c = await _campaign(s, campaign_id, lock=True)
        if c["state"] != State.draft:
            raise Conflict(f"This campaign is {c['state']}; only a draft can be sent.")
        if at is not None:
            problems = await _problems(s, c, config)
            if problems:
                raise Invalid(" ".join(problems))
            found = await _audience(s, caller.workspace_id, c["segment_id"], config.limits)
            if found.problem:
                raise Invalid(found.problem)
            await repo.set_state(s, campaign_id, State.scheduled.value, scheduled_at=at)
            await audit.record(s, caller, Area.campaigns, "scheduled", f"Campaign {c['name']}",
                               target_id=campaign_id,
                               detail={"recipients_now": found.recipients,
                                       "scheduled_at": at.isoformat()})  # fmt: skip
        else:
            n, why = await _start(s, caller.workspace_id, c, config)
            if why is not None:
                raise Invalid(why)
            await audit.record(s, caller, Area.campaigns, "sent", f"Campaign {c['name']}",
                               target_id=campaign_id, detail={"recipients": n})  # fmt: skip
        return await _view(s, await _campaign(s, campaign_id), None)


async def cancel(sm: Sessionmaker, caller: Caller, campaign_id: UUID) -> CampaignView:
    """Scheduled: back to a draft (nothing was sent). Sending or paused: stops; the
    recipients not sent yet are cancelled (an email already with the mail server goes)."""
    require(caller, Action.manage_campaigns)
    async with workspace_session(sm, caller.workspace_id) as s:
        c = await _campaign(s, campaign_id, lock=True)
        state = State(c["state"])
        if state not in rules.CANCELLABLE:
            raise Conflict(f"This campaign is {state}; it can't be cancelled.")
        detail: dict[str, Any] = {"was": state.value}
        if state is State.scheduled:
            await repo.set_state(s, campaign_id, State.draft.value)
        else:
            detail["not_sent"] = await repo.cancel_queued(s, campaign_id)
            await repo.set_state(s, campaign_id, State.cancelled.value)
        await audit.record(s, caller, Area.campaigns, "cancelled", f"Campaign {c['name']}",
                           target_id=campaign_id, detail=detail)  # fmt: skip
        return await _view(s, await _campaign(s, campaign_id), None)


async def pause(sm: Sessionmaker, caller: Caller, campaign_id: UUID) -> CampaignView:
    require(caller, Action.manage_campaigns)
    async with workspace_session(sm, caller.workspace_id) as s:
        c = await _campaign(s, campaign_id, lock=True)
        if c["state"] != State.sending:
            raise Conflict("Only a campaign that is sending can be paused.")
        await repo.set_state(s, campaign_id, State.paused.value)
        await audit.record(s, caller, Area.campaigns, "paused", f"Campaign {c['name']}",
                           target_id=campaign_id, detail={})  # fmt: skip
        return await _view(s, await _campaign(s, campaign_id), None)


async def resume(sm: Sessionmaker, caller: Caller, campaign_id: UUID) -> CampaignView:
    require(caller, Action.manage_campaigns)
    async with workspace_session(sm, caller.workspace_id) as s:
        c = await _campaign(s, campaign_id, lock=True)
        if c["state"] != State.paused:
            raise Conflict("Only a paused campaign can be resumed.")
        await repo.set_state(s, campaign_id, State.sending.value)
        await audit.record(s, caller, Area.campaigns, "resumed", f"Campaign {c['name']}",
                           target_id=campaign_id, detail={})  # fmt: skip
        return await _view(s, await _campaign(s, campaign_id), None)


# Results


@dataclass(frozen=True)
class RecipientPage:
    recipients: Sequence[Row]
    next_cursor: str | None


def _status(raw: str | None) -> str | None:
    if raw is None:
        return None
    if raw not in rules.RECIPIENT_FILTERS:
        raise Invalid("status is one of " + ", ".join(rules.RECIPIENT_FILTERS) + ".")
    return raw


async def recipients(
    sm: Sessionmaker,
    caller: Caller,
    campaign_id: UUID,
    *,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = paging.DEFAULT_LIMIT,
) -> RecipientPage:
    require(caller, Action.manage_campaigns)
    wanted = _status(status)
    after = None
    if cursor:
        try:
            kind, key, row_id = paging.decode(cursor)
            if kind != "recipient":
                raise paging.CursorError("This cursor belongs to another list.")
            after = (str(key), UUID(str(row_id)))
        except (paging.CursorError, ValueError, TypeError) as exc:
            raise Invalid("This cursor is not valid.") from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        await _campaign(s, campaign_id)
        rows = await repo.recipient_page(
            s, campaign_id, status=wanted, after=after, limit=limit + 1
        )
    found = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = found[-1]
        next_cursor = paging.encode(["recipient", last["email"] or "", str(last["id"])])
    return RecipientPage(found, next_cursor)


@dataclass(frozen=True)
class CsvFile:
    name: str
    body: str


CSV_HEADER = ("email", "name", "status", "queued_at", "sent_at", "bounced_at",
              "first_opened_at", "open_count", "unsubscribed_at", "complained_at",
              "error")  # fmt: skip


def _at(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def export_results(sm: Sessionmaker, caller: Caller, campaign_id: UUID) -> CsvFile:
    """Every recipient's result as CSV (formula-safe, UTF-8 with BOM). Audited."""
    require(caller, Action.manage_campaigns)
    async with workspace_session(sm, caller.workspace_id) as s:
        c = await _campaign(s, campaign_id)
        rows = await repo.recipient_page(s, campaign_id, status=None, after=None, limit=None)
        await audit.record(s, caller, Area.campaigns, "results_exported",
                           f"Campaign {c['name']}", target_id=campaign_id,
                           detail={"rows": len(rows)})  # fmt: skip
    table = report_csv.Table(
        "Recipients",
        CSV_HEADER,
        [
            (r["email"], r["name"], r["status"], _at(r["queued_at"]), _at(r["sent_at"]),
             _at(r["bounced_at"]), _at(r["first_opened_at"]), int(r["open_count"]),
             _at(r["unsubscribed_at"]), _at(r["complained_at"]), r["error"])
            for r in rows
        ],
    )  # fmt: skip
    body = report_csv.render(f"Campaign: {c['name']}", [("Subject", c["subject"])], [table])
    return CsvFile(f"campaign-{campaign_id}.csv", body)


# Public: the unsubscribe page and one-click, the open image


@dataclass(frozen=True)
class UnsubscribeInfo:
    workspace_name: str | None  # None: the link is unknown (shown as a general page)
    unsubscribed: bool


async def unsubscribe_info(sm: Sessionmaker, token: str) -> UnsubscribeInfo:
    """What the page shows. Never the address."""
    workspace_id = token_rules.token_workspace(token)
    if workspace_id is None:
        return UnsubscribeInfo(None, False)
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.by_unsubscribe(s, token_rules.token_hash(token))
        if row is None:
            return UnsubscribeInfo(None, False)
        return UnsubscribeInfo(await _workspace_name(s), row["unsubscribed_at"] is not None)


async def unsubscribe(sm: Sessionmaker, token: str, *, one_click: bool) -> None:
    """The person behind the link gets no more campaigns from this workspace. The same
    answer for every token (known, unknown, already done), so nothing is learned from it;
    doing it twice changes nothing."""
    workspace_id = token_rules.token_workspace(token)
    if workspace_id is None:
        return
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.by_unsubscribe(s, token_rules.token_hash(token))
        if row is None:
            return
        await repo.mark_unsubscribed(s, row["id"])
        if row["email"]:
            await repo.suppress(
                s, workspace_id, email_hash=rules.email_hash(workspace_id, str(row["email"])),
                email=str(row["email"]), reason=Reason.unsubscribed.value,
                source=(Source.one_click if one_click else Source.link).value,
                campaign_id=row["campaign_id"],
            )  # fmt: skip
    logger.info("campaign unsubscribe", extra={"workspace_id": workspace_id,
                                               "event": "campaign"})  # fmt: skip


async def record_open(sm: Sessionmaker, token: str) -> None:
    workspace_id = token_rules.token_workspace(token)
    if workspace_id is None:
        return
    async with workspace_session(sm, workspace_id) as s:
        await repo.record_open(s, token_rules.token_hash(token))


# Bounces and spam reports (from email in)


async def handle_report(sm: Sessionmaker, workspace_id: UUID, report: bounces.Report) -> bool:
    """A bounce or spam report about one of this workspace's campaign emails, found by the
    original's Message-ID. True when it was one (then it makes no ticket). A soft bounce
    changes nothing."""
    if not report.message_ids:
        return False
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.by_message_ids(s, report.message_ids)
        if row is None:
            return False
        if report.kind is bounces.Kind.soft:
            return True
        email = str(row["email"]) if row["email"] else None
        if report.kind is bounces.Kind.hard:
            await repo.mark_bounced(s, row["id"])
            reason, source = Reason.hard_bounce, Source.bounce
        else:
            await repo.mark_complained(s, row["id"])
            reason, source = Reason.complaint, Source.complaint
        if email:
            await repo.suppress(
                s, workspace_id, email_hash=rules.email_hash(workspace_id, email), email=email,
                reason=reason.value, source=source.value, campaign_id=row["campaign_id"],
            )  # fmt: skip
        await _pause_if_bouncing(s, workspace_id, row["campaign_id"])
    logger.info("campaign bounce", extra={"workspace_id": workspace_id, "event": report.kind.value})
    return True


async def _pause_if_bouncing(s: AsyncSession, workspace_id: UUID, campaign_id: UUID) -> None:
    c = await repo.get_campaign(s, campaign_id, lock=True)
    if c is None or c["state"] != State.sending:
        return
    st = _stats((await repo.stats(s, [campaign_id])).get(campaign_id))
    if rules.should_pause(st.sent, st.bounced):
        why = f"Paused: {st.bounced} of {st.sent} emails bounced. Check the segment's addresses."
        await repo.set_state(s, campaign_id, State.paused.value, error=why)
        await audit.record_server(s, workspace_id, Area.campaigns, "paused",
                                  f"Campaign {c['name']}",
                                  detail={"sent": st.sent, "bounced": st.bounced})  # fmt: skip


# The worker


@dataclass
class SendResult:
    started: int = 0
    sent: int = 0
    retrying: int = 0
    failed: int = 0
    skipped: int = 0
    finished: int = 0


@dataclass(frozen=True)
class _Prepared:
    recipient_id: UUID
    attempts: int
    email: OutgoingEmail


async def _start_due(
    sm: Sessionmaker, workspace_id: UUID, config: Config, now: datetime, result: SendResult
) -> None:
    async with workspace_session(sm, workspace_id) as s:
        due = await repo.due_to_start(s, now)
    for campaign_id in due:
        async with workspace_session(sm, workspace_id) as s:
            c = await repo.get_campaign(s, campaign_id, lock=True)
            if c is None or c["state"] != State.scheduled:
                continue  # another worker started it, or it was cancelled
            n, why = await _start(s, workspace_id, c, config)
            if why is not None:
                await repo.set_state(s, campaign_id, State.failed.value, error=why[:300])
                await audit.record_server(s, workspace_id, Area.campaigns, "failed",
                                          f"Campaign {c['name']}",
                                          detail={"could_not_start": True})  # fmt: skip
                continue
            await audit.record_server(s, workspace_id, Area.campaigns, "started",
                                      f"Campaign {c['name']}",
                                      detail={"recipients": n})  # fmt: skip
            result.started += 1


async def _prepare(
    s: AsyncSession,
    workspace_id: UUID,
    c: Row,
    quota: int,
    config: Config,
    now: datetime,
    result: SendResult,
) -> list[_Prepared]:
    """Claims and marks up to `quota` recipients, with their emails ready (in the
    caller's transaction: committed before anything is sent)."""
    rows = await repo.claim(s, workspace_id, c["id"], quota, now)
    if not rows:
        return []
    workspace = await _workspace_name(s)
    postal = await _postal(s)
    sender = str(c["from_address"])
    links = config.links
    prepared = []
    for r in rows:
        if r["gone"] or not r["email"]:
            await repo.skip(s, r["id"], "The customer was erased.")
            result.skipped += 1
            continue
        if r["suppressed"]:
            await repo.skip(s, r["id"], "On the suppression list.")
            result.skipped += 1
            continue
        unsub = token_rules.new_token(workspace_id)
        opened = token_rules.new_token(workspace_id) if c["track_opens"] else None
        message_id = new_message_id(sender)
        rendered = _render(
            c, customer_name=str(r["name"]), fields=dict(r["attributes"] or {}),
            workspace_name=workspace, postal_address=postal,
            unsubscribe_url=links.unsubscribe_page(unsub),
            pixel_url=links.pixel(opened) if opened else None,
        )  # fmt: skip
        await repo.mark_sending(
            s, r["id"], until=now + rules.LEASE, unsubscribe_hash=token_rules.token_hash(unsub),
            open_hash=token_rules.token_hash(opened) if opened else None,
            header_message_id=message_id,
        )  # fmt: skip
        email = OutgoingEmail(
            from_address=sender, from_name=str(c["from_name"] or workspace),
            to=[str(r["email"])], subject=rendered.subject, text=rendered.text,
            html=rendered.html, message_id=message_id,
            headers=rules.unsubscribe_headers(links.one_click(unsub)),
        )  # fmt: skip
        prepared.append(_Prepared(r["id"], int(r["attempts"]) + 1, email))
    return prepared


async def _send_campaign(
    sm: Sessionmaker,
    mailer: Mailer,
    workspace_id: UUID,
    campaign_id: UUID,
    quota: int,
    config: Config,
    now: datetime,
    result: SendResult,
) -> int:
    """One batch of one campaign. Returns how many were handed to the mail server."""
    async with workspace_session(sm, workspace_id) as s:
        c = await repo.get_campaign(s, campaign_id)
        if c is None or c["state"] != State.sending:
            return 0
        prepared = await _prepare(s, workspace_id, c, quota, config, now, result)
    # Sent outside any transaction: a slow mail server never holds a lock.
    for p in prepared:
        error: SendError | None = None
        try:
            await mailer.send(p.email)
        except SendError as exc:
            error = exc
        async with workspace_session(sm, workspace_id) as s:
            if error is None:
                await repo.mark_sent(s, p.recipient_id)
                result.sent += 1
                continue
            wait = None if error.permanent else rules.retry_wait(p.attempts)
            if wait is None:
                await repo.mark_failed(s, p.recipient_id, str(error))
                result.failed += 1
            else:
                await repo.mark_retry(s, p.recipient_id, str(error), datetime.now(UTC) + wait)
                result.retrying += 1
    if prepared:
        logger.info(
            "campaign batch",
            extra={"workspace_id": workspace_id, "count": len(prepared), "event": "campaign"},
        )
    async with workspace_session(sm, workspace_id) as s:
        c = await repo.get_campaign(s, campaign_id, lock=True)
        if (
            c is not None
            and c["state"] == State.sending
            and not await repo.left_to_send(s, campaign_id)
        ):
            await repo.set_state(s, campaign_id, State.sent.value)
            st = _stats((await repo.stats(s, [campaign_id])).get(campaign_id))
            await audit.record_server(s, workspace_id, Area.campaigns, "finished",
                                      f"Campaign {c['name']}",
                                      detail={"sent": st.sent, "failed": st.failed,
                                              "skipped": st.skipped})  # fmt: skip
            result.finished += 1
    return len(prepared)


async def _send_workspace(
    sm: Sessionmaker,
    mailer: Mailer,
    workspace_id: UUID,
    config: Config,
    now: datetime,
    result: SendResult,
) -> None:
    await _start_due(sm, workspace_id, config, now, result)
    async with workspace_session(sm, workspace_id) as s:
        await repo.expire_claims(s, now, rules.INTERRUPTED)
        campaign_ids = await repo.sending(s)
        if not campaign_ids:
            return
        minute = await repo.sent_since(s, now - timedelta(minutes=1))
        day = await repo.sent_since(s, now - timedelta(days=1))
    limits = config.limits
    quota = min(limits.per_minute - minute, limits.daily_cap - day)
    for campaign_id in campaign_ids:
        if quota <= 0:
            break
        quota -= await _send_campaign(
            sm, mailer, workspace_id, campaign_id, min(quota, limits.batch), config, now, result
        )


async def run_due(
    sm: Sessionmaker, mailer: Mailer, config: Config, now: datetime | None = None
) -> SendResult:
    """One pass over every workspace (the worker): starts scheduled campaigns whose time
    came, then sends a batch of each sending campaign within the workspace's limits."""
    now = now or datetime.now(UTC)
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    result = SendResult()
    for workspace_id in workspace_ids:
        try:
            await _send_workspace(sm, mailer, workspace_id, config, now, result)
        except Exception:  # one workspace's trouble never stops the others
            logger.exception("campaign pass failed", extra={"workspace_id": workspace_id})
    return result


# Privacy (called from service/privacy.py, in its transaction)


def _hashes(workspace_id: UUID, emails: Sequence[str]) -> list[bytes]:
    return [rules.email_hash(workspace_id, e) for e in dict.fromkeys(emails) if e]


async def export_rows(
    s: AsyncSession, workspace_id: UUID, contact_id: UUID, emails: Sequence[str]
) -> dict[str, list[dict[str, Any]]]:
    """For the customer's privacy export: the campaigns sent to them and their entries
    on the suppression list."""
    sent = await repo.recipients_for_export(s, contact_id, emails)
    listed = await repo.suppressions_for_hashes(s, _hashes(workspace_id, emails))
    return {
        "campaign_emails": [
            {"campaign": r["campaign"], "subject": r["subject"], "email": r["email"],
             "status": r["status"], "queued_at": _at(r["queued_at"]), "sent_at": _at(r["sent_at"]),
             "bounced_at": _at(r["bounced_at"]), "first_opened_at": _at(r["first_opened_at"]),
             "open_count": int(r["open_count"]), "unsubscribed_at": _at(r["unsubscribed_at"]),
             "complained_at": _at(r["complained_at"])}
            for r in sent
        ],
        "suppression_list": [
            {"email": r["email"], "reason": r["reason"], "source": r["source"],
             "campaign": r["campaign_name"], "added_at": _at(r["created_at"])}
            for r in listed
        ],
    }  # fmt: skip


async def erase(
    s: AsyncSession, workspace_id: UUID, contact_id: UUID, emails: Sequence[str], name: str
) -> dict[str, int]:
    """For a privacy erase, before the contact's addresses are scrubbed: their recipient
    rows lose the address, name and link tokens (the campaigns' totals stay); each of
    their addresses stays on the suppression list as a hash only, so the person is never
    mailed again, even if they later write in as a new customer."""
    return {
        "campaign_emails": await repo.erase_recipients(s, contact_id, emails, name),
        "suppression_kept": await repo.forget_addresses(
            s, workspace_id, _hashes(workspace_id, emails)
        ),
    }
