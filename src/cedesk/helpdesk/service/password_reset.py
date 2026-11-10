"""Forgot password (md A3).

`forgot_password` emails a one-time link (30 minutes). It answers the same whatever
happens (unknown workspace or email, inactive member, too many links), so nobody can use
it to find out who has an account. `reset_with_link` sets the new password, unlocks the
member, ends all their sessions and removes all their links.

The email comes from the workspace's default support address, else from the server's
`CEDESK_MAIL_FROM_ADDRESS`; with neither, nothing can be sent (logged).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import auth as rules
from cedesk.helpdesk.domain.email import new_message_id
from cedesk.helpdesk.repo import auth as repo
from cedesk.helpdesk.repo import email as email_repo
from cedesk.helpdesk.service import passwords
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Gone, Invalid

logger = logging.getLogger("cedesk.auth")

BAD_LINK = "This reset link is invalid, used or expired. Ask for a new one."
SUBJECT = "Reset your CeDesk password"


@dataclass(frozen=True)
class Sender:
    """The server's fallback sender (`CEDESK_MAIL_FROM_ADDRESS`), if set."""

    address: str
    name: str


async def forgot_password(
    sm: Sessionmaker, *, workspace: str, email: str, app_url: str, fallback: Sender | None
) -> None:
    slug, address = workspace.strip().lower(), rules.normalise_email(email)
    async with sm() as s, s.begin():
        workspace_id = await repo.workspace_for_slug(s, slug)
    if workspace_id is None:
        return
    now = datetime.now(UTC)
    async with workspace_session(sm, workspace_id) as s:
        member = await repo.active_member_for_email(s, address)
        if member is None:
            return
        if await repo.recent_resets(s, member["id"], now - timedelta(hours=1)) >= (
            rules.RESETS_PER_HOUR
        ):
            logger.info("reset link not sent: too many", extra={"member_id": member["id"]})
            return
        default = next((a for a in await email_repo.list_addresses(s) if a["is_default"]), None)
        if default is not None:
            sender = Sender(str(default["address"]), str(default["name"]))
        elif fallback is not None:
            sender = fallback
        else:
            logger.warning(
                "reset link not sent: no support address and no CEDESK_MAIL_FROM_ADDRESS",
                extra={"workspace_id": workspace_id},
            )
            return
        workspace_row = await repo.workspace_row(s)
        token = rules.new_token(workspace_id)
        await repo.insert_reset(
            s,
            workspace_id=workspace_id,
            member_id=member["id"],
            token_hash=rules.token_hash(token),
            expires_at=now + rules.RESET_LIFETIME,
        )
        await email_repo.enqueue(
            s,
            workspace_id,
            ticket_id=None,
            message_id=None,
            from_address=sender.address,
            from_name=sender.name,
            to=[str(member["email"])],
            cc=[],
            subject=SUBJECT,
            body=rules.reset_email(
                str(member["name"]),
                str(workspace_row["name"]) if workspace_row else slug,
                rules.reset_link(app_url, token),
            ),
            header_message_id=new_message_id(sender.address),
            in_reply_to=None,
            references=[],
            sensitive=True,
        )
    logger.info("reset link queued", extra={"member_id": member["id"]})


async def reset_with_link(sm: Sessionmaker, *, token: str, new_password: str) -> None:
    problem = rules.password_problem(new_password)
    if problem:
        raise Invalid(problem)
    workspace_id = rules.token_workspace(token)
    if workspace_id is None:
        raise Gone(BAD_LINK)
    # Slow (Argon2): hashed before any row is locked.
    new_hash = await asyncio.to_thread(passwords.hash_password, new_password)
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.take_reset(s, rules.token_hash(token))
        if row is None or not row["is_active"]:
            raise Gone(BAD_LINK)
        await repo.set_password(s, row["member_id"], new_hash)  # also clears any lockout
        await repo.revoke_member_sessions(s, row["member_id"])
        await repo.drop_resets(s, row["member_id"])
    logger.info("password reset by email link", extra={"member_id": row["member_id"]})
