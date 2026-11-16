"""Marketplace apps (md PL7) and the ticket sidebar's orders and payments (C12)."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SealerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain.apps import App
from cedesk.helpdesk.service import apps
from cedesk.helpdesk.service.apps import AppHttp, AppView, Card
from cedesk.helpdesk.service.jira import PostFactory

router = APIRouter(tags=["apps"])


def get_http(request: Request) -> AppHttp:
    http: AppHttp = request.app.state.app_http
    return http


HttpDep = Annotated[AppHttp, Depends(get_http)]


def get_post(request: Request) -> PostFactory:
    post: PostFactory = request.app.state.app_post
    return post


PostDep = Annotated[PostFactory, Depends(get_post)]


class AppOut(BaseModel):
    app: App
    name: str
    card: str = Field(
        description="What it adds: orders or payments (ticket sidebar), jira, alerts, or "
        "side (side conversations in Slack), book (the saved reply /book)."
    )
    settings_fields: list[str] = Field(description="Settings to send at install (not secret).")
    credential: str | None = Field(
        description="The name of the credential to send; null: none (Calendly)."
    )
    credential_help: str
    second_credential: str | None = Field(
        description="A second secret to send at install (Slack side conversations: "
        "signing_secret), or null."
    )
    installed: bool
    settings: dict[str, Any]
    secret_hint: str | None = Field(description="… and the credential's last 4 characters.")
    installed_at: datetime | None
    installed_by_name: str | None
    blocked: str | None = Field(
        description="Why it can't be installed here, e.g. Cevanos is connected (md D16)."
    )
    note: str | None = Field(
        default=None,
        description="Only in the answer to a save: something the Admin should know, e.g. "
        "Calendly's /book was edited by hand, so its link wasn't changed.",
    )


class InstallIn(Body):
    settings: dict[str, str] = Field(
        default={},
        max_length=5,
        description='Shopify: {"shop": "acme"}. Jira: {"site": "acme", "email": '
        '"ops@acme.com", "project": "SUP"}. Calendly: {"link": '
        '"https://calendly.com/acme/30min"}. Stripe and both Slack apps: none.',
    )
    secret: str | None = Field(
        default=None,
        max_length=300,
        description="The app's credential (Slack alerts: the Incoming Webhook URL; Slack "
        "side conversations: the bot token, xoxb-...). Required, except for Calendly, which "
        "takes none.",
    )
    signing_secret: str | None = Field(
        default=None,
        max_length=300,
        description="Slack side conversations only: the Slack app's signing secret.",
    )


class OrderOut(BaseModel):
    id: str
    number: str
    created_at: datetime | None
    payment: str | None
    fulfillment: str | None
    total: str
    currency: str
    items: int
    url: str | None


class PaymentOut(BaseModel):
    id: str
    amount: str
    currency: str
    status: str
    refunded: bool
    created_at: datetime | None
    description: str | None
    url: str | None = Field(description="The payment in Stripe; null when the store has none.")


class CardOut(BaseModel):
    source: str | None = Field(description="shopify, stripe, cevanos, or null (no app).")
    available: bool = Field(
        description="false: nothing to show, and `message` says why (not installed, no "
        "customer email, the provider refused or didn't answer)."
    )
    message: str | None


class OrdersOut(CardOut):
    orders: list[OrderOut]


class PaymentsOut(CardOut):
    payments: list[PaymentOut]


def _out(view: AppView) -> AppOut:
    return AppOut(**(view.__dict__ | {"settings_fields": list(view.settings_fields)}))


@router.get("/apps", response_model=list[AppOut], summary="Apps and what is installed (Settings)")
async def list_apps(caller: CallerDep, sm: SessionmakerDep) -> list[AppOut]:
    return [_out(v) for v in await apps.catalog(sm, caller)]


@router.put(
    "/apps/{app}",
    response_model=AppOut,
    summary="Install an app, or change its settings and credential (Settings)",
    description="The credential is checked with the provider first and then kept "
    "encrypted; only its last 4 characters are shown again. Audited.",
    responses={
        409: {"description": "Cevanos is connected: its integrations are used instead"},
        422: {"description": "Bad settings, or the provider refused the credential"},
        503: {"description": "The provider didn't answer; try again"},
    },
)
async def install_app(
    app: App, body: InstallIn, caller: CallerDep, sm: SessionmakerDep, sealer: SealerDep,
    http: HttpDep, post: PostDep,
) -> AppOut:  # fmt: skip
    return _out(
        await apps.install(
            sm,
            sealer,
            http,
            post,
            caller,
            app,
            settings=body.settings,
            secret=body.secret,
            second=body.signing_secret,
        )
    )


@router.delete(
    "/apps/{app}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Uninstall an app (Settings; its credential is deleted)",
    responses={404: {"description": "Not installed"}},
)
async def uninstall_app(app: App, caller: CallerDep, sm: SessionmakerDep) -> Response:
    await apps.uninstall(sm, caller, app)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _card(card: Card) -> dict[str, Any]:
    return {"source": card.source, "available": card.available, "message": card.message}


@router.get(
    "/tickets/{ticket_id}/orders",
    response_model=OrdersOut,
    summary="The customer's latest orders (sidebar)",
    description="Read live from the Shopify app for the ticket's customer (by email), at "
    "most 5; nothing is stored. Never fails because of the shop: see `available`.",
)
async def ticket_orders(
    ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep, sealer: SealerDep, http: HttpDep
) -> OrdersOut:
    card = await apps.orders(sm, sealer, http, caller, ticket_id)
    return OrdersOut(**_card(card), orders=[OrderOut(**o.__dict__) for o in card.orders])


@router.get(
    "/tickets/{ticket_id}/payments",
    response_model=PaymentsOut,
    summary="The customer's latest payments (sidebar)",
    description="Read live from the Stripe app for the ticket's customer (by email), at "
    "most 5; nothing is stored. Never fails because of Stripe: see `available`.",
)
async def ticket_payments(
    ticket_id: UUID, caller: CallerDep, sm: SessionmakerDep, sealer: SealerDep, http: HttpDep
) -> PaymentsOut:
    card = await apps.payments(sm, sealer, http, caller, ticket_id)
    return PaymentsOut(**_card(card), payments=[PaymentOut(**p.__dict__) for p in card.payments])
