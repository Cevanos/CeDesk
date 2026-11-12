"""SAML single sign-on (md S1).

- `hd_sso_settings`, one row per workspace: the identity provider's entity id, sign-in
  URL and signing certificate (public, so not sealed); `enabled` shows "Sign in with
  SSO"; `enforced` turns password sign-in off for everyone except Admins.
- `hd_sso_request`: each sign-in we start (the AuthnRequest id). An answer from the
  identity provider must name one of ours, unused and under 10 minutes old, and arrive
  from the browser that started it (`browser_hash`: the hash of a cookie set at start),
  so nobody can sign someone else's browser into their own account (login CSRF).
- `auth_session.via_sso`: signed in through the identity provider, so CeDesk's password
  expiry and 2FA rules don't apply to that session (the IdP has its own).

Revision ID: 0037
Revises: 0036
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_sso_settings (
            workspace_id   uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            enabled        boolean NOT NULL DEFAULT false,
            enforced       boolean NOT NULL DEFAULT false,
            idp_entity_id  text CHECK (length(idp_entity_id) <= 1024),
            idp_sso_url    text CHECK (length(idp_sso_url) <= 2048),
            idp_cert       text CHECK (length(idp_cert) <= 20000),
            updated_by     uuid,
            updated_at     timestamptz NOT NULL DEFAULT now(),
            CHECK (enabled OR NOT enforced),
            CHECK (NOT enabled OR (idp_entity_id IS NOT NULL AND idp_sso_url IS NOT NULL
                                   AND idp_cert IS NOT NULL)),
            FOREIGN KEY (workspace_id, updated_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (updated_by)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE hd_sso_request (
            id            text PRIMARY KEY CHECK (length(id) <= 200),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            browser_hash  bytea NOT NULL CHECK (length(browser_hash) = 32),
            created_at    timestamptz NOT NULL DEFAULT now(),
            used_at       timestamptz
        )
        """
    )
    op.execute("CREATE INDEX hd_sso_request_created ON hd_sso_request (workspace_id, created_at)")
    for table in ("hd_sso_settings", "hd_sso_request"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)
    op.execute("ALTER TABLE auth_session ADD COLUMN via_sso boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE auth_session DROP COLUMN via_sso")
    op.execute("DROP TABLE hd_sso_request")
    op.execute("DROP TABLE hd_sso_settings")
