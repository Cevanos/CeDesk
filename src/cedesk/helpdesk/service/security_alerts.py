"""Emails every active Admin about a security change (md S6): a member added,
deactivated or reactivated, the IP list changed, or the alerts turned off.

An outbox handler on `security.alert`, so the email is queued only after the change
commits and a failure is retried by the worker. Each Admin's email has a header id made
from the event, so a retried event never sends a second copy.
"""

import hashlib
import logging
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import events
from cedesk.helpdesk.domain import security as rules
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import email as email_repo
from cedesk.helpdesk.repo import portal as portal_repo
from cedesk.helpdesk.repo import security as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.delivery import Event
from cedesk.helpdesk.service.password_reset import Sender

logger = logging.getLogger("cedesk.security")


def header_id(effect_key: str, to: str, from_address: str) -> str:
    digest = hashlib.sha256(f"{effect_key}|{to}".encode()).hexdigest()[:32]
    domain = from_address.rsplit("@", 1)[-1] or "cedesk.local"
    return f"<security-{digest}@{domain}>"


class SecurityAlertHandler:
    name = "security_alert"
    events = frozenset({events.Event.security_alert.value})

    def __init__(self, sm: Sessionmaker, *, fallback: Sender | None) -> None:
        self._sm = sm
        self._fallback = fallback

    async def handle(self, event: Event, effect_key: str) -> None:
        what = str(event.payload.get("what") or "")
        by_id = event.payload.get("by")
        ws = event.workspace_id
        async with workspace_session(self._sm, ws) as s:
            default = next((a for a in await email_repo.list_addresses(s) if a["is_default"]), None)
            sender = (
                Sender(str(default["address"]), str(default["name"]))
                if default is not None
                else self._fallback
            )
            if sender is None:
                logger.warning(
                    "security alert not sent: no support address and no CEDESK_MAIL_FROM_ADDRESS",
                    extra={"workspace_id": ws},
                )
                return
            workspace = await auth_repo.workspace_row(s)
            by = await ticket_repo.member_brief(s, UUID(str(by_id))) if by_id else None
            subject, body = rules.alert_email(
                str(workspace["name"]) if workspace else "your workspace",
                what,
                str(by["name"]) if by else "someone",
            )
            for admin in await repo.admins(s):
                to = str(admin["email"])
                hid = header_id(effect_key, to, sender.address)
                if await portal_repo.notice_queued(s, hid):
                    continue  # a retried event
                await email_repo.enqueue(
                    s,
                    ws,
                    ticket_id=None,
                    message_id=None,
                    from_address=sender.address,
                    from_name=sender.name,
                    to=[to],
                    cc=[],
                    subject=subject,
                    body=body,
                    header_message_id=hid,
                    in_reply_to=None,
                    references=[],
                )
