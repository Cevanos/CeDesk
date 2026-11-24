"""Customer portal sign-in (md P1, S7) and the portal settings (Admin).

1. `request_code`: emails a 6-digit code. Any email may ask (a new customer can open a
   ticket), unless the workspace limits the portal to some domains. The answer is the
   same whether or not we know the email; an unknown workspace answers the same too.
2. `verify_code`: the right code (the latest one, within 10 minutes, under 5 wrong
   tries) signs the customer in: their contact is found or made, and a portal session
   token is returned.
3. `resolve`: a portal token → the signed-in customer. Agent tokens don't work here,
   and portal tokens don't work on the agent API (separate tables).

The code email comes from the workspace's default support address, else the server's
`CEDESK_MAIL_FROM_ADDRESS`; it is marked sensitive, so its text is wiped once sent.
"""

import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import auth as auth_rules
from cedesk.helpdesk.domain import portal as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.email import new_message_id
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import email as email_repo
from cedesk.helpdesk.repo import portal as repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Forbidden, Invalid, NotSignedIn
from cedesk.helpdesk.service.members import require
from cedesk.helpdesk.service.password_reset import Sender

logger = logging.getLogger("cedesk.portal")

NOT_AN_EMAIL = "Enter a valid email address."


@dataclass(frozen=True)
class Customer:
    """The signed-in portal customer behind a request."""

    workspace_id: UUID
    contact_id: UUID
    session_id: UUID
    email: str
    name: str


@dataclass(frozen=True)
class SignedIn:
    token: str
    expires_at: datetime
    customer: Customer


@dataclass(frozen=True)
class PortalSettings:
    allowed_domains: list[str]
    csat_survey: bool
    web_form: bool
    updated_at: datetime | None
    updated_by_name: str | None
    show_owner: bool = True  # the assignee's first name in the portal (plan #9)


def _now() -> datetime:
    return datetime.now(UTC)


async def _workspace(sm: Sessionmaker, slug: str) -> UUID | None:
    async with sm() as s, s.begin():
        return await auth_repo.workspace_for_slug(s, slug.strip().lower())


def _email(email: str) -> str:
    value = rules.normalise_email(email)
    if value is None:
        raise Invalid(NOT_AN_EMAIL)
    return value


async def request_code(
    sm: Sessionmaker, *, workspace: str, email: str, secret_key: str, fallback: Sender | None
) -> None:
    address = _email(email)
    workspace_id = await _workspace(sm, workspace)
    if workspace_id is None:
        return
    now = _now()
    async with workspace_session(sm, workspace_id) as s:
        domains = await repo.allowed_domains(s)
        if not rules.domain_allowed(address, domains):
            raise Forbidden(rules.domains_message(domains))
        await repo.lock_email(s, workspace_id, address)
        if await repo.codes_since(s, address, now - timedelta(hours=1)) >= rules.CODES_PER_HOUR:
            logger.info("portal code not sent: too many", extra={"workspace_id": workspace_id})
            return
        default = next((a for a in await email_repo.list_addresses(s) if a["is_default"]), None)
        if default is not None:
            sender = Sender(str(default["address"]), str(default["name"]))
        elif fallback is not None:
            sender = fallback
        else:
            logger.warning(
                "portal code not sent: no support address and no CEDESK_MAIL_FROM_ADDRESS",
                extra={"workspace_id": workspace_id},
            )
            return
        workspace_row = await auth_repo.workspace_row(s)
        name = str(workspace_row["name"]) if workspace_row else workspace
        code = rules.new_code()
        await repo.prune_codes(s, now - timedelta(days=1))
        await repo.insert_code(
            s,
            workspace_id=workspace_id,
            email=address,
            code_hash=rules.code_hash(secret_key, workspace_id, address, code),
            expires_at=now + rules.CODE_LIFETIME,
        )
        await email_repo.enqueue(
            s,
            workspace_id,
            ticket_id=None,
            message_id=None,
            from_address=sender.address,
            from_name=sender.name,
            to=[address],
            cc=[],
            subject=rules.code_subject(name),
            body=rules.code_email(name, code),
            header_message_id=new_message_id(sender.address),
            in_reply_to=None,
            references=[],
            sensitive=True,
        )
    logger.info("portal code queued", extra={"workspace_id": workspace_id})


async def verify_code(
    sm: Sessionmaker, *, workspace: str, email: str, code: str, secret_key: str
) -> SignedIn:
    address = _email(email)
    typed = rules.clean_code(code)
    workspace_id = await _workspace(sm, workspace)
    if workspace_id is None or typed is None:
        raise NotSignedIn(rules.BAD_CODE)
    now = _now()
    async with workspace_session(sm, workspace_id) as s:
        await repo.lock_email(s, workspace_id, address)
        row = await repo.live_code(s, address, now)
        if row is None:
            failed = True
        else:
            expected = rules.code_hash(secret_key, workspace_id, address, typed)
            failed = not hmac.compare_digest(bytes(row["code_hash"]), expected)
            if failed:
                await repo.wrong_try(s, row["id"], burn=row["tries"] + 1 >= rules.CODE_TRIES)
            else:
                await repo.use_code(s, row["id"])
        if not failed:
            # The settings may have changed since the code was sent.
            domains = await repo.allowed_domains(s)
            if not rules.domain_allowed(address, domains):
                raise Forbidden(rules.domains_message(domains))
            contact_id = await ticket_repo.upsert_contact(
                s, workspace_id, address, rules.contact_name(address)
            )
            contact = await ticket_repo.get_contact(s, contact_id)
            await repo.prune_sessions(s, now - auth_rules.ENDED_SESSION_KEPT)
            token = auth_rules.new_token(workspace_id)
            expires_at = now + rules.SESSION_LIFETIME
            session_id = await repo.insert_session(
                s,
                workspace_id=workspace_id,
                contact_id=contact_id,
                token_hash=auth_rules.token_hash(token),
                expires_at=expires_at,
            )
    # Raised after the commit, so the wrong try is counted.
    if failed:
        raise NotSignedIn(rules.BAD_CODE)
    name = str(contact["name"]) if contact else rules.contact_name(address)
    customer = Customer(workspace_id, contact_id, session_id, address, name)
    logger.info("portal sign-in", extra={"workspace_id": workspace_id})
    return SignedIn(token=token, expires_at=expires_at, customer=customer)


async def resolve(sm: Sessionmaker, token: str) -> Customer:
    workspace_id = auth_rules.token_workspace(token)
    if workspace_id is None:
        raise NotSignedIn("Sign in again.")
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.live_session(s, auth_rules.token_hash(token))
    if row is None:
        raise NotSignedIn("Sign in again.")
    # Saving a narrower domain list ends those sessions (save_settings); this also catches
    # one made at the same moment, so a blocked email never gets through.
    domains = list(row["allowed_domains"] or [])
    if not rules.domain_allowed(str(row["email"]), domains):
        raise NotSignedIn(rules.domains_message(domains))
    return Customer(
        workspace_id, row["contact_id"], row["session_id"], str(row["email"]), str(row["name"])
    )


async def sign_out(sm: Sessionmaker, customer: Customer) -> None:
    async with workspace_session(sm, customer.workspace_id) as s:
        await repo.revoke_session(s, customer.session_id)


async def workspace_name(sm: Sessionmaker, customer: Customer) -> str:
    async with workspace_session(sm, customer.workspace_id) as s:
        row = await auth_repo.workspace_row(s)
    return str(row["name"]) if row else ""


# Settings > Portal (Admin)


async def get_settings(sm: Sessionmaker, caller: Caller) -> PortalSettings:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.settings_row(s)
    if row is None:
        return PortalSettings([], True, False, None, None)
    return PortalSettings(
        list(row["allowed_domains"]),
        bool(row["csat_survey"]),
        bool(row["web_form"]),
        row["updated_at"],
        row["updated_by_name"],
        bool(row["show_owner"]),
    )


async def save_settings(
    sm: Sessionmaker,
    caller: Caller,
    domains: list[str],
    *,
    csat_survey: bool = True,
    web_form: bool = False,
    show_owner: bool | None = None,
) -> PortalSettings:
    require(caller, Action.settings)
    try:
        cleaned = rules.clean_domains(domains)
    except rules.BadSetting as exc:
        raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await repo.settings_row(s)
        if show_owner is None:  # not sent: kept (plan #9; older clients don't send it)
            show_owner = bool(before["show_owner"]) if before is not None else True
        await repo.save_settings(
            s, caller.workspace_id, cleaned, csat_survey, web_form, caller.member_id,
            show_owner=show_owner,
        )  # fmt: skip
        # Customers already signed in with an email the list no longer allows are signed
        # out now (their open live stream ends too), as a new IP list applies at once.
        # Before, only new sign-ins were refused (live, Phase 3 retest).
        ended: list[UUID] = []
        if cleaned:
            live = await repo.live_sessions(s)
            ended = [r["id"] for r in live if not rules.domain_allowed(str(r["email"]), cleaned)]
            if ended:
                await repo.revoke_sessions(s, ended)
        changed = audit_rules.changes(
            before
            or {"allowed_domains": [], "csat_survey": True, "web_form": False, "show_owner": True},
            await repo.settings_row(s),
            fields=("allowed_domains", "csat_survey", "web_form", "show_owner"),
        )
        if changed:
            detail: dict[str, object] = {"changes": changed}
            if ended:
                detail["portal_sessions_ended"] = len(ended)
            await audit.record(s, caller, Area.portal, "changed", "Customer portal", detail=detail)
    return await get_settings(sm, caller)
