"""Outgoing webhooks (md PL3-PL6): Admins add URLs; ticket events reach them signed, with
retries and a delivery log. Rules are in domain/webhooks.py.

How an event travels:
1. A change commits with its outbox event (as for every side effect, md 3.2).
2. The outbox worker hands the event to `WebhookFanout`, which adds one delivery per
   turned-on webhook listening to it. Done once per event: a repeat hand-out adds nothing.
3. `send_due` (each worker pass) sends due deliveries, each on its own clock: a URL that
   is down never holds up the others. Every try is logged.

Managing webhooks is Admin only (`manage_access`, as API keys): a webhook sends ticket data
out of CeDesk. Adding, changing, deleting and new secrets are audited (area `webhooks`).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from cedesk.crypto import CannotOpen, Sealer
from cedesk.db import workspace_session
from cedesk.egress import BadAddress
from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.domain import webhooks as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import webhooks as repo
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.delivery import Event
from cedesk.helpdesk.service.errors import Conflict, Forbidden, Invalid, NotFound
from cedesk.helpdesk.service.members import require
from cedesk.webhook_client import Answer, WebhookClient

logger = logging.getLogger("cedesk.webhooks")

NOT_FOUND = "Webhook not found."
CURSOR = "webhook_delivery"
BATCH = 50
LEASE_SECONDS = 60


@dataclass(frozen=True)
class WebhookView:
    id: UUID
    url: str
    events: list[str]
    secret_hint: str
    enabled: bool
    created_at: datetime
    updated_at: datetime
    created_by_name: str | None


@dataclass(frozen=True)
class WithSecret:
    view: WebhookView
    secret: str  # shown once


@dataclass(frozen=True)
class Attempt:
    at: datetime
    status: int | None
    ms: int
    error: str | None
    response: str | None


@dataclass(frozen=True)
class DeliveryView:
    id: UUID
    webhook_id: UUID
    url: str
    event: str
    body: dict[str, Any]
    state: str
    tries: int
    next_try_at: datetime | None  # pending only
    last_status: int | None
    last_ms: int | None
    last_error: str | None
    delivered_at: datetime | None
    created_at: datetime
    attempts: list[Attempt]


@dataclass(frozen=True)
class DeliveryPage:
    deliveries: list[DeliveryView]
    next_cursor: str | None


def _bound(workspace_id: UUID, webhook_id: UUID) -> str:
    """Sealed for this webhook only: a sealed secret copied to another row won't open."""
    return f"{workspace_id}:webhook:{webhook_id}"


def _view(row: Any) -> WebhookView:
    return WebhookView(
        id=row["id"],
        url=str(row["url"]),
        events=list(row["events"]),
        secret_hint=str(row["secret_hint"]),
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by_name=row["created_by_name"],
    )


def _admin(caller: Caller) -> None:
    require(caller, Action.manage_access)
    if caller.api_key_id is not None:
        raise Forbidden("An API key can't manage webhooks.")


async def _checked_url(client: WebhookClient, url: str) -> str:
    try:
        url = rules.check_url(url)
        await client.check_host(url)
    except (rules.BadUrl, BadAddress) as exc:
        raise Invalid(str(exc)) from exc
    return url


def _events(events: list[str]) -> list[str]:
    try:
        return rules.check_events(events)
    except rules.BadUrl as exc:
        raise Invalid(str(exc)) from exc


async def create_webhook(
    sm: Sessionmaker,
    sealer: Sealer,
    client: WebhookClient,
    caller: Caller,
    *,
    url: str,
    events: list[str],
    enabled: bool = True,
) -> WithSecret:
    _admin(caller)
    url = await _checked_url(client, url)
    events = _events(events)
    webhook_id, secret = uuid4(), rules.new_secret()
    async with workspace_session(sm, caller.workspace_id) as s:
        if await repo.count(s) >= rules.MAX_WEBHOOKS:
            raise Conflict(f"A workspace can have at most {rules.MAX_WEBHOOKS} webhooks.")
        await repo.insert(
            s, caller.workspace_id, webhook_id=webhook_id, url=url, events=events,
            secret_sealed=sealer.seal(secret, bound_to=_bound(caller.workspace_id, webhook_id)),
            secret_hint=rules.hint(secret), enabled=enabled, created_by=caller.member_id,
        )  # fmt: skip
        await audit.record(
            s, caller, Area.webhooks, "created", url, target_id=webhook_id,
            detail={"events": events, "enabled": enabled},
        )  # fmt: skip
        row = await repo.get(s, webhook_id)
    assert row is not None
    return WithSecret(_view(row), secret)


async def all_webhooks(sm: Sessionmaker, caller: Caller) -> list[WebhookView]:
    _admin(caller)
    async with workspace_session(sm, caller.workspace_id) as s:
        return [_view(r) for r in await repo.all_hooks(s)]


async def update_webhook(
    sm: Sessionmaker,
    client: WebhookClient,
    caller: Caller,
    webhook_id: UUID,
    *,
    url: str | None = None,
    events: list[str] | None = None,
    enabled: bool | None = None,
) -> WebhookView:
    """Only what is given changes. Deliveries already queued keep the URL they were made
    for until they are sent: a changed URL is used from the next try on."""
    _admin(caller)
    values: dict[str, Any] = {}
    if url is not None:
        values["url"] = await _checked_url(client, url)
    if events is not None:
        values["events"] = _events(events)
    if enabled is not None:
        values["enabled"] = enabled
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await repo.get(s, webhook_id)
        if before is None:
            raise NotFound(NOT_FOUND)
        changes = {k: [_plain(before[k]), _plain(v)] for k, v in values.items() if before[k] != v}
        if changes:
            await repo.update(s, webhook_id, values)
            await audit.record(
                s, caller, Area.webhooks, "changed", str(before["url"]), target_id=webhook_id,
                detail={"changes": changes},
            )  # fmt: skip
        row = await repo.get(s, webhook_id)
    assert row is not None
    return _view(row)


def _plain(value: Any) -> Any:
    return list(value) if isinstance(value, list | tuple) else value


async def new_secret(
    sm: Sessionmaker, sealer: Sealer, caller: Caller, webhook_id: UUID
) -> WithSecret:
    """A new secret, shown once; the old one stops signing at once."""
    _admin(caller)
    secret = rules.new_secret()
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get(s, webhook_id)
        if row is None:
            raise NotFound(NOT_FOUND)
        sealed = sealer.seal(secret, bound_to=_bound(caller.workspace_id, webhook_id))
        await repo.set_secret(s, webhook_id, sealed, rules.hint(secret))
        await audit.record(
            s, caller, Area.webhooks, "secret_changed", str(row["url"]), target_id=webhook_id
        )
        row = await repo.get(s, webhook_id)
    assert row is not None
    return WithSecret(_view(row), secret)


async def delete_webhook(sm: Sessionmaker, caller: Caller, webhook_id: UUID) -> None:
    """Its queued deliveries and its log go with it."""
    _admin(caller)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get(s, webhook_id)
        if row is None or not await repo.delete(s, webhook_id):
            raise NotFound(NOT_FOUND)
        await audit.record(s, caller, Area.webhooks, "deleted", str(row["url"]))


# The delivery log (PL6)


def _attempt(row: Any) -> Attempt:
    return Attempt(row["at"], row["status"], int(row["ms"]), row["error"], row["response"])


def _delivery(row: Any, tries: Sequence[Attempt]) -> DeliveryView:
    return DeliveryView(
        id=row["id"],
        webhook_id=row["webhook_id"],
        url=str(row["url"]),
        event=str(row["event"]),
        body=dict(row["body"]),
        state=str(row["state"]),
        tries=int(row["tries"]),
        next_try_at=row["next_try_at"] if row["state"] == "pending" else None,
        last_status=row["last_status"],
        last_ms=row["last_ms"],
        last_error=row["last_error"],
        delivered_at=row["delivered_at"],
        created_at=row["created_at"],
        attempts=list(tries),
    )


async def deliveries(
    sm: Sessionmaker,
    caller: Caller,
    *,
    state: str | None = None,
    webhook_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = paging.DEFAULT_LIMIT,
) -> DeliveryPage:
    _admin(caller)
    before = None
    if cursor:
        try:
            before = paging.decode_time(cursor, CURSOR)
        except paging.CursorError as exc:
            raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        rows = await repo.deliveries(
            s, state=state, webhook_id=webhook_id, before=before, limit=limit + 1
        )
        page, next_cursor = paging.time_page(rows, limit, CURSOR)
        tries = await repo.attempts(s, [r["id"] for r in page])
    by_delivery: dict[UUID, list[Attempt]] = {}
    for t in tries:
        by_delivery.setdefault(t["delivery_id"], []).append(_attempt(t))
    return DeliveryPage([_delivery(r, by_delivery.get(r["id"], [])) for r in page], next_cursor)


async def retry_delivery(sm: Sessionmaker, caller: Caller, delivery_id: UUID) -> None:
    """A failed delivery goes back in the queue, with a full set of tries."""
    _admin(caller)
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.retry(s, delivery_id):
            raise NotFound("No failed delivery with that id.")


# Sending


async def _record(
    sm: Sessionmaker, workspace_id: UUID, delivery_id: UUID, tries_before: int, got: Answer,
    *, retry: bool,
) -> str:  # fmt: skip
    """Logs one try and moves the delivery on. Returns its new state."""
    wait_seconds = None
    if rules.delivered(got.status):
        state = "delivered"
    else:
        wait = rules.next_try(tries_before + 1) if retry else None
        state = "pending" if wait is not None else "failed"
        wait_seconds = int(wait.total_seconds()) if wait is not None else None
    async with workspace_session(sm, workspace_id) as s:
        await repo.record_try(
            s, workspace_id, delivery_id, status=got.status, ms=got.ms,
            error=(got.error or "")[: rules.RESPONSE_KEPT] or None,
            response=got.response, state=state, wait_seconds=wait_seconds,
        )  # fmt: skip
    return state


async def ping(
    sm: Sessionmaker, sealer: Sealer, client: WebhookClient, caller: Caller, webhook_id: UUID
) -> DeliveryView:
    """Sends a `ping` now and waits for the answer (at most 10 s): one try, no retries.
    It is logged like any delivery."""
    _admin(caller)
    now = datetime.now(UTC)
    async with workspace_session(sm, caller.workspace_id) as s:
        hook = await repo.get(s, webhook_id)
        if hook is None:
            raise NotFound(NOT_FOUND)
        sealed = await repo.sealed_secret(s, webhook_id)
        delivery_id = uuid4()
        body = {
            "event": rules.PING,
            "id": str(delivery_id),
            "occurred_at": now.isoformat(),
            "webhook": {"id": str(webhook_id)},
        }
        await repo.add_ping(s, caller.workspace_id, delivery_id, webhook_id, body)
    secret = sealer.open(sealed, bound_to=_bound(caller.workspace_id, webhook_id))
    got = await client.send(
        str(hook["url"]), secret, delivery_id=str(delivery_id), event=rules.PING, body=body
    )
    await _record(sm, caller.workspace_id, delivery_id, 0, got, retry=False)
    page = await deliveries(sm, caller, webhook_id=webhook_id, limit=1)
    return page.deliveries[0]


class WebhookFanout:
    """Outbox handler: one delivery per turned-on webhook that listens to the event."""

    name = "webhooks"
    events = frozenset(rules.EVENTS)

    def __init__(self, sm: Sessionmaker) -> None:
        self._sm = sm

    async def handle(self, event: Event, effect_key: str) -> None:
        async with workspace_session(self._sm, event.workspace_id) as s:
            for hook in await repo.listening(s, event.event):
                if rules.wants(list(hook["events"]), event.event, event.payload):
                    await repo.add_delivery(
                        s, event.workspace_id, webhook_id=hook["id"], event_id=event.id,
                        event=event.event, body=event.payload,
                    )  # fmt: skip


@dataclass
class SendResult:
    delivered: int = 0
    retrying: int = 0
    failed: int = 0


async def send_due(
    sm: Sessionmaker, sealer: Sealer, client: WebhookClient, *, batch: int = BATCH
) -> SendResult:
    """Each workspace's due deliveries: claimed with a lease (safe with several workers),
    sent outside any transaction, then logged."""
    result = SendResult()
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    for workspace_id in workspace_ids:
        async with workspace_session(sm, workspace_id) as s:
            due = await repo.claim_due(s, batch, LEASE_SECONDS)
        for row in due:
            delivery_id = UUID(str(row["id"]))
            if not row["enabled"]:
                got = Answer(None, 0, "The webhook is turned off.", None)
                state = await _record(sm, workspace_id, delivery_id, 99, got, retry=False)
            else:
                try:
                    secret = sealer.open(
                        bytes(row["secret_sealed"]),
                        bound_to=_bound(workspace_id, row["webhook_id"]),
                    )
                except CannotOpen:
                    logger.warning("webhook secret can't be opened", extra={"event": "webhook"})
                    got = Answer(None, 0, "The secret can't be read: make a new one.", None)
                    state = await _record(sm, workspace_id, delivery_id, 99, got, retry=False)
                else:
                    got = await client.send(
                        str(row["url"]), secret, delivery_id=str(delivery_id),
                        event=str(row["event"]), body=dict(row["body"]),
                    )  # fmt: skip
                    state = await _record(
                        sm, workspace_id, delivery_id, int(row["tries"]), got, retry=True
                    )
            if state == "delivered":
                result.delivered += 1
            elif state == "pending":
                result.retrying += 1
            else:
                result.failed += 1
    return result
