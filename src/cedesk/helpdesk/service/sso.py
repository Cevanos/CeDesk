"""Single sign-on with SAML (md S1).

An Admin enters the identity provider's entity id, sign-in URL and certificate (from the
IdP's setup screen), and gives the IdP our entity id and ACS URL (or our metadata).
`enabled` adds "Sign in with SSO"; `enforced` refuses password sign-in for everyone but
Admins, who keep their password so a broken IdP setup can still be fixed.

Signing in: `start` remembers a request and sends the browser to the IdP; the IdP posts
its signed answer to `finish`, which accepts it only for a request of ours, once, and
only for an active member with that email (members are not created by SSO). The session
counts as fully signed in: the IdP's own MFA replaces CeDesk's 2FA for it.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.engine import RowMapping

from cedesk import saml
from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import auth as auth_rules
from cedesk.helpdesk.domain import security as security_rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import security as security_repo
from cedesk.helpdesk.repo import sso as repo
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, IpNotAllowed, NotFound, NotSignedIn
from cedesk.helpdesk.service.members import require

NOT_SET_UP = "Single sign-on isn't set up for this workspace."


@dataclass(frozen=True)
class SsoIn:
    enabled: bool
    enforced: bool
    idp_entity_id: str | None
    idp_sso_url: str | None
    idp_cert: str | None


@dataclass(frozen=True)
class SignedIn:
    token: str
    expires_at: datetime
    next_path: str


@dataclass(frozen=True)
class Started:
    url: str
    browser: str  # goes in a cookie; the answer must come back with it


def _browser_hash(browser: str | None) -> bytes:
    return hashlib.sha256((browser or "").encode()).digest()


def sp_for(api_base: str, slug: str) -> saml.Sp:
    base = f"{api_base.rstrip('/')}/api/v1/sso/{slug}"
    return saml.Sp(entity_id=f"{base}/metadata", acs_url=f"{base}/acs")


def safe_next(path: str | None) -> str:
    """Only a path inside the app (no other site), else the inbox."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path[:500]


def _idp(row: RowMapping | None) -> saml.Idp | None:
    if row is None or not row["enabled"]:
        return None
    return saml.Idp(str(row["idp_entity_id"]), str(row["idp_sso_url"]), str(row["idp_cert"]))


async def get_settings(sm: Sessionmaker, caller: Caller) -> RowMapping | None:
    require(caller, Action.manage_access)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.settings_row(s)


def _checked(new: SsoIn) -> dict[str, object]:
    values: dict[str, object] = {
        "enabled": new.enabled,
        "enforced": new.enforced,
        "idp_entity_id": (new.idp_entity_id or "").strip() or None,
        "idp_sso_url": (new.idp_sso_url or "").strip() or None,
        "idp_cert": (new.idp_cert or "").strip() or None,
    }
    if new.enforced and not new.enabled:
        raise Invalid("Turn single sign-on on before requiring it.")
    if not new.enabled:
        return values
    if not (values["idp_entity_id"] and values["idp_sso_url"] and values["idp_cert"]):
        raise Invalid("Enter the identity provider's entity id, sign-in URL and certificate.")
    url = urlsplit(str(values["idp_sso_url"]))
    if url.scheme != "https" or not url.hostname:
        raise Invalid("The identity provider's sign-in URL must start with https://.")
    try:
        saml.cert_body(str(values["idp_cert"]))
    except saml.SamlError as exc:
        raise Invalid(str(exc)) from exc
    return values


async def save_settings(sm: Sessionmaker, caller: Caller, new: SsoIn) -> RowMapping | None:
    require(caller, Action.manage_access)
    values = _checked(new)
    async with workspace_session(sm, caller.workspace_id) as s:
        old = await repo.lock_settings(s, caller.workspace_id)
        assert old is not None
        changes = []
        if bool(old["enabled"]) != new.enabled:
            changes.append("Single sign-on was turned " + ("on." if new.enabled else "off."))
        if bool(old["enforced"]) != new.enforced:
            changes.append(
                "Password sign-in is now "
                + ("off for everyone except Admins (SSO required)." if new.enforced else "on.")
            )
        if (
            new.enabled
            and old["enabled"]
            and any(old[k] != values[k] for k in ("idp_entity_id", "idp_sso_url", "idp_cert"))
        ):
            changes.append("The single sign-on identity provider settings changed.")
        for what in changes:
            await security_repo.queue_alert(s, caller.workspace_id, what, caller.member_id)
        await repo.save(s, values, caller.member_id)
        after = await repo.settings_row(s)
        changed = audit_rules.changes(
            old, after, fields=("enabled", "enforced", "idp_entity_id", "idp_sso_url", "idp_cert")
        )
        if changed:
            await audit.record(
                s, caller, Area.sso, "changed", "Single sign-on", detail={"changes": changed}
            )
        return after


async def _workspace(sm: Sessionmaker, slug: str) -> UUID:
    async with sm() as s, s.begin():
        workspace_id = await auth_repo.workspace_for_slug(s, slug.strip().lower())
    if workspace_id is None:
        raise NotFound(NOT_SET_UP)
    return workspace_id


async def status(sm: Sessionmaker, slug: str) -> tuple[bool, bool]:
    """(enabled, enforced) for the sign-in page; an unknown workspace looks like one
    without SSO."""
    try:
        workspace_id = await _workspace(sm, slug)
    except NotFound:
        return False, False
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.settings_row(s)
    return (bool(row["enabled"]), bool(row["enforced"])) if row else (False, False)


async def start(sm: Sessionmaker, *, slug: str, api_base: str, next_path: str | None) -> Started:
    """Where to send the browser (the IdP, with a request we remember), and the secret
    the browser must bring back to the ACS URL."""
    browser = secrets.token_urlsafe(32)
    workspace_id = await _workspace(sm, slug)
    async with workspace_session(sm, workspace_id) as s:
        idp = _idp(await repo.settings_row(s))
        if idp is None:
            raise NotFound(NOT_SET_UP)
        try:
            url, request_id = saml.login_url(sp_for(api_base, slug), idp, safe_next(next_path))
        except saml.SamlError as exc:
            raise Invalid(str(exc)) from exc
        await repo.add_request(s, workspace_id, request_id, _browser_hash(browser))
    return Started(url, browser)


async def finish(
    sm: Sessionmaker,
    *,
    slug: str,
    api_base: str,
    saml_response: str,
    relay_state: str | None,
    ip: str | None,
    browser: str | None,
) -> SignedIn:
    """Checks the IdP's answer and signs the member in."""
    workspace_id = await _workspace(sm, slug)
    sp = sp_for(api_base, slug)
    now = datetime.now(UTC)
    async with workspace_session(sm, workspace_id) as s:
        idp = _idp(await repo.settings_row(s))
        if idp is None:
            raise NotFound(NOT_SET_UP)
        request_id = saml.answered_request(sp, idp, saml_response)
        # Used up before the answer is checked: a replayed or forged answer can't use it.
        if (
            request_id is None
            or not browser
            or not await repo.take_request(s, request_id, _browser_hash(browser))
        ):
            raise NotSignedIn("This sign-in expired or was already used. Start again.")
    try:
        identity = saml.read_answer(sp, idp, saml_response, request_id)
    except saml.SamlError as exc:
        raise NotSignedIn(str(exc)) from exc
    async with workspace_session(sm, workspace_id) as s:
        security = await security_repo.current(s)
        if not security_rules.ip_allowed(ip, security.ip_allowlist):
            raise IpNotAllowed(security_rules.ip_refused(ip))
        member = await repo.active_member(s, identity.email)
        if member is None:
            raise NotSignedIn(
                f"{identity.email} isn't an active member of this workspace. Ask an Admin."
            )
        token = auth_rules.new_token(workspace_id)
        expires_at = security_rules.session_ends(security.session_length, now, now)
        await auth_repo.insert_session(
            s,
            workspace_id=workspace_id,
            member_id=member["id"],
            token_hash=auth_rules.token_hash(token),
            expires_at=expires_at,
            via_sso=True,
        )
    return SignedIn(token, expires_at, safe_next(relay_state))


async def workspace_slug(sm: Sessionmaker, caller: Caller) -> str:
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await auth_repo.workspace_row(s)
    if row is None:
        raise NotFound("Workspace not found.")
    return str(row["slug"])
