"""Customer portal sign-in (md P1, S7).

- `hd_portal_settings`: one row per workspace. `allowed_domains`: empty means any email
  can sign in; otherwise only those domains (and their subdomains).
- `hd_portal_code`: a 6-digit sign-in code, stored only as an HMAC bound to the
  workspace and email (never in plain text). Lasts 10 minutes; `tries` counts wrong
  guesses (5 burn it); `used_at` once it signed someone in or was burned.
- `hd_portal_session`: a signed-in customer (the contact). Only the token's SHA-256 is
  stored, like agent sessions; a separate table, so a portal token never works on the
  agent API and the other way round.

Revision ID: 0025
Revises: 0024
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_portal_settings (
            workspace_id     uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            allowed_domains  text[] NOT NULL DEFAULT '{}'
                             CHECK (cardinality(allowed_domains) <= 50),
            updated_by       uuid,
            updated_at       timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (workspace_id, updated_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (updated_by)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE hd_portal_code (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            email         text NOT NULL CHECK (email = lower(email) AND length(email) <= 254),
            code_hash     bytea NOT NULL,
            tries         int NOT NULL DEFAULT 0 CHECK (tries >= 0),
            expires_at    timestamptz NOT NULL,
            used_at       timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_portal_code_email ON hd_portal_code (workspace_id, email, created_at)"
    )
    op.execute(
        """
        CREATE TABLE hd_portal_session (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            contact_id    uuid NOT NULL,
            token_hash    bytea NOT NULL UNIQUE,
            expires_at    timestamptz NOT NULL,
            revoked_at    timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (workspace_id, contact_id)
                REFERENCES hd_contact (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_portal_session_contact ON hd_portal_session (workspace_id, contact_id)"
    )
    for table in ("hd_portal_settings", "hd_portal_code", "hd_portal_session"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_portal_session")
    op.execute("DROP TABLE hd_portal_code")
    op.execute("DROP TABLE hd_portal_settings")
