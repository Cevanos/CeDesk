"""The public web form channel (md C2): name, email, type, fields and message make a
`web` ticket with `source: web_form`, through `create_from_channel` (rules, routing,
SLA, events). The customer hears back by email: replies on form tickets are emailed.

The Admin turns it on in the portal settings (off by default). Spam protection:
- a honeypot: a field people never see. A bot that fills it gets the same answer as a
  person, but nothing is made;
- per IP: `form_limit` posts per `form_window_seconds` (the API's `FormLimit`);
- per email: at most 5 tickets an hour (more get 429).
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import RowMapping

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import portal as rules
from cedesk.helpdesk.domain.tickets import Channel
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import catalog as catalog_repo
from cedesk.helpdesk.repo import portal as repo
from cedesk.helpdesk.service import tickets
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, NotFound, TooManyAttempts
from cedesk.helpdesk.service.messages import WEB_FORM_SOURCE
from cedesk.helpdesk.service.portal_tickets import check_customer_type

logger = logging.getLogger("cedesk.web_form")

NO_FORM = "This contact form is not available."
MESSAGE_MAX = 50_000


@dataclass(frozen=True)
class Form:
    workspace_name: str
    types: Sequence[RowMapping]


async def _open_workspace(sm: Sessionmaker, slug: str) -> UUID:
    """The workspace behind the slug, if its form is on. Otherwise NotFound, the same for
    an unknown workspace and a form that is off."""
    async with sm() as s, s.begin():
        workspace_id = await auth_repo.workspace_for_slug(s, slug.strip().lower())
    if workspace_id is None:
        raise NotFound(NO_FORM)
    async with workspace_session(sm, workspace_id) as s:
        if not await repo.web_form_on(s):
            raise NotFound(NO_FORM)
    return workspace_id


async def form(sm: Sessionmaker, slug: str) -> Form:
    workspace_id = await _open_workspace(sm, slug)
    async with workspace_session(sm, workspace_id) as s:
        workspace = await auth_repo.workspace_row(s)
        types = list(await catalog_repo.list_types(s, customers_only=True))
    return Form(str(workspace["name"]) if workspace else "", types)


async def submit(
    sm: Sessionmaker,
    slug: str,
    *,
    name: str,
    email: str,
    message: str,
    type_id: UUID | None = None,
    fields: Mapping[str, object] | None = None,
    honeypot: str = "",
) -> None:
    workspace_id = await _open_workspace(sm, slug)
    address = rules.normalise_email(email)
    if address is None:
        raise Invalid("Enter a valid email address.")
    body = message.strip()
    if not body:
        raise Invalid("Write your message.")
    if len(body) > MESSAGE_MAX:
        raise Invalid(f"A message can be at most {MESSAGE_MAX} characters.")
    if honeypot.strip():
        logger.info("web form: honeypot filled, ignored", extra={"workspace_id": workspace_id})
        return
    async with workspace_session(sm, workspace_id) as s:
        await check_customer_type(s, type_id)
        since = datetime.now(UTC) - timedelta(hours=1)
        if await repo.web_forms_since(s, address, since) >= rules.FORMS_PER_EMAIL_HOUR:
            raise TooManyAttempts(
                "You've sent a lot of messages. We'll answer those first; try again later.",
                3600,
            )
    await tickets.create_from_channel(
        sm,
        workspace_id,
        tickets.NewTicket(
            subject=rules.subject_from(body),  # remade after redaction
            subject_from_description=True,
            contact_email=address,
            contact_name=rules.clean_name(name, address),
            description=body,
            channel=Channel.web,
            source=WEB_FORM_SOURCE,
            type_id=type_id,
            fields=fields or {},
        ),
    )
    logger.info("web form ticket", extra={"workspace_id": workspace_id})
