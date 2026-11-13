"""Slack alerts (md W20): an outbox handler that posts Urgent and High tickets to the
channel of the workspace's Slack app. Rules in domain/slack.py.

It runs after the change commits, so the ticket is what the rules made of it, and the
outbox retries it (1 min ... 12 h) when Slack doesn't answer. A workspace without the app
gets nothing. A refused webhook (deleted in Slack) fails at once and shows in the
Admins' failed list.
"""

from collections.abc import Callable
from uuid import UUID

from cedesk.crypto import CannotOpen, Sealer
from cedesk.db import workspace_session
from cedesk.helpdesk.domain import slack as rules
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import apps as app_repo
from cedesk.helpdesk.repo import demo as demo_repo
from cedesk.helpdesk.service.app_post import AppCallError
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.delivery import Event, HandlerError
from cedesk.helpdesk.service.jira import PostFactory, ticket_link

APP = "slack_alerts"


class SlackAlertHandler:
    name = "slack_alerts"
    events = rules.EVENTS

    def __init__(
        self,
        sm: Sessionmaker,
        sealer: Sealer,
        post: PostFactory,
        bound: Callable[[UUID], str],
        *,
        app_url: str | None,
    ) -> None:
        self._sm = sm
        self._sealer = sealer
        self._post = post
        self._bound = bound
        self._app_url = app_url

    async def handle(self, event: Event, effect_key: str) -> None:
        if not rules.alerts_for(event.event, event.payload):
            return
        async with workspace_session(self._sm, event.workspace_id) as s:
            if event.ticket_id is not None and await demo_repo.is_demo(s, event.ticket_id):
                return  # demo data (md D1) never leaves CeDesk
            app = await app_repo.get(s, APP)
        if app is None:
            return
        try:
            url = self._sealer.open(
                bytes(app["secret_sealed"]), bound_to=self._bound(event.workspace_id)
            )
        except CannotOpen:
            raise HandlerError("The Slack webhook can't be read: install it again.",
                               permanent=True) from None  # fmt: skip
        ticket = event.payload["ticket"]
        link = ticket_link(self._app_url, UUID(str(ticket["id"]))) if self._app_url else None
        number = ticket.get("number")
        text = rules.message(ticket, label(int(number)) if number else "Ticket", link)
        try:
            await self._post(rules.HOST, {}, "Slack").post(rules.path_of(url), text)
        except AppCallError as exc:
            raise HandlerError(str(exc), permanent=exc.permanent) from None
