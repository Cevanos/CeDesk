"""AI apps (Claude, ChatGPT, Codex, ...) can be connected to a member's workspace.

The OAuth 2.1 authorization server behind the CeDesk MCP connector keeps:

- `oauth_client` (global, no workspace): every app that has asked to connect. An app
  exists before any workspace grants it anything, and the same Claude is the same client
  for every workspace. A client found through its Client ID Metadata Document keeps the
  document's name and redirect URIs and when to fetch it again; a registered one keeps
  what it registered, and only the SHA-256 of its secret. The tenancy test lists it in
  GLOBAL_TABLES.
- `oauth_grant`: one member's consent for one app, with the scopes they agreed to. One
  live grant per member, app and resource; revoking it ends every token issued under it.
- `oauth_code`: single-use authorization codes, bound to the redirect URI, the PKCE
  challenge, the scopes and the resource.
- `oauth_token`: access and refresh tokens. Rotated refresh tokens share a `family_id`;
  a used one presented again revokes the family.
- `rate_limit_hit` (global): request counts per fixed window, shared by every API
  process (the OAuth endpoints per client address, /mcp per grant). Keys are SHA-256
  hashes, never an address or a token.

Codes and tokens are `<prefix><workspace hex>_<secret>`, like sessions and API keys: the
workspace part only says where to look, and the SHA-256 of the whole value must match a
row in that workspace under row-level security. Nothing existing is changed; downgrade
drops the five tables (every connected app must connect again).

Revision ID: 0087
Revises: 0086
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0087"
down_revision: str | None = "0086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE oauth_client (
            client_id                  text PRIMARY KEY CHECK (length(client_id) <= 2048),
            registration               text NOT NULL
                CHECK (registration IN ('dynamic', 'metadata_document', 'manual')),
            client_name                text CHECK (length(client_name) <= 200),
            client_uri                 text CHECK (length(client_uri) <= 2048),
            logo_uri                   text CHECK (length(logo_uri) <= 2048),
            redirect_uris              text[] NOT NULL
                CHECK (cardinality(redirect_uris) BETWEEN 1 AND 20),
            token_endpoint_auth_method text NOT NULL DEFAULT 'none' CHECK (
                token_endpoint_auth_method IN ('none', 'client_secret_post', 'client_secret_basic')
            ),
            client_secret_hash         bytea CHECK (length(client_secret_hash) = 32),
            scope                      text CHECK (length(scope) <= 1000),
            document_expires_at        timestamptz,
            disabled_at                timestamptz,
            created_at                 timestamptz NOT NULL DEFAULT now(),
            updated_at                 timestamptz NOT NULL DEFAULT now(),
            CHECK ((token_endpoint_auth_method = 'none') = (client_secret_hash IS NULL)),
            CHECK ((registration = 'metadata_document') = (document_expires_at IS NOT NULL)),
            CHECK (registration <> 'metadata_document' OR token_endpoint_auth_method = 'none')
        )
        """
    )
    # Clients are never deleted by the app: a grant references them.
    op.execute("REVOKE DELETE, TRUNCATE ON oauth_client FROM cedesk_app")

    op.execute(
        """
        CREATE TABLE oauth_grant (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            member_id     uuid NOT NULL,
            client_id     text NOT NULL REFERENCES oauth_client(client_id) ON DELETE CASCADE,
            resource      text NOT NULL CHECK (length(resource) <= 2048),
            scopes        text[] NOT NULL CHECK (cardinality(scopes) >= 1),
            -- Consented from a single sign-on session: the identity provider's own MFA
            -- stands in for the workspace's required 2FA, as for that session.
            via_sso       boolean NOT NULL DEFAULT false,
            created_at    timestamptz NOT NULL DEFAULT now(),
            last_used_at  timestamptz,
            revoked_at    timestamptz,
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, member_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    # One live consent per member, app and resource: consenting again updates it.
    op.execute(
        "CREATE UNIQUE INDEX oauth_grant_live ON oauth_grant "
        "(workspace_id, member_id, client_id, resource) WHERE revoked_at IS NULL"
    )
    op.execute("CREATE INDEX oauth_grant_member ON oauth_grant (workspace_id, member_id)")
    for statement in tenant_rls_sql("oauth_grant"):
        op.execute(statement)

    op.execute(
        """
        CREATE TABLE oauth_code (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id          uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            grant_id              uuid NOT NULL,
            code_hash             bytea NOT NULL UNIQUE CHECK (length(code_hash) = 32),
            redirect_uri          text NOT NULL CHECK (length(redirect_uri) <= 2048),
            redirect_uri_explicit boolean NOT NULL,
            code_challenge        text NOT NULL CHECK (code_challenge ~ '^[A-Za-z0-9_-]{43}$'),
            scopes                text[] NOT NULL,
            resource              text NOT NULL,
            expires_at            timestamptz NOT NULL,
            used_at               timestamptz,
            created_at            timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, grant_id)
                REFERENCES oauth_grant (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX oauth_code_age ON oauth_code (workspace_id, expires_at)")
    for statement in tenant_rls_sql("oauth_code"):
        op.execute(statement)

    op.execute(
        """
        CREATE TABLE oauth_token (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            grant_id      uuid NOT NULL,
            code_id       uuid NOT NULL,
            family_id     uuid NOT NULL,
            kind          text NOT NULL CHECK (kind IN ('access', 'refresh')),
            token_hash    bytea NOT NULL UNIQUE CHECK (length(token_hash) = 32),
            -- The code's scopes, or fewer when a refresh asked for fewer.
            scopes        text[] NOT NULL CHECK (cardinality(scopes) >= 1),
            expires_at    timestamptz NOT NULL,
            used_at       timestamptz,
            revoked_at    timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now(),
            CHECK (kind = 'refresh' OR used_at IS NULL),
            FOREIGN KEY (workspace_id, grant_id)
                REFERENCES oauth_grant (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, code_id)
                REFERENCES oauth_code (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX oauth_token_family ON oauth_token (workspace_id, family_id)")
    op.execute("CREATE INDEX oauth_token_grant ON oauth_token (workspace_id, grant_id)")
    op.execute("CREATE INDEX oauth_token_code ON oauth_token (workspace_id, code_id)")
    for statement in tenant_rls_sql("oauth_token"):
        op.execute(statement)

    op.execute(
        """
        CREATE TABLE rate_limit_hit (
            bucket        text NOT NULL CHECK (length(bucket) <= 40),
            key_hash      bytea NOT NULL CHECK (length(key_hash) = 32),
            window_start  timestamptz NOT NULL,
            hits          integer NOT NULL CHECK (hits >= 1),
            PRIMARY KEY (bucket, key_hash, window_start)
        )
        """
    )
    op.execute("CREATE INDEX rate_limit_hit_age ON rate_limit_hit (window_start)")


def downgrade() -> None:
    op.execute("DROP TABLE rate_limit_hit")
    op.execute("DROP TABLE oauth_token")
    op.execute("DROP TABLE oauth_code")
    op.execute("DROP TABLE oauth_grant")
    op.execute("DROP TABLE oauth_client")
