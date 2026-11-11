"""Tells a portal customer that an agent replied (md P4): their ticket lives in the
portal, so without this email they would only see the reply the next time they look.

An outbox handler on `message.created` (kind `agent`, on a `portal` ticket): after the
commit, retried by the worker. The email has no message text, only a link to the ticket
in the portal (sign-in by code). At most one per ticket in 15 minutes, so a burst of
replies sends one email; the header id comes from the event, so a retry never sends two.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import events
from cedesk.helpdesk.domain import portal as rules
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import email as email_repo
from cedesk.helpdesk.repo import portal as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.delivery import Event
from cedesk.helpdesk.service.password_reset import Sender

logger = logging.getLogger("cedesk.portal")


class PortalNoticeHandler:
    name = "portal_notice"
    events = frozenset({events.Event.message_created.value})

    def __init__(self, sm: Sessionmaker, *, base_url: str, fallback: Sender | None) -> None:
        self._sm = sm
        self._base_url = base_url
        self._fallback = fallback

    async def handle(self, event: Event, effect_key: str) -> None:
        info = event.payload.get("message") or {}
        if event.ticket_id is None or info.get("kind") != "agent":
            return
        ws, ticket_id = event.workspace_id, event.ticket_id
        async with workspace_session(self._sm, ws) as s:
            ticket = await ticket_repo.ticket_detail(s, ticket_id)
            if ticket is None or ticket["channel"] != "portal":
                return
            default = next((a for a in await email_repo.list_addresses(s) if a["is_default"]), None)
            sender = (
                Sender(str(default["address"]), str(default["name"]))
                if default is not None
                else self._fallback
            )
            if sender is None:
                logger.warning(
                    "portal reply email not sent: no support address and no "
                    "CEDESK_MAIL_FROM_ADDRESS",
                    extra={"workspace_id": ws},
                )
                return
            header_id = rules.notice_header_id(effect_key, sender.address)
            if await repo.notice_queued(s, header_id):
                return  # a retried event
            since = datetime.now(UTC) - rules.NOTICE_QUIET
            if await repo.recent_notice(s, ticket_id, since):
                return  # one email per burst of replies
            workspace = await auth_repo.workspace_row(s)
            if workspace is None or not workspace["slug"]:
                return
            author = info.get("author_member_id")
            member = await ticket_repo.member_brief(s, UUID(str(author))) if author else None
            ticket_label = label(int(ticket["number"]))
            await email_repo.enqueue(
                s,
                ws,
                ticket_id=ticket_id,
                message_id=None,
                from_address=sender.address,
                from_name=sender.name,
                to=[str(ticket["contact_email"])],
                cc=[],
                subject=rules.notice_subject(ticket_label, str(ticket["subject"])),
                body=rules.notice_email(
                    str(ticket["contact_name"]),
                    str(member["name"]) if member else "An agent",
                    str(workspace["name"]),
                    ticket_label,
                    rules.ticket_link(self._base_url, str(workspace["slug"]), ticket_id),
                ),
                header_message_id=header_id,
                in_reply_to=None,
                references=[],
            )
        logger.info("portal reply email queued", extra={"workspace_id": ws})
