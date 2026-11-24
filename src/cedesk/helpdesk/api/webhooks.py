"""Outgoing webhooks (md PL3-PL6): Admins add URLs, test them and read the delivery log.
How deliveries are signed and retried: domain/webhooks.py."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SealerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.domain import webhooks as rules
from cedesk.helpdesk.service import webhooks
from cedesk.helpdesk.service.webhooks import DeliveryView, WebhookView
from cedesk.webhook_client import WebhookClient

router = APIRouter(tags=["webhooks"])

EventName = Literal["ticket.created", "ticket.updated", "ticket.resolved", "message.created"]

_SIGNED = (
    "Each delivery is a POST with the JSON event as body and the headers X-CeDesk-Event, "
    "X-CeDesk-Delivery (the same on every retry), X-CeDesk-Timestamp (unix seconds) and "
    "X-CeDesk-Signature: sha256=<hex HMAC-SHA256(secret, '<timestamp>.<body>')>. Check the "
    "signature and that the timestamp is within 5 minutes. Answer 2xx within 10 s; "
    "otherwise it is tried again after 1 min, 5 min, 30 min, 2 h and 12 h."
)
_ADMIN: dict[int | str, dict[str, Any]] = {
    403: {"description": "Admins only, signed in (not with an API key)"}
}


def get_client(request: Request) -> WebhookClient:
    client: WebhookClient = request.app.state.webhook_client
    return client


ClientDep = Annotated[WebhookClient, Depends(get_client)]


class WebhookIn(Body):
    url: str = Field(max_length=rules.URL_MAX, description="https, a public host name.")
    events: list[EventName] = Field(
        min_length=1,
        max_length=len(rules.EVENTS),
        description="message.created: customer and agent replies only (never notes); it "
        "names the message, the text is read through the API.",
    )
    enabled: bool = True


class WebhookPatch(Body):
    url: str | None = Field(default=None, max_length=rules.URL_MAX)
    events: list[EventName] | None = Field(default=None, min_length=1)
    enabled: bool | None = None


class WebhookOut(BaseModel):
    id: UUID
    url: str
    events: list[str]
    secret_hint: str = Field(description="whsec_… and the secret's last 4 characters.")
    enabled: bool
    created_at: datetime
    updated_at: datetime
    created_by_name: str | None


class WebhookSecretOut(WebhookOut):
    secret: str = Field(description="Shown this once: sign checks need it.")


class AttemptOut(BaseModel):
    at: datetime
    status: int | None = Field(description="The answer's HTTP status; null: no answer.")
    ms: int
    error: str | None
    response: str | None = Field(description="The start of the answer (300 characters).")


class DeliveryOut(BaseModel):
    id: UUID = Field(description="As sent in X-CeDesk-Delivery.")
    webhook_id: UUID
    url: str
    event: str
    body: dict[str, Any] = Field(description="Exactly what was (or will be) sent.")
    state: Literal["pending", "delivered", "failed"]
    tries: int
    next_try_at: datetime | None
    last_status: int | None
    last_ms: int | None
    last_error: str | None
    delivered_at: datetime | None
    created_at: datetime
    attempts: list[AttemptOut]


class DeliveryPageOut(BaseModel):
    deliveries: list[DeliveryOut]
    next_cursor: str | None


def _out(view: WebhookView) -> WebhookOut:
    return WebhookOut(**view.__dict__)


def _delivery(view: DeliveryView) -> DeliveryOut:
    data = view.__dict__ | {"attempts": [AttemptOut(**a.__dict__) for a in view.attempts]}
    return DeliveryOut(**data)


@router.get("/webhooks", response_model=list[WebhookOut], summary="Webhooks (Admin)",
            responses=_ADMIN)  # fmt: skip
async def list_webhooks(caller: CallerDep, sm: SessionmakerDep) -> list[WebhookOut]:
    return [_out(v) for v in await webhooks.all_webhooks(sm, caller)]


@router.post(
    "/webhooks",
    response_model=WebhookSecretOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a webhook (Admin; the secret is shown once)",
    description=_SIGNED + f" At most {rules.MAX_WEBHOOKS} per workspace (409). Audited.",
    responses=_ADMIN | {422: {"description": "Not https, not a public host, unknown event"}},
)
async def create_webhook(
    body: WebhookIn, caller: CallerDep, sm: SessionmakerDep, sealer: SealerDep, client: ClientDep
) -> WebhookSecretOut:
    made = await webhooks.create_webhook(
        sm, sealer, client, caller, url=body.url, events=list(body.events), enabled=body.enabled
    )
    return WebhookSecretOut(**made.view.__dict__, secret=made.secret)


@router.patch(
    "/webhooks/{webhook_id}",
    response_model=WebhookOut,
    summary="Change a webhook (Admin; only what is sent changes)",
    responses=_ADMIN | {404: {"description": "No such webhook"}},
)
async def update_webhook(
    webhook_id: UUID, body: WebhookPatch, caller: CallerDep, sm: SessionmakerDep, client: ClientDep
) -> WebhookOut:
    view = await webhooks.update_webhook(
        sm, client, caller, webhook_id, url=body.url,
        events=list(body.events) if body.events is not None else None, enabled=body.enabled,
    )  # fmt: skip
    return _out(view)


@router.post(
    "/webhooks/{webhook_id}/secret",
    response_model=WebhookSecretOut,
    summary="A new secret (Admin; shown once, the old one stops at once)",
    responses=_ADMIN | {404: {"description": "No such webhook"}},
)
async def new_secret(
    webhook_id: UUID, caller: CallerDep, sm: SessionmakerDep, sealer: SealerDep
) -> WebhookSecretOut:
    made = await webhooks.new_secret(sm, sealer, caller, webhook_id)
    return WebhookSecretOut(**made.view.__dict__, secret=made.secret)


@router.delete(
    "/webhooks/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a webhook (Admin; its queue and log go too)",
    responses=_ADMIN | {404: {"description": "No such webhook"}},
)
async def delete_webhook(webhook_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> Response:
    await webhooks.delete_webhook(sm, caller, webhook_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/webhooks/{webhook_id}/test",
    response_model=DeliveryOut,
    summary="Send a test ping now (Admin)",
    description="Body `{event: 'ping', id, occurred_at, webhook: {id}}`, signed like any "
    "delivery. One try, no retries; waits for the answer (at most 10 s) and logs it.",
    responses=_ADMIN
    | {
        404: {"description": "No such webhook"},
        409: {"description": "Its secret can't be read (the server key changed): make a new one"},
    },
)
async def test_webhook(
    webhook_id: UUID, caller: CallerDep, sm: SessionmakerDep, sealer: SealerDep, client: ClientDep
) -> DeliveryOut:
    return _delivery(await webhooks.ping(sm, sealer, client, caller, webhook_id))


@router.get(
    "/webhooks/deliveries",
    response_model=DeliveryPageOut,
    summary="Delivery log (Admin)",
    description="Newest first, each with every try. `state=failed`: the ones that gave up "
    "(send them again with POST /webhooks/deliveries/{id}/retry).",
    responses=_ADMIN,
)
async def list_deliveries(
    caller: CallerDep,
    sm: SessionmakerDep,
    state: Literal["pending", "delivered", "failed"] | None = None,
    webhook_id: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=paging.MAX_LIMIT)] = paging.DEFAULT_LIMIT,
) -> DeliveryPageOut:
    page = await webhooks.deliveries(
        sm, caller, state=state, webhook_id=webhook_id, cursor=cursor, limit=limit
    )
    return DeliveryPageOut(
        deliveries=[_delivery(d) for d in page.deliveries], next_cursor=page.next_cursor
    )


@router.post(
    "/webhooks/deliveries/{delivery_id}/retry",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Send a failed delivery again (Admin)",
    responses=_ADMIN
    | {
        404: {"description": "No failed delivery with that id"},
        409: {"description": "Its webhook is turned off: turn it on first (the message says so)"},
    },
)
async def retry_delivery(delivery_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> Response:
    await webhooks.retry_delivery(sm, caller, delivery_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
