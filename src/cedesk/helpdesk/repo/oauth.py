"""SQL for the AI-app authorization server: clients, grants, codes and tokens.

`oauth_client` is global (no workspace): its functions run in a plain session. Everything
else runs inside a `workspace_session`, so RLS limits it to that workspace.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.repo.auth import ROLE_COLUMNS, ROLE_JOIN, TWO_FACTOR_ON

Row = RowMapping

_CLIENT = (
    "client_id, registration, client_name, client_uri, logo_uri, redirect_uris, "
    "token_endpoint_auth_method, client_secret_hash, scope, document_expires_at, disabled_at"
)


# --- Clients (global) ------------------------------------------------------------


async def get_client(session: AsyncSession, client_id: str) -> Row | None:
    result = await session.execute(
        text(f"SELECT {_CLIENT} FROM oauth_client WHERE client_id = :id"),  # noqa: S608
        {"id": client_id},
    )
    return result.mappings().first()


async def save_document_client(
    session: AsyncSession,
    *,
    client_id: str,
    client_name: str | None,
    client_uri: str | None,
    logo_uri: str | None,
    redirect_uris: Sequence[str],
    expires_at: datetime,
) -> Row:
    """A client known by its metadata document: the document's latest copy."""
    result = await session.execute(
        text(
            "INSERT INTO oauth_client (client_id, registration, client_name, client_uri, "  # noqa: S608
            "logo_uri, redirect_uris, document_expires_at) "
            "VALUES (:id, 'metadata_document', :name, :uri, :logo, :redirects, :expires) "
            "ON CONFLICT (client_id) DO UPDATE SET client_name = EXCLUDED.client_name, "
            "client_uri = EXCLUDED.client_uri, logo_uri = EXCLUDED.logo_uri, "
            "redirect_uris = EXCLUDED.redirect_uris, "
            "document_expires_at = EXCLUDED.document_expires_at, updated_at = now() "
            "WHERE oauth_client.registration = 'metadata_document' "
            f"RETURNING {_CLIENT}"
        ),
        {
            "id": client_id,
            "name": client_name,
            "uri": client_uri,
            "logo": logo_uri,
            "redirects": list(redirect_uris),
            "expires": expires_at,
        },
    )
    return result.mappings().one()


async def insert_registered_client(
    session: AsyncSession,
    *,
    client_id: str,
    registration: str,
    client_name: str | None,
    client_uri: str | None,
    logo_uri: str | None,
    redirect_uris: Sequence[str],
    auth_method: str,
    secret_hash: bytes | None,
    scope: str | None,
) -> Row:
    result = await session.execute(
        text(
            "INSERT INTO oauth_client (client_id, registration, client_name, client_uri, "  # noqa: S608
            "logo_uri, redirect_uris, token_endpoint_auth_method, client_secret_hash, scope) "
            "VALUES (:id, :registration, :name, :uri, :logo, :redirects, :method, :secret, "
            f":scope) RETURNING {_CLIENT}, created_at"
        ),
        {
            "id": client_id,
            "registration": registration,
            "name": client_name,
            "uri": client_uri,
            "logo": logo_uri,
            "redirects": list(redirect_uris),
            "method": auth_method,
            "secret": secret_hash,
            "scope": scope,
        },
    )
    return result.mappings().one()


# --- Grants ----------------------------------------------------------------------


async def session_via_sso(session: AsyncSession, session_id: UUID) -> bool:
    result = await session.execute(
        text("SELECT via_sso FROM auth_session WHERE id = :id"), {"id": session_id}
    )
    return bool(result.scalar_one_or_none())


async def upsert_grant(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    member_id: UUID,
    client_id: str,
    resource: str,
    scopes: Sequence[str],
    via_sso: bool,
) -> UUID:
    """The member's live grant for this app, made or given the newly agreed scopes."""
    result = await session.execute(
        text(
            "INSERT INTO oauth_grant (workspace_id, member_id, client_id, resource, scopes, "
            "via_sso) VALUES (:ws, :member, :client, :resource, :scopes, :sso) "
            "ON CONFLICT (workspace_id, member_id, client_id, resource) "
            "WHERE revoked_at IS NULL "
            "DO UPDATE SET scopes = EXCLUDED.scopes, via_sso = EXCLUDED.via_sso "
            "RETURNING id"
        ),
        {
            "ws": workspace_id,
            "member": member_id,
            "client": client_id,
            "resource": resource,
            "scopes": list(scopes),
            "sso": via_sso,
        },
    )
    return UUID(str(result.scalar_one()))


async def live_grants(session: AsyncSession, member_id: UUID) -> Sequence[Row]:
    result = await session.execute(
        text(
            "SELECT g.id, g.client_id, c.client_name, c.client_uri, c.logo_uri, "
            "c.registration, g.scopes, g.created_at, g.last_used_at "
            "FROM oauth_grant g JOIN oauth_client c ON c.client_id = g.client_id "
            "WHERE g.member_id = :member AND g.revoked_at IS NULL "
            "ORDER BY g.created_at DESC"
        ),
        {"member": member_id},
    )
    return result.mappings().all()


async def revoke_grant(session: AsyncSession, grant_id: UUID, member_id: UUID) -> Row | None:
    """Ends the member's own grant and every token under it. None: not theirs, or gone."""
    result = await session.execute(
        text(
            "UPDATE oauth_grant g SET revoked_at = now() FROM oauth_client c "
            "WHERE g.id = :id AND g.member_id = :member AND g.revoked_at IS NULL "
            "AND c.client_id = g.client_id RETURNING g.id, g.client_id, c.client_name"
        ),
        {"id": grant_id, "member": member_id},
    )
    row = result.mappings().first()
    if row is not None:
        await session.execute(
            text(
                "UPDATE oauth_token SET revoked_at = now() "
                "WHERE grant_id = :id AND revoked_at IS NULL"
            ),
            {"id": grant_id},
        )
    return row


async def touch_grant(session: AsyncSession, grant_id: UUID) -> None:
    """`last_used_at`, at most once a minute (Connected apps shows it)."""
    await session.execute(
        text(
            "UPDATE oauth_grant SET last_used_at = now() WHERE id = :id "
            "AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')"
        ),
        {"id": grant_id},
    )


# --- Codes -----------------------------------------------------------------------


async def insert_code(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    grant_id: UUID,
    code_hash: bytes,
    redirect_uri: str,
    redirect_uri_explicit: bool,
    code_challenge: str,
    scopes: Sequence[str],
    resource: str,
    expires_at: datetime,
) -> None:
    await session.execute(
        text(
            "INSERT INTO oauth_code (workspace_id, grant_id, code_hash, redirect_uri, "
            "redirect_uri_explicit, code_challenge, scopes, resource, expires_at) "
            "VALUES (:ws, :grant, :hash, :redirect, :explicit, :challenge, :scopes, "
            ":resource, :expires)"
        ),
        {
            "ws": workspace_id,
            "grant": grant_id,
            "hash": code_hash,
            "redirect": redirect_uri,
            "explicit": redirect_uri_explicit,
            "challenge": code_challenge,
            "scopes": list(scopes),
            "resource": resource,
            "expires": expires_at,
        },
    )


async def code_for_update(session: AsyncSession, code_hash: bytes) -> Row | None:
    """The code, locked, with its grant and whether the member is still active."""
    result = await session.execute(
        text(
            "SELECT c.id, c.grant_id, c.redirect_uri, c.redirect_uri_explicit, "
            "c.code_challenge, c.scopes, c.resource, c.expires_at, c.used_at, "
            "g.client_id, g.member_id, g.revoked_at AS grant_revoked_at, m.is_active "
            "FROM oauth_code c JOIN oauth_grant g ON g.id = c.grant_id "
            "JOIN member m ON m.id = g.member_id "
            "WHERE c.code_hash = :hash FOR UPDATE OF c"
        ),
        {"hash": code_hash},
    )
    return result.mappings().first()


async def mark_code_used(session: AsyncSession, code_id: UUID) -> None:
    await session.execute(
        text("UPDATE oauth_code SET used_at = now() WHERE id = :id"), {"id": code_id}
    )


async def revoke_code_tokens(session: AsyncSession, code_id: UUID) -> None:
    """A code redeemed twice: whoever has the first answer loses it too (RFC 6749 4.1.2)."""
    await session.execute(
        text(
            "UPDATE oauth_token SET revoked_at = now() WHERE code_id = :id AND revoked_at IS NULL"
        ),
        {"id": code_id},
    )


# --- Tokens ----------------------------------------------------------------------


async def insert_token(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    grant_id: UUID,
    code_id: UUID,
    family_id: UUID,
    kind: str,
    token_hash: bytes,
    scopes: Sequence[str],
    expires_at: datetime,
) -> None:
    await session.execute(
        text(
            "INSERT INTO oauth_token (workspace_id, grant_id, code_id, family_id, kind, "
            "token_hash, scopes, expires_at) "
            "VALUES (:ws, :grant, :code, :family, :kind, :hash, :scopes, :expires)"
        ),
        {
            "ws": workspace_id,
            "grant": grant_id,
            "code": code_id,
            "family": family_id,
            "kind": kind,
            "hash": token_hash,
            "scopes": list(scopes),
            "expires": expires_at,
        },
    )


async def token_for_update(session: AsyncSession, token_hash: bytes) -> Row | None:
    """A token, locked, with its grant and whether the member is still active."""
    result = await session.execute(
        text(
            "SELECT t.id, t.kind, t.grant_id, t.code_id, t.family_id, t.expires_at, "
            "t.used_at, t.revoked_at, t.scopes, g.client_id, g.member_id, g.resource, "
            "g.revoked_at AS grant_revoked_at, m.is_active "
            "FROM oauth_token t JOIN oauth_grant g ON g.id = t.grant_id "
            "JOIN member m ON m.id = g.member_id "
            "WHERE t.token_hash = :hash FOR UPDATE OF t"
        ),
        {"hash": token_hash},
    )
    return result.mappings().first()


async def mark_token_used(session: AsyncSession, token_id: UUID) -> None:
    await session.execute(
        text("UPDATE oauth_token SET used_at = now() WHERE id = :id"), {"id": token_id}
    )


async def revoke_family(session: AsyncSession, family_id: UUID) -> None:
    await session.execute(
        text(
            "UPDATE oauth_token SET revoked_at = now() "
            "WHERE family_id = :family AND revoked_at IS NULL"
        ),
        {"family": family_id},
    )


async def live_access(session: AsyncSession, token_hash: bytes) -> Row | None:
    """An unexpired, unrevoked access token under a live grant of an active member,
    with what the MCP caller is built from (role, 2FA, the workspace's IP list)."""
    result = await session.execute(
        text(
            "SELECT t.expires_at, t.scopes, g.scopes AS grant_scopes, g.id AS grant_id, "  # noqa: S608
            "g.client_id, g.member_id, "
            "g.resource, g.via_sso, c.client_name, m.role, "
            "COALESCE(ss.require_2fa, false) AS require_2fa, "
            "CAST(ss.ip_allowlist AS text[]) AS ip_allowlist, "
            + TWO_FACTOR_ON
            + ", "
            + ROLE_COLUMNS
            + " FROM oauth_token t JOIN oauth_grant g ON g.id = t.grant_id "
            "JOIN oauth_client c ON c.client_id = g.client_id "
            "JOIN member m ON m.id = g.member_id "
            + ROLE_JOIN
            + "LEFT JOIN hd_security_settings ss ON ss.workspace_id = t.workspace_id "
            "WHERE t.token_hash = :hash AND t.kind = 'access' AND t.revoked_at IS NULL "
            "AND t.expires_at > now() AND g.revoked_at IS NULL AND m.is_active "
            "AND c.disabled_at IS NULL"
        ),
        {"hash": token_hash},
    )
    return result.mappings().first()


async def sweep(session: AsyncSession, before: datetime) -> int:
    """Deletes codes and tokens that ended (expired or revoked) before `before`."""
    tokens = await session.execute(
        text("DELETE FROM oauth_token WHERE expires_at < :before OR revoked_at < :before"),
        {"before": before},
    )
    # A code stays while a token made from it lives (its reuse must still revoke them).
    codes = await session.execute(
        text(
            "DELETE FROM oauth_code c WHERE c.expires_at < :before "
            "AND NOT EXISTS (SELECT 1 FROM oauth_token t WHERE t.code_id = c.id)"
        ),
        {"before": before},
    )
    return int(tokens.rowcount) + int(codes.rowcount)  # type: ignore[attr-defined]
